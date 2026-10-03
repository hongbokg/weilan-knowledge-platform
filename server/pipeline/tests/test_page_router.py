import unittest
from types import SimpleNamespace
from page_router import structured_table


def table(grid, values):
    return SimpleNamespace(row_count=len(grid),col_count=len(grid[0]),
                           rows=[SimpleNamespace(cells=row) for row in grid],extract=lambda:values)


class TableTests(unittest.TestCase):
    def test_merged_header_and_rowspan_keep_exact_grid(self):
        t=table([[(0,0,20,10),None],[(0,10,10,30),(10,10,20,20)],[None,(10,20,20,30)]],
                [['标题',None],['摄像头','217.00'],[None,'245.21']])
        rows, content, spans=structured_table(t)
        self.assertIn('colspan="2">标题',content)
        self.assertIn('rowspan="2">摄像头',content)
        self.assertEqual(len(spans),4)
        self.assertIn('245.21',content)
        self.assertEqual(rows[2][0],None)

    def test_missing_uncovered_cell_falls_back(self):
        t=table([[(0,0,10,10),(10,0,20,10)],[(0,10,10,20),None]],
                [['A','B'],['C',None]])
        with self.assertRaisesRegex(ValueError,'missing_table_cells'):
            structured_table(t)

    def test_overlap_is_not_silently_flattened(self):
        t=table([[(0,0,20,10),(10,0,20,10)]],[['A','B']])
        with self.assertRaisesRegex(ValueError,'overlapping_table_cells'):
            structured_table(t)


if __name__=='__main__':unittest.main()
