"""Detector: blocks that sit close together in the source (dimension groups, callouts, the view they
belong to) must keep their relative position in the output.  Flags any pair of 1:1 blocks whose
source gap is small but whose displacement differs."""
import sys, json, math
import pymupdf as fitz

def union(cs):
    r = fitz.Rect(cs[0])
    for c in cs[1:]: r |= fitz.Rect(c)
    return r

def displaced(manifest, near_pt=15.0, tol_pt=2.0):
    m = json.load(open(manifest))
    gs = [g for g in m['groups'] if g['kind'] in ('view', 'pcb', 'isometric', 'note') and abs(g['scale'] - 1) < 1e-6]
    out = []
    for i, a in enumerate(gs):
        ra = union(a['clips'])
        da = (a['dst'][0] - ra.x0, a['dst'][1] - ra.y0)
        for b in gs[i + 1:]:
            rb = union(b['clips'])
            gap = math.hypot(max(0, max(ra.x0, rb.x0) - min(ra.x1, rb.x1)), max(0, max(ra.y0, rb.y0) - min(ra.y1, rb.y1)))
            if gap > near_pt: continue
            db = (b['dst'][0] - rb.x0, b['dst'][1] - rb.y0)
            shift = math.hypot(da[0] - db[0], da[1] - db[1])
            if shift > tol_pt:
                out.append({'a': a['id'], 'b': b['id'], 'source_gap_pt': round(gap, 1), 'relative_shift_pt': round(shift, 1)})
    return out

if __name__ == '__main__':
    for d in sys.argv[1:]:
        print(d.split('/')[-1], displaced(d + '/manifest.json'))


def narrow_displaced(manifest, near=10.0, tol=2.0, narrow=40.0):
    """A narrow block (dimension group / caption) must stay put relative to the big block beside it."""
    m = json.load(open(manifest))
    gs = [g for g in m['groups'] if g['kind'] in ('view', 'pcb') and abs(g['scale'] - 1) < 1e-6]
    out = []
    for a in gs:
        ra = union(a['clips'])
        if min(ra.width, ra.height) >= narrow: continue
        best = None
        for b in gs:
            if b is a: continue
            rb = union(b['clips'])
            if min(rb.width, rb.height) < narrow: continue
            gap = math.hypot(max(0, max(ra.x0, rb.x0) - min(ra.x1, rb.x1)), max(0, max(ra.y0, rb.y0) - min(ra.y1, rb.y1)))
            if gap <= near and (best is None or gap < best[0]): best = (gap, b, rb)
        if best:
            gap, b, rb = best
            da = (a['dst'][0] - ra.x0, a['dst'][1] - ra.y0); db = (b['dst'][0] - rb.x0, b['dst'][1] - rb.y0)
            shift = math.hypot(da[0] - db[0], da[1] - db[1])
            if shift > tol:
                out.append({'narrow': a['id'], 'neighbor': b['id'], 'source_gap_pt': round(gap, 1), 'relative_shift_pt': round(shift, 1)})
    return out
