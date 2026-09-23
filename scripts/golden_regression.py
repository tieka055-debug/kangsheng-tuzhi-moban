#!/usr/bin/env python3
"""Read-only, anchor-bound regression of user-approved private PDF fixtures.

The approval anchor and golden ledger live outside this repository.  This tool
never creates or updates either one; in particular a failed replay cannot be
"fixed" by replacing the approved PDF or its recorded SHA256.  A visual pass
is deliberately not an engineering release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pymupdf as fitz
import numpy as np

import approved_recipe as approved


LEDGER_SCHEMA = 'kangsheng-golden-ledger-v1'
ANCHOR_SCHEMA = 'kangsheng-approval-anchor-v1'
CANDIDATES_SCHEMA = 'kangsheng-golden-candidates-v1'
PASS = 'PASS_VISUAL_REGRESSION'
HEX64 = re.compile(r'^[0-9a-f]{64}$')
ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$')
ACCEPTED = {'ACCEPTED', 'USER_ACCEPTED', 'ACCEPTED_VISUAL'}


class CheckError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _read(path):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise CheckError('INVALID_RECORD', f'Duplicate JSON key: {key}')
            value[key] = item
        return value

    def constant(value):
        raise CheckError('INVALID_RECORD', f'Non-finite JSON number: {value}')

    return json.loads(Path(path).read_text(encoding='utf-8'),
                      object_pairs_hook=pairs, parse_constant=constant)


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _must(condition, status, message):
    if not condition:
        raise CheckError(status, message)


def _hex(value, label):
    _must(isinstance(value, str) and HEX64.fullmatch(value) is not None,
          'INVALID_RECORD', f'{label} must be a lowercase SHA256')
    return value


def _path(value, label):
    _must(isinstance(value, str) and value and Path(value).is_absolute(),
          'INVALID_RECORD', f'{label} must be an absolute file path')
    path = Path(value)
    _must(not path.is_symlink() and path.is_file(), 'MISSING_INPUT',
          f'{label} is missing, a symlink, or not a regular file: {path}')
    return path


def _bound_file(row, stem, required=False):
    path_key, sha_key = stem + '_path', stem + '_sha256'
    value, expected = row.get(path_key), row.get(sha_key)
    if value is None and expected is None:
        _must(not required, 'MISSING_INPUT', f'{stem} is required')
        return None
    _must(value is not None and expected is not None, 'INVALID_RECORD',
          f'{path_key} and {sha_key} must be set together')
    path = _path(value, path_key)
    _must(_sha(path) == _hex(expected, sha_key), stem.upper() + '_HASH_MISMATCH',
          f'{stem} SHA256 differs from ledger: {path}')
    return path


def _pdf_text(path):
    try:
        with fitz.open(path) as doc:
            _must(len(doc) == 1, 'PDF_STRUCTURE_MISMATCH',
                  f'Expected one page: {path}')
            # Exact page pixels separately preserve positions.  This catches
            # a rasterized or altered-text PDF that looks the same at 4x.
            return Counter(word[4] for word in doc[0].get_text('words')
                           if word[4].strip())
    except CheckError:
        raise
    except Exception as exc:
        raise CheckError('PDF_READ_FAILED', f'PDF text read failed: {exc}') from exc


def _visual(path):
    try:
        visual, _ = approved._visual(path)
        return visual
    except Exception as exc:
        raise CheckError('PDF_READ_FAILED', f'PDF render failed: {exc}') from exc


def _vector_signature(path):
    """Distinguish opaque duplicate rules and raster-only lookalikes.

    Pixel equality alone cannot detect painting the same blue border twice.
    This comparison is intentionally strict: a vector rewrite must receive
    separate engineering review even if its preview is indistinguishable.
    """
    try:
        with fitz.open(path) as doc:
            _must(len(doc) == 1, 'PDF_STRUCTURE_MISMATCH',
                  f'Expected one page: {path}')
            drawings = doc[0].get_cdrawings()
            digest = hashlib.sha256()
            for item in drawings:
                digest.update(_canonical(item))
                digest.update(b'\0')
            return {'path_count': len(drawings), 'sha256': digest.hexdigest()}
    except CheckError:
        raise
    except Exception as exc:
        raise CheckError('PDF_READ_FAILED', f'PDF vector read failed: {exc}') from exc


def _validate_golden_preview(path, baseline):
    try:
        preview = fitz.Pixmap(str(path))
        _must(preview.colorspace.n == 3 and preview.n == 3 and not preview.alpha,
              'GOLDEN_PREVIEW_MISMATCH', 'Golden preview must be RGB')
        with fitz.open(baseline) as doc:
            _must(len(doc) == 1, 'PDF_STRUCTURE_MISMATCH', 'Golden PDF is not one page')
            scale = round(preview.width / doc[0].rect.width)
            _must(scale in (1, 2, 3, 4, 5, 6, 7, 8),
                  'GOLDEN_PREVIEW_MISMATCH', 'Unexpected golden preview scale')
            expected = doc[0].get_pixmap(matrix=fitz.Matrix(scale, scale),
                                         colorspace=fitz.csRGB, alpha=False)
        _must((preview.width, preview.height, preview.n) ==
              (expected.width, expected.height, expected.n),
              'GOLDEN_PREVIEW_MISMATCH', 'Golden preview dimensions differ from PDF')
        a = np.frombuffer(preview.samples, np.uint8)
        b = np.frombuffer(expected.samples, np.uint8)
        difference = np.abs(a.astype(np.int16) - b.astype(np.int16))
        # Legacy 2x PNG previews differ by at most one channel unit at 58
        # antialiased bytes; do not treat a substituted or stale page as this.
        _must(int(difference.max(initial=0)) <= 1 and
              int(np.count_nonzero(difference)) <= 300,
              'GOLDEN_PREVIEW_MISMATCH', 'Golden preview does not depict approved PDF')
        return {'scale': scale, 'differing_channel_bytes': int(np.count_nonzero(difference))}
    except CheckError:
        raise
    except Exception as exc:
        raise CheckError('GOLDEN_PREVIEW_MISMATCH', f'Cannot verify golden preview: {exc}') from exc


def _runtime_engine(runtime, expected_version='1.26.5'):
    executable = _path(str(runtime), 'pinned runtime')
    scripts = Path(__file__).resolve().parent
    code = ('import sys,json;sys.path.insert(0,sys.argv[1]);'
            'import approved_recipe as a;print(json.dumps(a._engine(),sort_keys=True))')
    try:
        proc = subprocess.run([str(executable), '-c', code, str(scripts)],
                              capture_output=True, text=True, timeout=30, check=False)
    except Exception as exc:
        raise CheckError('RUNTIME_UNAVAILABLE', f'Cannot inspect pinned runtime: {exc}') from exc
    _must(proc.returncode == 0, 'RUNTIME_UNAVAILABLE',
          f'Pinned runtime introspection failed: {proc.stderr.strip()}')
    try:
        engine = json.loads(proc.stdout.strip())
    except Exception as exc:
        raise CheckError('RUNTIME_UNAVAILABLE', 'Pinned runtime returned invalid engine metadata') from exc
    _must(engine.get('fitz_version') == expected_version, 'RUNTIME_VERSION_MISMATCH',
          f'Pinned runtime must use PyMuPDF {expected_version}')
    return engine


def _validate_recipe(row, recipe_path, source_sha, baseline_sha, runtime_126=None):
    recipe = _read(recipe_path)
    _must(recipe.get('recipe_schema') == approved.SCHEMA,
          'RECIPE_INVALID', 'Unsupported frozen recipe schema')
    recorded = recipe.get('recipe_sha256')
    calculated = hashlib.sha256(_canonical({k: v for k, v in recipe.items()
                                             if k != 'recipe_sha256'})).hexdigest()
    _must(recorded == calculated, 'RECIPE_HASH_MISMATCH',
          'Frozen recipe content checksum changed')
    bindings = recipe.get('bindings') or {}
    cfg = recipe.get('configuration') or {}
    _must(bindings.get('source_sha256') == source_sha and
          (cfg.get('source') or {}).get('sha256') == source_sha,
          'WRONG_SOURCE_BINDING', 'Recipe is bound to a different source PDF')
    _must((bindings.get('baseline') or {}).get('sha256') == baseline_sha,
          'WRONG_BASELINE_BINDING', 'Recipe is bound to a different approved PDF')
    _must(recipe.get('engineering_release') is False,
          'RECIPE_INVALID', 'Visual recipe must not claim engineering release')
    try:
        resolved_cfg, operations = approved._validate(recipe, recipe_path.parent)
    except Exception as exc:
        raise CheckError('RECIPE_INVALID', f'Frozen operations/configuration invalid: {exc}') from exc
    _must(recipe.get('configuration_operations_sha256') ==
          approved._hash({'configuration': resolved_cfg, 'operations': operations}),
          'RECIPE_HASH_MISMATCH', 'Frozen configuration/operations checksum changed')
    # A path may refer to a byte-identical, private archive copy; compare its
    # content, not its spelling or the supplier filename.
    for label, bound in [('source', (cfg.get('source') or {}).get('path')),
                         ('baseline', (bindings.get('baseline') or {}).get('path'))]:
        bound_path = _path(bound, f'recipe {label}')
        expected = source_sha if label == 'source' else baseline_sha
        _must(_sha(bound_path) == expected, 'WRONG_' + label.upper() + '_BINDING',
              f'Recipe {label} bytes differ from approval anchor')
    try:
        current_bindings = approved._bindings(
            resolved_cfg, Path(resolved_cfg['source']['path']),
            Path(bindings['baseline']['path']))
    except Exception as exc:
        raise CheckError('RECIPE_ENV_CHANGED', str(exc)) from exc
    _must({key: value for key, value in current_bindings.items() if key != 'engine'} ==
          {key: value for key, value in bindings.items() if key != 'engine'},
          'RECIPE_ENV_CHANGED', 'Source, baseline, or asset differs from frozen recipe')
    if current_bindings['engine'] == bindings.get('engine'):
        return recipe, 'current'
    _must(runtime_126 is not None, 'RECIPE_ENV_CHANGED',
          'Recipe engine differs; pinned PyMuPDF 1.26.5 runtime not supplied')
    _must((bindings.get('engine') or {}).get('fitz_version') == '1.26.5' and
          (bindings.get('engine') or {}).get('scripts') == current_bindings['engine']['scripts'],
          'RECIPE_ENV_CHANGED',
          'Legacy recipe script SHA256 differs from current candidate scripts')
    child_engine = _runtime_engine(runtime_126)
    _must(child_engine == bindings['engine'], 'RECIPE_ENV_CHANGED',
          'Pinned runtime engine or script SHA256 differs from frozen recipe')
    return recipe, 'pinned-1.26.5'


def _candidate_report(candidate_pdf, evidence_path, recipe_path, recipe,
                      source_sha, actual_visual, legacy=False):
    _must(evidence_path is not None, 'MISSING_CANDIDATE_QA',
          'Candidate requires its own replay-check.json')
    report = _read(evidence_path)
    _must(report.get('status') == approved.STATUS and
          report.get('engineering_release') is False,
          'CANDIDATE_QA_FAILED', 'Candidate replay report is not a verified visual replay')
    _must(report.get('source_sha256') == source_sha and
          report.get('recipe_sha256') == recipe['recipe_sha256'] and
          report.get('recipe_file_sha256') == _sha(recipe_path),
          'CANDIDATE_QA_FAILED', 'Candidate QA is bound to a different source or recipe')
    frozen_visual = recipe['baseline_visual'] if legacy else actual_visual
    _must(report.get('output_sha256') == _sha(candidate_pdf) and
          report.get('visual_sha256') == frozen_visual['sha256'] and
          report.get('actual_visual') == frozen_visual and
          report.get('expected_visual') == recipe['baseline_visual'] and
          report.get('different_pixels') == 0,
          'CANDIDATE_QA_FAILED', 'Candidate QA hashes differ from actual PDF')
    preview = evidence_path.parent / 'preview.png'
    _must(preview.is_file() and not preview.is_symlink() and
          report.get('preview_sha256') == _sha(preview) and
          approved._preview_matches(preview, frozen_visual),
          'CANDIDATE_QA_FAILED', 'Candidate QA preview differs from PDF')
    return report, actual_visual


def _ledger_qa(row, source_sha, baseline_sha, baseline):
    path = _bound_file(row, 'qa_evidence', required=True)
    qa = _read(path)
    _must(isinstance(qa, dict) and qa.get('source_sha256') == source_sha,
          'LEDGER_QA_MISMATCH', 'Historic QA source binding differs from ledger')
    status = row.get('qa_status')
    if status == approved.STATUS:
        recipe = _read(row['recipe_path']) if row.get('recipe_path') else None
        expected_visual = recipe.get('baseline_visual') if recipe else _visual(baseline)
        _must(qa.get('status') == approved.STATUS and
              qa.get('engineering_release') is False and
              qa.get('different_pixels') == 0 and
              qa.get('expected_visual') == expected_visual,
              'LEDGER_QA_MISMATCH',
              'Historic visual QA does not verify the recorded approved page')
        if recipe:
            _must(qa.get('recipe_sha256') == recipe.get('recipe_sha256'),
                  'LEDGER_QA_MISMATCH', 'Historic QA is for a different recipe')
    elif status in {'HISTORICAL_AUTO_QA_PASS', 'STABLE_CANDIDATE_AUTO_QA_PASS',
                    'PARTIAL_LOCAL_CHECK_ONLY'}:
        _must(qa.get('output_sha256') == baseline_sha,
              'LEDGER_QA_MISMATCH', 'Historic QA output differs from approved PDF')
        if status == 'STABLE_CANDIDATE_AUTO_QA_PASS':
            _must(qa.get('pass') is True, 'LEDGER_QA_MISMATCH',
                  'Recorded stable candidate automatic QA did not pass')
    else:
        raise CheckError('LEDGER_QA_UNVERIFIED',
                         f'Unsupported or missing historic QA status: {status}')


def _anchor_map(anchor):
    _must(anchor.get('schema_version') == ANCHOR_SCHEMA,
          'INVALID_ANCHOR', 'Unknown approval anchor schema')
    result = {}
    for row in anchor.get('samples', []):
        key = row.get('sample_id')
        _must(isinstance(key, str) and ID.fullmatch(key) and key not in result,
              'INVALID_ANCHOR', 'Duplicate or invalid anchor sample_id')
        result[key] = {
            'source_sha256': _hex(row.get('source_sha256'), 'anchor source_sha256'),
            'approved_pdf_sha256': _hex(row.get('approved_pdf_sha256'),
                                        'anchor approved_pdf_sha256'),
        }
    _must(result, 'INVALID_ANCHOR', 'Empty approval anchor')
    return result


def _candidate_map(data):
    if data is None:
        return {}
    _must(data.get('schema_version') == CANDIDATES_SCHEMA,
          'INVALID_CANDIDATES', 'Unknown candidates schema')
    result = {}
    for row in data.get('candidates', []):
        key = row.get('sample_id')
        _must(isinstance(key, str) and ID.fullmatch(key) and key not in result,
              'INVALID_CANDIDATES', 'Duplicate or invalid candidate sample_id')
        result[key] = row
    return result


def _check_one(row, anchor, candidate, replay_root, runtime_126=None):
    sample_id = row.get('sample_id')
    result = {'sample_id': sample_id, 'record_id': row.get('record_id'),
              'model': row.get('model'), 'supported_layout_id': row.get('supported_layout_id'),
              'engineering_release': False}
    if row.get('current_nts_candidate_pdf_sha256') is not None:
        result['current_nts_candidate_pdf_sha256'] = row['current_nts_candidate_pdf_sha256']
    try:
        _must(isinstance(sample_id, str) and ID.fullmatch(sample_id),
              'INVALID_RECORD', 'Invalid sample_id')
        _must(anchor is not None, 'UNANCHORED_GOLDEN',
              'No independent approval anchor for this sample')
        source_sha = _hex(row.get('source_sha256'), 'source_sha256')
        baseline_sha = _hex(row.get('approved_pdf_sha256'), 'approved_pdf_sha256')
        _must(source_sha == anchor['source_sha256'] and
              baseline_sha == anchor['approved_pdf_sha256'],
              'ANCHOR_MISMATCH', 'Ledger source/approved hashes differ from approval anchor')
        source = _bound_file(row, 'source', required=True)
        baseline = _bound_file(row, 'approved_pdf', required=True)
        _must(source.resolve() != baseline.resolve(), 'INVALID_RECORD',
              'Source and approved PDF must be different files')
        # A future NTS candidate is evidence of an attempted transition, not
        # the historical approved PDF and not a new approval anchor.
        for optional in ('manifest', 'current_nts_candidate_pdf'):
            _bound_file(row, optional)
        preview_path = _bound_file(row, 'preview')
        _ledger_qa(row, source_sha, baseline_sha, baseline)
        acceptance = row.get('user_acceptance_status')
        _must(acceptance in ACCEPTED and not row.get('blocker_reason') and
              not row.get('unresolved_discrepancy'), 'BLOCKED_GOLDEN_CONFLICT',
              f'Acceptance baseline unresolved: {acceptance}; '
              f'{row.get("blocker_reason") or row.get("unresolved_discrepancy") or "no accepted status"}')
        recipe_path = _bound_file(row, 'recipe')
        _must(recipe_path is not None, 'NEEDS_RECIPE',
              'Accepted PDF exists, but no frozen, source-bound recipe is registered')
        recipe, runtime_kind = _validate_recipe(row, recipe_path, source_sha,
                                                baseline_sha, runtime_126)
        result['runtime'] = runtime_kind
        if preview_path is not None:
            if runtime_kind == 'pinned-1.26.5':
                _must(approved._preview_matches(preview_path, recipe['baseline_visual']),
                      'GOLDEN_PREVIEW_MISMATCH',
                      'Golden preview differs from legacy frozen baseline render')
                result['golden_preview'] = {'scale': 4, 'frozen_visual_sha256':
                                            recipe['baseline_visual']['sha256']}
            else:
                result['golden_preview'] = _validate_golden_preview(preview_path, baseline)
        baseline_visual = _visual(baseline)
        if runtime_kind == 'current':
            _must(recipe.get('baseline_visual') == baseline_visual,
                  'BASELINE_VISUAL_MISMATCH', 'Recipe baseline render differs from approved PDF')

        if replay_root is not None:
            output = replay_root / sample_id
            protected = [source, baseline, recipe_path]
            _must(not output.exists() or not output.is_symlink(),
                  'UNSAFE_OUTPUT', 'Replay output directory is a symlink')
            for item in protected:
                _must(not output.resolve().is_relative_to(item.parent.resolve()),
                      'UNSAFE_OUTPUT', 'Replay output directory overlaps private golden inputs')
            try:
                if runtime_kind == 'pinned-1.26.5':
                    proc = subprocess.run([str(runtime_126),
                                           str(Path(__file__).resolve().parent / 'kangsheng.py'),
                                           'replay', str(recipe_path), '--output', str(output)],
                                          capture_output=True, text=True, timeout=300,
                                          check=False)
                    _must(proc.returncode == 0, 'REPLAY_FAILED',
                          f'Pinned replay failed: {proc.stderr.strip()}')
                    replay_report = json.loads(proc.stdout.strip().splitlines()[-1])
                    _must(_runtime_engine(runtime_126) == recipe['bindings']['engine'],
                          'RECIPE_ENV_CHANGED', 'Pinned engine changed during replay')
                else:
                    replay_report = approved._replay(SimpleNamespace(recipe=str(recipe_path),
                                                                        output=str(output)))
            except Exception as exc:
                raise CheckError('REPLAY_FAILED', str(exc)) from exc
            candidate_pdf = output / 'drawing.pdf'
            evidence_path = output / 'replay-check.json'
            result['reused'] = replay_report.get('reused')
        elif candidate is not None:
            candidate_pdf = _path(candidate.get('pdf_path'), 'candidate pdf_path')
            evidence_path = _path(candidate.get('qa_evidence_path'),
                                  'candidate qa_evidence_path')
            _must(not candidate_pdf.samefile(baseline) and
                  not candidate_pdf.samefile(source), 'INVALID_CANDIDATE',
                  'Golden input cannot be submitted as its own candidate')
        else:
            raise CheckError('NEEDS_CANDIDATE', 'No replay requested or candidate supplied')

        candidate_visual = _visual(candidate_pdf)
        candidate_report, _ = _candidate_report(
            candidate_pdf, evidence_path, recipe_path, recipe, source_sha,
            candidate_visual, legacy=runtime_kind == 'pinned-1.26.5')
        _must(candidate_visual == baseline_visual, 'VISUAL_MISMATCH',
              'Whole-page 4x RGB visual comparison differs from approved PDF')
        baseline_words, candidate_words = _pdf_text(baseline), _pdf_text(candidate_pdf)
        _must(baseline_words == candidate_words, 'TECHNICAL_TEXT_MISMATCH',
              'Extractable PDF text, including signs and part numbers, differs')
        _must(baseline_words or row.get('qa_evidence_path'),
              'MISSING_TECHNICAL_QA',
              'No extractable baseline text or separate QA evidence')
        baseline_vectors = _vector_signature(baseline)
        candidate_vectors = _vector_signature(candidate_pdf)
        _must(baseline_vectors == candidate_vectors, 'VECTOR_CONTENT_MISMATCH',
              'Vector paths differ despite identical rendered page; check duplicate or missing rules')
        # Re-read input bytes after replay and comparison; a concurrent edit is
        # never accepted as a successful result.
        _must(_sha(source) == source_sha and _sha(baseline) == baseline_sha and
              _sha(recipe_path) == row['recipe_sha256'],
              'INPUT_CHANGED_DURING_CHECK', 'Golden inputs changed during regression')
        result.update(status=PASS, source_sha256=source_sha,
                      approved_pdf_sha256=baseline_sha,
                      candidate_pdf_sha256=_sha(candidate_pdf),
                      visual_sha256=candidate_visual['sha256'],
                      candidate_pdf_path=str(candidate_pdf),
                      qa_evidence_path=str(evidence_path),
                      extractable_word_count=sum(baseline_words.values()),
                      vector_path_count=baseline_vectors['path_count'],
                      vector_sha256=baseline_vectors['sha256'],
                      qa_status=candidate_report['status'])
    except CheckError as exc:
        result.update(status=exc.status, reason=str(exc))
    except Exception as exc:
        result.update(status='CHECK_FAILED', reason=f'{type(exc).__name__}: {exc}')
    return result


def run(ledger_path, anchor_path, candidate_index=None, replay_root=None,
        runtime_126=None):
    ledger_path = _path(str(Path(ledger_path).resolve()), 'ledger')
    anchor_path = _path(str(Path(anchor_path).resolve()), 'anchor')
    _must(ledger_path != anchor_path, 'INVALID_RECORD', 'Ledger and approval anchor must be separate files')
    ledger, anchor = _read(ledger_path), _read(anchor_path)
    _must(ledger.get('schema_version') == LEDGER_SCHEMA,
          'INVALID_RECORD', 'Unknown golden ledger schema')
    anchors = _anchor_map(anchor)
    candidates = _candidate_map(_read(candidate_index) if candidate_index else None)
    samples = ledger.get('samples')
    _must(isinstance(samples, list) and samples, 'INVALID_RECORD', 'Empty or invalid golden ledger')
    ids = [row.get('sample_id') for row in samples if isinstance(row, dict)]
    _must(len(ids) == len(samples) and len(set(ids)) == len(ids),
          'INVALID_RECORD', 'Duplicate or invalid ledger sample_id')
    _must(set(ids) == set(anchors), 'INVALID_ANCHOR',
          'Ledger and independent approval anchor sample sets differ')
    _must(not (candidate_index and replay_root), 'INVALID_OPTIONS',
          'Choose either candidate index or replay-known, not both')
    root = Path(replay_root).resolve() if replay_root else None
    results = [_check_one(row, anchors[row['sample_id']], candidates.get(row['sample_id']),
                          root, runtime_126)
               for row in samples]
    counts = dict(sorted(Counter(item['status'] for item in results).items()))
    return {'schema_version': 'kangsheng-golden-regression-v1',
            'ledger_path': str(ledger_path), 'ledger_sha256': _sha(ledger_path),
            'anchor_path': str(anchor_path), 'anchor_sha256': _sha(anchor_path),
            'mode': 'replay-known' if root else 'candidate-index' if candidate_index else 'inventory-only',
            'runtime_126': str(runtime_126) if runtime_126 else None,
            'engineering_release': False,
            'all_pass': counts == {PASS: len(samples)}, 'counts': counts,
            'results': results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ledger', help='Private golden-ledger.json, never modified')
    parser.add_argument('--anchor', required=True,
                        help='Separate approval-anchor.json with fixed source/output SHA256')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--replay-known', metavar='OUTPUT_ROOT',
                       help='Execute registered frozen recipes in this isolated output root')
    group.add_argument('--candidates', metavar='INDEX_JSON',
                       help='Verify generated PDFs and their replay-check.json evidence')
    parser.add_argument('--runtime-126', metavar='PYTHON',
                        help='Pinned PyMuPDF 1.26.5 interpreter for exact legacy recipes')
    parser.add_argument('--report', help='Write a detailed JSON report outside golden inputs')
    args = parser.parse_args(argv)
    try:
        report = run(args.ledger, args.anchor, args.candidates,
                     args.replay_known, args.runtime_126)
        if args.report:
            report_path = Path(args.report).resolve()
            protected = {Path(args.ledger).resolve(), Path(args.anchor).resolve()}
            if args.candidates:
                protected.add(Path(args.candidates).resolve())
                for entry in _candidate_map(_read(args.candidates)).values():
                    for key in ('pdf_path', 'qa_evidence_path'):
                        if entry.get(key):
                            item = Path(entry[key]).resolve()
                            protected.add(item)
                            if key == 'qa_evidence_path':
                                protected.add(item.parent / 'preview.png')
            for row in _read(args.ledger)['samples']:
                for key in ('source_path', 'approved_pdf_path', 'manifest_path',
                            'recipe_path', 'preview_path', 'qa_evidence_path',
                            'current_nts_candidate_pdf_path'):
                    if row.get(key):
                        protected.add(Path(row[key]).resolve())
                if args.replay_known:
                    output = Path(args.replay_known).resolve() / row['sample_id']
                    protected.update(output / name for name in approved.ARTIFACTS)
            _must(report_path not in protected and not report_path.is_symlink(),
                  'UNSAFE_OUTPUT', 'Regression report would overwrite a golden input')
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                              allow_nan=False) + '\n', encoding='utf-8')
        print(json.dumps({'all_pass': report['all_pass'], 'counts': report['counts'],
                          'report': str(Path(args.report).resolve()) if args.report else None},
                         ensure_ascii=False))
        return 0 if report['all_pass'] else 1
    except Exception as exc:
        print(json.dumps({'all_pass': False, 'error': str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
