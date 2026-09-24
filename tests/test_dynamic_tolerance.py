"""Variable tolerance counts retain every same-source field in the footer."""

import copy
import sys
import unittest
from pathlib import Path

import pymupdf as fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from dynamic_tolerance import (audit_dynamic_tolerance, normalize_tolerance_schema,
                               plan_dynamic_tolerance, render_dynamic_tolerance)
from frame import PAGE, TOLERANCE_BOX


def eight_field_schema():
    return {
        "linear_tolerances": [
            {"tier": "X.", "value": "±0.31"},
            {"tier": "X.X", "value": "±0.21"},
            {"tier": "X.XX", "value": "±0.11"},
            {"tier": "X.XXX", "value": "±0.01"},
        ],
        "angular_tolerances": [
            {"tier": "X.°", "value": "±5°"},
            {"tier": "X.X°", "value": "±4°"},
            {"tier": "X.XX°", "value": "±3°"},
            {"tier": "X.XXX°", "value": "±2°"},
        ],
        "additional_tolerance_conditions": [],
    }


class DynamicToleranceTests(unittest.TestCase):
    def test_eight_fields_fit_fixed_slot_with_no_loss(self):
        schema = eight_field_schema()
        doc = fitz.open()
        page = doc.new_page(width=PAGE[0], height=PAGE[1])
        plan = render_dynamic_tolerance(page, schema)
        self.assertEqual(plan["box"], [float(x) for x in TOLERANCE_BOX])
        self.assertEqual(plan["mode"], "category_columns")
        self.assertEqual(len(plan["items"]), 8)
        self.assertGreaterEqual(plan["font_size_pt"], 4.75)
        audit = audit_dynamic_tolerance(page, plan)
        self.assertTrue(audit["pass"], audit)
        for field in ("X. ±0.31", "X.X ±0.21", "X.XX ±0.11", "X.XXX ±0.01",
                      "X.° ±5°", "X.X° ±4°", "X.XX° ±3°", "X.XXX° ±2°"):
            self.assertEqual(audit["actual_lines"].count(field), 1)
        doc.close()

    def test_variable_length_and_additional_condition(self):
        schema = eight_field_schema()
        schema["angular_tolerances"].append({"tier": "X.XXXX°", "value": "±0.5°"})
        schema["additional_tolerance_conditions"] = ["RADIUS ±0.1"]
        # More source fields may require a larger bottom strip; none is cut.
        plan = plan_dynamic_tolerance(schema, box=[350, 480, 488, 564])
        self.assertEqual(len(plan["items"]), 10)
        self.assertEqual(plan["items"][-1]["text"], "RADIUS ±0.1")
        doc = fitz.open()
        page = doc.new_page(width=PAGE[0], height=PAGE[1])
        rendered = render_dynamic_tolerance(page, schema, box=[350, 480, 488, 564])
        self.assertTrue(audit_dynamic_tolerance(page, rendered)["pass"])
        doc.close()

    def test_excess_fields_fail_before_mutating_page(self):
        schema = eight_field_schema()
        schema["linear_tolerances"] = [
            {"tier": "X." + "X" * i, "value": "±0.09"} for i in range(20)
        ]
        doc = fitz.open()
        page = doc.new_page(width=PAGE[0], height=PAGE[1])
        with self.assertRaisesRegex(ValueError, "overflows"):
            render_dynamic_tolerance(page, schema)
        self.assertEqual(page.get_text().strip(), "")
        self.assertEqual(page.get_drawings(), [])
        doc.close()

    def test_required_arrays_and_exact_source_strings(self):
        schema = eight_field_schema()
        schema["new_supplier_condition"] = ["MUST RETAIN"]
        with self.assertRaisesRegex(ValueError, "Unknown tolerance categories"):
            normalize_tolerance_schema(schema)
        schema = eight_field_schema()
        del schema["angular_tolerances"]
        with self.assertRaisesRegex(ValueError, "explicit array"):
            normalize_tolerance_schema(schema)
        schema = eight_field_schema()
        schema["linear_tolerances"][0]["tier"] = " X."
        with self.assertRaisesRegex(ValueError, "outer whitespace"):
            normalize_tolerance_schema(schema)
        schema = eight_field_schema()
        schema["linear_tolerances"][1] = copy.deepcopy(schema["linear_tolerances"][0])
        with self.assertRaisesRegex(ValueError, "duplicate tier"):
            normalize_tolerance_schema(schema)


if __name__ == "__main__":
    unittest.main()
