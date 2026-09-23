"""Hash-bound visual replay of explicitly approved sheets, never engineering release."""
from __future__ import annotations
import copy
import hashlib
import json
import math
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pymupdf as fitz
import kangsheng as k

SCHEMA = 'kangsheng-approved-recipe-v1'
STATUS = 'APPROVED_VISUAL_REPLAY_VERIFIED'
ARTIFACTS = ('drawing.pdf', 'preview.png', 'replay-check.json')


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _read(path):
    def bad(value):
        raise ValueError('Non-finite JSON number: ' + value)
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(Path(path).read_text(), parse_constant=bad, object_pairs_hook=pairs)


def _write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def _engine():
    root = Path(__file__).resolve().parent
    return {'fitz_version': fitz.__version__, 'scripts': {
        name: k.digest(root / name) for name in ('kangsheng.py', 'frame.py', 'approved_recipe.py')}}


def _finite(value):
    if isinstance(value, float):
        _require(math.isfinite(value), 'NaN/Inf is not valid recipe data')
    elif isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)


def _point(value):
    _require(isinstance(value, list) and len(value) == 2 and all(
        isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in value), 'Invalid point')
    return fitz.Point(value)


def _rect(value):
    _require(isinstance(value, list) and len(value) == 4 and all(
        isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in value), 'Invalid rectangle')
    result = fitz.Rect(value)
    _require(result.is_valid and not result.is_empty and not result.is_infinite, 'Invalid rectangle bounds')
    return result


def _date(value):
    _require(isinstance(value, str), 'Only date metadata can be inserted as text')
    for pattern in ('%Y-%m-%d', '%Y.%m.%d', '%Y/%m/%d', '%d-%m-%Y', '%d.%m.%Y', '%d/%m/%Y'):
        try:
            if datetime.strptime(value, pattern).strftime(pattern) == value:
                return
        except ValueError:
            pass
    raise ValueError('Text operations accept dates only; retain technical values as source vectors')


def _validate(plan, base):
    _finite(plan)
    _require(plan.get('recipe_schema') == SCHEMA, 'Unknown approved recipe schema')
    cfg = copy.deepcopy(plan['configuration'])
    _require(cfg.get('color_profile') in k.COLOR_PROFILES, 'Explicit supported color profile required')
    _require(cfg.get('schema_version') in (1, 2), 'Unsupported configuration schema')
    source = cfg['source']
    source['path'] = str(k.resolve(base, source['path']))
    _require(source.get('rotation') in (0, 90, 180, 270) and source.get('page') == 1
             and source.get('expected_pages') == 1, 'Explicit single-page source and rotation required')
    _require(isinstance(source.get('sha256'), str) and len(source['sha256']) == 64, 'Source SHA256 required')
    assets = cfg['assets']
    for name in ('background', 'brand_strip', 'font'):
        _require(name in assets, 'Missing asset: ' + name)
    for name, value in assets.items():
        _require(isinstance(value, str), 'Asset must be a file path: ' + name)
        if value:
            assets[name] = str(k.resolve(base, value))
    _require(not cfg['fields'].get('tolerances'), 'Tolerance values must remain source vectors')
    groups = cfg['groups']
    _require(groups and len({g['id'] for g in groups}) == len(groups), 'Missing or duplicate content groups')
    for group in groups:
        _require(group['kind'] in k.KINDS and group['clips'], 'Invalid content group')
        _point(group['dst'])
        scale = group['scale']
        _require(isinstance(scale, (int, float)) and not isinstance(scale, bool) and 0 < scale <= 4, 'Invalid scale')
        if group['kind'] in ('view', 'pcb'):
            _require(scale == 1, 'Dimensional view/PCB scale must remain 1.0')
        for box in group['clips']:
            _rect(box)
    operations = plan['operations']
    _require(isinstance(operations, list), 'Operations must be an ordered list')
    for op in operations:
        kind = op['op']
        allowed = {'source': {'op', 'source_box', 'target_box'}, 'rect': {'op', 'box', 'width'},
                   'line': {'op', 'points', 'width'}, 'text': {'op', 'point', 'value', 'fontsize', 'metadata_field'}}
        _require(kind in allowed and not (set(op) - allowed[kind]), 'Unknown operation or operation property')
        if kind == 'source':
            _rect(op['source_box']); _rect(op['target_box'])
        elif kind == 'rect':
            _rect(op['box'])
        elif kind == 'line':
            _require(len(op['points']) == 2, 'Line must have exactly two points')
            _require(_point(op['points'][0]) != _point(op['points'][1]), 'Empty line')
        else:
            _point(op['point']); _date(op['value'])
            _require(op.get('metadata_field', 'date') == 'date', 'Only date text metadata is supported')
        if kind in ('rect', 'line', 'text'):
            size = op['fontsize' if kind == 'text' else 'width']
            _require(isinstance(size, (int, float)) and not isinstance(size, bool) and 0 < size <= 72, 'Invalid width/font size')
    return cfg, copy.deepcopy(operations)


