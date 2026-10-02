import sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
import brand as BR
import frame as KF


class BrandConfigTest(unittest.TestCase):
    def test_kangsheng_values_match_the_original_hardcoded_ones(self):
        b = BR.load('kangsheng')
        self.assertEqual(b['blue'], KF.BLUE)
        self.assertEqual(b['gold'], (217 / 255, 154 / 255, 0))
        self.assertEqual(list(b['frame']), KF.FRAME)
        self.assertEqual(list(b['reserved']), [400, 450, 820, 564])
        self.assertEqual(list(b['rail']), [600, 38, 814, 444])
        self.assertEqual(b['rail_min_x'], 524)
        self.assertEqual(list(b['views_area']), [KF.FRAME[0] + 10, KF.FRAME[1] + 10, KF.FRAME[2] - 8, KF.FRAME[3] - 8])
        self.assertEqual(b['views_area_bottom_with_slot'], 450 - 6)
        self.assertEqual(list(b['slot']), [KF.FRAME[0] + 10, 450 + 2, 400 - 10, KF.FRAME[3] - 6])
        self.assertEqual(list(b['title_box']), KF.TITLE_BOX)
        self.assertEqual(list(b['tolerance_box']), KF.TOLERANCE_BOX)
        self.assertEqual(b['output_suffix'], '康生图纸')
        self.assertEqual(b['compare_suffix'], '原图对照')

    def test_runqing_has_its_own_suffix_and_colours(self):
        b = BR.load('runqing')
        self.assertEqual(b['output_suffix'], '润擎图纸')
        self.assertNotEqual(b['blue'], KF.BLUE)
        self.assertTrue((ROOT / b['logo']).exists())

    def test_unknown_brand_is_rejected(self):
        with self.assertRaises(SystemExit):
            BR.load('nope')


if __name__ == '__main__':
    unittest.main()
