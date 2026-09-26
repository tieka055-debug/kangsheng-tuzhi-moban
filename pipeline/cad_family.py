#!/usr/bin/env python3
"""非质源 CAD 矢量图（cad2pdf / DWG 导出，文字为轮廓线）→ 康生图纸。

与质源流程不同：这类图没有文字层，排版保留原图整体布局，只做：
  1. 找到原图内框、标题栏、修订栏（按线条自动定位，不用单张坐标），这些供应商框架不搬运；
  2. 其余全部矢量路径原样重画到康生底板上（等比缩放，放在康生内框里，避开康生标题栏）；
  3. 颜色：黑/灰和原图主标注色 → 康生蓝；其他彩色（端子、填充等重点）→ 康生金；
  4. 康生标题栏 + 英文公差栏（公差值由人/模型照原图读出，写在 job.json 里）。
自检：搬运路径数 = 原图内框里除标题栏/修订栏外的路径数；输出无越界、不压标题栏。

  python pipeline/cad_family.py job.json --out 输出目录 --font 字体 [--font-index N]
job.json: {"source": "原图.pdf", "model": "...", "title": "...", "tolerance": {...engine schema...}}
"""
import argparse, collections, json, math, sys
from pathlib import Path
import pymupdf as fitz

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / 'engine'))
import frame as KF                      # noqa: E402
import dynamic_tolerance as DT          # noqa: E402

BLUE = KF.BLUE
GOLD = (217 / 255, 154 / 255, 0)
FRAME = fitz.Rect(KF.FRAME)
RESERVED = fitz.Rect(400, 450, 820, 564)     # 康生标题栏 + 公差栏


def is_neutral(c):
    return c is not None and max(c) - min(c) < 0.10


def ckey(c):
    return None if c is None else tuple(round(v, 2) for v in c)


def segments(D, orient):
    """all axis-parallel straight segments (lines and rectangle edges): (lo, hi, coord)"""
    out = []
    for d in D:
        for it in d['items']:
            if it[0] == 'l':
                a, b = it[1], it[2]
                if orient == 'H' and abs(a.y - b.y) < 0.3: out.append((min(a.x, b.x), max(a.x, b.x), (a.y + b.y) / 2))
                if orient == 'V' and abs(a.x - b.x) < 0.3: out.append((min(a.y, b.y), max(a.y, b.y), (a.x + b.x) / 2))
            elif it[0] in ('re', 'qu'):
                r = it[1] if it[0] == 're' else it[1].rect
                if it[0] == 'qu' and not it[1].is_rectangular: continue
                if orient == 'H': out += [(r.x0, r.x1, r.y0), (r.x0, r.x1, r.y1)]
                else: out += [(r.y0, r.y1, r.x0), (r.y0, r.y1, r.x1)]
    return out


def long_lines(D, orient, min_len, lo=None, hi=None):
    """collinear segments merged per coordinate (0.4pt bins); returns (lo, hi, coord) of coverage >= min_len"""
    bins = collections.defaultdict(list)
    for a, b, c in segments(D, orient):
        bins[round(c / 0.4)].append((a, b, c))
    out = []
    for segs in bins.values():
        segs.sort(); cov = 0; cur = None; lo_ = min(s[0] for s in segs); hi_ = max(s[1] for s in segs)
        for a, b, _ in segs:
            if cur is None or a > cur: cov += b - a; cur = b
            elif b > cur: cov += b - cur; cur = b
        if cov >= min_len: out.append((lo_, hi_, sum(s[2] for s in segs) / len(segs)))
    return out


