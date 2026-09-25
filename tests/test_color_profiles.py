import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'engine'))
import kangsheng as k
class ColorProfileTest(unittest.TestCase):
    def test_legacy_is_unchanged(self):
        self.assertEqual(k.classify_color((1,0,0)),(217/255,154/255,0))
        self.assertEqual(k.classify_color((0,0,0)),k.BLUE)
    def test_approved_cyan_contacts_only(self):
        self.assertEqual(k.classify_color((0,1,1),'cyan-gold-v1'),(217/255,154/255,0))
        for rgb in [(1,0,0),(0,0,1),(0,0,0),(0,1,0)]:
            self.assertEqual(k.classify_color(rgb,'cyan-gold-v1'),k.BLUE)
        self.assertEqual(k.classify_color((1,1,1),'cyan-gold-v1'),(1,1,1))
    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(ValueError):k.classify_color((0,0,0),'missing')
    def test_svg_profile_is_explicit(self):
        text=k.svg_recolor('<svg><path stroke="#ff0000" fill="#00ffff" /></svg>','cyan-gold-v1')
        self.assertIn('stroke="#0642a8"',text);self.assertIn('fill="#d99a00"',text)


class NonblackGoldTest(unittest.TestCase):
    """User rule 2026-09-25: black/grey -> Kangsheng blue; every other colour -> gold."""
    def test_black_and_grey_are_blue(self):
        for rgb in [(0,0,0),(.3,.3,.3),(.6,.62,.6)]:
            self.assertEqual(k.classify_color(rgb,'nonblack-gold-v1'),k.BLUE)
    def test_any_colour_is_gold(self):
        for rgb in [(1,0,0),(0,1,0),(0,1,1),(0,0,1),(1,0,1),(1,1,0)]:
            self.assertEqual(k.classify_color(rgb,'nonblack-gold-v1'),(217/255,154/255,0))
    def test_white_stays(self):
        self.assertEqual(k.classify_color((1,1,1),'nonblack-gold-v1'),(1,1,1))
