#!/usr/bin/env python3
"""一批认不出的图，先按图框分组再建模板：每组只起草一次（docs/NEW_SUPPLIER.md 第 2-3 步）。
  python tools/cluster_frames.py 原图目录 输出目录 [--survey 判框结果.json ...] [--threshold 0.88]
                                 [--status UNKNOWN_FRAME AMBIGUOUS]
目录里判框是 UNKNOWN_FRAME / AMBIGUOUS 的图，用 frame_match 的图框指纹两两比（对方取 4 个方向里最像的），
相似度 >= 0.88 的归一组（以组里最像大家的那张为中心，不会一串串连起来）。指纹只取长过内框 20% 的线（图框、标题栏、
表格的线）：判框用的 10% 会把视图里的线也算进去，图一密就互相都像。输出：
  组NN-M张.png   每组一张缩略图拼图，红框 = 建议拿来起草的代表图，标签 = 编号、和代表图的相似度、最像的已有模板
  单张-N张.png   和谁都不像的图
  groups.json    分组明细；并打印每组的起草命令
--survey 给 tools/survey_classify.py 的结果（可多个），省得重判；不给就逐张判，结果存 输出目录/survey.json（可断点续跑）。
只读原图，不改仓库。"""
import argparse, base64, collections, glob, json, sys
from pathlib import Path
import numpy as np
import fitz
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline')); sys.path.insert(0, str(ROOT / 'tools'))
import frame_match as fm   # noqa: E402
from contact_sheet import contact_sheet, page_pixmap   # noqa: E402

SHEET_MAX = 24   # thumbnails per group sheet
MIN_FRAC = 0.20  # rules >= 20% of the inner frame: on 200 unknown sheets 0.10 grouped 61% same-supplier, 0.20 82%


def grids(pdf, search):
    """frame fingerprint in the 4 orientations: {rotate: packed grid}"""
    out = {}
    for r in fm.ROTS:
        p = fitz.open(pdf)[0]
        p.set_rotation((p.rotation + r) % 360)
        if p.rotation: p.remove_rotation()
        g = fm._grids(p, None, search, MIN_FRAC)
        if g is not None: out[str(r)] = base64.b64encode(np.packbits(fm._arr(g).ravel()).tobytes()).decode()
    return out


def sim(a, B):
    """fingerprint a (with its dilation) against B in its best orientation; same measure as frame_match.score_arr"""
    a, ad = a; best = 0.0
    for b, bd in B.values():
        best = max(best, min((a & bd).sum() / max(1, a.sum()), (b & ad).sum() / max(1, b.sum())))
    return float(best)


