import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
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
