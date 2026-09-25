import json, sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
import auto_manifest as am


class FamilyConfigTest(unittest.TestCase):
    def test_family_has_only_relative_rules(self):
        fam = json.loads((ROOT / 'families' / 'zhiyuan.json').read_text())
        self.assertEqual(fam['color_profile'], 'nonblack-gold-v1')
        self.assertGreaterEqual(fam['layout']['min_font_pt'], 4.75)
        self.assertIn('fallback_performance_min_font_pt', fam['layout'])

    def test_rect_algebra_never_loses_area(self):
        import pymupdf as fitz
        a = fitz.Rect(0, 0, 10, 10); b = fitz.Rect(3, 3, 6, 6)
        parts = am.subtract(a, b)
        area = sum(p.width * p.height for p in parts)
        self.assertAlmostEqual(area, 100 - 9, places=6)
        self.assertTrue(all((p & b).is_empty or (p & b).width * (p & b).height == 0 for p in parts))

    def test_coordinates_are_float32_exact(self):
        for v in (123.456789, 0.1, 599.99):
            r = am.R([v, v, v, v])[0]
            import numpy as np
            self.assertEqual(float(np.float32(r)), r)


if __name__ == '__main__':
    unittest.main()
