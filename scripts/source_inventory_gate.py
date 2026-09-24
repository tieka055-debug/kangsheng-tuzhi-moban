"""Full-source technical inventory gate for *new* Kangsheng candidates.

This is deliberately independent of layout grouping and selected-group QA.  A
candidate may be generated only after every foreground pixel on the uncropped
source page belongs to a placed technical group, an approved semantic
replacement, or a specifically reviewed nontechnical exclusion.  An empty or
partial inventory therefore blocks rather than claiming success.

Source coordinates are image pixels for raster inputs and page points for PDF
inputs.  Nothing in this module crops or changes the supplier original.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import fitz
import numpy as np
from PIL import Image
from scipy.ndimage import find_objects, label


PASS = "SOURCE_INVENTORY_PASS"
BLOCKED = "SOURCE_INVENTORY_BLOCKED"
FAIL_COMPLETENESS = "FAIL_SOURCE_COMPLETENESS"
TECHNICAL_KINDS = frozenset({
    "view", "isometric", "dimension", "pcb", "part_table", "table",
    "performance", "tolerance", "projection", "technical_note", "note",
    "extra_condition", "other_technical_chart",
})
NONTECHNICAL_KINDS = frozenset({
    "supplier_logo", "supplier_company", "supplier_title_furniture",
    "outer_frame", "watermark", "nontechnical_annotation",
})


class SourceInventoryBlocked(RuntimeError):
    """Raised when a caller attempts production after a blocked inventory."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_full_page(path: Path) -> tuple[np.ndarray, tuple[float, float], float]:
    if path.suffix.lower() == ".pdf":
        with fitz.open(path) as doc:
            if len(doc) != 1:
                raise ValueError("SOURCE_INVENTORY_GATE requires the complete single source page")
            page = doc[0]
            native_size = (float(page.rect.width), float(page.rect.height))
            scale = 2.0
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3].copy()
            return rgb, native_size, scale
    with Image.open(path) as image:
        if getattr(image, "n_frames", 1) != 1:
            raise ValueError("SOURCE_INVENTORY_GATE requires the complete single source page")
        rgb = np.asarray(image.convert("RGB"))
    return rgb, (float(rgb.shape[1]), float(rgb.shape[0])), 1.0


def _validated_box(box: Any, size: tuple[float, float], name: str) -> tuple[float, float, float, float]:
    if not isinstance(box, (tuple, list)) or len(box) != 4:
        raise ValueError(f"{name}: box must contain four coordinates")
    if any(not isinstance(v, (int, float)) or not np.isfinite(v) for v in box):
        raise ValueError(f"{name}: box has a nonfinite coordinate")
    x0, y0, x1, y1 = map(float, box)
    if not (0 <= x0 < x1 <= size[0] and 0 <= y0 < y1 <= size[1]):
        raise ValueError(f"{name}: box is empty or outside the complete source page")
    return x0, y0, x1, y1


def _fill(mask: np.ndarray, box: tuple[float, float, float, float], scale: float) -> None:
    x0, y0, x1, y1 = box
    # Outward rounding avoids counting antialiasing along an inventoried edge as
    # a missing object.  It does not create coverage outside the declared box.
    xs = max(0, int(np.floor(x0 * scale)))
    ys = max(0, int(np.floor(y0 * scale)))
    xe = min(mask.shape[1], int(np.ceil(x1 * scale)))
    ye = min(mask.shape[0], int(np.ceil(y1 * scale)))
    mask[ys:ye, xs:xe] = True


def source_region_sha256(rgb: np.ndarray, box: tuple[int, int, int, int]) -> str:
    """Fingerprint the exact source pixels within an approved replacement box.

    For PDF, pass the full-page rendering returned by the PDF renderer and box
    coordinates converted to that rendering's pixels.
    """
    x0, y0, x1, y1 = box
    return hashlib.sha256(np.ascontiguousarray(rgb[y0:y1, x0:x1, :3]).tobytes()).hexdigest()


def _components(mask: np.ndarray, scale: float, limit: int = 100) -> tuple[int, list[dict[str, Any]]]:
    labels, count = label(mask, structure=np.ones((3, 3), dtype=np.uint8))
    found = []
    for component_id, slices in enumerate(find_objects(labels), 1):
        if slices is None:
            continue
        ys, xs = slices
        pixels = int(np.count_nonzero(labels[slices] == component_id))
        found.append({
            "box": [round(xs.start / scale, 3), round(ys.start / scale, 3),
                    round(xs.stop / scale, 3), round(ys.stop / scale, 3)],
            "ink_pixels": pixels,
        })
    found.sort(key=lambda item: item["ink_pixels"], reverse=True)
    return count, found[:limit]


