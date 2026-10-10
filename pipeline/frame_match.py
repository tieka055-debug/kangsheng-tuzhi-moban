#!/usr/bin/env python3
"""图框指纹：把原图里的长横线/长竖线按内框比例栅格化（40x40），和已做过的图框比对，
自动给出 (模板, 旋转)；比不上任何已知图框 -> UNKNOWN_FRAME（要先建模板，不硬做）。

  python pipeline/frame_match.py 原图.pdf [--clip x0,y0,x1,y1] [--page N]        # 判定
  python pipeline/frame_match.py --learn 模板名 原图.pdf [--rotate 270] [--clip ..]  # 把这张图的图框登记为模板的样本
  python pipeline/frame_match.py --report                                            # 各模板样本数

样本存 families/cad_signatures.json（每个样本 3200 位，几百字节）。只用线条几何，不读文字，
所以对 cad2pdf 把文字炸成线条的图也有效。"""
import argparse, base64, json, sys
from pathlib import Path
import fitz
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cad_family import analyse, long_lines

N = 40
SIGS = HERE.parent / 'families' / 'cad_signatures.json'
ROTS = (0, 90, 180, 270)
SEARCHES = (0.12, 0.30)


def _grids(page, clip=None, search=0.12, min_frac=0.10):
    """(Hgrid, Vgrid) as sets of (ix, iy) cells relative to the inner frame (rules >= min_frac of it); None if no frame"""
    try:
        D, bb, I, _ = analyse(page, [[0, 0, 0.01, 0.01]], None, clip, search)
    except SystemExit:
        return None
    if I.width < 50 or I.height < 50: return None
    H = set(); V = set()
    def cx(x): return int((x - I.x0) / I.width * N)
    def cy(y): return int((y - I.y0) / I.height * N)
    for lo, hi, y in long_lines(D, 'H', min_frac * I.width):
        if not (I.y0 - 0.02 * I.height <= y <= I.y1 + 0.02 * I.height): continue
        iy = min(N - 1, max(0, cy(y)))
        for ix in range(max(0, cx(max(lo, I.x0))), min(N - 1, cx(min(hi, I.x1))) + 1): H.add((ix, iy))
    for lo, hi, x in long_lines(D, 'V', min_frac * I.height):
        if not (I.x0 - 0.02 * I.width <= x <= I.x1 + 0.02 * I.width): continue
        ix = min(N - 1, max(0, cx(x)))
        for iy in range(max(0, cy(max(lo, I.y0))), min(N - 1, cy(min(hi, I.y1))) + 1): V.add((ix, iy))
    return H, V


def _arr(g):
    import numpy as np
    m = np.zeros((2, N, N), dtype=bool)
    for ix, iy in g[0]: m[0, iy, ix] = True
    for ix, iy in g[1]: m[1, iy, ix] = True
    return m


def _dil(m):
    import numpy as np
    out = m.copy()
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            out |= np.roll(np.roll(m, dx, axis=2), dy, axis=1)
    return out


def _pack(g):
    import numpy as np
    return base64.b64encode(np.packbits(_arr(g).ravel()).tobytes()).decode()


def _unpack_arr(s):
    import numpy as np
    return np.unpackbits(np.frombuffer(base64.b64decode(s), dtype=np.uint8))[:2 * N * N].reshape(2, N, N).astype(bool)


def score_arr(a, b):
    """symmetric coverage of long rules (tolerance one cell): content noise lowers the candidate-side value a little"""
    ad, bd = _dil(a), _dil(b)
    ca = (a & bd).sum() / max(1, a.sum()); cb = (b & ad).sum() / max(1, b.sum())
    return float(min(ca, cb))


def score(g, proto):
    return score_arr(_arr(g), _arr(proto))


def load():
    return json.load(open(SIGS, encoding='utf-8')) if SIGS.exists() else {'samples': []}


def candidates(pdf, clip=None, page_no=0):
    doc = fitz.open(pdf); out = []
    for r in ROTS:
        p = doc[page_no]
        p.set_rotation((p.rotation + r) % 360)
        if p.rotation: p.remove_rotation()
        gs = {}
        for se in SEARCHES:   # a big title/label outside the sheet border pushes the border out of the usual 12% search band
            gs[se] = _grids(p, clip, se)
        doc.close(); doc = fitz.open(pdf)
        for se, g in gs.items():
            if g is not None: out.append((r, g, se))
    return out


def scored(pdf, clip=None, page_no=0, sigs=None, exclude=None):
    """every (score, rotate, template, search) of this page against every sample, best first"""
    sigs = sigs or load(); best = []
    for r, g, se in candidates(pdf, clip, page_no):
        for s in sigs['samples']:
            if exclude and s.get('id') == exclude: continue
            if s.get('search', 0.12) != se: continue
            best.append((score_arr(_arr(g), _unpack_arr(s['G'])), r, s['template'], se))
    best.sort(reverse=True)
    return best


def pick(best):
    """top of a scored() list; when the same template scores within 0.01 with the page as it stands (rotate 0), take
    that: sorting alone put the highest rotation first, which turned pages carrying /Rotate once more (联攀 270°)"""
    top = best[0]
    return next((b for b in best if b[1] == 0 and b[2] == top[2] and top[0] - b[0] < 0.01), top)


def match(pdf, clip=None, page_no=0, sigs=None, exclude=None):
    """-> {'template','rotate','score','runner_up':(template,score),'status'}"""
    best = scored(pdf, clip, page_no, sigs, exclude)
    if not best: return {'status': 'NO_FRAME', 'template': None, 'rotate': None, 'score': 0}
    sc, r, t, se = pick(best)
    ru = next(((tt, ss) for ss, rr, tt, _ in best if tt != t), (None, 0))
    st = 'OK' if sc >= 0.90 and sc - ru[1] >= 0.03 else ('AMBIGUOUS' if sc >= 0.90 else 'UNKNOWN_FRAME')
    return {'status': st, 'template': t, 'rotate': r, 'score': round(sc, 3), 'runner_up': [ru[0], round(ru[1], 3)], 'search': se}


def learn(template, pdf, rotate=0, clip=None, page_no=0, sid=None, search=0.12):
    doc = fitz.open(pdf); p = doc[page_no]
    if rotate: p.set_rotation((p.rotation + rotate) % 360)
    if p.rotation: p.remove_rotation()
    g = _grids(p, clip, search)
    if g is None: raise SystemExit('FRAME_NOT_FOUND')
    sigs = load()
    sigs['samples'].append({'id': sid or Path(pdf).name, 'template': template, 'rotate_hint': rotate, 'search': search, 'G': _pack(g)})
    json.dump(sigs, open(SIGS, 'w', encoding='utf-8'), ensure_ascii=False)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('pdf', nargs='?'); ap.add_argument('--clip'); ap.add_argument('--page', type=int, default=0)
    ap.add_argument('--learn'); ap.add_argument('--rotate', type=int, default=0); ap.add_argument('--report', action='store_true'); ap.add_argument('--search', type=float, default=0.12)
    a = ap.parse_args()
    clip = [float(v) for v in a.clip.split(',')] if a.clip else None
    if a.report:
        import collections
        print(collections.Counter(s['template'] for s in load()['samples'])); sys.exit()
    if a.learn: learn(a.learn, a.pdf, a.rotate, clip, a.page, search=a.search); print('learned', a.learn)
    else: print(json.dumps(match(a.pdf, clip, a.page), ensure_ascii=False))
