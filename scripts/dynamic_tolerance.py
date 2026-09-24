"""Variable-length English tolerance footer for independently sourced values.

This module is opt-in.  It does not change frozen recipes or the existing
manifest-v2 tolerance modes.  The caller must independently bind these values
to the *same* source drawing; this renderer only lays them out and checks that
every declared field fits the bottom tolerance cell without truncation.
"""

from __future__ import annotations

import math
from typing import Any

import pymupdf as fitz

from frame import BLUE, TOLERANCE_BOX


HEADING = ("UNLESS OTHERWISE", "SPECIFIED, TOLERANCE:")
MIN_FONT_PT = 4.75
BODY_FONT_SIZES = (7.0, 6.5, 6.0, 5.5, 5.0, MIN_FONT_PT)
SCHEMA_KEYS = (
    "linear_tolerances",
    "angular_tolerances",
    "additional_tolerance_conditions",
)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be nonempty source text without outer whitespace")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"{label} contains a control character")
    return value


def normalize_tolerance_schema(value: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """Validate a complete linear / angular / other-condition ledger.

    Counts are deliberately not fixed at four.  No value or tier is inferred,
    reformatted, rounded, deduplicated, or silently discarded.  Empty arrays
    must be explicit so a newly encountered category cannot disappear by
    defaulting to an absent key.
    """
    if not isinstance(value, dict):
        raise ValueError("Tolerance schema must be an object")
    unknown_keys = set(value) - set(SCHEMA_KEYS)
    if unknown_keys:
        raise ValueError(f"Unknown tolerance categories need review: {sorted(unknown_keys)}")
    result: dict[str, list[dict[str, str]]] = {}
    for key in SCHEMA_KEYS:
        rows = value.get(key)
        if not isinstance(rows, list):
            raise ValueError(f"{key} must be an explicit array")
        normalized = []
        seen_tiers = set()
        for index, row in enumerate(rows):
            if key == "additional_tolerance_conditions":
                if isinstance(row, str):
                    text = _text(row, f"{key}[{index}]")
                elif isinstance(row, dict) and set(row) == {"text"}:
                    text = _text(row["text"], f"{key}[{index}].text")
                else:
                    raise ValueError(f"{key}[{index}] must have exact source text")
                normalized.append({"text": text})
            else:
                if not isinstance(row, dict) or set(row) != {"tier", "value"}:
                    raise ValueError(f"{key}[{index}] needs tier and value")
                tier = _text(row["tier"], f"{key}[{index}].tier")
                amount = _text(row["value"], f"{key}[{index}].value")
                if tier in seen_tiers:
                    raise ValueError(f"{key} has a duplicate tier: {tier}")
                seen_tiers.add(tier)
                normalized.append({"tier": tier, "value": amount})
        result[key] = normalized
    if not any(result.values()):
        raise ValueError("Tolerance schema has no source fields")
    return result


def _line(row: dict[str, str]) -> str:
    return row["text"] if "text" in row else f"{row['tier']} {row['value']}"


def _body_arrangements(schema: dict[str, list[dict[str, str]]]):
    linear = [("linear", _line(row)) for row in schema["linear_tolerances"]]
    angular = [("angular", _line(row)) for row in schema["angular_tolerances"]]
    conditions = [("condition", _line(row)) for row in schema["additional_tolerance_conditions"]]
    if linear and angular:
        yield "category_columns", linear, angular, conditions
    else:
        single = linear + angular
        yield "single_column", single, [], conditions
        if len(single) > 1:
            split = math.ceil(len(single) / 2)
            yield "flow_columns", single[:split], single[split:], conditions


def plan_dynamic_tolerance(
    value: dict[str, Any],
    box: fitz.Rect | list[float] | tuple[float, ...] = TOLERANCE_BOX,
    *,
    fontname: str = "helv",
) -> dict[str, Any]:
    """Return every field's planned position, or fail before drawing anything."""
    schema = normalize_tolerance_schema(value)
    target = fitz.Rect(box)
    if not all(math.isfinite(v) for v in target) or target.width <= 0 or target.height <= 0:
        raise ValueError("Invalid tolerance box")
    font = fitz.Font(fontname=fontname)
    all_text = [*HEADING] + [
        _line(row) for key in SCHEMA_KEYS for row in schema[key]
    ]
    if any(not font.has_glyph(ord(c)) for line in all_text for c in line):
        raise ValueError("Tolerance contains a glyph unsupported by the chosen font")

    pad = 4.0
    heading_x = target.x0 + pad
    heading_y = (target.y0 + 10.0, target.y0 + 18.0)
    heading_sizes = (6.0, 5.6)
    if target.height < 38 or any(
        font.text_length(line, fontsize=size) > target.width - 2 * pad
        for line, size in zip(HEADING, heading_sizes)
    ):
        raise ValueError("Tolerance heading does not fit its fixed footer cell")
    body_top = target.y0 + 23.0
    body_bottom = target.y1 - pad
    usable_width = target.width - 2 * pad
    gap = 4.0
    for mode, left, right, conditions in _body_arrangements(schema):
        columns = 2 if right else 1
        col_width = (usable_width - gap) / 2 if columns == 2 else usable_width
        if col_width <= 0:
            continue
        for font_size in BODY_FONT_SIZES:
            # A little leading makes the four-tier source values legible while
            # still allowing two categories in the original 88-by-71 pt cell.
            row_step = max(font_size * 1.4, font_size + 1.5)
            row_count = max(len(left), len(right)) + len(conditions)
            if body_top + font_size + (row_count - 1) * row_step > body_bottom:
                continue
            if any(font.text_length(line, fontsize=font_size) > col_width
                   for _, line in left + right):
                continue
            if any(font.text_length(line, fontsize=font_size) > usable_width
                   for _, line in conditions):
                continue
            items = []
            for column, rows in enumerate((left, right)):
                x = target.x0 + pad + column * (col_width + gap)
                for index, (kind, line) in enumerate(rows):
                    items.append({"kind": kind, "text": line, "x": x,
                                  "y": body_top + font_size + index * row_step})
            condition_y = body_top + font_size + max(len(left), len(right)) * row_step
            for index, (_, line) in enumerate(conditions):
                items.append({"kind": "condition", "text": line,
                              "x": target.x0 + pad, "y": condition_y + index * row_step})
            if len(items) != sum(len(schema[key]) for key in SCHEMA_KEYS):
                raise AssertionError("Tolerance planner dropped a source field")
            return {"box": list(target), "mode": mode, "fontname": fontname,
                    "font_size_pt": font_size, "row_step_pt": row_step,
                    "heading": [{"text": line, "x": heading_x, "y": y, "font_size_pt": size}
                                for line, y, size in zip(HEADING, heading_y, heading_sizes)],
                    "items": items, "schema": schema}
    raise ValueError("Complete tolerance data overflows its fixed footer cell")


def render_dynamic_tolerance(
    page: fitz.Page,
    value: dict[str, Any],
    box: fitz.Rect | list[float] | tuple[float, ...] = TOLERANCE_BOX,
    *,
    color: tuple[float, float, float] = BLUE,
    fontname: str = "helv",
) -> dict[str, Any]:
    """Draw the approved sparse English footer with every source field."""
    plan = plan_dynamic_tolerance(value, box, fontname=fontname)
    if not page.rect.contains(fitz.Rect(plan["box"])):
        raise ValueError("Tolerance footer lies outside the output page")
    page.draw_rect(fitz.Rect(plan["box"]), color=color, width=0.8)
    for item in plan["heading"]:
        page.insert_text((item["x"], item["y"]), item["text"], fontname=fontname,
                         fontsize=item["font_size_pt"], color=color)
    for item in plan["items"]:
        page.insert_text((item["x"], item["y"]), item["text"], fontname=fontname,
                         fontsize=plan["font_size_pt"], color=color)
    return plan


def audit_dynamic_tolerance(page: fitz.Page, plan: dict[str, Any]) -> dict[str, Any]:
    """Check the actual PDF contains each planned line exactly once in-cell."""
    box = fitz.Rect(plan["box"])
    expected = [item["text"] for item in plan["heading"] + plan["items"]]
    spans = [span["text"] for block in page.get_text("dict", clip=box)["blocks"]
             for line in block.get("lines", []) for span in line["spans"]]
    missing = [line for line in expected if spans.count(line) != expected.count(line)]
    extra = [line for line in spans if line not in expected]
    return {"pass": not missing and not extra and len(spans) == len(expected),
            "expected_lines": expected, "actual_lines": spans,
            "missing_or_mismatched": missing, "unexpected": extra}