def assess_source_inventory(source: str | Path, inventory: dict[str, Any]) -> dict[str, Any]:
    """Audit the *entire original page* before final candidate generation.

    Required inventory keys: ``source_sha256``, ``technical_groups``,
    ``authorized_transforms``, ``nontechnical_exclusions``.  Every group needs
    an id, technical kind, source_boxes and planned output slot.  Every
    exclusion needs an approved nontechnical kind, explicit reason, and named
    reviewer.  Authorized replacements need source-region fingerprint plus a
    field-by-field ledger; their source region remains part of technical scope.

    The returned PASS is source-inventory clearance only, *not* output QA or
    engineering release.  The caller must hard-stop on BLOCKED.
    """
    source = Path(source)
    rgb, native_size, scale = _load_full_page(source)
    full_hash = sha256_file(source)
    errors: list[str] = []
    if inventory.get("source_sha256") != full_hash:
        errors.append("source SHA256 differs from the reviewed complete original")
    expected_size = inventory.get("source_size")
    if expected_size is not None:
        try:
            if len(expected_size) != 2 or tuple(map(float, expected_size)) != native_size:
                errors.append("source dimensions differ from the reviewed complete original")
        except (TypeError, ValueError):
            errors.append("source dimensions are malformed")
    if inventory.get("inventory_scope") != "complete_original_page":
        errors.append("inventory_scope must be complete_original_page")
    for key in ("technical_groups", "authorized_transforms", "nontechnical_exclusions"):
        if key not in inventory or not isinstance(inventory[key], list):
            errors.append(f"{key} must be an explicit list, even when empty")
    groups = inventory.get("technical_groups") if isinstance(inventory.get("technical_groups"), list) else []
    transforms = inventory.get("authorized_transforms") if isinstance(inventory.get("authorized_transforms"), list) else []
    exclusions = inventory.get("nontechnical_exclusions") if isinstance(inventory.get("nontechnical_exclusions"), list) else []

    shape = rgb.shape[:2]
    technical = np.zeros(shape, dtype=bool)
    transformed = np.zeros(shape, dtype=bool)
    excluded = np.zeros(shape, dtype=bool)
    ids: set[str] = set()
    group_audit = []
    replacement_audit = []
    exclusion_audit = []

    for group in groups:
        if not isinstance(group, dict):
            errors.append("technical group is not an object")
            continue
        ident = group.get("id")
        if not isinstance(ident, str) or not ident or ident in ids:
            errors.append(f"technical group has missing/duplicate id: {ident!r}")
        else:
            ids.add(ident)
        if group.get("kind") not in TECHNICAL_KINDS:
            errors.append(f"{ident}: unknown technical kind")
        if not group.get("slot"):
            errors.append(f"{ident}: no planned output slot")
        boxes = group.get("source_boxes")
        if not isinstance(boxes, list) or not boxes:
            errors.append(f"{ident}: no source boxes")
            continue
        for index, value in enumerate(boxes):
            try:
                box = _validated_box(value, native_size, f"{ident}[{index}]")
                _fill(technical, box, scale)
            except ValueError as exc:
                errors.append(str(exc))
        group_audit.append({"id": ident, "kind": group.get("kind"), "slot": group.get("slot"),
                            "source_boxes": boxes})

    for item in transforms:
        if not isinstance(item, dict):
            errors.append("authorized transform is not an object")
            continue
        ident = item.get("id")
        if not isinstance(ident, str) or not ident or ident in ids:
            errors.append(f"authorized transform has missing/duplicate id: {ident!r}")
        else:
            ids.add(ident)
        if item.get("kind") not in TECHNICAL_KINDS:
            errors.append(f"{ident}: replacement must have a technical kind")
        if not item.get("slot") or not item.get("approval_reference") or not item.get("source_reviewer"):
            errors.append(f"{ident}: replacement needs slot, approval reference and source reviewer")
        if item.get("source_inventory_complete") is not True:
            errors.append(f"{ident}: replacement source-field inventory not confirmed complete")
        try:
            box = _validated_box(item.get("source_box"), native_size, str(ident))
            pixel_box = (int(np.floor(box[0] * scale)), int(np.floor(box[1] * scale)),
                         int(np.ceil(box[2] * scale)), int(np.ceil(box[3] * scale)))
            actual_region_hash = source_region_sha256(rgb, pixel_box)
            if item.get("source_region_sha256") != actual_region_hash:
                errors.append(f"{ident}: source replacement region fingerprint differs/missing")
            local = np.zeros(shape, dtype=bool)
            _fill(local, box, scale)
            if np.any(local & technical):
                errors.append(f"{ident}: authorized replacement overlaps another inventoried technical region")
            _fill(technical, box, scale)
            _fill(transformed, box, scale)
        except ValueError as exc:
            errors.append(str(exc))
            actual_region_hash = None
        fields = item.get("fields")
        if not isinstance(fields, list) or not fields:
            errors.append(f"{ident}: replacement needs a nonempty field ledger")
            fields = []
        field_ids = set()
        for row in fields:
            if not isinstance(row, dict):
                errors.append(f"{ident}: replacement field is not an object")
                continue
            key = row.get("source_field")
            if not key or key in field_ids or not row.get("output_field"):
                errors.append(f"{ident}: missing/duplicate source field or missing output field")
            field_ids.add(key)
            if not isinstance(row.get("source_value"), str) or not isinstance(row.get("output_value"), str):
                errors.append(f"{ident}/{key}: source/output values must both be present as text")
            elif row.get("comparison") == "exact":
                if row["source_value"] != row["output_value"]:
                    errors.append(f"{ident}/{key}: technical value changed in exact replacement")
            elif row.get("comparison") == "approved_translation":
                if not row.get("reviewed_equivalent") or not row.get("reviewer"):
                    errors.append(f"{ident}/{key}: translation lacks reviewed equivalence")
            else:
                errors.append(f"{ident}/{key}: unsupported semantic comparison")
        replacement_audit.append({"id": ident, "kind": item.get("kind"), "slot": item.get("slot"),
                                  "source_box": item.get("source_box"),
                                  "source_region_sha256": actual_region_hash,
                                  "field_count": len(fields), "fields": fields})

    for item in exclusions:
        if not isinstance(item, dict):
            errors.append("nontechnical exclusion is not an object")
            continue
        ident = item.get("id")
        if not isinstance(ident, str) or not ident or ident in ids:
            errors.append(f"nontechnical exclusion has missing/duplicate id: {ident!r}")
        else:
            ids.add(ident)
        if item.get("kind") not in NONTECHNICAL_KINDS:
            errors.append(f"{ident}: unknown/non-approved nontechnical exclusion kind")
        if not item.get("reason") or not item.get("reviewer") or item.get("review_status") != "APPROVED_NONTECHNICAL":
            errors.append(f"{ident}: exclusion needs explicit reason and approved review")
        try:
            box = _validated_box(item.get("source_box"), native_size, str(ident))
            local = np.zeros(shape, dtype=bool)
            _fill(local, box, scale)
            if np.any(local & technical):
                errors.append(f"{ident}: nontechnical exclusion overlaps inventoried technical source")
            excluded |= local
            exclusion_audit.append({"id": ident, "kind": item.get("kind"),
                                    "source_box": item.get("source_box"),
                                    "reason": item.get("reason"), "reviewer": item.get("reviewer"),
                                    "excluded_ink_pixels": int(np.count_nonzero(local & (rgb.min(2) < 245)))})
        except ValueError as exc:
            errors.append(str(exc))

    # Entire page is the interest range.  Never use a locator's already-selected
    # group mask as the denominator; even red/green supplier emphasis is ink.
    source_ink = rgb.min(axis=2) < 245
    unexplained = source_ink & ~(technical | excluded)
    unexplained_pixels = int(np.count_nonzero(unexplained))
    count, regions = _components(unexplained, scale)
    zone_audit = []
    zones = inventory.get("audit_zones", [])
    if not isinstance(zones, list):
        errors.append("audit_zones must be a list")
        zones = []
    for zone in zones:
        if not isinstance(zone, dict):
            errors.append("audit zone is not an object")
            continue
        try:
            box = _validated_box(zone.get("source_box"), native_size, "audit_zone")
            local = np.zeros(shape, dtype=bool)
            _fill(local, box, scale)
            zone_audit.append({"id": zone.get("id"), "source_box": zone.get("source_box"),
                               "unplaced_technical_ink_pixels": int(np.count_nonzero(unexplained & local))})
        except ValueError as exc:
            errors.append(str(exc))
    if unexplained_pixels:
        errors.append(f"{FAIL_COMPLETENESS}: {unexplained_pixels} unexplained foreground pixels in full original page")
    status = PASS if not errors else BLOCKED
    return {
        "status": status,
        "failure_code": (None if status == PASS else
                         FAIL_COMPLETENESS if unexplained_pixels else "INVALID_SOURCE_INVENTORY"),
        "source_sha256": full_hash,
        "source_size": list(native_size),
        "scope": "complete_original_page",
        "ink_threshold_rgb_min_lt": 245,
        "full_source_ink_pixels": int(np.count_nonzero(source_ink)),
        "accounted_technical_ink_pixels": int(np.count_nonzero(source_ink & technical)),
        "authorized_transform_source_ink_pixels": int(np.count_nonzero(source_ink & transformed)),
        "approved_nontechnical_ink_pixels": int(np.count_nonzero(source_ink & excluded)),
        "technical_groups": group_audit,
        "authorized_transforms": replacement_audit,
        "approved_nontechnical_exclusions": exclusion_audit,
        "unplaced_technical_ink_pixels": unexplained_pixels,
        "unplaced_object_count": count,
        "unplaced_regions": regions,
        "unplaced_by_zone": zone_audit,
        "issues": errors,
    }


