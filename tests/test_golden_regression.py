"""Synthetic-only regression guard tests; real accepted PDFs stay outside Git."""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pymupdf as fitz

import test_pipeline as fixtures

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import approved_recipe as approved
import golden_regression as golden
import kangsheng as k


class GoldenRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PipelineTest.setUpClass.__func__(cls)
        cls.cfg = copy.deepcopy(cls.template)
        cls.cfg['renderer'] = 'native'
        cls.cfg['color_profile'] = 'cyan-gold-v1'
        cls.operations = []
        cls.plan = cls.base / 'plan.json'
        cls.plan.write_text(json.dumps({'recipe_schema': approved.SCHEMA,
                                        'configuration': cls.cfg,
                                        'operations': cls.operations}), encoding='utf-8')
        cls.composed = cls.base / 'composed'
        cls.composed.mkdir()
        approved._compose(cls.cfg, cls.operations, cls.source, cls.composed)
        cls.baseline = cls.base / 'approved.pdf'
        shutil.copyfile(cls.composed / 'drawing.pdf', cls.baseline)
        cls.recipe_path = cls.base / 'recipe.json'
        approved.freeze(SimpleNamespace(plan=cls.plan, baseline=cls.baseline,
                                        output=cls.recipe_path,
                                        approval_note='Synthetic approved baseline'))
        cls.replay_dir = cls.base / 'replay'
        approved._replay(SimpleNamespace(recipe=cls.recipe_path, output=cls.replay_dir))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.temp_case = tempfile.TemporaryDirectory(dir=self.base)
        self.addCleanup(self.temp_case.cleanup)
        self.case = Path(self.temp_case.name)
        self.sample_id = 'synthetic-connector'
        self.ledger = {'schema_version': golden.LEDGER_SCHEMA, 'samples': [{
            'sample_id': self.sample_id, 'record_id': 'synthetic-record',
            'model': 'MODEL-XR125', 'source_path': str(self.source),
            'source_sha256': golden._sha(self.source),
            'approved_pdf_path': str(self.baseline),
            'approved_pdf_sha256': golden._sha(self.baseline),
            'preview_path': None, 'preview_sha256': None,
            'manifest_path': None, 'manifest_sha256': None,
            'recipe_path': str(self.recipe_path),
            'recipe_sha256': golden._sha(self.recipe_path),
            'supported_layout_id': 'synthetic-layout',
            'template_version': 'synthetic', 'engine_version': k.VERSION,
            'qa_rules_version': 'synthetic',
            'qa_status': 'APPROVED_VISUAL_REPLAY_VERIFIED',
            'qa_evidence_path': None, 'qa_evidence_sha256': None,
            'user_acceptance_status': 'ACCEPTED'}]}
        self.anchor = {'schema_version': golden.ANCHOR_SCHEMA, 'samples': [{
            'sample_id': self.sample_id,
            'source_sha256': golden._sha(self.source),
            'approved_pdf_sha256': golden._sha(self.baseline),
            'approval_basis': 'synthetic test acceptance'}]}
        self.candidate_pdf = self.case / 'candidate.pdf'
        shutil.copyfile(self.replay_dir / 'drawing.pdf', self.candidate_pdf)
        self.candidate_qa = self.case / 'replay-check.json'
        shutil.copyfile(self.replay_dir / 'replay-check.json', self.candidate_qa)
        self.candidate_preview = self.case / 'preview.png'
        shutil.copyfile(self.replay_dir / 'preview.png', self.candidate_preview)
        self.historic_qa = self.case / 'historic-qa.json'
        historical = {
            'status': approved.STATUS, 'engineering_release': False,
            'different_pixels': 0, 'source_sha256': golden._sha(self.source),
            'recipe_sha256': json.loads(self.recipe_path.read_text())['recipe_sha256'],
            'expected_visual': golden._visual(self.baseline)}
        self.historic_qa.write_text(json.dumps(historical), encoding='utf-8')
        self.ledger['samples'][0]['qa_evidence_path'] = str(self.historic_qa)
        self.ledger['samples'][0]['qa_evidence_sha256'] = golden._sha(self.historic_qa)
        self.candidates = {'schema_version': golden.CANDIDATES_SCHEMA,
                           'candidates': [{'sample_id': self.sample_id,
                                           'pdf_path': str(self.candidate_pdf),
                                           'qa_evidence_path': str(self.candidate_qa)}]}
        self.write_records()

    def write_records(self):
        self.ledger_path = self.case / 'ledger.json'
        self.anchor_path = self.case / 'anchor.json'
        self.candidates_path = self.case / 'candidates.json'
        for path, obj in ((self.ledger_path, self.ledger),
                          (self.anchor_path, self.anchor),
                          (self.candidates_path, self.candidates)):
            path.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')

    def check(self):
        self.write_records()
        report = golden.run(self.ledger_path, self.anchor_path,
                            candidate_index=self.candidates_path)
        return report['results'][0]

    def update_qa_for_candidate(self):
        report = json.loads(self.candidate_qa.read_text())
        visual, _ = approved._visual(self.candidate_pdf, self.candidate_preview)
        report['output_sha256'] = golden._sha(self.candidate_pdf)
        report['visual_sha256'] = visual['sha256']
        report['actual_visual'] = visual
        report['preview_sha256'] = golden._sha(self.candidate_preview)
        self.candidate_qa.write_text(json.dumps(report), encoding='utf-8')

    def changed_pdf(self, draw):
        changed = self.case / 'changed.pdf'
        with fitz.open(self.candidate_pdf) as doc:
            draw(doc[0])
            doc.save(changed)
        changed.replace(self.candidate_pdf)
        self.update_qa_for_candidate()

    def test_exact_full_page_with_bound_source_recipe_and_qa_passes_visual_only(self):
        result = self.check()
        self.assertEqual(result['status'], golden.PASS, result)
        self.assertFalse(result['engineering_release'])
        self.assertGreater(result['extractable_word_count'], 0)

    def test_ledger_or_golden_change_cannot_move_approval_anchor(self):
        self.ledger['samples'][0]['approved_pdf_sha256'] = '0' * 64
        self.assertEqual(self.check()['status'], 'ANCHOR_MISMATCH')
        self.ledger['samples'][0]['approved_pdf_sha256'] = golden._sha(self.baseline)
        changed = self.case / 'new-golden.pdf'
        with fitz.open(self.baseline) as doc:
            doc[0].insert_text((300, 300), 'WRONG ±0.50', fontsize=10)
            doc.save(changed)
        self.ledger['samples'][0]['approved_pdf_path'] = str(changed)
        self.assertEqual(self.check()['status'], 'APPROVED_PDF_HASH_MISMATCH')
        # Even updating the ledger hash cannot promote this replacement.
        self.ledger['samples'][0]['approved_pdf_sha256'] = golden._sha(changed)
        self.assertEqual(self.check()['status'], 'ANCHOR_MISMATCH')

    def test_stale_or_replaced_golden_preview_is_not_accepted(self):
        self.ledger['samples'][0]['preview_path'] = str(self.candidate_preview)
        self.ledger['samples'][0]['preview_sha256'] = golden._sha(self.candidate_preview)
        self.assertEqual(self.check()['status'], golden.PASS)
        preview = fitz.Pixmap(str(self.candidate_preview))
        preview.clear_with(255)
        preview.save(self.candidate_preview)
        self.ledger['samples'][0]['preview_sha256'] = golden._sha(self.candidate_preview)
        self.assertEqual(self.check()['status'], 'GOLDEN_PREVIEW_MISMATCH')

    def test_wrong_source_or_new_technical_element_cannot_reuse_cached_output(self):
        wrong = self.case / 'different-source.pdf'
        with fitz.open(self.source) as doc:
            doc[0].insert_text((300, 320), 'EXTRA TECHNICAL CONDITION', fontsize=10)
            doc.save(wrong)
        self.ledger['samples'][0]['source_path'] = str(wrong)
        self.assertEqual(self.check()['status'], 'SOURCE_HASH_MISMATCH')
        self.ledger['samples'][0]['source_sha256'] = golden._sha(wrong)
        self.assertEqual(self.check()['status'], 'ANCHOR_MISMATCH')

    def test_numeric_sign_part_number_and_missing_view_mutations_fail(self):
        for text, box in [('WRONG ±0.50', (180, 300)),
                          ('PART NO. X-9P-R125', (180, 300)),
                          ('-85 C', (180, 300))]:
            with self.subTest(text=text):
                shutil.copyfile(self.replay_dir / 'drawing.pdf', self.candidate_pdf)
                self.changed_pdf(lambda page: page.insert_text(box, text, fontsize=10))
                self.assertEqual(self.check()['status'], 'VISUAL_MISMATCH')
        shutil.copyfile(self.replay_dir / 'drawing.pdf', self.candidate_pdf)
        self.changed_pdf(lambda page: page.draw_rect(fitz.Rect(60, 100, 230, 200),
                                                     fill=(1, 1, 1), color=(1, 1, 1)))
        self.assertEqual(self.check()['status'], 'VISUAL_MISMATCH')

    def test_invisible_text_change_fails_even_when_pixels_match(self):
        original_visual = golden._visual(self.candidate_pdf)
        self.changed_pdf(lambda page: page.insert_text((300, 300), 'PART NO. X-9P-R125',
                                                       fontsize=10, render_mode=3))
        self.assertEqual(golden._visual(self.candidate_pdf), original_visual)
        self.assertEqual(self.check()['status'], 'TECHNICAL_TEXT_MISMATCH')

    def test_duplicate_opaque_border_is_detected_when_pixels_match(self):
        original_visual = golden._visual(self.candidate_pdf)
        # Paint a narrower rule inside the already opaque centre of the top
        # frame rule.  Its geometry is an extra line although RGB is identical.
        self.changed_pdf(lambda page: page.draw_line((30, 29), (800, 29),
                                                     color=k.BLUE, width=.2))
        self.assertEqual(golden._visual(self.candidate_pdf), original_visual)
        self.assertEqual(self.check()['status'], 'VECTOR_CONTENT_MISMATCH')

    def test_stale_qa_report_and_missing_report_are_rejected(self):
        self.candidate_qa.unlink()
        self.assertEqual(self.check()['status'], 'MISSING_INPUT')
        shutil.copyfile(self.replay_dir / 'replay-check.json', self.candidate_qa)
        self.changed_pdf(lambda page: page.insert_text((300, 300), 'WRONG', fontsize=10))
        report = json.loads(self.candidate_qa.read_text())
        report['output_sha256'] = '0' * 64
        self.candidate_qa.write_text(json.dumps(report), encoding='utf-8')
        self.assertEqual(self.check()['status'], 'CANDIDATE_QA_FAILED')

    def test_engine_change_or_recipe_change_invalidates_old_result(self):
        changed = self.case / 'changed-recipe.json'
        recipe = json.loads(self.recipe_path.read_text())
        recipe['bindings']['engine']['scripts']['kangsheng.py'] = '0' * 64
        recipe['recipe_sha256'] = approved._hash({key: value for key, value in recipe.items()
                                                 if key != 'recipe_sha256'})
        changed.write_text(json.dumps(recipe), encoding='utf-8')
        self.ledger['samples'][0]['recipe_path'] = str(changed)
        self.ledger['samples'][0]['recipe_sha256'] = golden._sha(changed)
        historic = json.loads(self.historic_qa.read_text())
        historic['recipe_sha256'] = recipe['recipe_sha256']
        self.historic_qa.write_text(json.dumps(historic), encoding='utf-8')
        self.ledger['samples'][0]['qa_evidence_sha256'] = golden._sha(self.historic_qa)
        self.assertEqual(self.check()['status'], 'RECIPE_ENV_CHANGED')

    def test_unregistered_recipe_or_unresolved_nts_is_not_a_pass(self):
        self.ledger['samples'][0]['recipe_path'] = None
        self.ledger['samples'][0]['recipe_sha256'] = None
        self.assertEqual(self.check()['status'], 'NEEDS_RECIPE')
        self.ledger['samples'][0]['user_acceptance_status'] = 'UNRESOLVED_BASELINE_CONFLICT'
        self.ledger['samples'][0]['blocker_reason'] = 'historical 3:1, NTS target not yet verified'
        self.ledger['samples'][0]['current_nts_candidate_pdf_path'] = str(self.candidate_pdf)
        self.ledger['samples'][0]['current_nts_candidate_pdf_sha256'] = golden._sha(self.candidate_pdf)
        result = self.check()
        self.assertEqual(result['status'], 'BLOCKED_GOLDEN_CONFLICT')
        self.assertEqual(result['current_nts_candidate_pdf_sha256'], golden._sha(self.candidate_pdf))
        self.ledger['samples'][0]['current_nts_candidate_pdf_sha256'] = '0' * 64
        self.assertEqual(self.check()['status'], 'CURRENT_NTS_CANDIDATE_PDF_HASH_MISMATCH')

    def test_candidate_cannot_be_the_approved_input(self):
        self.candidates['candidates'][0]['pdf_path'] = str(self.baseline)
        self.assertEqual(self.check()['status'], 'INVALID_CANDIDATE')

    def test_pinned_126_replay_is_explicit_and_fails_closed_without_it(self):
        runtime = Path('/usr/bin/python3')
        inspection = subprocess.run([str(runtime), '-c',
                                     'import pymupdf;print(pymupdf.__version__)'],
                                    capture_output=True, text=True)
        if inspection.returncode or inspection.stdout.strip() != '1.26.5':
            self.skipTest('Pinned PyMuPDF 1.26.5 runtime not installed')
        legacy_cfg = copy.deepcopy(self.cfg)
        legacy_cfg['renderer'] = 'svg'  # System runtime has no pikepdf.
        legacy_plan = self.case / 'legacy-plan.json'
        legacy_plan.write_text(json.dumps({'recipe_schema': approved.SCHEMA,
                                           'configuration': legacy_cfg,
                                           'operations': []}), encoding='utf-8')
        composed = self.case / 'legacy-compose'
        composed.mkdir()
        code = ('import json,sys,pathlib;sys.path.insert(0,sys.argv[1]);'
                'import approved_recipe as a;'
                'p=pathlib.Path(sys.argv[2]);plan=a._read(p);'
                'cfg,ops=a._validate(plan,p.parent);'
                'a._compose(cfg,ops,pathlib.Path(cfg["source"]["path"]),'
                'pathlib.Path(sys.argv[3]))')
        made = subprocess.run([str(runtime), '-c', code, str(ROOT / 'scripts'),
                               str(legacy_plan), str(composed)],
                              capture_output=True, text=True)
        self.assertEqual(made.returncode, 0, made.stderr)
        legacy_baseline = self.case / 'legacy-approved.pdf'
        shutil.copyfile(composed / 'drawing.pdf', legacy_baseline)
        legacy_recipe = self.case / 'legacy-recipe.json'
        frozen = subprocess.run([str(runtime), str(ROOT / 'scripts/kangsheng.py'),
                                 'freeze-approved', str(legacy_plan), '--baseline',
                                 str(legacy_baseline), '--output', str(legacy_recipe),
                                 '--approval-note', 'Synthetic 1.26.5 fixture'],
                                capture_output=True, text=True)
        self.assertEqual(frozen.returncode, 0, frozen.stderr)
        freeze_report = json.loads(frozen.stdout)
        diagnostics = Path(freeze_report['diagnostics'])
        row = self.ledger['samples'][0]
        row.update(approved_pdf_path=str(legacy_baseline),
                   approved_pdf_sha256=golden._sha(legacy_baseline),
                   preview_path=str(diagnostics / 'baseline-preview.png'),
                   preview_sha256=golden._sha(diagnostics / 'baseline-preview.png'),
                   recipe_path=str(legacy_recipe),
                   recipe_sha256=golden._sha(legacy_recipe),
                   qa_evidence_path=str(diagnostics / 'replay-check.json'),
                   qa_evidence_sha256=golden._sha(diagnostics / 'replay-check.json'))
        self.anchor['samples'][0]['approved_pdf_sha256'] = golden._sha(legacy_baseline)
        self.write_records()
        no_runtime = golden.run(self.ledger_path, self.anchor_path,
                                replay_root=self.case / 'without-runtime')
        self.assertEqual(no_runtime['results'][0]['status'], 'RECIPE_ENV_CHANGED')
        wrong_runtime = golden.run(self.ledger_path, self.anchor_path,
                                   replay_root=self.case / 'wrong-runtime',
                                   runtime_126=str(Path(sys.executable).resolve()))
        self.assertEqual(wrong_runtime['results'][0]['status'], 'RUNTIME_VERSION_MISMATCH')
        external = tempfile.TemporaryDirectory()
        self.addCleanup(external.cleanup)
        pinned = golden.run(self.ledger_path, self.anchor_path,
                            replay_root=Path(external.name) / 'pinned-runtime',
                            runtime_126=str(runtime))
        self.assertEqual(pinned['results'][0]['status'], golden.PASS,
                         pinned['results'][0])
        self.assertEqual(pinned['results'][0]['runtime'], 'pinned-1.26.5')


if __name__ == '__main__':
    unittest.main()
