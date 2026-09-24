"""Synthetic color-gate tests; private customer fixtures stay outside the repository."""
import io
import sys
import tempfile
import unittest
from pathlib import Path

import fitz
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import source_color_roles as color


class SourceColorRoleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = color.load_family_profile(
            ROOT / "supported-layouts/source-color-roles.json",
            "cn-battery-multiview-pcb-part-table-raster-variant")

    def source(self):
        data = np.full((50, 100, 3), 255, dtype=np.uint8)
        data[5:15, 5:25] = (0, 0, 0)      # ordinary technical line
        data[20:30, 5:25] = (100, 100, 100)  # ordinary gray annotation
        data[5:15, 35:55] = (255, 0, 0)    # red dimension label
        data[20:30, 35:55] = (0, 255, 0)   # green PCB/dimension leader
        data[35:39, 35:45] = (160, 143, 49) # red-green overprint
        return Image.fromarray(data)

    def test_explicit_roles_and_unknown_are_not_default_blue(self):
        report = color.scan_source_colors(self.source(), self.profile)
        self.assertEqual(report["status"], color.PASS)
        self.assertGreater(report["technical_emphasis_gold_pixels"], 0)
        self.assertGreater(report["ordinary_blue_pixels"], 0)
        self.assertEqual(report["unknown_chromatic_pixels"], 0)
        rgba, _ = color.recolor_source_crop(self.source(), self.profile)
        self.assertEqual(rgba.getpixel((6, 6))[:3], color.BLUE)
        self.assertEqual(rgba.getpixel((6, 21))[:3], color.BLUE)
        for point in ((36, 6), (36, 21), (36, 36)):
            self.assertEqual(rgba.getpixel(point)[:3], color.GOLD)
        # Recoloring does not alter any source ink silhouette or numeric glyph.
        raw = np.asarray(self.source(), dtype=np.int16)
        expected_alpha = np.minimum(255, (255 - raw.min(2)) * 2.3).astype(np.uint8)
        expected_alpha[expected_alpha < 7] = 0
        np.testing.assert_array_equal(np.asarray(rgba)[:, :, 3], expected_alpha)

        unknown = np.array(self.source())
        unknown[40:45, 50:60] = (0, 255, 255) # cyan never approved here
        unknown = Image.fromarray(unknown)
        review = color.scan_source_colors(unknown, self.profile)
        self.assertEqual(review["status"], color.REVIEW)
        self.assertGreater(review["unknown_chromatic_pixels"], 0)
        with self.assertRaisesRegex(ValueError, color.REVIEW):
            color.recolor_source_crop(unknown, self.profile)

    def test_unreviewed_profile_cannot_pass(self):
        profile = dict(self.profile, review_status="PENDING_LOCAL_REVIEW")
        self.assertEqual(color.scan_source_colors(self.source(), profile)["status"], color.REVIEW)
        with self.assertRaisesRegex(ValueError, color.REVIEW):
            color.recolor_source_crop(self.source(), profile)

    def _pdf_with_image(self, path, image):
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        doc = fitz.open()
        page = doc.new_page(width=200, height=100)
        page.insert_image(fitz.Rect(10, 10, 110, 60), stream=stream.getvalue())
        doc.save(path)
        doc.close()

    def test_pdf_color_qa_rejects_all_blue_and_all_gold(self):
        source = self.source()
        good, _ = color.recolor_source_crop(source, self.profile)
        placement = [{"role": "technical_view", "source": [0, 0, 100, 50],
                      "target": [10, 10, 110, 60]}]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "source.png"
            source.save(source_path)
            good_path = root / "good.pdf"
            self._pdf_with_image(good_path, good)
            report = color.audit_pdf_color_semantics(
                source_path, good_path, placement, self.profile, "SOURCE_INVENTORY_PASS")
            self.assertEqual(report["status"], color.PASS, report)
            self.assertEqual(report["geometry_mismatch_pixels"], 0)
            self.assertEqual(report["gold_mismatch_pixels"], 0)
            self.assertEqual(report["blue_mismatch_pixels"], 0)

            blocked = color.audit_pdf_color_semantics(
                source_path, good_path, placement, self.profile, "SOURCE_INVENTORY_BLOCKED")
            self.assertEqual(blocked["status"], color.REVIEW)

            rgba = np.asarray(good).copy()
            rgba[:, :, :3] = color.BLUE
            all_blue = root / "wrong-blue.pdf"
            self._pdf_with_image(all_blue, Image.fromarray(rgba))
            failed = color.audit_pdf_color_semantics(
                source_path, all_blue, placement, self.profile, "SOURCE_INVENTORY_PASS")
            self.assertEqual(failed["status"], color.FAIL)
            self.assertGreater(failed["gold_mismatch_pixels"], 0)
            self.assertEqual(failed["geometry_mismatch_pixels"], 0)

            rgba[:, :, :3] = color.GOLD
            all_gold = root / "wrong-gold.pdf"
            self._pdf_with_image(all_gold, Image.fromarray(rgba))
            failed = color.audit_pdf_color_semantics(
                source_path, all_gold, placement, self.profile, "SOURCE_INVENTORY_PASS")
            self.assertEqual(failed["status"], color.FAIL)
            self.assertGreater(failed["blue_mismatch_pixels"], 0)

            geometry = np.asarray(good).copy()
            geometry[6, 6, 3] = 0
            wrong_geometry = root / "wrong-geometry.pdf"
            self._pdf_with_image(wrong_geometry, Image.fromarray(geometry))
            failed = color.audit_pdf_color_semantics(
                source_path, wrong_geometry, placement, self.profile, "SOURCE_INVENTORY_PASS")
            self.assertEqual(failed["status"], color.FAIL)
            self.assertGreater(failed["geometry_mismatch_pixels"], 0)

if __name__ == "__main__":
    unittest.main()
