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

    def test_runqing_drops_only_listed_supplier_prefixes(self):
        b = BR.load('runqing')
        self.assertEqual(BR.brand_model(b, 'TF-A20F-C系列'), 'A20F-C系列')
        self.assertEqual(BR.brand_model(b, 'ND-D27M-12Pin-R2'), 'D27M-12Pin-R2')
        self.assertEqual(BR.brand_model(b, 'bg-A02F-C-S2'), 'A02F-C-S2')
        for keep in ('DC-002', 'BC-5P-5.0-003', 'LH-DC-022E', 'D27M-12Pin-R2', 'ATF-1'):
            self.assertEqual(BR.brand_model(b, keep), keep)
        self.assertEqual(BR.brand_model(BR.load('kangsheng'), 'TF-A20F-C'), 'TF-A20F-C')

    def test_unknown_brand_is_rejected(self):
        with self.assertRaises(SystemExit):
            BR.load('nope')


if __name__ == '__main__':
    unittest.main()
