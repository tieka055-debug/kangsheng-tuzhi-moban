import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from canonical_slots import right_rail_placements


class CanonicalSlotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rule = json.loads((ROOT / 'supported-layouts/canonical-output-layout.json').read_text())

    def test_table_above_performance_and_same_horizontal_edges(self):
        out = right_rail_placements({
            'part_table': [1417, 761, 1800, 1062],
            'performance': [703, 94, 1173, 430],
        }, self.rule)
        self.assertEqual(out['status'], 'SUPPORTED_COMPATIBLE')
        table = out['placements']['part_table']['target']
        perf = out['placements']['performance']['target']
        self.assertEqual((table[0], table[2]), (perf[0], perf[2]))
        self.assertGreaterEqual(perf[1] - table[3], 12)
        self.assertLessEqual(perf[3], 440)

    def test_source_position_change_cannot_change_output_slot(self):
        a = {'part_table': [1417, 761, 1800, 1062], 'performance': [703, 94, 1173, 430]}
        b = {'part_table': [7, 20, 390, 321], 'performance': [900, 800, 1370, 1136]}
        self.assertEqual(right_rail_placements(a, self.rule), right_rail_placements(b, self.rule))

    def test_right_rail_space_conflict_requires_local_review(self):
        out = right_rail_placements({'part_table': [0,0,383,900],
                                     'performance': [0,0,470,800]}, self.rule)
        self.assertEqual(out['status'], 'AI_LOCAL_REVIEW')


if __name__ == '__main__':
    unittest.main()
