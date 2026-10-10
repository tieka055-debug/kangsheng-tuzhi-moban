import sys, unittest
from pathlib import Path
import fitz
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'pipeline'))
import new_template as NT   # noqa: E402

I = fitz.Rect(20, 20, 820, 575)


def sheet(part_rows, header='NO. NAME MATERIAL QTY', row='{n}  PART{k}'):
    """inner frame + title block (bottom right, 460..575) + a part list standing on it (rows of 15pt ending at 460)"""
    doc = fitz.open(); p = doc.new_page(width=842, height=595); sh = p.new_shape()
    sh.draw_rect(I)
    for y in (460, 490, 520, 545):   # title block rows (different heights, no common columns)
        sh.draw_line((500, y), (820, y))
    sh.draw_line((500, 460), (500, 575)); sh.draw_line((640, 490), (640, 575))
    top = 460 - 15 * part_rows
    for k in range(part_rows + 1):
        sh.draw_line((500, top + 15 * k), (820, top + 15 * k))
    for x in (500, 540, 680, 740, 820):
        sh.draw_line((x, top), (x, 460))
    sh.finish(color=(0, 0, 0), width=0.5); sh.commit()
    for k in range(part_rows - 1):
        p.insert_text((505, top + 15 * k + 11), row.format(n=part_rows - 1 - k, k=k), fontsize=8)
    p.insert_text((505, 456), header, fontsize=8)
    p.insert_text((505, 480), 'DRAWN  CHECKED', fontsize=8)
    return doc, p


class PartTableTest(unittest.TestCase):
    def test_region_cut_through_part_list_moves_below_it(self):
        doc, p = sheet(6)
        D, W = p.get_drawings(), p.get_text('words')
        R = fitz.Rect(500, 430, 820, 575)   # title-block region whose top cuts the part list
        out, found, alt = NT.clear_tables(D, W, I, [R])
        self.assertEqual(len(found), 1)
        T, n, sure = found[0]
        self.assertTrue(sure); self.assertEqual(n, 6)
        self.assertAlmostEqual(T.y1, 460, delta=0.5); self.assertAlmostEqual(out[0].y0, 460, delta=0.5)

    def test_title_block_alone_is_left_alone(self):
        doc, p = sheet(0)
        out, found, alt = NT.clear_tables(p.get_drawings(), p.get_text('words'), I, [fitz.Rect(500, 460, 820, 575)])
        self.assertEqual(found, []); self.assertAlmostEqual(out[0].y0, 460, delta=0.5)

    def test_table_starting_at_region_top_is_only_a_guess(self):
        doc, p = sheet(3)
        out, found, alt = NT.clear_tables(p.get_drawings(), p.get_text('words'), I, [fitz.Rect(500, 415, 820, 575)])
        self.assertEqual(len(found), 1); self.assertFalse(found[0][2])   # header word but no rows above the region
        self.assertAlmostEqual(out[0].y0, 415, delta=0.5); self.assertAlmostEqual(alt[0].y0, 460, delta=0.5)

    def test_revision_table_stays_furniture(self):
        doc, p = sheet(4, header='REV DESCRIPTION DATE', row='A{k}  2021-09-17')
        out, found, alt = NT.clear_tables(p.get_drawings(), p.get_text('words'), I, [fitz.Rect(500, 430, 820, 575)])
        self.assertEqual(found, [])


if __name__ == '__main__':
    unittest.main()