def analyse(page, furniture_frac=None):
    D = page.get_drawings()
    bb = fitz.Rect()
    for d in D: bb |= d['rect']
    # inner frame: the innermost of the long border lines on each side
    H = long_lines(D, 'H', 0.6 * bb.width); V = long_lines(D, 'V', 0.6 * bb.height)
    tops = [y for _, _, y in H if y < bb.y0 + 0.12 * bb.height]
    bots = [y for _, _, y in H if y > bb.y1 - 0.12 * bb.height]
    lefs = [x for _, _, x in V if x < bb.x0 + 0.12 * bb.width]
    rigs = [x for _, _, x in V if x > bb.x1 - 0.12 * bb.width]
    if not (tops and bots and lefs and rigs): raise SystemExit('FRAME_NOT_FOUND')
    I = fitz.Rect(max(lefs), max(tops), min(rigs), min(bots))
    # title block: horizontal rules that end on the inner right edge in the lower part -> stepped region
    h_all = long_lines(D, 'H', 0.08 * I.width)
    tol = 1.5
    if furniture_frac:
        furn = [fitz.Rect(I.x0 + a * I.width, I.y0 + b * I.height, I.x0 + c * I.width, I.y0 + d * I.height)
                for a, b, c, d in furniture_frac]
        return D, bb, I, furn
    V_all = long_lines(D, 'V', 6.0)
    def v_to_bottom(x):
        return any(abs(c - x) < tol and hi >= I.y1 - tol for lo, hi, c in V_all) or abs(x - I.x1) < tol
    def v_down_from(x, y):
        return any(abs(c - x) < tol and lo <= y + tol and hi >= I.y1 - tol for lo, hi, c in V_all)
    title = []
    for x0, x1, y in h_all:
        if not (I.y0 + 0.5 * I.height < y < I.y1 - 2): continue
        # a title-block cell row: its left end drops straight to the bottom frame, its right end
        # is the frame edge or another rule that also reaches the bottom
        if v_down_from(x0, y) and v_to_bottom(x1):
            title.append(fitz.Rect(x0, y, x1, I.y1))
    # keep only blocks connected to the bottom-right corner
    blk = [r for r in title if abs(r.x1 - I.x1) < tol]
    grew = True
    while grew:
        grew = False
        for r in title:
            if r not in blk and any(abs(r.x1 - b.x0) < tol or abs(r.x0 - b.x1) < tol for b in blk):
                blk.append(r); grew = True
    title = blk
    # a rule reaching the bottom edge from a rule's left end extends the block down (stepped blocks)
    if not title: raise SystemExit('TITLE_BLOCK_NOT_FOUND')
    top = min(title, key=lambda r: r.y0)
    # rev table: rules ending on the right edge in the top part, shorter than half the width
    rev = [fitz.Rect(x0, I.y0, I.x1, y) for x0, x1, y in h_all
           if abs(x1 - I.x1) < tol and y < I.y0 + 0.15 * I.height and (x1 - x0) < 0.5 * I.width]
    furn = title + rev
    return D, bb, I, furn


def inside_any(r, rects, pad=0.6):
    c = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
    return any(fitz.Rect(q.x0 - pad, q.y0 - pad, q.x1 + pad, q.y1 + pad).contains(c) for q in rects)


