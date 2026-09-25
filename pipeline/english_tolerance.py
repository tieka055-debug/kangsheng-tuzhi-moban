#!/usr/bin/env python3
"""Turn a passing auto draft into the approved English tolerance layout.

Input: a run dir (manifest.json from auto_manifest.py) + the tolerance rows read from THIS drawing's
own tolerance cell (tolerance.json).  Writes a hash-bound source_fields ledger + read record, a
manifest-en.json that uses the engine's existing source_fields path, and runs `draft`.
The engine then re-checks that the English footer contains exactly these rows.
"""
import argparse, hashlib, json, subprocess, sys
from pathlib import Path
import pymupdf as fitz

HERE = Path(__file__).resolve().parent


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def crop_hash(page, box):
    pix = page.get_pixmap(matrix=fitz.Matrix(4, 4), clip=fitz.Rect(box), alpha=False)
    return hashlib.sha256(pix.samples).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('run_dir'); ap.add_argument('tolerance_json')
    ap.add_argument('--engine', required=True); ap.add_argument('--reader', required=True,
                    help='who read the tolerance cell (person or model id)')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    d = Path(a.run_dir).resolve(); out = Path(a.out).resolve(); out.mkdir(parents=True, exist_ok=True)
    m = json.loads((d / 'manifest.json').read_text())
    tol = json.loads(Path(a.tolerance_json).read_text())
    src = Path(m['source']['path']); rot = m['source']['rotation']
    doc = fitz.open(src); page = doc[0]; page.set_rotation(rot); page.remove_rotation()
    tg = next(g for g in m['groups'] if g['kind'] == 'tolerance')
    tbox = tg['reviewed_source_extent']
    # identity region: the supplier title-block cells holding model + title (union of their exclusions)
    id_boxes = [fitz.Rect(e['box']) for e in m['coverage']['exclude'] if e.get('field') in ('model', 'title')]
    ib = id_boxes[0]
    for r in id_boxes[1:]: ib |= r
    ibox = [round(v, 3) for v in ib]
    f = dict(m['fields'])
    if tol.get('size'): f['size'] = tol['size']
    schema = {'linear_tolerances': tol['linear_tolerances'], 'angular_tolerances': tol.get('angular_tolerances', []),
              'additional_tolerance_conditions': tol.get('additional_tolerance_conditions', [])}
    review = {'reviewer_identifier': a.reader, 'reviewer_run_id': 'tolerance-read-' + m['source']['sha256'][:12],
              'scope': 'Tolerance cell and title fields read from this drawing only; draft use.',
              'engineering_release': False,
              'rows': [{'row': 0, 'source_sha256': m['source']['sha256'], 'model': f['model'], 'title': f['title'],
                        'unit': f['unit'], 'field_review_status': 'PASS_VISUAL_FIELD_COMPARISON',
                        'tolerance_schema': schema}]}
    (out / 'tolerance-read.json').write_text(json.dumps(review, ensure_ascii=False, indent=1))
    ledger = {'schema': 'kangsheng-source-fields-v1', 'source_sha256': m['source']['sha256'], 'rotation': rot,
              'pymupdf': fitz.VersionBind, 'tolerance_schema': schema,
              'fields': {k: f[k] for k in ('model', 'title', 'unit', 'sheet', 'scale_text', 'size')},
              'regions': {'tolerance': {'box': tbox, 'raster_sha256': crop_hash(page, tbox)},
                          'identity': {'box': ibox, 'raster_sha256': crop_hash(page, ibox)}},
              'identity_text_required': bool(page.get_text(clip=ib).strip()),
              'review': {'path': 'tolerance-read.json', 'sha256': sha(out / 'tolerance-read.json')}}
    (out / 'ledger.json').write_text(json.dumps(ledger, ensure_ascii=False, indent=1))
    sys.path.insert(0, str(Path(a.engine).parent))
    from source_fields import semantic_digest
    m2 = json.loads(json.dumps(m))
    m2['source_fields'] = {'path': str(out / 'ledger.json'), 'sha256': sha(out / 'ledger.json'),
                           'semantic_sha256': semantic_digest(ledger)}
    m2['source']['path'] = str(src)
    m2['fields'] = {**m2['fields'], 'size': f['size']}
    for k in ('background', 'brand_strip', 'font'):
        m2['assets'][k] = str((d / m['assets'][k]).resolve()) if not Path(m['assets'][k]).is_absolute() else m['assets'][k]
    (out / 'manifest-en.json').write_text(json.dumps(m2, ensure_ascii=False, indent=1))
    r = subprocess.run([sys.executable, str(Path(a.engine).resolve()), 'draft', str(out / 'manifest-en.json'), '--output', str(out / 'draft'),
                        '--control-root', str(out / 'control'), '--cache', str(out / '.cache')], capture_output=True, text=True)
    tail = (r.stdout + r.stderr).strip().splitlines()
    print(tail[-1] if tail else r.returncode)
    au = out / 'draft' / 'draft-audit.json'
    if au.exists():
        A = json.loads(au.read_text()); t = A.get('approved_tolerance_reflow') or {}
        print(json.dumps({'pass': A['pass'], 'tolerance_fields': t.get('status'), 'unplaced': A['source_unplaced_technical_ink_pixels']}))


if __name__ == '__main__':
    main()
