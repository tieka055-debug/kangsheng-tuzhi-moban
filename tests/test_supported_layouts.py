"""Synthetic-only tests for fail-closed structural preflight."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf as fitz


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import supported_layouts as layouts
import approved_recipe as approved


class SupportedLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        source = fitz.open()
        page = source.new_page(width=842, height=595)
        page.insert_text((40, 50), "SYNTHETIC PART NO. X-1", fontsize=12)
        page.insert_text((40, 70), "PCB LAYOUT TOLERANCE MATERIAL", fontsize=10)
        page.draw_rect(fitz.Rect(40, 100, 180, 160), color=(0, 0, 0))
        self.source = self.base / "source.pdf"
        source.save(self.source)
        source.close()
        self.approved = self.base / "approved.pdf"
        self.approved.write_bytes(self.source.read_bytes())
        self.sha = layouts.sha256_file(self.source)
        self.approved_sha = layouts.sha256_file(self.approved)
        self.catalog_dir = self.base / "supported-layouts"
        self.catalog_dir.mkdir()
        self.layout = {"layout_id": "synthetic-vector-exact", "status": "SUPPORTED_EXACT",
                       "programmatically_verified_distinct_sources": 1,
                       "fallback": "NEEDS_AI_REVIEW", "structure_probe": {
                           "page_count": 1, "orientation": "landscape", "rotation": 0,
                           "text_extractable": True, "signals": {"pcb_label": True,
                                                                  "part_number_header": True,
                                                                  "tolerance_label": True}}}
        self.catalog = {"schema_version": layouts.CATALOG_SCHEMA, "layouts": [self.layout]}
        self.save_catalog()
        self.ledger = self.base / "golden-ledger.json"
        self.recipe = self.base / "recipe.json"
        self.recipe.write_text("{}")
        self.sample = {"sample_id": "synthetic-a", "model": "SYNTHETIC X-1",
                       "source_sha256": self.sha, "approved_pdf_path": str(self.approved),
                       "approved_pdf_sha256": self.approved_sha, "recipe_path": str(self.recipe),
                       "recipe_sha256": layouts.sha256_file(self.recipe),
                       "supported_layout_id": self.layout["layout_id"],
                       "user_acceptance_status": "ACCEPTED", "blocker_reason": None}
        self.save_private()

    def save_catalog(self):
        (self.catalog_dir / "catalog.json").write_text(json.dumps(self.catalog))

    def save_private(self):
        self.ledger.write_text(json.dumps({"schema_version": layouts.LEDGER_SCHEMA,
                                           "samples": [self.sample]}))
        self.ledger.with_name("approval-anchor.json").write_text(json.dumps({
            "schema_version": layouts.ANCHOR_SCHEMA,
            "samples": [{"sample_id": self.sample["sample_id"],
                         "source_sha256": self.sample["source_sha256"],
                         "approved_pdf_sha256": self.sample["approved_pdf_sha256"]}]}))

    def preflight(self, source=None, **kwargs):
        return layouts.preflight_source(source or self.source,
                                        catalog_dir=self.catalog_dir,
                                        private_ledger=self.ledger, **kwargs)

    def test_public_catalog_contains_only_structure(self):
        actual = layouts.load_catalog(ROOT / "supported-layouts" / "catalog.json")
        self.assertEqual(len(actual["layouts"]), 5)
        self.assertEqual([x["status"] for x in actual["layouts"]].count("SUPPORTED_EXACT"), 4)
        data = (ROOT / "supported-layouts" / "catalog.json").read_text()
        self.assertNotIn("/Users/", data)
        self.assertNotIn(self.sha, data)

    def test_generic_promotion_requires_two_sources_and_extractor(self):
        self.layout["status"] = "SUPPORTED"
        self.layout["parameter_extractor"] = "synthetic-extractor"
        self.save_catalog()
        with self.assertRaisesRegex(ValueError, "two verified distinct sources"):
            layouts.load_catalog(self.catalog_dir / "catalog.json")
        self.layout["programmatically_verified_distinct_sources"] = 2
        del self.layout["parameter_extractor"]
        self.save_catalog()
        with self.assertRaisesRegex(ValueError, "parameter extractor"):
            layouts.load_catalog(self.catalog_dir / "catalog.json")

    def test_structural_probe_is_observation_not_permission(self):
        found = layouts.inspect_source(self.source)
        self.assertEqual(found["page_count"], 1)
        self.assertTrue(found["signals"]["pcb_label"])
        result = layouts.preflight_source(self.source, catalog_dir=self.catalog_dir)
        self.assertEqual(result["candidate_layout_ids"], ["synthetic-vector-exact"])
        self.assertEqual(result["status"], layouts.REVIEW)
        self.assertIn("PRIVATE_GOLDEN_LEDGER_NOT_PROVIDED", result["reasons"])

    def test_registered_source_needs_valid_recipe_binding(self):
        result = self.preflight(expected_identity="SYNTHETIC X-1")
        self.assertEqual(result["status"], layouts.REVIEW)
        self.assertIn("RECIPE_VALIDATION_ERROR", result["reasons"])

    def test_exact_route_when_all_bindings_validate(self):
        with patch.object(layouts, "_check_exact_recipe", return_value=[]):
            result = self.preflight(expected_identity="SYNTHETIC X-1")
        self.assertEqual(result["status"], layouts.EXACT_READY)
        self.assertEqual(result["recipe_path"], str(self.recipe))

    def test_wrong_identity_blocks_even_when_hash_matches(self):
        with patch.object(layouts, "_check_exact_recipe", return_value=[]):
            result = self.preflight(expected_identity="OTHER MODEL")
        self.assertEqual(result["status"], layouts.REVIEW)
        self.assertIn("EXPECTED_IDENTITY_MISMATCH", result["reasons"])

    def test_same_filename_or_similar_structure_never_routes_unknown_source(self):
        candidate = self.base / "same-name" / "source.pdf"
        candidate.parent.mkdir()
        pdf = fitz.open(self.source)
        pdf[0].insert_text((40, 190), "EXTRA TECHNICAL NOTE", fontsize=9)
        pdf.save(candidate)
        pdf.close()
        self.assertNotEqual(layouts.sha256_file(candidate), self.sha)
        result = self.preflight(candidate)
        self.assertEqual(result["status"], layouts.REVIEW)
        self.assertIn("SOURCE_NOT_UNIQUELY_REGISTERED", result["reasons"])

    def test_sha_mismatch_blocks_before_catalog_route(self):
        result = self.preflight(expected_sha256="0" * 64)
        self.assertIn("SOURCE_SHA_MISMATCH", result["reasons"])

    def test_anchor_mismatch_blocks(self):
        anchor = self.ledger.with_name("approval-anchor.json")
        value = json.loads(anchor.read_text())
        value["samples"][0]["approved_pdf_sha256"] = "0" * 64
        anchor.write_text(json.dumps(value))
        with patch.object(layouts, "_check_exact_recipe", return_value=[]):
            result = self.preflight()
        self.assertIn("APPROVAL_ANCHOR_MISMATCH", result["reasons"])

    def test_blocked_approval_cannot_route(self):
        self.sample["user_acceptance_status"] = "UNRESOLVED_BASELINE_CONFLICT"
        self.sample["blocker_reason"] = "synthetic conflict"
        self.save_private()
        with patch.object(layouts, "_check_exact_recipe", return_value=[]):
            result = self.preflight()
        self.assertIn("GOLDEN_APPROVAL_BLOCKED", result["reasons"])

    def test_candidate_cannot_route_even_with_registered_exact_source(self):
        self.layout["status"] = "CANDIDATE"
        self.save_catalog()
        with patch.object(layouts, "_check_exact_recipe", return_value=[]):
            result = self.preflight()
        self.assertIn("LAYOUT_CANDIDATE_UNPROMOTED", result["reasons"])

    def test_multi_page_source_blocks(self):
        candidate = self.base / "multi.pdf"
        document = fitz.open(self.source)
        document.new_page()
        document.save(candidate)
        document.close()
        result = self.preflight(candidate)
        self.assertEqual(result["status"], layouts.REVIEW)
        self.assertIn("SOURCE_NOT_SINGLE_UNENCRYPTED_PAGE", result["reasons"])

    def test_modified_approved_file_blocks(self):
        self.approved.write_bytes(b"different")
        with patch.object(layouts, "_check_exact_recipe", return_value=[]):
            result = self.preflight()
        self.assertIn("APPROVED_PDF_CHANGED", result["reasons"])

    def test_old_fitz_runtime_requires_explicit_availability(self):
        old_version = "1.26.5-test"
        recipe = {"recipe_schema": approved.SCHEMA,
                  "configuration": {"source": {"sha256": self.sha, "path": str(self.source)}},
                  "operations": [], "engineering_release": False,
                  "bindings": {"source_sha256": self.sha,
                               "baseline": {"path": str(self.approved), "sha256": self.approved_sha},
                               "assets": {},
                               "engine": {"scripts": approved._engine()["scripts"],
                                          "fitz_version": old_version}}}
        recipe["configuration_operations_sha256"] = approved._hash({
            "configuration": recipe["configuration"], "operations": recipe["operations"]})
        recipe["recipe_sha256"] = approved._hash(recipe)
        self.recipe.write_text(json.dumps(recipe))
        self.sample["recipe_sha256"] = layouts.sha256_file(self.recipe)
        self.save_private()
        with patch.object(approved, "_validate", return_value=(recipe["configuration"], [])):
            result = self.preflight()
            self.assertIn("RECIPE_FITZ_RUNTIME_UNAVAILABLE", result["reasons"])
            allowed = self.preflight(available_fitz_versions=frozenset({fitz.__version__, old_version}))
        self.assertEqual(allowed["status"], layouts.EXACT_READY)

    def test_invalid_fitz_runtime_set_fails_closed(self):
        result = self.preflight(available_fitz_versions={})
        self.assertEqual(result["status"], layouts.REVIEW)
        self.assertIn("INVALID_FITZ_RUNTIME_SET", result["reasons"])


if __name__ == "__main__":
    unittest.main()