def _bindings(cfg, source, baseline):
    _require(source.is_file() and k.digest(source) == cfg['source']['sha256'], 'Source missing or changed; inspect again')
    assets = {name: {'path': path, 'sha256': k.digest(path)} for name, path in cfg['assets'].items() if path}
    return {'source_sha256': k.digest(source), 'baseline': {'path': str(baseline), 'sha256': k.digest(baseline)},
            'assets': assets, 'engine': _engine()}


def _visual(path, preview=None):
    with fitz.open(path) as doc:
        _require(len(doc) == 1, 'Visual replay requires exactly one PDF page')
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(4, 4), colorspace=fitz.csRGB, alpha=False)
        spec = {'scale': 4, 'width': pix.width, 'height': pix.height, 'channels': pix.n, 'page_rect': list(doc[0].rect)}
        sha = hashlib.sha256(_canonical(spec) + b'\0' + pix.samples).hexdigest()
        if preview:
            pix.save(preview)
        return dict(spec, sha256=sha), np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)


def _preview_matches(path, visual):
    try:
        pix = fitz.Pixmap(str(path))
        spec = {key: value for key, value in visual.items() if key != 'sha256'}
        return ((pix.width, pix.height, pix.n) == (visual['width'], visual['height'], 3)
                and pix.colorspace.n == 3 and not pix.alpha
                and hashlib.sha256(_canonical(spec) + b'\0' + pix.samples).hexdigest() == visual['sha256'])
    except Exception:
        return False


def _protect(paths, protected):
    paths = [Path(p) for p in paths]
    for path in paths:
        _require(not path.is_symlink() and not (path.is_file() and path.stat().st_nlink > 1),
                 'Artifact must not be a symbolic or hard link: ' + str(path))
    resolved = [p.resolve() for p in paths]
    _require(len(set(resolved)) == len(resolved), 'Artifact paths overlap')
    for path in resolved:
        for item in protected:
            item = Path(item).resolve()
            _require(path != item and not (path.exists() and item.exists() and path.samefile(item)),
                     'Output would overwrite a protected input: ' + str(item))


def _compose(cfg, operations, source, directory):
    _, colored, _, _ = k.cached_source(cfg, source, directory / '.cache')
    ps = list(k.placements(cfg))
    with fitz.open(colored) as original:
        pagebox = fitz.Rect(0, 0, *k.PAGE)
        for item in ps + [op for op in operations if op['op'] == 'source']:
            _require(original[0].rect.contains(_rect(item['source_box'])) and pagebox.contains(_rect(item['target_box'])),
                     'Source or target crop lies outside its page')
        doc, page = k.compose_document(cfg, colored, cfg['assets'], ps)
        with doc:
            for op in operations:
                if op['op'] == 'source':
                    page.show_pdf_page(_rect(op['target_box']), original, 0, clip=_rect(op['source_box']))
                elif op['op'] == 'rect':
                    _require(pagebox.contains(_rect(op['box'])), 'Rectangle outside page')
                    page.draw_rect(_rect(op['box']), color=k.BLUE, width=op['width'])
                elif op['op'] == 'line':
                    _require(all(pagebox.contains(_point(p)) for p in op['points']), 'Line outside page')
                    page.draw_line(*op['points'], color=k.BLUE, width=op['width'])
                else:
                    _require(pagebox.contains(_point(op['point'])), 'Text point outside page')
                    page.insert_text(op['point'], op['value'], fontsize=op['fontsize'], color=k.BLUE)
            doc.save(directory / 'drawing.pdf', garbage=4, deflate=True)


def _inputs(cfg, recipe_path, source, baseline):
    return [recipe_path, source, cfg['source']['path'], baseline, *filter(None, cfg['assets'].values()),
            *(Path(__file__).parent / name for name in _engine()['scripts'])]


