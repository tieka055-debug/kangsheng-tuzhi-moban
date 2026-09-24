"""Reviewed supplier/family color semantics for *new* raster-source candidates.

This is intentionally separate from the frozen ``legacy-v1`` and
``cyan-gold-v1`` replay profiles.  A family profile classifies source *roles*,
not a universal hue-to-gold rule.  Unclassified chromatic ink is a local
review request, never silently ordinary blue.  The caller must independently
pass the full-page source inventory gate before this module can return PASS.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import fitz
import numpy as np
from PIL import Image

SCHEMA = "kangsheng-source-color-roles-v1"
BLUE = (6, 66, 168)
GOLD = (217, 154, 0)
PASS = "COLOR_SEMANTIC_PASS"
REVIEW = "COLOR_SEMANTIC_REVIEW"
FAIL = "COLOR_SEMANTIC_FAIL"
_HUES = {"red", "green", "yellow", "cyan", "blue", "magenta"}


def load_family_profile(path: str | Path, family_id: str) -> dict:
    """Load an explicit, reviewed family mapping; never infer it from a title."""
    doc = json.loads(Path(path).read_text())
    if doc.get("schema_version") != SCHEMA:
        raise ValueError("Unknown source-color-role profile schema")
    matches = [p for p in doc.get("families", []) if p.get("family_id") == family_id]
    if len(matches) != 1:
        raise ValueError("Family color-role profile missing or ambiguous")
    profile = matches[0]
    roles = profile.get("hue_roles", {})
    if not isinstance(roles, dict) or not roles or not set(roles).issubset(_HUES):
        raise ValueError("Family color-role hues must be explicitly named")
    if any(v not in {"GOLD", "BLUE"} for v in roles.values()):
        raise ValueError("Invalid family color role")
    if profile.get("ordinary_achromatic_role") != "BLUE":
        raise ValueError("Black/gray engineering content must remain Kangsheng blue")
    if profile.get("unknown_chromatic_policy") != REVIEW:
        raise ValueError("Unknown source emphasis must route to local review")
    if profile.get("review_status") not in {"AI_LOCAL_REVIEWED", "PENDING_LOCAL_REVIEW"}:
        raise ValueError("Color profile must disclose its review status")
    return profile


def _color_arrays(image: Image.Image, profile: dict, technical_mask=None):
    """Return source-bound alpha, role and unknown masks, without retyping ink."""
    a = np.asarray(image.convert("RGB"), dtype=np.int16)
    lo = a.min(axis=2)
    hi = a.max(axis=2)
    # Keep the existing raster family's source-opacity transform exactly:
    # truncation (not rounding) preserves every glyph/line silhouette.
    alpha = np.minimum(255, (255 - lo) * 2.3).astype(np.uint8)
    alpha[alpha < 7] = 0
    active = alpha > 0
    if technical_mask is not None:
        mask = np.asarray(technical_mask, dtype=bool)
        if mask.shape != active.shape:
            raise ValueError("Technical inventory mask/source dimensions disagree")
        active &= mask
        alpha[~mask] = 0

    r, g, b = (a[:, :, i] for i in range(3))
    delta = hi - lo
    achromatic = active & (delta <= 7)
    remaining = active & ~achromatic
    # Channel dominance is robust to white anti-aliasing.  Yellow catches
    # the red/green overprint pixels on approved two-color CAD drawings.
    hue_masks = {
        "red": remaining & (r - np.maximum(g, b) >= 8) & (np.abs(g - b) <= 24),
        "green": remaining & (g - np.maximum(r, b) >= 8) & (np.abs(r - b) <= 24),
        "blue": remaining & (b - np.maximum(r, g) >= 8) & (np.abs(r - g) <= 24),
        "yellow": remaining & (np.minimum(r, g) - b >= 8),
        "cyan": remaining & (np.minimum(g, b) - r >= 8),
        "magenta": remaining & (np.minimum(r, b) - g >= 8),
    }
    # Dominant primary hue wins before secondary/overprint categories.
    assigned = achromatic.copy()
    for hue in ("red", "green", "blue", "yellow", "cyan", "magenta"):
        hue_masks[hue] &= ~assigned
        assigned |= hue_masks[hue]
    unknown = active & ~assigned
    gold = np.zeros(active.shape, dtype=bool)
    blue = achromatic.copy()
    for hue, mask in hue_masks.items():
        role = profile.get("hue_roles", {}).get(hue)
        if role == "GOLD":
            gold |= mask
        elif role == "BLUE":
            blue |= mask
        else:
            unknown |= mask
    return alpha, blue, gold, unknown, {h: int(m.sum()) for h, m in hue_masks.items()}


def scan_source_colors(image: Image.Image, profile: dict, technical_mask=None) -> dict:
    """Inspect complete source or an inventory-approved technical mask.

    A blank/missing profile approval is REVIEW, and every unknown colored
    technical pixel is reported with its bounding box.  No default-to-blue.
    """
    alpha, blue, gold, unknown, hues = _color_arrays(image, profile, technical_mask)
    ys, xs = np.where(unknown)
    bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if len(xs) else None
    approved = profile.get("review_status") == "AI_LOCAL_REVIEWED"
    return {
        "status": PASS if approved and not len(xs) else REVIEW,
        "family_id": profile.get("family_id"),
        "review_status": profile.get("review_status"),
        "ordinary_blue_pixels": int(blue.sum()),
        "technical_emphasis_gold_pixels": int(gold.sum()),
        "unknown_chromatic_pixels": int(len(xs)),
        "unknown_bbox": bbox,
        "source_hue_counts": hues,
        "visible_source_pixels": int(np.count_nonzero(alpha)),
    }


def recolor_source_crop(image: Image.Image, profile: dict, technical_mask=None) -> tuple[Image.Image, dict]:
    """Preserve each source pixel's opacity/geometry while changing only color.

    A caller may create a candidate only when the returned status is PASS.
    This function raises on unknown semantics instead of emitting blue output.
    """
    report = scan_source_colors(image, profile, technical_mask)
    if report["status"] != PASS:
        raise ValueError(f'{REVIEW}: {report["unknown_chromatic_pixels"]} unknown pixels or unreviewed family')
    alpha, _, gold, _, _ = _color_arrays(image, profile, technical_mask)
    rgba = np.empty((*alpha.shape, 4), dtype=np.uint8)
    rgba[:, :, :3] = BLUE
    rgba[gold, :3] = GOLD
    rgba[:, :, 3] = alpha
    return Image.fromarray(rgba), report


def _matching_pdf_image(pdf: fitz.Document, page: fitz.Page, source_box, target_box):
    width = int(source_box[2] - source_box[0])
    height = int(source_box[3] - source_box[1])
    matches = []
    for item in page.get_images(full=True):
        xref, smask, w, h = item[:4]
        if (w, h) != (width, height) or not smask:
            continue
        for rect in page.get_image_rects(xref):
            if max(abs(a - b) for a, b in zip(rect, target_box)) < .07:
                matches.append((xref, smask))
    if len(matches) != 1:
        raise ValueError("Technical group image absent or repeated at its audited destination")
    return matches[0]


def audit_pdf_color_semantics(source_path: str | Path, pdf_path: str | Path,
                              placements: list[dict], profile: dict,
                              inventory_status: str) -> dict:
    """Compare source roles against embedded candidate pixels, not just groups.

    ``placements`` use the same ``source``/``target`` boxes as the raster
    locator.  The full-page source inventory is a separate prerequisite;
    without its PASS this function never reports color PASS.  Exact alpha
    equality checks geometry and numeric glyph silhouettes while color RGB
    matching checks both GOLD and BLUE, independently.
    """
    source = Image.open(source_path).convert("RGB")
    scan = scan_source_colors(source, profile)
    result = {"status": REVIEW, "source_scan": scan, "groups": [],
              "inventory_status": inventory_status, "gold_mismatch_pixels": 0,
              "blue_mismatch_pixels": 0, "geometry_mismatch_pixels": 0}
    if scan["status"] != PASS:
        result["reason"] = "Unreviewed or unknown source color semantics"
        return result
    try:
        with fitz.open(pdf_path) as pdf:
            if len(pdf) != 1:
                raise ValueError("Expected exactly one candidate sheet")
            page = pdf[0]
            for placement in placements:
                box = placement["source"]
                crop = source.crop(tuple(map(int, box)))
                technical_mask = np.ones((crop.height, crop.width), dtype=bool)
                if placement.get("excluded_supplier_rect"):
                    sx0, sy0, sx1, sy1 = placement["excluded_supplier_rect"]
                    x0, y0 = box[:2]
                    technical_mask[sy0-y0:sy1-y0, sx0-x0:sx1-x0] = False
                alpha, blue, gold, unknown, _ = _color_arrays(crop, profile, technical_mask)
                if np.any(unknown):
                    result["reason"] = "Unknown chromatic ink inside a placed group"
                    return result
                xref, smask = _matching_pdf_image(pdf, page, box, placement["target"])
                rgb = np.asarray(Image.open(io.BytesIO(pdf.extract_image(xref)["image"])).convert("RGB"), dtype=np.int16)
                actual_alpha = np.asarray(Image.open(io.BytesIO(pdf.extract_image(smask)["image"])).convert("L"))
                if rgb.shape[:2] != alpha.shape or actual_alpha.shape != alpha.shape:
                    raise ValueError("Embedded technical image dimensions changed")
                geom = int(np.count_nonzero(actual_alpha != alpha))
                # Ignore invisible anti-alias fringe.  Source-to-PDF ICC
                # conversion may shift a channel by a few counts, hence 24.
                blue_visible = blue & (alpha >= 64)
                gold_visible = gold & (alpha >= 64)
                blue_delta = np.max(np.abs(rgb - BLUE), axis=2)
                gold_delta = np.max(np.abs(rgb - GOLD), axis=2)
                blue_bad = int(np.count_nonzero(blue_visible & (blue_delta > 24)))
                gold_bad = int(np.count_nonzero(gold_visible & (gold_delta > 24)))
                result["geometry_mismatch_pixels"] += geom
                result["blue_mismatch_pixels"] += blue_bad
                result["gold_mismatch_pixels"] += gold_bad
                result["groups"].append({"role": placement.get("role"),
                    "source_gold_pixels": int(gold_visible.sum()),
                    "source_blue_pixels": int(blue_visible.sum()),
                    "gold_mismatch_pixels": gold_bad,
                    "blue_mismatch_pixels": blue_bad,
                    "geometry_mismatch_pixels": geom})
    except (OSError, ValueError, KeyError) as exc:
        result["status"] = FAIL
        result["reason"] = str(exc)
        return result
    if any(result[k] for k in ("gold_mismatch_pixels", "blue_mismatch_pixels", "geometry_mismatch_pixels")):
        result["status"] = FAIL
        result["reason"] = "Source-bound color or technical pixel geometry differs"
    elif inventory_status != "SOURCE_INVENTORY_PASS":
        result["status"] = REVIEW
        result["reason"] = "Full-page source inventory gate has not passed"
    else:
        result["status"] = PASS
        result["reason"] = "Every placed pixel preserves geometry and reviewed color role"
    return result
