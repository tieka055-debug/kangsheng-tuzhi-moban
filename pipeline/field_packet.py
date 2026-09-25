#!/usr/bin/env python3
"""Build the small 'field packet' a cheap model or a person fills in per drawing.

Everything readable from the PDF text layer is pre-filled.  Only values drawn as outlined
curves (typically the tolerance rows and the sheet size 'A4') are left as READ_ME with a
high-resolution crop, so the reader looks at 2 small images instead of the whole sheet.
The packet never marks anything as reviewed; a filled packet still needs its own review.
"""
import argparse, json, sys
from pathlib import Path
import pymupdf as fitz

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auto_manifest as am


def crop(page, box, out, zoom=8):
    r = fitz.Rect(box)
    page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=r, alpha=False).save(out)


def rows_from_words(words):
    """Group text-layer words of the tolerance cell into visual rows (left->right)."""
    rows = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        cy = (w[1] + w[3]) / 2
        if rows and abs(rows[-1][0] - cy) < 2.0: rows[-1][1].append(w)
        else: rows.append([cy, [w]])
    out = []
    for cy, ws in rows:
        t = '  '.join(x[4] for x in sorted(ws, key=lambda x: x[0]))
        if '公差' in t or 'TOLERANCE' in t.upper(): continue
        out.append(t)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('run_dir', help='directory holding manifest.json + auto-report.json')
    a = ap.parse_args()
    d = Path(a.run_dir)
    m = json.loads((d / 'manifest.json').read_text())
    rep = json.loads((d / 'auto-report.json').read_text())
    doc, page, rot = am.normalized(m['source']['path'])
    out = d / 'field-packet'; out.mkdir(exist_ok=True)
    tol = next(g for g in m['groups'] if g['kind'] == 'tolerance')
    crop(page, tol['clips'][0], out / 'tolerance-cell.png')
    tol_text = page.get_text('words', clip=fitz.Rect(tol['clips'][0]))
    size_box = next((e['box'] for e in m['coverage']['exclude'] if e.get('field') == 'size'), None)
    if size_box: crop(page, size_box, out / 'size-cell.png', zoom=10)
    f = rep.get('fields', {})
    packet = {
        'schema': 'kangsheng-field-packet-v0',
        'source_sha256': m['source']['sha256'],
        'status': 'UNREVIEWED_PROPOSAL',
        'prefilled_from_text_layer': {k: f.get(k) for k in ('model', 'title', 'unit', 'sheet', 'scale_text')},
        'size': f.get('size') or 'READ_ME: size-cell.png',
        'tolerance_rows': 'READ_ME: tolerance-cell.png  (write every row exactly as drawn, e.g. "X.  ±0.35"; '
                          'include angular rows and extra conditions; never copy from another model)',
        'tolerance_rows_from_text_layer': rows_from_words(tol_text),
        'field_flags': rep.get('field_flags', {}),
    }
    (out / 'packet.json').write_text(json.dumps(packet, ensure_ascii=False, indent=1))
    print(out)


if __name__ == '__main__':
    main()
