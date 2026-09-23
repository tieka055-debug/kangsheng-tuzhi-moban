"""Fail-closed local product batch for exact approved-source replay.

This is called only through kangsheng.py's batch command.  It does not upload,
approve engineering release, or use a similar product's geometry or values.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import multiprocessing
import re
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pymupdf as fitz

import supported_layouts


SCHEMA = "kangsheng-products-v1"
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}\Z")


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def _resolve(base, path):
    path = Path(path).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _fingerprint(source_sha, recipe, catalog, ledger, engine_dir, runtime):
    # This is a cache namespace, not an approval.  Every relevant implementation
    # or policy change forces a fresh output directory and QA run.
    files = [
        Path(recipe), Path(catalog), Path(ledger),
        Path(ledger).with_name("approval-anchor.json"),
        *(Path(engine_dir) / name for name in (
            "kangsheng.py", "approved_recipe.py", "frame.py",
            "supported_layouts.py", "golden_regression.py", "stable_batch.py")),
    ]
    data = {"source_sha256": source_sha, "runtime": runtime,
            "files": {str(path): _sha(path) for path in files}}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _replay_worker(task):
    """Process-isolated PDF work.  Only the parent writes the batch ledger."""
    import approved_recipe

    started = time.perf_counter()
    result = {"product_id": task["product_id"], "model": task["model"],
              "layout": task["layout"], "source_sha256": task["source_sha256"],
              "engineering_release": False}
    try:
        if task["runtime_version"] == fitz.__version__:
            replay = approved_recipe._replay(SimpleNamespace(
                recipe=task["recipe"], output=task["output"], _source=task["source"]))
        else:
            command = [task["runtime_python"], str(Path(__file__).with_name("kangsheng.py")),
                       "replay", task["recipe"], "--output", task["output"]]
            process = subprocess.run(command, capture_output=True, text=True)
            log = Path(task["output"]) / "replay-command.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("stdout:\n" + process.stdout + "\nstderr:\n" + process.stderr)
            if process.returncode:
                raise ValueError(f"Pinned runtime replay failed: {process.stderr[-1000:]}")
            replay = json.loads(process.stdout)
        result.update(status="PASS_VISUAL_REPLAY",
                      output_pdf=str(Path(task["output"]) / "drawing.pdf"),
                      output_sha256=replay["output_sha256"],
                      qa_report=str(Path(task["output"]) / "replay-check.json"),
                      reused=bool(replay.get("reused")),
                      different_pixels=replay.get("different_pixels", 0),
                      generation_seconds=replay.get("generation_seconds", 0),
                      qa_seconds=replay.get("qa_seconds", 0))
    except Exception as exc:
        result.update(status="NEEDS_AI_REVIEW", reason_codes=["REPLAY_OR_QA_FAILED"],
                      error_type=type(exc).__name__, error=str(exc))
    result["total_seconds"] = round(time.perf_counter() - started, 3)
    return result


def run_products(args, spec, spec_path):
    if spec.get("schema_version") != SCHEMA or not isinstance(spec.get("products"), list):
        raise ValueError("Product batch needs kangsheng-products-v1 and a products list")
    if not spec["products"]:
        raise ValueError("Product batch is empty")
    if not args.golden_ledger:
        raise ValueError("Product batch requires --golden-ledger outside the public repository")
    if args.workers not in (1, 2, 4):
        raise ValueError("--workers must be 1, 2, or 4")

    source_root = Path(spec_path).parent
    out = Path(args.output_root).resolve()
    out.mkdir(parents=True, exist_ok=True)
    ledger = Path(args.golden_ledger).resolve()
    catalog = (Path(args.catalog_dir).resolve() / "catalog.json" if args.catalog_dir
               else supported_layouts.DEFAULT_CATALOG)
    engine_dir = Path(__file__).resolve().parent
    runtimes = {fitz.__version__: sys.executable}
    if getattr(args, "runtime_126", None):
        pinned = str(Path(args.runtime_126).resolve())
        process = subprocess.run([pinned, "-c", "import pymupdf; print(pymupdf.__version__)"],
                                 capture_output=True, text=True)
        if process.returncode or process.stdout.strip() != "1.26.5":
            raise ValueError("--runtime-126 must run a Python with PyMuPDF 1.26.5")
        runtimes["1.26.5"] = pinned
    ids = [item.get("id") for item in spec["products"]]
    if any(not isinstance(item, str) or not ID.fullmatch(item) for item in ids):
        raise ValueError("Product IDs must be unique filesystem-safe strings")
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate product ID")

    results = []
    ready = []
    seen_sources = set()
    for product in spec["products"]:
        started = time.perf_counter()
        source = _resolve(source_root, product.get("source", ""))
        base = {"product_id": product["id"], "model": product.get("model"),
                "source": str(source), "engineering_release": False}
        try:
            if (not isinstance(product.get("model"), str) or not product["model"].strip()
                    or not isinstance(product.get("source_sha256"), str)
                    or re.fullmatch(r"[0-9a-f]{64}", product["source_sha256"]) is None):
                base.update(status="NEEDS_AI_REVIEW", reason_codes=["IDENTITY_OR_SOURCE_SHA_MISSING"])
                results.append(base)
                continue
            preflight = supported_layouts.preflight_source(
                source, expected_sha256=product.get("source_sha256"),
                expected_identity=product.get("model"),
                catalog_dir=args.catalog_dir, private_ledger=ledger,
                available_fitz_versions=set(runtimes))
            base.update(layout=preflight.get("layout_id"),
                        source_sha256=preflight.get("source_sha256"),
                        preflight_seconds=round(time.perf_counter() - started, 3))
            if preflight["status"] != supported_layouts.EXACT_READY:
                base.update(status="NEEDS_AI_REVIEW",
                            reason_codes=preflight.get("reasons") or ["UNKNOWN_STRUCTURE"],
                            candidate_layout_ids=preflight.get("candidate_layout_ids", []))
                results.append(base)
                continue
            if preflight["source_sha256"] in seen_sources:
                base.update(status="NEEDS_AI_REVIEW", reason_codes=["DUPLICATE_SOURCE_IN_BATCH"])
                results.append(base)
                continue
            seen_sources.add(preflight["source_sha256"])
            recipe = json.loads(Path(preflight["recipe_path"]).read_text())
            runtime_version = recipe["bindings"]["engine"]["fitz_version"]
            runtime_python = runtimes[runtime_version]
            fingerprint = _fingerprint(preflight["source_sha256"],
                                       preflight["recipe_path"], catalog, ledger,
                                       engine_dir, {"fitz_version": runtime_version,
                                                    "python": runtime_python})
            ready.append({**base, "recipe": preflight["recipe_path"],
                          "output": str(out / product["id"] / fingerprint[:20]),
                          "fingerprint": fingerprint,
                          "runtime_version": runtime_version,
                          "runtime_python": runtime_python})
        except Exception as exc:
            base.update(status="NEEDS_AI_REVIEW", reason_codes=["PREFLIGHT_ERROR"],
                        error_type=type(exc).__name__, error=str(exc),
                        preflight_seconds=round(time.perf_counter() - started, 3))
            results.append(base)

    def tasks():
        for item in ready:
            yield {"product_id": item["product_id"], "model": item["model"],
                   "layout": item["layout"], "source_sha256": item["source_sha256"],
                   "source": item["source"], "recipe": item["recipe"],
                   "output": item["output"],
                   "runtime_version": item["runtime_version"],
                   "runtime_python": item["runtime_python"]}

    if args.workers == 1:
        produced = [_replay_worker(task) for task in tasks()]
    else:
        # PyMuPDF documents are never shared across workers. Spawn new
        # interpreters rather than forking a parent that has opened PDFs.
        with concurrent.futures.ProcessPoolExecutor(
                max_workers=args.workers,
                mp_context=multiprocessing.get_context("spawn")) as pool:
            produced = list(pool.map(_replay_worker, tasks()))
    preflight_by_id = {item["product_id"]: item for item in ready}
    for item in produced:
        initial = preflight_by_id[item["product_id"]]
        item["preflight_seconds"] = initial["preflight_seconds"]
        item["cache_fingerprint"] = initial["fingerprint"]
        item["total_seconds"] = round(item["total_seconds"] + initial["preflight_seconds"], 3)
        results.append(item)
    order = {item["id"]: number for number, item in enumerate(spec["products"])}
    results.sort(key=lambda item: order[item["product_id"]])
    queue = [{key: value for key, value in row.items()
              if key in ("product_id", "model", "source", "source_sha256",
                         "layout", "reason_codes", "candidate_layout_ids",
                         "error_type", "error")}
             for row in results if row["status"] == "NEEDS_AI_REVIEW"]
    report = {"schema_version": SCHEMA, "engineering_release": False,
              "workers": args.workers, "results": results,
              "counts": {status: sum(row["status"] == status for row in results)
                         for status in sorted({row["status"] for row in results})}}
    _json(out / "batch-result.json", report)
    _json(out / "needs-ai-review.json", {"schema_version": SCHEMA, "items": queue})
    for row in results:
        print(" | ".join(str(row.get(key, "")) for key in (
            "product_id", "model", "layout", "status",
            "generation_seconds", "qa_seconds", "total_seconds")))
    return report