def freeze(args):
    plan_path = Path(args.plan).resolve(); target = Path(args.output).resolve()
    baseline = Path(args.baseline).resolve(); note = args.approval_note.strip()
    _require(note, 'Explicit user visual approval note required')
    cfg, operations = _validate(_read(plan_path), plan_path.parent)
    source = Path(cfg['source']['path'])
    _protect([target], _inputs(cfg, plan_path, source, baseline))
    _require(not target.exists(), 'Frozen recipe already exists; use a new filename')
    binding = _bindings(cfg, source, baseline)
    target.parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=target.stem + '-freeze-', dir=target.parent))
    report = {'status': 'FREEZE_FAILED', 'engineering_release': False, 'baseline': str(baseline), 'diagnostics': str(directory)}
    try:
        expected, baseline_pixels = _visual(baseline, directory / 'baseline-preview.png')
        _compose(cfg, operations, source, directory)
        actual, pixels = _visual(directory / 'drawing.pdf', directory / 'preview.png')
        count = int(np.any(pixels != baseline_pixels, axis=2).sum()) if pixels.shape == baseline_pixels.shape else None
        report.update(expected_visual=expected, actual_visual=actual, different_pixels=count)
        _require(expected == actual and count == 0, 'Approved baseline differs from composed page; inspect freeze diagnostics: ' + str(directory))
        _require(_bindings(cfg, source, baseline) == binding, 'Input/engine changed during freeze')
        recipe = {'recipe_schema': SCHEMA, 'configuration': cfg, 'operations': operations, 'approval_note': note,
                  'engineering_release': False, 'bindings': binding, 'baseline_visual': expected}
        recipe['configuration_operations_sha256'] = _hash({'configuration': cfg, 'operations': operations})
        recipe['recipe_sha256'] = _hash(recipe)
        report.update(status=STATUS, source_sha256=binding['source_sha256'], recipe_sha256=recipe['recipe_sha256'],
                      output_sha256=k.digest(directory / 'drawing.pdf'), visual_sha256=actual['sha256'])
        # Exclusive creation protects an existing archive even if a concurrent writer arrives.
        with target.open('x') as stream:
            stream.write(json.dumps(recipe, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    except Exception as exc:
        report.update(status='FREEZE_FAILED', error=str(exc)); _write(directory / 'replay-check.json', report)
        raise
    _write(directory / 'replay-check.json', report)
    result = {'recipe': str(target), **report}; print(json.dumps(result, ensure_ascii=False)); return result


def _replay(args):
    recipe_path = Path(args.recipe).resolve(); recipe = _read(recipe_path)
    _require(recipe.get('engineering_release') is False, 'Recipe must be visual-only, not an engineering release')
    _require(recipe.get('recipe_sha256') == _hash({key: value for key, value in recipe.items() if key != 'recipe_sha256'}), 'Frozen recipe changed')
    cfg, operations = _validate(recipe, recipe_path.parent)
    _require(recipe['configuration_operations_sha256'] == _hash({'configuration': cfg, 'operations': operations}), 'Configuration or operations changed')
    source = Path(getattr(args, '_source', None) or cfg['source']['path']).resolve()
    baseline = Path(recipe['bindings']['baseline']['path']).resolve()
    binding = _bindings(cfg, source, baseline)
    _require(binding == recipe['bindings'], 'Source, baseline, font, asset, or engine changed; inspect and freeze again')
    expected, _ = _visual(baseline)
    _require(expected == recipe['baseline_visual'], 'Baseline rendering changed')
    out = Path(args.output).resolve()
    _protect([out / name for name in ARTIFACTS], _inputs(cfg, recipe_path, source, baseline))
    _require(not (out / 'release.json').exists(), 'Visual replay must not share an engineering release directory')
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / 'replay-check.json'
    owner = _read(report_path) if report_path.exists() else None
    if any((out / name).exists() for name in ARTIFACTS):
        _require(owner and owner.get('recipe_sha256') == recipe['recipe_sha256'], 'Output directory belongs to another recipe or is unmanaged')
    if owner and owner.get('status') == STATUS and owner.get('engineering_release') is False and owner.get('source_sha256') == binding['source_sha256'] and owner.get('recipe_file_sha256') == k.digest(recipe_path):
        pdf, png = out / 'drawing.pdf', out / 'preview.png'
        if pdf.is_file() and png.is_file() and k.digest(pdf) == owner.get('output_sha256') and k.digest(png) == owner.get('preview_sha256'):
            visual, _ = _visual(pdf)
            if visual == expected and owner.get('visual_sha256') == expected['sha256'] and _preview_matches(png, expected):
                _require(_bindings(cfg, source, baseline) == binding and k.digest(recipe_path) == owner['recipe_file_sha256']
                         and k.digest(pdf) == owner['output_sha256'] and k.digest(png) == owner['preview_sha256'],
                         'Input, recipe, or output changed while checking cached replay')
                return dict(owner, reused=True, output=str(out))
    report = {'status': 'REPLAY_FAILED', 'engineering_release': False, 'source_sha256': binding['source_sha256'],
              'recipe_sha256': recipe['recipe_sha256'], 'recipe_file_sha256': k.digest(recipe_path), 'output': str(out)}
    try:
        with tempfile.TemporaryDirectory(prefix='.replay-', dir=out) as temp:
            stage = Path(temp); _compose(cfg, operations, source, stage)
            actual, _ = _visual(stage / 'drawing.pdf', stage / 'preview.png')
            report.update(actual_visual=actual, expected_visual=expected)
            _require(actual == expected, 'Replayed page differs from frozen baseline')
            _require(_bindings(cfg, source, baseline) == binding and k.digest(recipe_path) == report['recipe_file_sha256'], 'Input/recipe/engine changed during replay')
            report.update(status=STATUS, output_sha256=k.digest(stage / 'drawing.pdf'),
                          preview_sha256=k.digest(stage / 'preview.png'), visual_sha256=actual['sha256'], different_pixels=0, reused=False)
            for name in ARTIFACTS[:2]:
                (stage / name).replace(out / name)
    except Exception as exc:
        report.update(status='REPLAY_FAILED', error=str(exc)); _write(report_path, report)
        raise
    _write(report_path, report)
    return report


def replay(args):
    result = _replay(args); print(json.dumps(result, ensure_ascii=False)); return result


def replay_batch(args):
    registry_path = Path(args.registry).resolve(); registry = _read(registry_path)
    _require(registry.get('schema_version') == 1, 'Unknown registry schema')
    entries = registry['recipes']; lookup = {}
    for entry in entries:
        sha = entry['source_sha256']; _require(sha not in lookup, 'Duplicate source SHA in registry')
        lookup[sha] = k.resolve(registry_path.parent, entry['recipe_path'])
    root = Path(args.output_root).resolve(); results = []; seen = set()
    for value in args.sources:
        source = Path(value).resolve()
        item = {'source': str(source), 'engineering_release': False}
        try:
            sha = k.digest(source); item['source_sha256'] = sha
            if sha not in lookup:
                item['status'] = 'skip_needs_inspection'
            elif sha in seen:
                item['status'] = 'skip_duplicate_source'
            else:
                seen.add(sha); recipe = _read(lookup[sha])
                _require(recipe['bindings']['source_sha256'] == sha, 'Registry source SHA does not match recipe')
                output = root / sha
                _protect([output / name for name in ARTIFACTS], [registry_path, *args.sources, *lookup.values()])
                item.update(_replay(SimpleNamespace(recipe=str(lookup[sha]), output=str(output), _source=str(source))))
        except Exception as exc:
            item.update(status='REPLAY_FAILED', error=str(exc))
        results.append(item)
    report = {'schema_version': 1, 'engineering_release': False, 'results': results}
    print(json.dumps(report, ensure_ascii=False)); return report


def register_cli(subs):
    p = subs.add_parser('freeze-approved', help='Freeze an exactly reproduced user-approved visual, not engineering release')
    p.add_argument('plan'); p.add_argument('--baseline', required=True); p.add_argument('--output', required=True)
    p.add_argument('--approval-note', required=True); p.set_defaults(func=freeze)
    p = subs.add_parser('replay', help='Strictly replay one frozen visual recipe')
    p.add_argument('recipe'); p.add_argument('--output', required=True); p.set_defaults(func=replay)
    p = subs.add_parser('replay-batch', help='Replay SHA-matched sources; leave unknown inputs unchanged')
    p.add_argument('--registry', required=True); p.add_argument('--sources', nargs='+', required=True)
    p.add_argument('--output-root', required=True); p.set_defaults(func=replay_batch)
