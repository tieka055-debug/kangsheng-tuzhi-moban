"""Synthetic geometry-first inventory tests; no supplier or approved files used."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pymupdf as fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from geometry_blocks import (GeometryInventoryReview, extract_page_blocks,
                             require_geometry_inventory_pass)  # noqa: E402


class GeometryBlocksTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "synthetic.pdf"
        doc = fitz.open()
        page = doc.new_page(width=200, height=120)
        page.draw_rect(fitz.Rect(20, 20, 40, 40), color=(0, 0, 0), width=1)
        page.draw_rect(fitz.Rect(80, 20, 100, 40), color=(0, 0, 0), width=1)
        page.insert_text((22, 70), "TECH", fontsize=10)
        doc.save(self.source)
        doc.close()

    def test_unknown_is_owned_not_discarded(self):
        graph = extract_page_blocks(self.source, zoom=2, merge_gap_px=1)
        report = graph["report"]
        self.assertEqual(report["UNACCOUNTED_TECHNICAL_INK"], 0)
        self.assertGreater(report["UNKNOWN_TECHNICAL_BLOCKS"], 0)
        self.assertEqual(report["SOURCE_INVENTORY_STATUS"], "SOURCE_INVENTORY_PASS")
        self.assertEqual(report["source_ink_pixels"],
                         report["technical_or_unknown_ink_pixels"])
        self.assertEqual(sum(block["ink_pixels"] for block in report["blocks"]),
                         report["technical_or_unknown_ink_pixels"])

    def test_exclusion_requires_review_and_protected_ink_remains(self):
        exclusion = {"kind": "supplier_logo", "reason": "synthetic test logo",
                     "bbox": [160, 40, 200, 80]}
        unreviewed = extract_page_blocks(self.source, zoom=2,
                                         approved_nontechnical=[exclusion])
        self.assertEqual(unreviewed["report"]["pending_nontechnical_approval_count"], 1)
        self.assertEqual(unreviewed["report"]["SOURCE_INVENTORY_STATUS"],
                         "SOURCE_INVENTORY_REVIEW")
        self.assertGreater(unreviewed["report"]["UNACCOUNTED_TECHNICAL_INK"], 0)
        self.assertEqual(unreviewed["report"]["GEOMETRY_UNACCOUNTED_TECHNICAL_INK"], 0)
        with self.assertRaises(GeometryInventoryReview):
            require_geometry_inventory_pass(unreviewed)
        exclusion.update({"review_status": "APPROVED_NONTECHNICAL",
                          "reviewer": "synthetic independent review"})
        approved = extract_page_blocks(self.source, zoom=2,
                                       approved_nontechnical=[exclusion])
        self.assertEqual(approved["report"]["SOURCE_INVENTORY_STATUS"],
                         "SOURCE_INVENTORY_PASS")
        self.assertGreater(approved["report"]["approved_nontechnical_ink_pixels"], 0)
        self.assertEqual(approved["report"]["UNACCOUNTED_TECHNICAL_INK"], 0)
        self.assertIs(require_geometry_inventory_pass(approved), approved["report"])
        protected = extract_page_blocks(self.source, zoom=2,
            approved_nontechnical=[exclusion],
            protected_technical=[{"bbox": [160, 40, 200, 80]}])
        self.assertEqual(protected["report"]["approved_nontechnical_ink_pixels"], 0)
        self.assertEqual(protected["report"]["UNACCOUNTED_TECHNICAL_INK"], 0)

    def test_partition_is_semantic_annotation_not_coverage_shortcut(self):
        graph = extract_page_blocks(self.source, zoom=2,
            partition_regions=[{"bbox": [35, 35, 90, 90],
                                "classification": "part_table", "slot": "RIGHT_TOP_TABLE"}])
        report = graph["report"]
        self.assertEqual(report["UNACCOUNTED_TECHNICAL_INK"], 0)
        self.assertEqual(report["SOURCE_INVENTORY_STATUS"], "SOURCE_INVENTORY_PASS")
        self.assertTrue(any(b["classification"] == "part_table" for b in report["blocks"]))
        self.assertTrue(any(b["classification"] == "UNKNOWN_TECHNICAL" for b in report["blocks"]))

    def test_invalid_region_fails_instead_of_clipping_silently(self):
        with self.assertRaisesRegex(ValueError, "inside"):
            extract_page_blocks(self.source, zoom=2,
                approved_nontechnical=[{"kind": "outer_frame", "reason": "bad",
                                        "bbox": [-1, 0, 5, 5]}])


if __name__ == "__main__":
    unittest.main()