def require_source_inventory_pass(report: dict[str, Any]) -> None:
    """A generation entrypoint must call this before writing a candidate."""
    if report.get("status") != PASS or report.get("unplaced_technical_ink_pixels") != 0:
        raise SourceInventoryBlocked(
            f"{BLOCKED}: {report.get('unplaced_technical_ink_pixels')} unexplained source pixels; "
            f"{report.get('issues', [])}"
        )


def assess_candidate_completeness(
    inventory_report: dict[str, Any],
    moved_groups: list[dict[str, Any]],
    actual_transform_fields: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Join full-source inventory to independently checked candidate output.

    ``moved_groups`` must be built from the *actual* candidate PDF QA, not the
    layout plan: each row has ``id``, ``source_boxes`` and ``pixel_qa_pass``.
    ``actual_transform_fields`` must be extracted or independently read from
    the *actual* output, keyed by replacement id then output field.  This join
    prevents a PASS inventory from being reused to bless a different/partial
    set of placed groups.  It does not replace output pixel/text QA itself.
    """
    issues: list[str] = []
    if inventory_report.get("status") != PASS or inventory_report.get("unplaced_technical_ink_pixels") != 0:
        issues.append("complete original source inventory has not passed")
    if not isinstance(moved_groups, list) or not isinstance(actual_transform_fields, dict):
        issues.append("actual output accounting is missing/malformed")
        moved_groups = []
        actual_transform_fields = {}
    expected = {group["id"]: group for group in inventory_report.get("technical_groups", [])}
    actual = {}
    for group in moved_groups:
        if not isinstance(group, dict) or not group.get("id"):
            issues.append("actual PDF QA has an unnamed/malformed technical group")
            continue
        ident = group["id"]
        if ident in actual:
            issues.append(f"actual PDF QA duplicated technical group {ident}")
        actual[ident] = group
    for ident, expected_group in expected.items():
        observed = actual.get(ident)
        if observed is None:
            issues.append(f"source technical group {ident} absent from actual candidate")
            continue
        if json.dumps(observed.get("source_boxes"), sort_keys=True) != json.dumps(expected_group["source_boxes"], sort_keys=True):
            issues.append(f"source technical group {ident} has different crop in candidate")
        if observed.get("pixel_qa_pass") is not True:
            issues.append(f"source technical group {ident} lacks actual-PDF pixel QA PASS")
    for ident in actual.keys() - expected.keys():
        issues.append(f"candidate technical group {ident} was not in source inventory")
    for transform in inventory_report.get("authorized_transforms", []):
        ident = transform["id"]
        actual_fields = actual_transform_fields.get(ident)
        if not isinstance(actual_fields, dict):
            issues.append(f"authorized transform {ident} has no actual-output field check")
            continue
        expected_fields = {row["output_field"]: row["output_value"] for row in transform["fields"]}
        if actual_fields != expected_fields:
            issues.append(f"authorized transform {ident} actual-output fields differ from ledger")
    for ident in actual_transform_fields.keys() - {x["id"] for x in inventory_report.get("authorized_transforms", [])}:
        issues.append(f"candidate transform {ident} was not authorized in source inventory")
    return {
        "status": "SOURCE_COMPLETENESS_PASS" if not issues else FAIL_COMPLETENESS,
        "full_source_unexplained_ink_pixels": inventory_report.get("unplaced_technical_ink_pixels"),
        "inventoried_technical_group_count": len(expected),
        "actual_moved_group_count": len(actual),
        "authorized_transform_count": len(inventory_report.get("authorized_transforms", [])),
        "issues": issues,
    }


def require_candidate_completeness_pass(report: dict[str, Any]) -> None:
    if report.get("status") != "SOURCE_COMPLETENESS_PASS":
        raise SourceInventoryBlocked(f"{FAIL_COMPLETENESS}: {report.get('issues', [])}")
