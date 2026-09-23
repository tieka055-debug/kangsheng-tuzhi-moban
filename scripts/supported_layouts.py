"""Conservative, read-only source preflight and supported-layout routing.

The public catalog contains structural rules only.  Approved source hashes,
paths and frozen recipes live in a separate private golden ledger.  A family
being observed is not permission to reuse another product's geometry or data.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pymupdf as fitz


CATALOG_SCHEMA = "kangsheng-supported-layout-catalog-v1"
LEDGER_SCHEMA = "kangsheng-golden-ledger-v1"
ANCHOR_SCHEMA = "kangsheng-approval-anchor-v1"
EXACT_READY = "EXACT_REPLAY_READY"
REVIEW = "NEEDS_AI_REVIEW"
DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "supported-layouts" / "catalog.json"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: str | Path) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key: " + key)
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError("non-finite JSON number: " + value)

    value = json.loads(Path(path).read_text(encoding="utf-8"),
                       object_pairs_hook=pairs, parse_constant=nonfinite)
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def load_catalog(path: str | Path = DEFAULT_CATALOG) -> dict:
    catalog = _read_json(path)
    if catalog.get("schema_version") != CATALOG_SCHEMA:
        raise ValueError("unsupported layout catalog schema")
    layouts = catalog.get("layouts")
    if not isinstance(layouts, list):
        raise ValueError("layouts must be a list")
    ids = set()
    for layout in layouts:
        if not isinstance(layout, dict):
            raise ValueError("layout must be an object")
        layout_id = layout.get("layout_id")
        if not isinstance(layout_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", layout_id):
            raise ValueError("invalid layout ID")
        if layout_id in ids:
            raise ValueError("duplicate layout ID")
        ids.add(layout_id)
        if layout.get("status") not in {"CANDIDATE", "SUPPORTED_EXACT", "SUPPORTED"}:
            raise ValueError("invalid layout status")
        if layout.get("fallback") != REVIEW:
            raise ValueError("layout must fail closed")
        if not isinstance(layout.get("structure_probe"), dict):
            raise ValueError("structure_probe required")
        verified = layout.get("programmatically_verified_distinct_sources")
        if not isinstance(verified, int) or isinstance(verified, bool) or verified < 0:
            raise ValueError("verified distinct-source count required")
        if layout["status"] == "SUPPORTED_EXACT" and verified < 1:
            raise ValueError("exact replay requires a verified source")
        if layout["status"] == "SUPPORTED" and verified < 2:
            raise ValueError("generic support requires two verified distinct sources")
        if layout["status"] == "SUPPORTED" and not layout.get("parameter_extractor"):
            raise ValueError("generic support requires a parameter extractor")
    return catalog


def inspect_source(source_path: str | Path) -> dict:
    """Observe PDF structure, without interpreting/deleting technical ink."""
    source = Path(source_path)
    source_sha = sha256_file(source)
    with fitz.open(source) as document:
        pages = len(document)
        result = {"source_sha256": source_sha, "page_count": pages,
                  "encrypted": bool(document.is_encrypted)}
        if pages != 1 or document.is_encrypted:
            return result
        page = document[0]
        width, height = float(page.rect.width), float(page.rect.height)
        content = page.get_text("text")
        text_blocks = page.get_text("blocks")
        drawings = page.get_drawings()
        images = page.get_images(full=True)
        upper = content.upper()
        result.update({
            "page_width_pt": round(width, 3),
            "page_height_pt": round(height, 3),
            "orientation": "landscape" if width > height else "portrait" if height > width else "square",
            "rotation": int(page.rotation),
            "text_extractable": bool(content.strip()),
            "text_block_count": len(text_blocks),
            "drawing_count": len(drawings),
            "image_count": len(images),
            "signals": {
                "pcb_label": "PCB" in upper,
                "part_number_header": "PART NO" in upper or "规格" in content,
                "tolerance_label": "TOLERANCE" in upper or "公差" in content,
                "performance_label": "性能" in content or "PERFORMANCE" in upper,
                "projection_label": "PROJECTION" in upper or "视图方法" in content,
                "scale_label": "SCALE" in upper or "比例" in content,
                "material_table_label": "MATERIAL" in upper,
                "technical_note_label": "技术要求" in content or "REQUIREMENT" in upper,
                "watermark_hint": "AUTODESK" in upper or "教育版" in content or "WATERMARK" in upper,
            },
        })
        return result


def _probe_match(observation: dict, probe: dict) -> bool:
    for field in ("page_count", "orientation", "rotation", "text_extractable"):
        if field in probe and observation.get(field) != probe[field]:
            return False
    for field in ("page_width_pt", "page_height_pt", "text_block_count", "drawing_count", "image_count"):
        if field in probe:
            bounds = probe[field]
            if not (isinstance(bounds, list) and len(bounds) == 2 and
                    bounds[0] <= observation.get(field, -1) <= bounds[1]):
                return False
    for name, required in probe.get("signals", {}).items():
        if observation.get("signals", {}).get(name) is not required:
            return False
    return True


def _check_exact_recipe(sample: dict, source_sha: str,
                        available_fitz_versions: set[str] | frozenset[str]) -> list[str]:
    """Verify every immutable input binding; replay still performs final QA."""
    reasons = []
    recipe_path = sample.get("recipe_path")
    if not recipe_path or not sample.get("recipe_sha256"):
        return ["FROZEN_RECIPE_MISSING"]
    recipe_path = Path(recipe_path)
    if not recipe_path.is_file() or sha256_file(recipe_path) != sample["recipe_sha256"]:
        return ["FROZEN_RECIPE_CHANGED"]
    try:
        import approved_recipe

        recipe = _read_json(recipe_path)
        if recipe.get("recipe_schema") != approved_recipe.SCHEMA:
            reasons.append("RECIPE_SCHEMA_UNSUPPORTED")
        cfg, _ = approved_recipe._validate(recipe, recipe_path.parent)
        if cfg["source"]["sha256"] != source_sha or recipe["bindings"]["source_sha256"] != source_sha:
            reasons.append("RECIPE_SOURCE_BINDING_MISMATCH")
        if sha256_file(cfg["source"]["path"]) != source_sha:
            reasons.append("RECIPE_BOUND_SOURCE_CHANGED")
        if recipe.get("configuration_operations_sha256") != approved_recipe._hash(
                {"configuration": recipe["configuration"], "operations": recipe["operations"]}):
            reasons.append("RECIPE_PLAN_HASH_MISMATCH")
        if recipe.get("recipe_sha256") != approved_recipe._hash(
                {key: value for key, value in recipe.items() if key != "recipe_sha256"}):
            reasons.append("RECIPE_CONTENT_HASH_MISMATCH")
        if recipe.get("engineering_release") is not False:
            reasons.append("RECIPE_RELEASE_SCOPE_MISMATCH")
        baseline = recipe["bindings"]["baseline"]
        if (baseline["sha256"] != sample.get("approved_pdf_sha256") or
                Path(baseline["path"]).resolve() != Path(sample["approved_pdf_path"]).resolve() or
                sha256_file(baseline["path"]) != baseline["sha256"]):
            reasons.append("APPROVED_BASELINE_BINDING_MISMATCH")
        bound_engine = recipe["bindings"].get("engine", {})
        current_engine = approved_recipe._engine()
        if bound_engine.get("scripts") != current_engine.get("scripts"):
            reasons.append("RECIPE_ENGINE_BINDING_MISMATCH")
        if bound_engine.get("fitz_version") not in available_fitz_versions:
            reasons.append("RECIPE_FITZ_RUNTIME_UNAVAILABLE")
        for asset in recipe["bindings"].get("assets", {}).values():
            if sha256_file(asset["path"]) != asset["sha256"]:
                reasons.append("RECIPE_ASSET_BINDING_MISMATCH")
    except (OSError, KeyError, TypeError, ValueError, ImportError):
        reasons.append("RECIPE_VALIDATION_ERROR")
    return reasons


def preflight_source(source_path: str | Path, *, expected_sha256: str | None = None,
                     expected_identity: str | None = None,
                     catalog_dir: str | Path | None = None,
                     private_ledger: str | Path | None = None,
                     available_fitz_versions: set[str] | frozenset[str] | None = None) -> dict:
    """Return EXACT_REPLAY_READY only for an anchored, unchanged frozen recipe.

    Similar structures, names or models never authorize cross-model production.
    There is deliberately no generic automatic route until a second-source
    parameter extractor and its tests have been promoted separately.
    """
    catalog_path = Path(catalog_dir) / "catalog.json" if catalog_dir else DEFAULT_CATALOG
    catalog = load_catalog(catalog_path)
    result = {"status": REVIEW, "layout_id": None, "recipe_path": None,
              "source_sha256": None, "observation": None,
              "candidate_layout_ids": [], "reasons": []}
    if available_fitz_versions is None:
        available_fitz_versions = frozenset({fitz.__version__})
    elif (not isinstance(available_fitz_versions, (set, frozenset)) or
          not available_fitz_versions or
          not all(isinstance(value, str) and value for value in available_fitz_versions)):
        result["reasons"].append("INVALID_FITZ_RUNTIME_SET")
        return result
    try:
        observation = inspect_source(source_path)
    except (OSError, RuntimeError, ValueError):
        result["reasons"].append("SOURCE_UNREADABLE")
        return result
    result["source_sha256"] = observation["source_sha256"]
    result["observation"] = observation
    result["candidate_layout_ids"] = [entry["layout_id"] for entry in catalog["layouts"]
                                      if _probe_match(observation, entry["structure_probe"])]
    if observation.get("encrypted") or observation["page_count"] != 1:
        result["reasons"].append("SOURCE_NOT_SINGLE_UNENCRYPTED_PAGE")
        return result
    if expected_sha256 and observation["source_sha256"] != expected_sha256:
        result["reasons"].append("SOURCE_SHA_MISMATCH")
        return result
    if private_ledger is None:
        result["reasons"].append("PRIVATE_GOLDEN_LEDGER_NOT_PROVIDED")
        return result
    try:
        ledger_path = Path(private_ledger)
        ledger = _read_json(ledger_path)
        anchor = _read_json(ledger_path.with_name("approval-anchor.json"))
        if ledger.get("schema_version") != LEDGER_SCHEMA or anchor.get("schema_version") != ANCHOR_SCHEMA:
            raise ValueError("golden ledger/anchor schema mismatch")
        matches = [sample for sample in ledger.get("samples", [])
                   if sample.get("source_sha256") == observation["source_sha256"]]
        if len(matches) != 1:
            result["reasons"].append("SOURCE_NOT_UNIQUELY_REGISTERED")
            return result
        sample = matches[0]
        anchors = [entry for entry in anchor.get("samples", [])
                   if entry.get("sample_id") == sample.get("sample_id")]
        if len(anchors) != 1 or any(anchors[0].get(key) != sample.get(key)
                                    for key in ("source_sha256", "approved_pdf_sha256")):
            result["reasons"].append("APPROVAL_ANCHOR_MISMATCH")
            return result
        if expected_identity and expected_identity != sample.get("model"):
            result["reasons"].append("EXPECTED_IDENTITY_MISMATCH")
            return result
        if sample.get("user_acceptance_status") != "ACCEPTED" or sample.get("blocker_reason"):
            result["reasons"].append("GOLDEN_APPROVAL_BLOCKED")
            return result
        layout = next((entry for entry in catalog["layouts"]
                       if entry["layout_id"] == sample.get("supported_layout_id")), None)
        if not layout:
            result["reasons"].append("LAYOUT_NOT_REGISTERED")
            return result
        if not _probe_match(observation, layout["structure_probe"]):
            result["reasons"].append("REGISTERED_STRUCTURE_CONFLICT")
            return result
        if layout["status"] != "SUPPORTED_EXACT":
            result["reasons"].append("LAYOUT_CANDIDATE_UNPROMOTED" if layout["status"] == "CANDIDATE"
                                     else "GENERIC_LAYOUT_ROUTER_NOT_IMPLEMENTED")
            if not sample.get("recipe_path"):
                result["reasons"].append("FROZEN_RECIPE_MISSING")
            if not observation.get("text_extractable"):
                result["reasons"].append("NONEXTRACTABLE_SOURCE_TEXT")
            return result
        if (not sample.get("approved_pdf_path") or
                sha256_file(sample["approved_pdf_path"]) != sample.get("approved_pdf_sha256")):
            result["reasons"].append("APPROVED_PDF_CHANGED")
            return result
        errors = _check_exact_recipe(sample, observation["source_sha256"],
                                     available_fitz_versions)
        if errors:
            result["reasons"].extend(errors)
            return result
        result.update(status=EXACT_READY, layout_id=layout["layout_id"],
                      recipe_path=sample["recipe_path"], reasons=[])
        return result
    except (OSError, ValueError, TypeError, KeyError):
        result["reasons"].append("PRIVATE_LEDGER_VALIDATION_ERROR")
        return result
