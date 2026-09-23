"""Approved visual replay tests use only synthetic PDFs, fonts and brand assets."""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pymupdf as fitz

import test_pipeline as fixtures

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import kangsheng as k
import approved_recipe as approved


class ApprovedRecipeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the fixture maker, not the other TestCase's tests or live state.
        fixtures.PipelineTest.setUpClass.__func__(cls)
        cls.template['renderer'] = 'native'
        cls.template['color_profile'] = 'cyan-gold-v1'
        cls.operations = [
            {'op': 'source', 'source_box': [20, 10, 400, 60],
             'target_box': [50, 350, 430, 400]},
            {'op': 'rect', 'box': [600, 340, 720, 375], 'width': .5},
            {'op': 'line', 'points': [[600, 390], [720, 390]], 'width': .5},
            {'op': 'text', 'point': [620, 410], 'value': '2026-09-23', 'fontsize': 8},
        ]
        cls.reference = cls.base / 'reference.pdf'
        cls.make_baseline(cls.template, cls.operations, cls.reference)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @staticmethod
    def make_baseline(cfg, operations, path):
        """Compose independently of approved_recipe._compose, including overlays."""
        with tempfile.TemporaryDirectory() as cache:
            _, colored, _, _ = k.cached_source(cfg, Path(cfg['source']['path']), Path(cache))
            doc, page = k.compose_document(cfg, colored, cfg['assets'], list(k.placements(cfg)))
            with doc, fitz.open(colored) as source:
                for op in operations:
                    if op['op'] == 'source':
                        page.show_pdf_page(fitz.Rect(op['target_box']), source, 0,
                                           clip=fitz.Rect(op['source_box']))
                    elif op['op'] == 'rect':
                        page.draw_rect(fitz.Rect(op['box']), color=k.BLUE, width=op['width'])
                    elif op['op'] == 'line':
                        page.draw_line(*op['points'], color=k.BLUE, width=op['width'])
                    else:
                        page.insert_text(op['point'], op['value'],
                                         fontsize=op['fontsize'], color=k.BLUE)
                doc.save(path, garbage=4, deflate=True)

    def setUp(self):
        self.work = tempfile.TemporaryDirectory(dir=self.base)
        self.addCleanup(self.work.cleanup)
        self.case = Path(self.work.name)
        self.cfg = copy.deepcopy(self.template)
        self.source_path = self.case / 'source.pdf'
        shutil.copyfile(self.source, self.source_path)
        self.cfg['source']['path'] = str(self.source_path)
        for name, original in self.cfg['assets'].items():
            destination = self.case / Path(original).name
            shutil.copyfile(original, destination)
            self.cfg['assets'][name] = str(destination)
        self.baseline = self.case / 'baseline.pdf'
        shutil.copyfile(self.reference, self.baseline)
        self.plan = self.case / 'plan.json'
        self.recipe = self.case / 'approved.json'
        self.plan_data = {'recipe_schema': approved.SCHEMA,
                          'configuration': self.cfg,
                          'operations': copy.deepcopy(self.operations)}
        self.write(self.plan, self.plan_data)

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    @staticmethod
    def read(path):
        return json.loads(path.read_text(encoding='utf-8'))

    def cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / 'scripts/kangsheng.py'),
                               *map(str, args)], capture_output=True, text=True)

    def freeze(self, plan=None, baseline=None, output=None):
        result = self.cli('freeze-approved', plan or self.plan, '--baseline', baseline or self.baseline,
                          '--output', output or self.recipe,
                          '--approval-note', '明确认可：纯合成测试基准的品牌与版式。')
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def replay(self, output=None):
        output = output or self.case / 'replay'
        result = self.cli('replay', self.recipe, '--output', output)
        self.assertEqual(result.returncode, 0, result.stderr)
        return output, json.loads(result.stdout)

    def assert_pixels_equal(self, first, second):
        with fitz.open(first) as a, fitz.open(second) as b:
            self.assertEqual(len(a), 1)
            self.assertEqual(len(b), 1)
            self.assertEqual(a[0].rect, b[0].rect)
            left = a[0].get_pixmap(matrix=fitz.Matrix(4, 4), colorspace=fitz.csRGB, alpha=False)
            right = b[0].get_pixmap(matrix=fitz.Matrix(4, 4), colorspace=fitz.csRGB, alpha=False)
            self.assertEqual((left.width, left.height, left.n), (right.width, right.height, right.n))
            self.assertTrue(left.samples == right.samples, 'Frozen baseline and replay differ at 4x RGB')

    def test_native_vector_paths_require_exact_source_inventory(self):
        with fitz.open() as source, fitz.open() as output:
            page = source.new_page(width=100, height=100)
            page.draw_rect(fitz.Rect(12, 14, 35, 28), color=(0, 0, 0), width=.3)
            paths = page.get_drawings()
            self.assertEqual(len(paths), 1)
            target = output.new_page(width=200, height=200)
            op = {'op': 'native_paths', 'source_box': [0, 0, 100, 100],
                  'target_box': [50, 60, 100, 100], 'maxscale': .95,
                  'cell_margin': 3, 'stroke_width': .3,
                  'expected_path_count': 1,
                  'expected_source_bbox': list(paths[0]['rect'])}
            approved._draw_native_paths(target, paths, op)
            self.assertEqual(len(target.get_drawings()), 1)
            wrong = dict(op, expected_path_count=2)
            with self.assertRaisesRegex(ValueError, 'inventory changed'):
                approved._draw_native_paths(target, paths, wrong)
            wrong = dict(op, expected_source_bbox=[1, 1, 2, 2])
            with self.assertRaisesRegex(ValueError, 'bounds changed'):
                approved._draw_native_paths(target, paths, wrong)

    def test_freeze_and_replay_exact_pixels_without_engineering_release(self):
        frozen = self.freeze()
        recipe = self.read(self.recipe)
        self.assertEqual(recipe['recipe_schema'], 'kangsheng-approved-recipe-v1')
        self.assertEqual(recipe['operations'], self.operations)
        self.assertEqual(frozen['different_pixels'], 0)
        self.assertFalse(recipe['engineering_release'])
        self.assertEqual(recipe['bindings']['source_sha256'], k.digest(self.source_path))
        out, report = self.replay()
        self.assertEqual(report['status'], 'APPROVED_VISUAL_REPLAY_VERIFIED')
        self.assertEqual(report['different_pixels'], 0)
        self.assertFalse(report['engineering_release'])
        self.assertFalse(report['reused'])
        self.assertTrue((out / 'preview.png').is_file())
        self.assertEqual(report['output_sha256'], k.digest(out / 'drawing.pdf'))
        self.assert_pixels_equal(self.baseline, out / 'drawing.pdf')
        self.assertFalse(list(self.case.rglob('release.json')))

    def test_reuse_rechecks_pdf_pixels_and_preview_not_just_cached_hashes(self):
        self.freeze()
        out, _ = self.replay()
        _, reused = self.replay(out)
        self.assertTrue(reused['reused'])
        # Make the cached file hash look current: the visual comparison must
        # still reject this altered page, regenerate, and never claim release.
        pdf = out / 'drawing.pdf'
        changed = out / 'changed.pdf'
        with fitz.open(pdf) as doc:
            doc[0].insert_text((350, 300), 'WRONG 999', fontsize=12)
            doc.save(changed)
        changed.replace(pdf)
        report = self.read(out / 'replay-check.json')
        report['output_sha256'] = k.digest(pdf)
        self.write(out / 'replay-check.json', report)
        _, checked = self.replay(out)
        self.assertFalse(checked['reused'])
        self.assert_pixels_equal(self.baseline, pdf)
        preview = fitz.Pixmap(str(out / 'preview.png'))
        preview.clear_with(255)
        preview.save(out / 'preview.png')
        report = self.read(out / 'replay-check.json')
        report['preview_sha256'] = k.digest(out / 'preview.png')
        self.write(out / 'replay-check.json', report)
        _, checked = self.replay(out)
        self.assertFalse(checked['reused'])
        (out / 'preview.png').write_bytes(b'not a PNG')
        _, checked = self.replay(out)
        self.assertFalse(checked['reused'])
        self.assertEqual(checked['preview_sha256'], k.digest(out / 'preview.png'))
        self.assertFalse(checked['engineering_release'])
        report = self.read(out / 'replay-check.json')
        report['engineering_release'] = True
        self.write(out / 'replay-check.json', report)
        _, checked = self.replay(out)
        self.assertFalse(checked['reused'])
        self.assertFalse(checked['engineering_release'])
        self.assertFalse((out / 'release.json').exists())

    def test_source_baseline_and_every_asset_remain_hash_bound(self):
        self.freeze()
        for path in [self.source_path, self.baseline,
                     *map(Path, self.cfg['assets'].values())]:
            with self.subTest(input=path.name):
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b'\nchanged fixture bytes\n')
                    out = self.case / ('changed-' + path.name)
                    result = self.cli('replay', self.recipe, '--output', out)
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertFalse((out / 'drawing.pdf').exists())
                    self.assertFalse((out / 'release.json').exists())
                finally:
                    path.write_bytes(original)

    def test_frozen_configuration_operations_and_approval_are_immutable(self):
        self.freeze()
        original = self.read(self.recipe)
        changes = [
            lambda r: r['configuration']['fields'].update(model='WRONG'),
            lambda r: r['operations'][1].update(width=1),
            lambda r: r.update(approval_note='unreviewed replacement'),
            lambda r: r['baseline_visual'].update(sha256='0' * 64),
        ]
        for index, change in enumerate(changes):
            with self.subTest(change=index):
                modified = copy.deepcopy(original)
                change(modified)
                self.write(self.recipe, modified)
                result = self.cli('replay', self.recipe, '--output', self.case / ('changed-recipe-' + str(index)))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('recipe changed', result.stderr.lower())
        self.write(self.recipe, original)
        self.replay()

    def test_engine_binding_is_checked_even_with_consistent_recipe_checksum(self):
        self.freeze()
        recipe = self.read(self.recipe)
        recipe['bindings']['engine']['scripts']['frame.py'] = '0' * 64
        recipe['recipe_sha256'] = approved._hash({key: value for key, value in recipe.items()
                                                 if key != 'recipe_sha256'})
        self.write(self.recipe, recipe)
        result = self.cli('replay', self.recipe, '--output', self.case / 'engine-change')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('engine changed', result.stderr)

    def test_baseline_pixel_difference_prevents_freeze_and_keeps_diagnostics(self):
        altered = self.case / 'altered-baseline.pdf'
        with fitz.open(self.baseline) as doc:
            doc[0].insert_text((350, 300), 'CHANGED', fontsize=12)
            doc.save(altered)
        result = self.cli('freeze-approved', self.plan, '--baseline', altered,
                          '--output', self.recipe, '--approval-note', '明确认可')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.recipe.exists())
        reports = list(self.case.glob('*-freeze-*/replay-check.json'))
        self.assertEqual(len(reports), 1)
        report = self.read(reports[0])
        self.assertEqual(report['status'], 'FREEZE_FAILED')
        self.assertGreater(report['different_pixels'], 0)
        self.assertFalse(report['engineering_release'])
        self.assertFalse(list(self.case.rglob('release.json')))

    def test_freeze_requires_explicit_note_and_does_not_overwrite_any_input(self):
        result = self.cli('freeze-approved', self.plan, '--baseline', self.baseline,
                          '--output', self.recipe, '--approval-note', '   ')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.recipe.exists())
        protected = [self.plan, self.source_path, self.baseline, *map(Path, self.cfg['assets'].values())]
        for path in protected:
            with self.subTest(input=path.name):
                before = path.read_bytes()
                result = self.cli('freeze-approved', self.plan, '--baseline', self.baseline,
                                  '--output', path, '--approval-note', '明确认可')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(path.read_bytes(), before)
        self.freeze()
        before = self.recipe.read_bytes()
        result = self.cli('freeze-approved', self.plan, '--baseline', self.baseline,
                          '--output', self.recipe, '--approval-note', '明确认可')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.recipe.read_bytes(), before)

    def test_replay_protects_input_alias_and_unmanaged_or_release_directory(self):
        self.freeze()
        before = self.source_path.read_bytes()
        alias = self.case / 'drawing.pdf'
        for make_link in [alias.hardlink_to, alias.symlink_to]:
            with self.subTest(link=make_link.__name__):
                make_link(self.source_path)
                result = self.cli('replay', self.recipe, '--output', self.case)
                self.assertNotEqual(result.returncode, 0)
                self.assertRegex(result.stderr, 'protected input|symbolic or hard link')
                self.assertEqual(self.source_path.read_bytes(), before)
                alias.unlink()
        for name, artifact in [('unmanaged', 'drawing.pdf'), ('released', 'release.json')]:
            with self.subTest(directory=name):
                out = self.case / name
                out.mkdir()
                sentinel = out / artifact
                sentinel.write_bytes(b'previous artifact must survive')
                result = self.cli('replay', self.recipe, '--output', out)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(sentinel.read_bytes(), b'previous artifact must survive')

    def test_text_operations_accept_dates_but_never_technical_values(self):
        for value in ['2.0 A', '12 V', '999', '2026-02-30', '2026-09-23 DIM 1.8']:
            with self.subTest(value=value):
                plan = copy.deepcopy(self.plan_data)
                plan['operations'][-1]['value'] = value
                with self.assertRaisesRegex(ValueError, '[Dd]ate'):
                    approved._validate(plan, self.case)
        for change in [lambda p: p['operations'].append({'op': 'erase', 'box': [0, 0, 10, 10]}),
                       lambda p: p['operations'][0].update(payload='extra'),
                       lambda p: p['operations'][1].update(width=float('nan')),
                       lambda p: p['configuration'].update(color_profile='unknown')]:
            plan = copy.deepcopy(self.plan_data)
            change(plan)
            with self.assertRaises(ValueError):
                approved._validate(plan, self.case)

    def test_registry_batch_skips_unknown_and_keeps_success_failure_and_normal_batch_independent(self):
        self.freeze()
        second_source = self.case / 'second.pdf'
        second_source.write_bytes(self.source_path.read_bytes() + b'\nsecond synthetic PDF\n')
        second_cfg = copy.deepcopy(self.cfg)
        second_cfg['source'].update(path=str(second_source), sha256=k.digest(second_source))
        second_plan = self.case / 'second-plan.json'
        self.write(second_plan, dict(self.plan_data, configuration=second_cfg))
        second_recipe = self.case / 'second-approved.json'
        self.freeze(plan=second_plan, output=second_recipe)
        broken = self.read(second_recipe)
        broken['operations'][1]['width'] = 2
        self.write(second_recipe, broken)
        unknown = self.case / 'unknown.pdf'
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((50, 50), 'Unregistered synthetic drawing')
            doc.save(unknown)
        registry = self.case / 'registry.json'
        self.write(registry, {'schema_version': 1, 'recipes': [
            {'source_sha256': k.digest(self.source_path), 'recipe_path': self.recipe.name},
            {'source_sha256': k.digest(second_source), 'recipe_path': second_recipe.name},
        ]})
        root = self.case / 'batch'
        root.mkdir()
        normal_state = root / 'batch-state.json'
        normal_state.write_text('{"ordinary_pipeline_state":"unchanged"}')
        before = normal_state.read_bytes()
        result = self.cli('replay-batch', '--registry', registry, '--sources',
                          second_source, unknown, self.source_path, '--output-root', root)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report['engineering_release'])
        self.assertEqual([item['status'] for item in report['results']],
                         ['REPLAY_FAILED', 'skip_needs_inspection', 'APPROVED_VISUAL_REPLAY_VERIFIED'])
        known_out = root / k.digest(self.source_path)
        self.assert_pixels_equal(self.baseline, known_out / 'drawing.pdf')
        self.assertFalse((root / k.digest(unknown)).exists())
        self.assertEqual(normal_state.read_bytes(), before)
        self.assertFalse(list(root.rglob('release.json')))
        output_hash = k.digest(known_out / 'drawing.pdf')
        again = self.cli('replay-batch', '--registry', registry, '--sources',
                         second_source, unknown, self.source_path, '--output-root', root)
        self.assertEqual(again.returncode, 0, again.stderr)
        rerun = json.loads(again.stdout)['results']
        self.assertEqual(rerun[0]['status'], 'REPLAY_FAILED')
        self.assertEqual(rerun[1]['status'], 'skip_needs_inspection')
        self.assertTrue(rerun[2]['reused'])
        self.assertEqual(k.digest(known_out / 'drawing.pdf'), output_hash)
        self.assertEqual(normal_state.read_bytes(), before)

    def test_explicit_svg_renderer_has_its_own_exact_baseline(self):
        self.cfg['renderer'] = 'svg'
        baseline = self.case / 'svg-baseline.pdf'
        self.make_baseline(self.cfg, self.operations, baseline)
        self.write(self.plan, self.plan_data)
        self.freeze(baseline=baseline)
        out, report = self.replay()
        self.assertFalse(report['engineering_release'])
        self.assert_pixels_equal(baseline, out / 'drawing.pdf')

    def test_explicit_legacy_profile_is_supported_but_freeze_requires_a_profile(self):
        unspecified = copy.deepcopy(self.plan_data)
        unspecified['configuration'].pop('color_profile')
        with self.assertRaisesRegex(ValueError, '[Cc]olor profile'):
            approved._validate(unspecified, self.case)
        self.cfg['color_profile'] = 'legacy-v1'
        baseline = self.case / 'legacy-baseline.pdf'
        self.make_baseline(self.cfg, self.operations, baseline)
        self.write(self.plan, self.plan_data)
        self.freeze(baseline=baseline)
        out, _ = self.replay()
        self.assert_pixels_equal(baseline, out / 'drawing.pdf')


if __name__ == '__main__':
    unittest.main()
