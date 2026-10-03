"""Bounded CPU PDF rendering. Workers never share a PDFium document."""
from pathlib import Path
import math,time

def page_count(source):
    # PDFium is not thread safe, even when threads open different documents.
    # Metadata inspection therefore runs in the same process pool as rendering.
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(source) as doc:return len(doc)

def choose_scale(width,height,native,images):
    scale=200/72
    # Do not change vector/text-heavy pages or multi-image compositions.
    if not native.strip() and len(images)==1:
        image=images[0];x0,y0,x1,y1=image['bbox']
        if (x1-x0)*(y1-y0)>=width*height*.95 and x1>x0 and y1>y0:
            original=max(image['width']/(x1-x0),image['height']/(y1-y0))
            scale=min(scale,original)
    if math.ceil(width*scale)*math.ceil(height*scale)>24_000_000:
        raise ValueError('oversize_page_requires_tiling')
    return scale

def render_page(source,index,png):
    import pypdfium2 as pdfium
    start=time.monotonic();doc=pdfium.PdfDocument(source);page=doc[index]
    try:
        width,height=page.get_size();tp=page.get_textpage()
        try:native=tp.get_text_range()
        finally:tp.close()
        images=[]
        for obj in page.get_objects():
            if isinstance(obj,pdfium.PdfImage):
                m=obj.get_metadata();images.append({'width':m.width,'height':m.height,'bbox':obj.get_bounds()})
                if len(images)>1:break
        scale=choose_scale(width,height,native,images)
        render_started=time.monotonic();bitmap=page.render(scale=scale)
        pdf_render_seconds=time.monotonic()-render_started
        try:
            prepare_started=time.monotonic()
            image=bitmap.to_pil();hist=image.convert('L').histogram()
            blank=sum(hist[:235])/sum(hist)<.0003 and not native.strip()
            prepare_seconds=time.monotonic()-prepare_started
            encode_started=time.monotonic()
            try:image.save(png)
            finally:image.close()
            png_encode_seconds=time.monotonic()-encode_started
        finally:bitmap.close()
        if Path(png).stat().st_size>32*1024**2:
            Path(png).unlink();raise ValueError('page_image_budget')
        return {'native':native,'blank':blank,'source_size_pt':[width,height],'dpi':scale*72,
                'render_stage_seconds':{'pdf_render':round(pdf_render_seconds,6),
                                        'image_prepare':round(prepare_seconds,6),
                                        'png_encode':round(png_encode_seconds,6)},
                'render_seconds':round(time.monotonic()-start,3),'render_revision':'native-scale-prefetch-v1'}
    finally:page.close();doc.close()
