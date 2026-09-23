#!/usr/bin/env python3
"""Measure the real Kangsheng CLI stages without changing its production code.

Examples (write all reports/artifacts outside the repository)::

    python scripts/perf_profile.py profile draft MANIFEST.json --output RUN \
        --cache PRIVATE_CACHE --report profile.json
    python scripts/perf_profile.py profile replay RECIPE.json --output RUN \
        --report profile.json
    python scripts/perf_profile.py benchmark RECIPE_A.json RECIPE_B.json \
        --output-root PRIVATE_BENCH --workers 1 2 4 --copies 2

The profiler wraps functions in memory only. Its inclusive stage times overlap;
exclusive times subtract instrumented child calls. These measurements are
diagnostic, not a replacement for the unchanged automatic QA/review gates.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager, redirect_stdout
from functools import wraps
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace


def sha256(path):
    """Stream a file rather than adding a second full-file allocation."""
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def visual_sha256(path):
    """Hash exact 4x RGB page pixels plus dimensions, like frozen recipes."""
    import pymupdf as fitz
    with fitz.open(path) as doc:
        if len(doc) != 1:
            raise ValueError('Expected a single-page PDF')
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(4, 4), colorspace=fitz.csRGB, alpha=False)
        spec = {'scale': 4, 'width': pix.width, 'height': pix.height,
                'channels': pix.n, 'page_rect': list(doc[0].rect)}
        canonical = json.dumps(spec, sort_keys=True, ensure_ascii=False,
                               separators=(',', ':')).encode()
        return hashlib.sha256(canonical + b'\0' + pix.samples).hexdigest()


class StageClock:
    """Nested wall-clock spans with correctly exclusive parent accounting."""

    def __init__(self, timer=time.perf_counter):
        self.timer = timer
        self.stack = []
        self.samples = defaultdict(list)

    @contextmanager
    def span(self, name):
        frame = [name, self.timer(), 0.0]
        self.stack.append(frame)
        try:
            yield
        finally:
            elapsed = self.timer() - frame[1]
            self.stack.pop()
            self.samples[name].append((elapsed, max(0.0, elapsed - frame[2])))
            if self.stack:
                self.stack[-1][2] += elapsed

    def wrap(self, function, name):
        @wraps(function)
        def measured(*args, **kwargs):
            with self.span(name):
                return function(*args, **kwargs)
        return measured

    def summary(self):
        return {name: {'calls': len(values),
                       'inclusive_seconds': round(sum(x[0] for x in values), 6),
                       'exclusive_seconds': round(sum(x[1] for x in values), 6)}
                for name, values in sorted(self.samples.items())}


@contextmanager
def instrument(clock, k, approved):
    """Temporarily instrument IO, PDF primitives, and public workflow stages."""
    import pymupdf as fitz
    changes = []

    def patch(owner, attribute, label):
        original = getattr(owner, attribute)
        setattr(owner, attribute, clock.wrap(original, label))
        changes.append((owner, attribute, original))

    # Split reading from hashing: digest() includes read_bytes(), so its
    # exclusive time is the hash computation and its inclusive time is both.
    patch(Path, 'read_text', 'file_read_text')
    patch(Path, 'read_bytes', 'file_read_bytes')
    patch(k, 'digest', 'sha256_file_total')
    patch(fitz, 'open', 'pdf_open_parse')
    patch(fitz.Page, 'get_pixmap', 'pdf_render_pixmap')
    patch(fitz.Pixmap, 'save', 'png_encode_save')
    patch(fitz.Document, 'save', 'pdf_encode_save')

    for attribute, label in (
        ('read_manifest', 'manifest_validate'),
        ('cached_source', 'source_normalize_recolor_cache'),
        ('native_recolor', 'source_native_recolor'),
        ('cached_render', 'source_raster_cache'),
        ('render_array', 'qa_render_array'),
        ('check_geometry', 'qa_geometry'),
        ('compose_document', 'pdf_compose'),
        ('make_audit', 'qa_pixel_audit'),
        ('make_review_pack', 'qa_review_pack'),
        ('draft', 'workflow_draft'),
        ('build', 'workflow_build'),
        ('verify', 'workflow_verify'),
    ):
        patch(k, attribute, label)
    for attribute, label in (
        ('_draft_generate', 'workflow_draft_core'),
        ('_build_generate', 'workflow_build_core'),
    ):
        if hasattr(k, attribute):
            patch(k, attribute, label)
    for attribute, label in (
        ('_read', 'recipe_json_read'),
        ('_bindings', 'recipe_binding_validate'),
        ('_visual', 'recipe_fullpage_visual'),
        ('_compose', 'recipe_pdf_compose'),
        ('_preview_matches', 'recipe_preview_validate'),
        ('_replay', 'workflow_replay'),
    ):
        patch(approved, attribute, label)
    try:
        yield
    finally:
        for owner, attribute, original in reversed(changes):
            setattr(owner, attribute, original)


def profile(args):
    import kangsheng as k
    import approved_recipe as approved

    input_path = Path(args.input).resolve()
    output = Path(args.output).resolve()
    report = Path(args.report).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    clock = StageClock()
    engine = {name: sha256(Path(__file__).parent / name)
              for name in ('kangsheng.py', 'approved_recipe.py', 'frame.py')}
    captured = io.StringIO()
    failure = None
    result = None
    start = time.perf_counter()
    try:
        with instrument(clock, k, approved), redirect_stdout(captured):
            if args.mode == 'replay':
                result = approved._replay(SimpleNamespace(recipe=str(input_path), output=str(output)))
            elif args.mode in ('draft', 'draft-core'):
                fn = getattr(k, '_draft_generate', k.draft) if args.mode == 'draft-core' else k.draft
                result = fn(SimpleNamespace(manifest=str(input_path), output=str(output),
                                            cache=args.cache, control_root=args.control_root,
                                            allow_retry=args.allow_retry))
            elif args.mode in ('build', 'build-core'):
                fn = getattr(k, '_build_generate', k.build) if args.mode == 'build-core' else k.build
                result = fn(SimpleNamespace(manifest=str(input_path), output=str(output),
                                            cache=args.cache, control_root=args.control_root,
                                            allow_retry=args.allow_retry))
            else:
                if not args.pdf:
                    raise ValueError('verify requires --pdf')
                k.verify(SimpleNamespace(manifest=str(input_path), pdf=args.pdf,
                                         cache=args.cache, review=args.review,
                                         report=str(output / 'verify.json')))
    except Exception as exc:
        failure = f'{type(exc).__name__}: {exc}'
    elapsed = time.perf_counter() - start
    artifacts = {}
    artifact_visual = {}
    for name in ('drawing.pdf', 'draft.pdf', 'candidate.pdf', 'audit.json',
                 'draft-audit.json', 'verify.json', 'replay-check.json'):
        path = output / name
        if path.is_file():
            artifacts[name] = sha256(path)
            if path.suffix == '.pdf':
                artifact_visual[name] = visual_sha256(path)
    audit_path = next((output / name for name in ('audit.json', 'draft-audit.json', 'verify.json')
                       if (output / name).is_file()), None)
    audit = json.loads(audit_path.read_text()) if audit_path else {}
    data = {'mode': args.mode, 'input': str(input_path), 'output': str(output),
            'runtime': {'python': sys.version.split()[0], 'pymupdf': k.fitz.__version__},
            'engine_sha256': engine, 'input_sha256': sha256(input_path),
            'wall_seconds': round(elapsed, 6), 'stages': clock.summary(),
            'artifacts_sha256': artifacts, 'artifact_visual_sha256': artifact_visual,
            'automatic_qa_pass': audit.get('pass'),
            'replay_status': result.get('status') if result else None,
            'reused': result.get('reused') if result else None,
            'error': failure, 'engine_changed_during_run':
            engine != {name: sha256(Path(__file__).parent / name) for name in engine},
            'stdout': captured.getvalue().strip()}
    report.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'report': str(report), 'wall_seconds': data['wall_seconds'],
                      'automatic_qa_pass': data['automatic_qa_pass'],
                      'reused': data['reused'], 'error': failure}, ensure_ascii=False))
    return 0 if failure is None else 2


def _benchmark_job(job):
    python, cli, recipe, output = job
    start = time.perf_counter()
    command = [python, cli, 'replay', recipe, '--output', output]
    process = subprocess.run(command, capture_output=True, text=True)
    elapsed = time.perf_counter() - start
    try:
        result = json.loads(process.stdout)
    except json.JSONDecodeError:
        result = {}
    pdf = Path(output) / 'drawing.pdf'
    return {'recipe': recipe, 'output': output, 'returncode': process.returncode,
            'wall_seconds': round(elapsed, 6), 'status': result.get('status'),
            'reused': result.get('reused'), 'pdf_sha256': sha256(pdf) if pdf.is_file() else None,
            'visual_sha256': visual_sha256(pdf) if pdf.is_file() else None,
            'release_json_created': (Path(output) / 'release.json').exists(),
            'stderr': process.stderr[-1000:]}


def benchmark(args):
    root = Path(args.output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    recipes = [str(Path(value).resolve()) for value in args.recipes]
    cli = str((Path(__file__).parent / 'kangsheng.py').resolve())
    before = {name: sha256(Path(__file__).parent / name)
              for name in ('kangsheng.py', 'approved_recipe.py', 'frame.py')}
    expected_visual = {}
    expected_pdf = {}
    groups = []
    for workers in args.workers:
        jobs = []
        for copy in range(args.copies):
            for index, recipe in enumerate(recipes):
                output = root / f'w{workers}' / f'copy{copy}-recipe{index}'
                if output.exists():
                    raise ValueError(f'Fresh benchmark output required: {output}')
                jobs.append((sys.executable, cli, recipe, str(output)))
        start = time.perf_counter()
        with ProcessPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(_benchmark_job, jobs))
        elapsed = time.perf_counter() - start
        for row in results:
            if row['visual_sha256']:
                expected_visual.setdefault(row['recipe'], row['visual_sha256'])
                expected_pdf.setdefault(row['recipe'], row['pdf_sha256'])
        visual_consistent = all(row['returncode'] == 0 and
                         row['status'] == 'APPROVED_VISUAL_REPLAY_VERIFIED' and
                         row['visual_sha256'] == expected_visual.get(row['recipe'])
                         and not row['release_json_created'] for row in results)
        byte_consistent = all(row['pdf_sha256'] == expected_pdf.get(row['recipe'])
                              for row in results)
        groups.append({'workers': workers, 'jobs': len(jobs), 'wall_seconds': round(elapsed, 6),
                       'visual_hash_consistent': visual_consistent,
                       'pdf_byte_hash_consistent': byte_consistent, 'results': results})
    after = {name: sha256(Path(__file__).parent / name) for name in before}
    report = {'workflow': 'approved_visual_replay_only', 'distinct_recipes': len(recipes),
              'workers': groups, 'engine_sha256_before': before,
              'engine_changed_during_benchmark': before != after,
              'all_visual_hash_consistent': all(x['visual_hash_consistent'] for x in groups)
              and all(row['visual_sha256'] == expected_visual.get(row['recipe'])
                      for group in groups for row in group['results']) and before == after,
              'all_pdf_byte_hash_consistent': all(x['pdf_byte_hash_consistent'] for x in groups)
              and all(row['pdf_sha256'] == expected_pdf.get(row['recipe'])
                      for group in groups for row in group['results']),
              'scope_note': 'Only supplied frozen recipes are tested; duplicates stress concurrency but do not add golden layouts.'}
    report_path = root / 'benchmark.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'report': str(report_path),
                      'all_visual_hash_consistent': report['all_visual_hash_consistent'],
                      'all_pdf_byte_hash_consistent': report['all_pdf_byte_hash_consistent'],
                      'distinct_recipes': len(recipes),
                      'seconds_by_workers': {row['workers']: row['wall_seconds'] for row in groups}},
                     ensure_ascii=False))
    return 0 if report['all_visual_hash_consistent'] else 2


def _rss_bytes(pids):
    """Sample current RSS for direct child PIDs; None if `ps` is unavailable."""
    if not pids:
        return {}
    try:
        result = subprocess.run(['ps', '-o', 'pid=,rss=', '-p', ','.join(map(str, pids))],
                                capture_output=True, text=True, check=False)
        return {int(parts[0]): int(parts[1]) * 1024
                for line in result.stdout.splitlines() if len(parts := line.split()) == 2}
    except (OSError, ValueError):
        return {}


def benchmark_draft(args):
    """Run real five-source draft candidates against a shared ledger per worker tier.

    The output is QA evidence only. A PASS does not grant engineering release or
    prove that the candidate is pixel-identical to any user-approved golden PDF.
    """
    root = Path(args.output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifests = [Path(value).resolve() for value in args.manifests]
    if len(set(manifests)) != len(manifests):
        raise ValueError('Benchmark manifests must be distinct')
    for path in manifests:
        if not path.is_file():
            raise FileNotFoundError(path)
    cli = str((Path(__file__).parent / 'kangsheng.py').resolve())
    engine_before = sha256(cli)
    scenarios = []
    visual_reference = {}
    for workers in args.workers:
        tier = root / f'w{workers}'
        if tier.exists():
            raise ValueError(f'Fresh tier directory required: {tier}')
        tier.mkdir()
        shared_control = tier / 'control'
        cache = tier / 'cache'
        pending = list(enumerate(manifests))
        running = {}
        results = []
        peak_aggregate_rss = 0
        start = time.perf_counter()
        while pending or running:
            while pending and len(running) < workers:
                index, manifest = pending.pop(0)
                output = tier / f'row{index + 1}'
                output.mkdir()
                control = shared_control if args.ledger_mode == 'shared' else tier / 'controls' / f'row{index + 1}'
                stdout_path = output / 'cli.stdout'
                stderr_path = output / 'cli.stderr'
                stdout = stdout_path.open('w')
                stderr = stderr_path.open('w')
                command = [args.python, cli, 'draft', str(manifest), '--output', str(output),
                           '--cache', str(cache), '--control-root', str(control)]
                started = time.perf_counter()
                proc = subprocess.Popen(command, stdout=stdout, stderr=stderr)
                running[proc.pid] = {'proc': proc, 'index': index, 'manifest': manifest,
                                     'output': output, 'stdout': stdout, 'stderr': stderr,
                                     'started': started, 'peak_rss_bytes': 0}
            rss = _rss_bytes(running)
            peak_aggregate_rss = max(peak_aggregate_rss, sum(rss.values()))
            for pid, measured in rss.items():
                running[pid]['peak_rss_bytes'] = max(running[pid]['peak_rss_bytes'], measured)
            for pid, job in list(running.items()):
                code = job['proc'].poll()
                if code is None:
                    continue
                job['stdout'].close()
                job['stderr'].close()
                output = job['output']
                try:
                    stdout_data = json.loads((output / 'cli.stdout').read_text().strip().splitlines()[-1])
                except (ValueError, IndexError):
                    stdout_data = {}
                audit_path = output / 'draft-audit.json'
                audit = json.loads(audit_path.read_text()) if audit_path.is_file() else {}
                pdf = output / 'draft.pdf'
                item = {'index': job['index'], 'manifest': str(job['manifest']),
                        'record_id': json.loads(job['manifest'].read_text())['identity'].get('record_id'),
                        'returncode': code, 'status': stdout_data.get('status'),
                        'seconds': round(time.perf_counter() - job['started'], 6),
                        'peak_rss_bytes_sampled': job['peak_rss_bytes'],
                        'automatic_qa_pass': audit.get('pass'),
                        'source_sha256': audit.get('source_sha256'),
                        'source_inventory_sha256': audit.get('source_inventory_sha256'),
                        'visual_sha256': visual_sha256(pdf) if pdf.is_file() else None,
                        'pdf_sha256': sha256(pdf) if pdf.is_file() else None,
                        'error': (output / 'cli.stderr').read_text()[-1000:]}
                if item['visual_sha256']:
                    visual_reference.setdefault(item['record_id'], item['visual_sha256'])
                results.append(item)
                del running[pid]
            if running:
                time.sleep(max(.01, args.poll_ms / 1000))
        scenarios.append({'workers': workers, 'wall_seconds': round(time.perf_counter() - start, 6),
                          'peak_aggregate_rss_bytes_sampled': peak_aggregate_rss,
                          'results': sorted(results, key=lambda item: item['index'])})
    report = {'scope': 'candidate-draft-qa-not-golden-exact-reproduction',
              'ledger_mode': args.ledger_mode,
              'engine_sha256_before': engine_before,
              'engine_changed_during_benchmark': sha256(cli) != engine_before,
              'distinct_manifests': len(manifests), 'scenarios': scenarios,
              'all_auto_qa_pass': all(row['returncode'] == 0 and row['status'] == 'DRAFT_QA_PASS'
                                      and row['automatic_qa_pass'] is True
                                      for tier in scenarios for row in tier['results']),
              'all_visual_hash_consistent': all(row['visual_sha256'] == visual_reference.get(row['record_id'])
                                                 and row['visual_sha256'] is not None
                                                 for tier in scenarios for row in tier['results']),
              'memory_note': 'RSS is sampled with ps at the configured interval; peaks may be understated.'}
    target = root / 'benchmark-draft.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'report': str(target), 'all_auto_qa_pass': report['all_auto_qa_pass'],
                      'all_visual_hash_consistent': report['all_visual_hash_consistent'],
                      'seconds_by_workers': {tier['workers']: tier['wall_seconds'] for tier in scenarios}},
                     ensure_ascii=False))
    return 0 if report['all_auto_qa_pass'] and report['all_visual_hash_consistent'] else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    item = subs.add_parser('profile')
    item.add_argument('mode', choices=('draft', 'draft-core', 'build', 'build-core', 'verify', 'replay'))
    item.add_argument('input')
    item.add_argument('--output', required=True)
    item.add_argument('--report', required=True)
    item.add_argument('--cache')
    item.add_argument('--control-root', help='Required by the candidate control-ledger workflow')
    item.add_argument('--allow-retry', action='store_true')
    item.add_argument('--pdf')
    item.add_argument('--review')
    item.set_defaults(func=profile)
    item = subs.add_parser('benchmark')
    item.add_argument('recipes', nargs='+')
    item.add_argument('--output-root', required=True)
    item.add_argument('--workers', nargs='+', type=int, default=[1, 2, 4])
    item.add_argument('--copies', type=int, default=1)
    item.set_defaults(func=benchmark)
    item = subs.add_parser('benchmark-draft')
    item.add_argument('manifests', nargs='+')
    item.add_argument('--output-root', required=True)
    item.add_argument('--workers', nargs='+', type=int, default=[1, 2, 4])
    item.add_argument('--poll-ms', type=int, default=50)
    item.add_argument('--ledger-mode', choices=('shared', 'per-job'), default='shared')
    item.add_argument('--python', default=sys.executable)
    item.set_defaults(func=benchmark_draft)
    args = parser.parse_args(argv)
    if args.command in ('benchmark', 'benchmark-draft') and min(args.workers) < 1:
        parser.error('workers must be positive')
    if args.command == 'benchmark' and args.copies < 1:
        parser.error('workers and copies must be positive')
    if args.command == 'benchmark-draft' and args.poll_ms < 1:
        parser.error('poll-ms must be positive')
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
