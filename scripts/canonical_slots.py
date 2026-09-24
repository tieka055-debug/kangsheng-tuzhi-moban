"""Source-position-independent Kangsheng output slots for detected technical blocks."""

from __future__ import annotations


def right_rail_placements(source_boxes: dict[str, list[float]], rule: dict) -> dict:
    """Map table and performance to one aligned rail or request local review.

    Source boxes are already-reviewed crop boundaries; no OCR or source layout
    inference occurs here. Each block uses one isotropic scale.
    """
    rail = rule["slots"]["RIGHT_TOP_TABLE"]["rail_pt"]
    gap = rule["slots"]["RIGHT_MIDDLE_PERFORMANCE"]["minimum_gap_pt"]
    min_scale = rule["right_rail_fit"]["minimum_effective_scale_pt_per_source_px"]
    x0, y0, x1, max_bottom = map(float, rail)
    width = x1 - x0
    if width <= 0 or gap < 0:
        raise ValueError("Invalid canonical output rail")
    placements = {}
    y = y0
    for role in ("part_table", "performance"):
        if role not in source_boxes:
            continue
        sx0, sy0, sx1, sy1 = map(float, source_boxes[role])
        if sx1 <= sx0 or sy1 <= sy0:
            raise ValueError(f"Invalid source box: {role}")
        scale = width / (sx1 - sx0)
        if scale < min_scale:
            return {"status": "AI_LOCAL_REVIEW", "reason": f"{role} would be unreadably small at right-rail width"}
        if placements:
            y += gap
        bottom = y + (sy1 - sy0) * scale
        if bottom > max_bottom + 1e-6:
            return {"status": "AI_LOCAL_REVIEW", "reason": f"table + performance exceed right rail by {bottom - max_bottom:.2f} pt"}
        placements[role] = {"target": [x0, y, x1, bottom], "scale": scale}
        y = bottom
    return {"status": "SUPPORTED_COMPATIBLE", "placements": placements}
