"""Conservative native PDF extraction; uncertain pages retain GPU OCR.

This module never opens a NAS file for writing and does not create provider calls.
"""
import collections
import html
import math
import re
from pathlib import Path

POLICY = 'native-first-v2'


def structured_table(table):
    """Recover spans from actual PDF cell rectangles, never from guessed text."""
    rows = table.extract()
    grid = [row.cells for row in table.rows]
    if len(rows) != table.row_count or any(len(row) != table.col_count for row in rows):
        raise ValueError('inconsistent_table_grid')
    boxes = [box for row in grid for box in row if box is not None]
    def boundaries(values):
        result = []
        for value in sorted(values):
            if not result or abs(result[-1]-value) > .5:
                result.append(value)
        return result
    xs = boundaries([box[i] for box in boxes for i in (0, 2)])
    ys = boundaries([box[i] for box in boxes for i in (1, 3)])
    if len(xs) != table.col_count+1 or len(ys) != table.row_count+1:
        raise ValueError('ambiguous_table_boundaries')
    occupied = set()
    rendered = []
    spans = []
    for ri, row in enumerate(grid):
        cells = []
        for ci, box in enumerate(row):
            if box is None:
                continue
            x0, y0, x1, y1 = [min(range(len(axis)), key=lambda j: abs(axis[j]-v))
                              for axis, v in ((xs, box[0]), (ys, box[1]), (xs, box[2]), (ys, box[3]))]
            if (x0, y0) != (ci, ri) or x1 <= x0 or y1 <= y0:
                raise ValueError('inconsistent_table_cell')
            covered = {(r, c) for r in range(y0, y1) for c in range(x0, x1)}
            if occupied & covered:
                raise ValueError('overlapping_table_cells')
            occupied.update(covered)
            attrs = (f' rowspan="{y1-y0}"' if y1-y0 > 1 else '') + (f' colspan="{x1-x0}"' if x1-x0 > 1 else '')
            cells.append('<td'+attrs+'>'+html.escape(rows[ri][ci] or '').replace('\n','<br>')+'</td>')
            spans.append({'row':ri,'column':ci,'rowspan':y1-y0,'colspan':x1-x0,'bbox':list(box)})
        rendered.append('<tr>'+''.join(cells)+'</tr>')
    if len(occupied) != table.row_count*table.col_count:
        raise ValueError('missing_table_cells')
    return rows, '<table>\n'+'\n'.join(rendered)+'\n</table>', spans


def normalized(text):
    return re.sub(r'\s+', '', text)


def inside(box, rect):
    x = (box[0] + box[2]) / 2
    y = (box[1] + box[3]) / 2
    return rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]


def analyze_page(source, index):
    import pymupdf as fitz
    with fitz.open(source) as doc:
        page = doc[index]
        native = page.get_text('text', sort=True)
        result = {'route': 'gpu', 'reason': 'native_unreliable', 'native': native,
                  'policy': POLICY, 'page': index + 1, 'page_count': len(doc),
                  'size_pt': [page.rect.width, page.rect.height]}
        plain = normalized(native)
        if len(plain) < 10 or '\ufffd' in native or '\x00' in native:
            return result
        bad = sum(not (c.isprintable() or c.isspace()) for c in native)
        if bad or any(t.get('type') == 3 or t.get('opacity', 1) < .95 for t in page.get_texttrace()):
            result['reason'] = 'hidden_or_invalid_text'
            return result
        # get_image_info returns placement metadata without copying encoded images.
        images = page.get_image_info()
        area = page.rect.width * page.rect.height
        if any((b['bbox'][2]-b['bbox'][0])*(b['bbox'][3]-b['bbox'][1]) > .5*area for b in images):
            result['reason'] = 'scan_with_text_layer'
            return result
        tables = page.find_tables().tables
        table_rects = [tuple(t.bbox) for t in tables]
        pieces = []
        for t in tables:
            try:
                rows, content, spans = structured_table(t)
            except ValueError:
                result['reason'] = 'complex_table'
                return result
            pieces.append({'type': 'table', 'bbox': list(t.bbox), 'content': content,
                           'rows': rows, 'row_count': t.row_count, 'col_count': t.col_count,
                           'cells': [list(c) if c else None for c in t.cells], 'spans':spans})
        blocks = []
        for b in page.get_text('blocks', sort=True):
            if b[6] != 0 or any(inside(b[:4], rect) for rect in table_rects):
                continue
            blocks.append({'type': 'text', 'bbox': list(b[:4]), 'content': b[4].strip()})
        # Keep multi-column pages in the layout-aware OCR path.
        for i, a in enumerate(blocks):
            for b in blocks[i+1:]:
                if min(a['bbox'][3], b['bbox'][3])-max(a['bbox'][1], b['bbox'][1]) > 4:
                    separated = a['bbox'][2]+20 < b['bbox'][0] or b['bbox'][2]+20 < a['bbox'][0]
                    if separated and min(len(a['content']), len(b['content'])) > 30:
                        result['reason'] = 'multicolumn'
                        return result
        pieces += blocks
        pieces.sort(key=lambda p: (p['bbox'][1], p['bbox'][0]))
        represented = ''.join(''.join(''.join(c or '' for c in row) for row in p['rows'])
                              if p['type'] == 'table' else p['content'] for p in pieces)
        wanted, got = collections.Counter(plain), collections.Counter(normalized(represented))
        coverage = sum((wanted & got).values()) / max(1, sum(wanted.values()))
        if coverage < .995:
            result['reason'] = 'native_coverage_low'
            return result
        # Avoid mistaking an unrecognized ruled table for ordinary paragraphs.
        if not tables and len(page.get_drawings()) > 12:
            result['reason'] = 'unresolved_vector_layout'
            return result
        regions = []
        for image in images:
            r = fitz.Rect(image['bbox']) & page.rect
            if r.is_empty:
                continue
            regions.append(list(r))
        result.update(route='mixed' if regions else 'cpu', reason='native_verified',
                      blocks=pieces, regions=regions, coverage=coverage,
                      markdown='\n\n'.join(p['content'] for p in pieces))
        return result


def render_regions(source, index, rects, target):
    """Render region pixels only, retaining exact crop-to-page transforms."""
    import pymupdf as fitz
    paths = []
    with fitz.open(source) as doc:
        page = doc[index]
        for n, rect in enumerate(rects):
            clip = fitz.Rect(rect)
            scale = 200/72
            if math.ceil(clip.width*scale)*math.ceil(clip.height*scale) > 24_000_000:
                raise ValueError('region_requires_tiling')
            path = str(target) + '.region-' + str(n) + '.png'
            page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip,
                            colorspace=fitz.csRGB, alpha=False).save(path)
            paths.append({'path': path, 'bbox_pt': rect, 'scale': scale})
    return paths


def map_region_box(box, crop, normalized_coordinates=False):
    """Use actual pixel coordinates; uncertain coordinate conventions stay null."""
    x0, y0, x1, y1 = crop['bbox_pt']
    if not box or len(box) != 4:
        return None
    if normalized_coordinates:
        return [x0+box[0]*(x1-x0), y0+box[1]*(y1-y0),
                x0+box[2]*(x1-x0), y0+box[3]*(y1-y0)]
    return [x0+box[0]/crop['scale'], y0+box[1]/crop['scale'],
            x0+box[2]/crop['scale'], y0+box[3]/crop['scale']]
