"""Tests for the opt-in, production-code-neutral performance probe."""
import importlib.util
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'perf_profile.py'
SPEC = importlib.util.spec_from_file_location('perf_profile', MODULE_PATH)
profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile)


class FakeTimer:
    def __init__(self, values):
        self.values = iter(values)

    def __call__(self):
        return next(self.values)


class StageClockTests(unittest.TestCase):
    def test_nested_exclusive_time_does_not_double_count(self):
        timer = FakeTimer([0, 1, 3, 7])
        clock = profile.StageClock(timer)
        with clock.span('parent'):
            with clock.span('child'):
                pass
        data = clock.summary()
        self.assertEqual(data['parent'], {'calls': 1, 'inclusive_seconds': 7,
                                          'exclusive_seconds': 5})
        self.assertEqual(data['child'], {'calls': 1, 'inclusive_seconds': 2,
                                         'exclusive_seconds': 2})

    def test_exception_still_closes_span_and_restores_stack(self):
        clock = profile.StageClock(FakeTimer([4, 9]))
        with self.assertRaisesRegex(ValueError, 'measured'):
            with clock.span('failed'):
                raise ValueError('measured')
        self.assertEqual(clock.stack, [])
        self.assertEqual(clock.summary()['failed']['exclusive_seconds'], 5)

    def test_wrap_preserves_return_value(self):
        clock = profile.StageClock(FakeTimer([2, 4]))
        measured = clock.wrap(lambda x: x * 3, 'multiply')
        self.assertEqual(measured(7), 21)
        self.assertEqual(clock.summary()['multiply']['calls'], 1)


class FileHashTests(unittest.TestCase):
    def test_streaming_sha256(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'blob'
            payload = b'abc' * 1_000_000
            path.write_bytes(payload)
            self.assertEqual(profile.sha256(path), hashlib.sha256(payload).hexdigest())

    def test_visual_hash_ignores_pdf_trailer_id_but_byte_hash_does_not(self):
        import pymupdf as fitz
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / 'original.pdf'
            doc = fitz.open()
            page = doc.new_page(width=100, height=100)
            page.insert_text((10, 30), 'SAMPLE')
            doc.save(original)
            outputs = []
            for value in ('A', 'B'):
                source = fitz.open(original)
                source.xref_set_key(-1, 'ID', f'[<{value * 32}> <{value * 32}>]')
                target = root / f'{value}.pdf'
                source.save(target, no_new_id=True)
                outputs.append(target)
            self.assertNotEqual(profile.sha256(outputs[0]), profile.sha256(outputs[1]))
            self.assertEqual(profile.visual_sha256(outputs[0]), profile.visual_sha256(outputs[1]))


if __name__ == '__main__':
    unittest.main()
