"""The selected-group QA shortcut must not pass a partial source inventory."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from source_inventory_gate import (  # noqa: E402
    BLOCKED, PASS, SourceInventoryBlocked, assess_candidate_completeness,
    assess_source_inventory, require_candidate_completeness_pass,
    require_source_inventory_pass, sha256_file, source_region_sha256,
)


class SourceInventoryGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "source.png"
        self.rgb = np.full((80, 100, 3), 255, np.uint8)
        self.rgb[10:25, 10:30] = [0, 0, 0]       # ordinary view
        self.rgb[10:25, 42:65] = [255, 0, 0]     # source-color technical emphasis
        self.rgb[40:48, 10:30] = [0, 0, 0]       # original tolerance values
        self.rgb[55:63, 10:30] = [50, 50, 50]    # supplier logo
        Image.fromarray(self.rgb).save(self.path)
        self.inventory = {
            "source_sha256": sha256_file(self.path),
            "source_size": [100, 80],
            "inventory_scope": "complete_original_page",
            "technical_groups": [
                {"id": "view", "kind": "view", "slot": "MAIN_ENGINEERING",
                 "source_boxes": [[10, 10, 30, 25]]},
                {"id": "red_dimension", "kind": "dimension", "slot": "MAIN_ENGINEERING",
                 "source_boxes": [[42, 10, 65, 25]]},
            ],
            "authorized_transforms": [{
                "id": "tolerance", "kind": "tolerance", "slot": "BOTTOM_TOLERANCE",
                "source_box": [10, 40, 30, 48],
                "source_region_sha256": source_region_sha256(self.rgb, (10, 40, 30, 48)),
                "source_inventory_complete": True,
                "source_reviewer": "independent source reviewer",
                "approval_reference": "approved English tolerance template",
                "fields": [{"source_field": "X.", "source_value": "±0.35",
                            "output_field": "X.", "output_value": "±0.35",
                            "comparison": "exact"}],
            }],
            "nontechnical_exclusions": [{
                "id": "supplier_logo", "kind": "supplier_logo",
                "source_box": [10, 55, 30, 63], "reason": "supplier brand, not a technical note",
                "reviewer": "independent source reviewer",
                "review_status": "APPROVED_NONTECHNICAL",
            }],
            "audit_zones": [{"id": "red_dimension", "source_box": [42, 10, 65, 25]}],
        }

    def test_complete_page_with_ledger_passes(self):
        report = assess_source_inventory(self.path, self.inventory)
        self.assertEqual(report["status"], PASS)
        self.assertEqual(report["unplaced_technical_ink_pixels"], 0)
        self.assertEqual(report["unplaced_object_count"], 0)
        require_source_inventory_pass(report)

    def test_selected_group_consistency_does_not_prove_completeness(self):
        self.inventory["technical_groups"].pop()  # selected view is perfectly copied
        report = assess_source_inventory(self.path, self.inventory)
        self.assertEqual(report["status"], BLOCKED)
        self.assertIn("FAIL_SOURCE_COMPLETENESS", report["issues"][-1])
        self.assertEqual(report["unplaced_by_zone"][0]["unplaced_technical_ink_pixels"], 15 * 23)
        with self.assertRaises(SourceInventoryBlocked):
            require_source_inventory_pass(report)

    def test_unknown_or_unapproved_exclusion_cannot_hide_technical_content(self):
        self.inventory["technical_groups"].pop()
        self.inventory["nontechnical_exclusions"].append({
            "id": "hide_red_dimension", "kind": "unknown",
            "source_box": [42, 10, 65, 25], "reason": "unknown", "reviewer": "none",
            "review_status": "PENDING",
        })
        report = assess_source_inventory(self.path, self.inventory)
        self.assertEqual(report["status"], BLOCKED)
        self.assertTrue(any("unknown/non-approved" in issue for issue in report["issues"]))

    def test_authorized_replacement_numeric_change_blocks(self):
        self.inventory["authorized_transforms"][0]["fields"][0]["output_value"] = "±0.25"
        report = assess_source_inventory(self.path, self.inventory)
        self.assertEqual(report["status"], BLOCKED)
        self.assertTrue(any("technical value changed" in issue for issue in report["issues"]))

    def test_replacement_region_is_bound_to_original_pixels(self):
        self.inventory["authorized_transforms"][0]["source_region_sha256"] = "0" * 64
        report = assess_source_inventory(self.path, self.inventory)
        self.assertEqual(report["status"], BLOCKED)
        self.assertTrue(any("fingerprint differs" in issue for issue in report["issues"]))

    def test_exclusion_must_not_overlap_technical_source(self):
        self.inventory["nontechnical_exclusions"][0]["source_box"] = [10, 10, 30, 25]
        report = assess_source_inventory(self.path, self.inventory)
        self.assertEqual(report["status"], BLOCKED)
        self.assertTrue(any("overlaps inventoried technical" in issue for issue in report["issues"]))

    def test_output_qa_must_account_for_every_inventoried_group_and_transform(self):
        inventory_report = assess_source_inventory(self.path, self.inventory)
        moved = [{"id": row["id"], "source_boxes": row["source_boxes"], "pixel_qa_pass": True}
                 for row in inventory_report["technical_groups"]]
        fields = {"tolerance": {"X.": "±0.35"}}
        complete = assess_candidate_completeness(inventory_report, moved, fields)
        self.assertEqual(complete["status"], "SOURCE_COMPLETENESS_PASS")
        require_candidate_completeness_pass(complete)
        missing = assess_candidate_completeness(inventory_report, moved[:1], fields)
        self.assertEqual(missing["status"], "FAIL_SOURCE_COMPLETENESS")
        with self.assertRaises(SourceInventoryBlocked):
            require_candidate_completeness_pass(missing)
        wrong_field = assess_candidate_completeness(inventory_report, moved, {"tolerance": {"X.": "±0.25"}})
        self.assertEqual(wrong_field["status"], "FAIL_SOURCE_COMPLETENESS")


if __name__ == "__main__":
    unittest.main()
