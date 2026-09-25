"""Detector: a line of source text must not be split across different placement groups.
Same-baseline text pieces closer than 3x their font size count as one line."""
import sys, json
import pymupdf as fitz
sys.path.insert(0, __file__.rsplit('/', 1)[0]); import auto_manifest as am

def split_lines(manifest):
    m = json.load(open(manifest))
    d, p, rot = am.normalized(m['source']['path'])
    groups = [(g['id'], [fitz.Rect(c) for c in g['clips']]) for g in m['groups']]
    def gid(r):
        c = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
        for i, cl in groups:
            if any(k.contains(c) for k in cl): return i
        return None
    spans = []
    for b in p.get_text('dict')['blocks']:
        for l in b.get('lines', []):
            for s in l['spans']:
                if s['text'].strip(): spans.append((fitz.Rect(s['bbox']), s['text'], s['size']))
    spans.sort(key=lambda t: (round((t[0].y0 + t[0].y1) / 2), t[0].x0))
    issues = []
    words = [(fitz.Rect(w[:4]), w[4]) for w in p.get_text('words')]
    for r, t, sz in spans:   # one PDF text span = one line of meaning; its words must travel together
        ids = set()
        for wr, wt in words:
            c = fitz.Point((wr.x0 + wr.x1) / 2, (wr.y0 + wr.y1) / 2)
            if r.contains(c):
                g = gid(wr)
                if g: ids.add(g)
        if len(ids) > 1: issues.append({'span': t.strip(), 'groups': sorted(ids)})
    for i, (r, t, sz) in enumerate(spans):
        for r2, t2, sz2 in spans[i + 1:]:
            same_base = abs((r.y0 + r.y1) / 2 - (r2.y0 + r2.y1) / 2) < 0.3 * sz and abs(sz - sz2) < 0.5
            gap = r2.x0 - r.x1
            if same_base and -1 < gap < 3 * sz:
                a, b2 = gid(r), gid(r2)
                if a and b2 and a != b2: issues.append({'left': t, 'right': t2, 'groups': [a, b2]})
    return issues

if __name__ == '__main__':
    for d in sys.argv[1:]:
        print(d.split('/')[-1], split_lines(d + '/manifest.json'))
