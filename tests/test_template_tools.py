import sys, unittest
from pathlib import Path
import fitz
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'pipeline'))
import new_template as NT   # noqa: E402
import frame_match as FM   # noqa: E402
import try_templates as TT   # noqa: E402
import cluster_frames as CL   # noqa: E402

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


def side_sheet(words=True):
    """part list on the left (420..575: 10 rows of 15pt + header NO./QTY at the bottom, columns 20/45/200/260/320) and the
    title block on the right (320..820) sharing the top 4 row rules (one cell 320..450 merged over two rows), then rows of
    its own (490, 530)"""
    doc = fitz.open(); p = doc.new_page(width=842, height=595); sh = p.new_shape()
    sh.draw_rect(I)
    for k in range(11):
        sh.draw_line((20, 425 + 15 * k), (320, 425 + 15 * k))
    for y in (425, 440, 455, 470):   # shared rules
        sh.draw_line((450 if y == 440 else 320, y), (820, y))
    for y in (490, 530):   # the title block's own rows
        sh.draw_line((320, y), (820, y))
    for x in (45, 200, 260, 320):
        sh.draw_line((x, 425), (x, 575))
    for x in (450, 600, 700):   # title-block columns: down to its own rows only
        sh.draw_line((x, 425), (x, 490))
    sh.draw_line((560, 490), (560, 575))
    sh.finish(color=(0, 0, 0), width=0.5); sh.commit()
    if words:
        for k in range(9):
            p.insert_text((25, 425 + 15 * k + 11), str(9 - k), fontsize=8); p.insert_text((50, 425 + 15 * k + 11), f'PART{k}', fontsize=8)
        p.insert_text((25, 571), 'NO. NAME MATERIAL QTY', fontsize=8)
    else:   # vector text (cad2pdf): a little stroke in every row, no words
        for k in range(10):
            sh = p.new_shape(); sh.draw_line((28, 425 + 15 * k + 5), (34, 425 + 15 * k + 9))
            sh.finish(color=(0, 0, 0), width=0.3); sh.commit()
    p.insert_text((330, 450), 'REV DATE', fontsize=8); p.insert_text((330, 510), 'DRAWN  CHECKED', fontsize=8)
    return doc, p


class PartTableTest(unittest.TestCase):
    def test_region_cut_through_part_list_moves_below_it(self):
        doc, p = sheet(6)
        D, W = p.get_drawings(), p.get_text('words')
        R = fitz.Rect(500, 430, 820, 575)   # title-block region whose top cuts the part list
        out, found, alt = NT.clear_tables(D, W, I, [R])
        self.assertEqual(len(found), 1)
        T, n, sure = found[0].rect, found[0].rows, found[0].sure
        self.assertTrue(sure); self.assertEqual(n, 6); self.assertFalse(found[0].side)
        self.assertAlmostEqual(T.y1, 460, delta=0.5); self.assertAlmostEqual(out[0].y0, 460, delta=0.5)

    def test_side_by_side_clears_only_the_part_list_columns(self):
        for words in (True, False):
            doc, p = side_sheet(words)
            R = fitz.Rect(20, 425, 820, 575)   # the region starts at the part list's top and runs over both
            out, found, alt = NT.clear_tables(p.get_drawings(), p.get_text('words'), I, [R])
            self.assertEqual(len(found), 1, words)
            t = found[0]
            self.assertTrue(t.sure and t.side, words); self.assertEqual(t.rows, 10)
            self.assertAlmostEqual(t.rect.x1, 320, delta=0.5); self.assertAlmostEqual(t.rect.y1, 575, delta=0.5)
            self.assertEqual(len(out), 1)   # the title block beside it stays, from the region's top
            self.assertAlmostEqual(out[0].x0, 320, delta=0.5); self.assertAlmostEqual(out[0].y0, 425, delta=0.5)
            self.assertAlmostEqual(out[0].x1, 820, delta=0.5)

    def test_side_by_side_title_block_goes_up_with_the_shared_rows(self):
        doc, p = side_sheet()
        R = fitz.Rect(20, 470, 820, 575)   # region top cuts through the shared rows
        out, found, alt = NT.clear_tables(p.get_drawings(), p.get_text('words'), I, [R])
        self.assertEqual(len(found), 1); self.assertTrue(found[0].sure and found[0].side)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0].x0, 320, delta=0.5)
        self.assertAlmostEqual(out[0].y0, 455, delta=0.5)   # up to the merged cell 320..450 (the rule at 440 stops at 450)

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


class PickTest(unittest.TestCase):
    def test_near_tie_keeps_page_as_it_stands(self):
        best = [(1.0, 270, 'LP', 0.12), (0.995, 0, 'LP', 0.12), (0.9, 90, 'X', 0.12)]
        self.assertEqual(FM.pick(best)[1], 0)
        self.assertEqual(FM.pick([(1.0, 270, 'LP', 0.12), (0.985, 0, 'LP', 0.12)])[1], 270)   # a real difference wins
        self.assertEqual(FM.pick([(1.0, 270, 'LP', 0.12), (0.995, 0, 'X', 0.12)])[2], 'LP')   # never swaps the template


class TryTemplatesTest(unittest.TestCase):
    def test_top_templates_are_distinct_with_own_rotation(self):
        best = [(0.95, 270, 'A', 0.12), (0.945, 0, 'A', 0.12), (0.93, 90, 'B', 0.3), (0.92, 0, 'A', 0.3), (0.9, 0, 'C', 0.12),
                (0.8, 0, 'D', 0.12)]
        self.assertEqual([(t, r) for _, r, t, _ in TT.top_templates(best, 3)], [('A', 0), ('B', 90), ('C', 0)])


class ClusterTest(unittest.TestCase):
    def test_star_clusters_do_not_chain(self):
        import numpy as np
        M = np.eye(5)
        for i in range(4): M[i, i + 1] = M[i + 1, i] = 0.9   # a chain 0-1-2-3-4: single linkage would make one group
        self.assertEqual(CL.cluster(M, 0.88, lambda i: 0), [(1, [0, 1, 2]), (3, [3, 4])])


if __name__ == '__main__':
    unittest.main()
