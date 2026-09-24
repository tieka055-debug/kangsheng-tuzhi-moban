"""Product-batch routing tests use fictitious source files and mocked replay."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import stable_batch as batch
import pymupdf as fitz


class StableBatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "a.pdf").write_bytes(b"source-a")
        (self.root / "b.pdf").write_bytes(b"source-b")
        self.spec_path = self.root / "products.json"
        self.ledger = self.root / "golden-ledger.json"
        self.ledger.write_text("{}")
        self.args = SimpleNamespace(
            output_root=str(self.root / "out"), golden_ledger=str(self.ledger),
            catalog_dir=None, workers=1)

    def test_unknown_does_not_block_known_source(self):
        products = {"schema_version": batch.SCHEMA, "products": [
            {"id": "known", "model": "MODEL-A", "source": "a.pdf", "source_sha256": "a" * 64},
            {"id": "unknown", "model": "MODEL-B", "source": "b.pdf", "source_sha256": "b" * 64}]}
        self.spec_path.write_text(json.dumps(products))
        (self.root / "recipe.json").write_text(json.dumps({
            "bindings": {"engine": {"fitz_version": fitz.__version__}}}))

        def preflight(path, **kwargs):
            if Path(path).name == "a.pdf":
                return {"status": "EXACT_REPLAY_READY", "layout_id": "LAYOUT",
                        "recipe_path": str(self.root / "recipe.json"),
                        "source_sha256": "a" * 64, "reasons": []}
            return {"status": "NEEDS_AI_REVIEW", "layout_id": None,
                    "source_sha256": "b" * 64,
                    "candidate_layout_ids": [], "reasons": ["UNKNOWN_STRUCTURE"]}

        def replay(task):
            return {"product_id": task["product_id"], "model": task["model"],
                    "layout": task["layout"], "source_sha256": task["source_sha256"],
                    "engineering_release": False, "status": "PASS_VISUAL_REPLAY",
                    "total_seconds": 0.1, "reused": False, "different_pixels": 0}

        with patch.object(batch.supported_layouts, "preflight_source", side_effect=preflight), \
             patch.object(batch, "_fingerprint", return_value="f" * 64), \
             patch.object(batch, "_replay_worker", side_effect=replay):
            report = batch.run_products(self.args, products, self.spec_path)
        self.assertEqual(report["counts"],
                         {"NEEDS_AI_REVIEW": 1, "PASS_VISUAL_REPLAY": 1})
        self.assertEqual([row["product_id"] for row in report["results"]],
                         ["known", "unknown"])
        self.assertEqual(json.loads((self.root / "out" / "needs-ai-review.json").read_text())
                         ["items"][0]["reason_codes"], ["UNKNOWN_STRUCTURE"])

    def test_duplicate_ids_and_invalid_worker_count_stop_before_work(self):
        value = {"schema_version": batch.SCHEMA, "products": [
            {"id": "same", "source": "a.pdf"}, {"id": "same", "source": "b.pdf"}]}
        with self.assertRaisesRegex(ValueError, "Duplicate product ID"):
            batch.run_products(self.args, value, self.spec_path)
        self.args.workers = 3
        with self.assertRaisesRegex(ValueError, "workers"):
            batch.run_products(self.args, value, self.spec_path)

    def test_missing_explicit_source_binding_enters_review_queue(self):
        spec = {"schema_version": batch.SCHEMA, "products": [
            {"id": "missing-sha", "model": "MODEL-A", "source": "a.pdf"}]}
        with patch.object(batch.supported_layouts, "preflight_source") as preflight:
            report = batch.run_products(self.args, spec, self.spec_path)
        preflight.assert_not_called()
        self.assertEqual(report["results"][0]["reason_codes"],
                         ["IDENTITY_OR_SOURCE_SHA_MISSING"])

    def test_legacy_recipe_executes_only_with_pinned_runtime(self):
        out = self.root / "legacy-output"
        task = {"product_id": "legacy", "model": "MODEL-L", "layout": "LAYOUT",
                "source_sha256": "a" * 64, "source": str(self.root / "a.pdf"),
                "recipe": str(self.root / "recipe.json"), "output": str(out),
                "runtime_version": "1.26.5", "runtime_python": "/pinned/python"}
        answer = {"output_sha256": "b" * 64, "reused": False,
                  "different_pixels": 0, "generation_seconds": 1.25,
                  "qa_seconds": .5}
        with patch.object(batch.fitz, "__version__", "test-other-runtime"), \
             patch.object(batch.subprocess, "run", return_value=SimpleNamespace(
                returncode=0, stdout=json.dumps(answer), stderr="")) as process:
            row = batch._replay_worker(task)
        self.assertEqual(row["status"], "PASS_VISUAL_REPLAY")
        self.assertEqual(row["generation_seconds"], 1.25)
        self.assertEqual(row["qa_seconds"], .5)
        self.assertEqual(process.call_args.args[0][0], "/pinned/python")
        self.assertTrue((out / "replay-command.log").is_file())

    def test_cache_namespace_changes_with_policy_or_qa_file(self):
        recipe = self.root / "recipe.json"
        catalog = self.root / "catalog.json"
        anchor = self.root / "approval-anchor.json"
        recipe.write_text("r")
        catalog.write_text("c")
        anchor.write_text("a")
        scripts = self.root / "scripts"
        scripts.mkdir()
        for name in ("kangsheng.py", "approved_recipe.py", "frame.py",
                     "supported_layouts.py", "golden_regression.py", "stable_batch.py"):
            (scripts / name).write_text(name)
        runtime = {"fitz_version": fitz.__version__, "python": sys.executable}
        first = batch._fingerprint("0" * 64, recipe, catalog, self.ledger, scripts, runtime)
        (scripts / "golden_regression.py").write_text("updated QA")
        second = batch._fingerprint("0" * 64, recipe, catalog, self.ledger, scripts, runtime)
        self.assertNotEqual(first, second)
        catalog.write_text("changed layout")
        third = batch._fingerprint("0" * 64, recipe, catalog, self.ledger, scripts, runtime)
        self.assertNotEqual(second, third)
        fourth = batch._fingerprint("0" * 64, recipe, catalog, self.ledger, scripts,
                                   {"fitz_version": "1.26.5", "python": "/usr/bin/python3"})
        self.assertNotEqual(third, fourth)


if __name__ == "__main__":
    unittest.main()