def run(job, out, font, font_index=0):
    src = fitz.open(job['source']); page = src[0]
    ff = job.get('furniture_frac')
    if job.get('template'):
        ff = json.loads((ROOT / 'families' / 'cad_templates.json').read_text())['templates'][job['template']]['furniture_frac']
    D, bb, I, furn = analyse(page, ff)
    keep, dropped = [], collections.Counter()
    for d in D:
        r = d['rect']
        if not fitz.Rect(I.x0 - 0.5, I.y0 - 0.5, I.x1 + 0.5, I.y1 + 0.5).contains(r):
            dropped['frame_band'] += 1; continue
        if r.width > 0.9 * I.width or r.height > 0.9 * I.height:
            dropped['frame_rule'] += 1; continue
        if inside_any(r, furn):
            dropped['title_or_rev'] += 1; continue
        keep.append(d)
    if not keep: raise SystemExit('NOTHING_TO_PLACE')
    # colours: neutral + the dominant annotation colour -> blue; every other colour -> gold
    cnt = collections.Counter()
    for d in keep:
        c = d.get('color') if d['type'] != 'f' else d.get('fill')
        if c is not None and not is_neutral(c): cnt[ckey(c)] += 1
    dominant = cnt.most_common(1)[0][0] if cnt else None
    GREEN = (0.0, 1.0, 0.0)
    def mapc(c):
        if c is None: return None
        if is_neutral(c) or ckey(c) in (dominant, GREEN): return BLUE
        return GOLD
    cb = fitz.Rect()
    for d in keep: cb |= d['rect']
    # ---- blocks: cluster paths by proximity
    g = 0.015 * cb.width
    rects = [fitz.Rect(d['rect']) for d in keep]
    par = list(range(len(rects)))
    def f_(i):
        while par[i] != i: par[i] = par[par[i]]; i = par[i]
        return i
    order = sorted(range(len(rects)), key=lambda i: rects[i].x0)
    for ii, i in enumerate(order):     # sweep on x for speed
        ri = rects[i]
        for j in order[ii + 1:]:
            rj = rects[j]
            if rj.x0 > ri.x1 + g: break
            if rj.y0 <= ri.y1 + g and ri.y0 <= rj.y1 + g: par[f_(j)] = f_(i)
    groups = collections.defaultdict(list)
    for i in range(len(keep)): groups[f_(i)].append(i)
    blocks = []
    for idx in groups.values():
        r = fitz.Rect()
        for i in idx: r |= rects[i]
        blocks.append({'idx': idx, 'r': r})
    # merge tiny fragments into the nearest bigger block
    big = [b_ for b_ in blocks if b_['r'].width * b_['r'].height > 0.002 * cb.width * cb.height]
    for b_ in blocks:
        if b_ in big or not big: continue
        near = min(big, key=lambda q: abs((q['r'].x0 + q['r'].x1) / 2 - (b_['r'].x0 + b_['r'].x1) / 2)
                   + abs((q['r'].y0 + q['r'].y1) / 2 - (b_['r'].y0 + b_['r'].y1) / 2))
        near['idx'] += b_['idx']; near['r'] |= b_['r']
    blocks = big or blocks
    # right column (notes / dimension table / parts list) -> Kangsheng right rail; the rest are views
    split = cb.x0 + 0.58 * cb.width
    def to_rail(r):
        cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
        if cx >= split: return True
        # small marks in the top-right corner (e.g. a RoHS stamp) travel with the right column
        return cx >= cb.x0 + 0.5 * cb.width and cy <= cb.y0 + 0.12 * cb.height and r.width * r.height < 0.01 * cb.width * cb.height
    rail = [b_ for b_ in blocks if to_rail(b_['r'])]
    views = [b_ for b_ in blocks if b_ not in rail]
    if not views: views, rail = rail, []
    mats = {}
    RAIL = fitz.Rect(600, 38, 814, 444)
    if rail:
        # the right column moves as ONE unit (notes, tables and ordering diagrams keep their relative layout)
        rb = fitz.Rect()
        for b_ in rail: rb |= b_['r']
        rw = RAIL.x1 - 524          # the rail may widen leftwards up to x=524 (same limit as the Zhiyuan rule)
        sc = min(rw / rb.width, RAIL.height / rb.height)
        x = RAIL.x1 - rb.width * sc; y = RAIL.y0
        m_ = fitz.Matrix(sc, 0, 0, sc, x - rb.x0 * sc, y - rb.y0 * sc)
        for b_ in rail:
            for i in b_['idx']: mats[i] = (m_, sc)
        rail_x0 = x - 10
    else:
        rail_x0 = FRAME.x1 - 8
    # views keep their arrangement, enlarged uniformly into the left area, clear of the title block
    vb = fitz.Rect()
    for b_ in views: vb |= b_['r']
    area = fitz.Rect(FRAME.x0 + 10, FRAME.y0 + 10, rail_x0, FRAME.y1 - 8)
    s = min(area.width / vb.width, area.height / vb.height)
    placed = None
    while s > 0.2:
        ox = area.x0 + (area.width - vb.width * s) / 2; oy = area.y0 + max(0, (area.height - vb.height * s) / 2)
        m = fitz.Matrix(s, 0, 0, s, ox - vb.x0 * s, oy - vb.y0 * s)
        if not any((rects[i] * m).intersects(RESERVED) for b_ in views for i in b_['idx']):
            placed = (s, m); break
        s *= 0.98
    if placed is None: raise SystemExit('NO_ROOM')
    s, m = placed
    for b_ in views:
        for i in b_['idx']: mats[i] = (m, s)
    doc = fitz.open(); pg = doc.new_page(width=KF.PAGE[0], height=KF.PAGE[1])
    pg.insert_image(pg.rect, filename=str(ROOT / 'assets' / 'background.png'))
    sh = pg.new_shape()
    for n_, d in enumerate(keep):
        m, s = mats[n_]
        for it in d['items']:
            k = it[0]
            if k == 'l': sh.draw_line(it[1] * m, it[2] * m)
            elif k == 'c': sh.draw_bezier(it[1] * m, it[2] * m, it[3] * m, it[4] * m)
            elif k == 're': sh.draw_rect(it[1] * m)
            elif k == 'qu': sh.draw_quad(it[1] * m)
        t = d['type']
        col = mapc(d.get('color')) if t in ('s', 'fs') else None
        fil = mapc(d.get('fill')) if t in ('f', 'fs') else None
        w = max((d.get('width') or 0) * s, 0.4)
        sh.finish(color=col, fill=fil, width=w, closePath=d.get('closePath', False),
                  even_odd=d.get('even_odd', False), lineCap=0, lineJoin=0)
    sh.commit()
    # Kangsheng frame + title
    ff = Path(font)
    if font_index or ff.suffix.lower() in ('.ttc', '.otc', '.otf'):
        sys.path.insert(0, str(HERE)); import auto_manifest as am
        text = job['title'] + job['model']
        ttf = Path(out) / 'title-font.ttf'; Path(out).mkdir(parents=True, exist_ok=True)
        from fontTools.ttLib import TTFont
        from fontTools import subset
        tt = TTFont(str(ff), fontNumber=font_index)
        opts = subset.Options(); opts.name_IDs = ['*']
        sub = subset.Subsetter(opts); sub.populate(text=text + 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-_()/ '); sub.subset(tt)
        (am._cff_to_ttf(tt) if 'CFF ' in tt else tt).save(str(ttf))
        font = str(ttf)
    fields = {'title': job['title'], 'model': job['model'], 'unit': 'mm', 'size': job.get('size', 'A4'),
              'sheet': '1/1', 'scale_text': ''}
    KF.draw_frame_and_title(pg, fields, {'font': font, 'brand_strip': str(ROOT / 'assets' / 'brand-strip.png')},
                            tolerance_mode='source')
    DT.render_dynamic_tolerance(pg, job['tolerance'], KF.TOLERANCE_BOX)
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    pdf = out / f"{job['model']}-康生图纸.pdf"
    doc.save(pdf, garbage=3, deflate=True)
    rep = {'source': job['source'], 'inner_frame': list(I), 'supplier_furniture_rects': [list(r) for r in furn],
           'paths_total': len(D), 'paths_placed': len(keep), 'dropped': dict(dropped),
           'dominant_colour_to_blue': dominant, 'other_colours_to_gold': [k for k in cnt if k != dominant],
           'scale': round(s, 4), 'output': str(pdf)}
    (out / 'report.json').write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    # side-by-side review image
    rv = fitz.open(); r = rv.new_page(width=1700, height=640)
    r.show_pdf_page(fitz.Rect(5, 20, 845, 635), src, 0, clip=bb)
    r.show_pdf_page(fitz.Rect(855, 20, 1695, 635), doc, 0)
    r.get_pixmap(dpi=110).save(out / f"{job['model']}-原图对照.png")
    print(json.dumps({k: rep[k] for k in ('paths_total', 'paths_placed', 'dropped', 'scale')}, ensure_ascii=False))
    return rep


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('job'); ap.add_argument('--out', required=True)
    ap.add_argument('--font', required=True); ap.add_argument('--font-index', type=int, default=0)
    a = ap.parse_args()
    run(json.loads(Path(a.job).read_text()), a.out, a.font, a.font_index)


if __name__ == '__main__':
    main()