def cluster(M, thr, weight):
    """star clustering: the item with most unassigned neighbours (>= thr) takes them all; the group's representative is
    its medoid (ties: `weight`, then the earlier item). -> [(representative, [members])], biggest first"""
    left = set(range(len(M))); nb = [{int(j) for j in np.nonzero(M[i] >= thr)[0]} - {i} for i in range(len(M))]
    out = []
    while left:
        lead = max(left, key=lambda i: (len(nb[i] & left), weight(i), -i))
        g = sorted({lead} | (nb[lead] & left)); left -= set(g)
        rep = max(g, key=lambda i: (round(float(np.mean([M[i, j] for j in g])), 4), weight(i), -i))
        out.append((rep, g))
    out.sort(key=lambda t: -len(t[1]))
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('pdf_dir'); ap.add_argument('out')
    ap.add_argument('--survey', nargs='*', default=[], help='survey_classify.py 的结果 JSON，可多个')
    ap.add_argument('--threshold', type=float, default=0.88)
    ap.add_argument('--status', nargs='*', default=['UNKNOWN_FRAME', 'AMBIGUOUS'])
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    pdfs = {Path(f).stem: f for f in sorted(glob.glob(str(Path(a.pdf_dir) / '*.pdf')))}
    cache = out / 'survey.json'
    S = json.load(open(cache, encoding='utf-8')) if cache.exists() else {}
    for f in a.survey: S.update(json.load(open(f, encoding='utf-8')))
    sigs = fm.load()
    for k, f in pdfs.items():   # frame match for the sheets the given survey does not cover
        if k in S: continue
        try:
            d = fitz.open(f); r = fm.match(f, None, 0, sigs); r['pages'] = len(d); r['drawings'] = len(d[0].get_drawings())
        except BaseException as e:
            r = {'status': 'ERR', 'err': str(e)[:80]}
        S[k] = r; json.dump(S, open(cache, 'w', encoding='utf-8'), ensure_ascii=False)
        print('判框', k, r.get('status'), r.get('template'), r.get('score'), flush=True)
    ids = [k for k in pdfs if S.get(k, {}).get('status') in a.status]
    print(f'{len(pdfs)} 张原图，{len(ids)} 张要分组（{"/".join(a.status)}）', flush=True)
    gcache = out / 'grids.json'
    GC = json.load(open(gcache, encoding='utf-8')) if gcache.exists() else {}
    G = {}
    for n, k in enumerate(ids):
        key = f"{k}@{S[k].get('search') or 0.12}@{MIN_FRAC}"
        if key not in GC:
            try: GC[key] = grids(pdfs[k], S[k].get('search') or 0.12)
            except BaseException: GC[key] = {}
            if n % 20 == 19: json.dump(GC, open(gcache, 'w', encoding='utf-8')); print('指纹', n + 1, '/', len(ids), flush=True)
        G[k] = {}
        for r, v in GC[key].items():
            arr = fm._unpack_arr(v); G[k][int(r)] = (arr, fm._dil(arr))
    json.dump(GC, open(gcache, 'w', encoding='utf-8'))
    ids = [k for k in ids if G[k]]
    rot = {k: S[k].get('rotate') if S[k].get('rotate') in G[k] else min(G[k]) for k in ids}
    n = len(ids); M = np.eye(n)
    for i in range(n):
        A = G[ids[i]][rot[ids[i]]]
        for j in range(i + 1, n):
            B = G[ids[j]][rot[ids[j]]]
            M[i, j] = M[j, i] = max(sim(A, G[ids[j]]), sim(B, G[ids[i]]))
    weight = lambda i: (-(S[ids[i]].get('pages') or 1), S[ids[i]].get('drawings') or 0)   # one-page, richer sheets first
    groups = cluster(M, a.threshold, weight)

    def label(i, rep, s=None):
        r = S[ids[i]]
        head = '★代表 ' if i == rep else ''
        return (f"{head}{ids[i]}" + (f'  相似{s:.2f}' if s is not None else '')
                + f"  最像 {r.get('template')} {r.get('score')}")
    res = []; singles = []; gno = 0
    for rep, g in groups:
        k = ids[rep]
        if len(g) == 1: singles.append(rep); continue
        gno += 1
        mem = sorted(g, key=lambda i: (i != rep, -M[rep, i]))
        near = collections.Counter(S[ids[i]].get('template') for i in g).most_common(1)[0][0]
        png = out / f'组{gno:02d}-{len(g)}张.png'
        contact_sheet([(page_pixmap(pdfs[ids[i]], rot[ids[i]], size=600), label(i, rep, M[rep, i]), i == rep)
                       for i in mem[:SHEET_MAX]], png)
        res.append({'group': gno, 'size': len(g), 'representative': k, 'pdf': pdfs[k], 'rotate': rot[k], 'sheet': str(png),
                    'nearest_template': near,
                    'members': [{'id': ids[i], 'sim_to_rep': round(float(M[rep, i]), 3), 'status': S[ids[i]].get('status'),
                                 'template': S[ids[i]].get('template'), 'score': S[ids[i]].get('score'),
                                 'rotate': rot[ids[i]]} for i in mem]})
        print(f'组{gno:02d} {len(g)} 张  代表 {k}（rotate {rot[k]}）  组里最常见的「最像模板」{near}  拼图 {png.name}')
        print(f'   先试已有模板：python tools/try_templates.py {pdfs[k]} --out 试模板/组{gno:02d}')
        print(f'   起草新模板：  python tools/new_template.py {pdfs[k]} 新模板名 --rotate {rot[k]} --out 草稿/组{gno:02d}')
    if singles:
        png = out / f'单张-{len(singles)}张.png'
        contact_sheet([(page_pixmap(pdfs[ids[i]], rot[ids[i]], size=600), label(i, None), False) for i in singles[:40]], png)
        print(f'单张 {len(singles)} 张（和谁都不像）：{" ".join(ids[i] for i in singles)}  拼图 {png.name}')
    json.dump({'threshold': a.threshold, 'groups': res,
               'singles': [{'id': ids[i], 'pdf': pdfs[ids[i]], 'rotate': rot[ids[i]], 'template': S[ids[i]].get('template'),
                            'score': S[ids[i]].get('score')} for i in singles]},
              open(out / 'groups.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('分组明细', out / 'groups.json')


if __name__ == '__main__':
    main()
