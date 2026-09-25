"""Independent completeness check (not used by the generator): every source word outside the
supplier title block / frame band / watermark must appear in the output page text, with multiplicity."""
import sys, json, collections, re
import pymupdf as fitz
sys.path.insert(0, __file__.rsplit('/', 1)[0]); import auto_manifest as am

def check(src, manifest, draft_pdf, family):
    m = json.loads(open(manifest).read())
    d, p, rot = am.normalized(m['source']['path'])
    tb_like = [fitz.Rect(e['box']) for e in m['coverage']['exclude']]
    carried = [fitz.Rect(c) for g in m['groups'] if g['kind'] not in ('tolerance', 'projection') for c in g['clips']]
    wm = family['watermark_patterns'] + family['watermark_single_chars']
    need = collections.Counter()
    for w in p.get_text('words'):
        r = fitz.Rect(w[:4]); t = w[4]
        if any(x in t for x in wm): continue
        c = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
        if any(e.contains(c) for e in tb_like) and not any(k.contains(c) for k in carried): continue
        need[t] += 1
    out = fitz.open(draft_pdf)[0]
    have = collections.Counter(w[4] for w in out.get_text('words'))
    missing = {t: n - have.get(t, 0) for t, n in need.items() if have.get(t, 0) < n}
    return {'source_content_words': sum(need.values()), 'missing': missing}

if __name__ == '__main__':
    fam = json.load(open(sys.argv[1]))
    for run_dir in sys.argv[2:]:
        r = check(None, run_dir + '/manifest.json', run_dir + '/draft/draft.pdf', fam)
        print(run_dir.split('/')[-1], r['source_content_words'], 'missing:', r['missing'])
