#!/usr/bin/env python3
"""新供应商图框：自动起草模板（量标题栏/修订栏/角上小框的比例），出一张测试图给人看。
  python tools/new_template.py 原图.pdf 模板名 --desc "供应商+图框描述" [--rotate 270] [--frame-bottom title_top] [--search 0.3]
                               [--extra x0,y0,x1,y1 ...] [--out 目录] [--write]
不加 --write：只打印起草的 furniture_frac 并出测试图（<out>/草稿-原图对照.png），不改仓库。
看过对照图合格后加 --write 写进 families/cad_templates.json，再用 frame_match.py --learn 登记样本。
--extra 用来补自动没找到的区域（比例 0-1，相对内框），例如左上的 RoHS 框、四边水印带。"""
import argparse, json, sys, tempfile
from pathlib import Path
import fitz
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
from cad_family import analyse, long_lines, segments   # noqa: E402

PART_KW = ('QTY', "Q'TY", '数量', '用量', 'MATERIAL', '材料', '材质', 'PART', '零件', '部件', '料号', '品名', 'ITEM', '序号')
HEAD_KW = ('QTY', "Q'TY", '数量', '用量', '序号')   # part-list header words that title blocks do not use (PART NAME / ITEM NO are title fields)
REV_KW = ('REV', '版本', '版次', '修订', '变更', '更改', 'ECN', 'ECO')


def _cover(segs, a, b):
    """length of [a, b] covered by the (lo, hi) segments"""
    cov = 0; cur = a
    for lo, hi in sorted(segs):
        lo, hi = max(lo, cur), min(hi, b)
        if hi > lo: cov += hi - lo; cur = hi
    return cov


def part_tables(D, words, R, I):
    """part lists whose lower rows were caught in the top of a title-block region R: >= 3 nearly equally spaced rules
    spanning over half of R's width, crossed by >= 4 verticals running through all of them (>= 3 columns), content in
    >= 2 rows (an empty BOM, header only, stays furniture; so does a revision table). The table must end inside R (a grid
    reaching R's bottom is the title block's own); a header row (QTY/数量/序号) under data rows ends it. Sure only when the
    table goes on above R's top (R cut through it); one that just starts at R's top is a guess (title blocks have equal
    rows too), and is dropped when its words have no part-list header. -> [(table Rect, number of rows, sure)]"""
    tol = 1.5
    hs = sorted(y for lo, hi, y in long_lines(D, 'H', 0.5 * R.width)
                if min(hi, R.x1) - max(lo, R.x0) >= 0.5 * R.width and I.y0 - tol <= y <= R.y1 + tol)
    ys = []
    for y in hs:   # double rules -> one
        if not ys or y - ys[-1] > tol: ys.append(y)
    vb = {}
    for lo, hi, x in segments(D, 'V'):
        if R.x0 - tol <= x <= R.x1 + tol: vb.setdefault(round(x / 0.6), []).append((lo, hi, x))
    def cols(a, b):   # x of verticals covering the band a..b
        return sorted(sum(s[2] for s in v) / len(v) for v in vb.values() if _cover([(s[0], s[1]) for s in v], a, b) >= (b - a) - tol)
    def same_cols(a, b, ref):
        c = cols(a, b); inner = ref[1:-1]
        return sum(any(abs(x - r) < tol for x in c) for r in inner) >= min(2, len(inner))
    def text(T):
        return ''.join(w[4] for w in words if T.contains(fitz.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))).upper().replace(' ', '')
    out = []; i = 0
    while i + 2 < len(ys):
        g = ys[i + 1] - ys[i]; tg = max(0.8, 0.12 * g); bd = ys[i:i + 2]; j = i + 1
        while True:   # rows of the same height; a stray rule splitting one row does not end the table
            if j + 1 < len(ys) and abs(ys[j + 1] - ys[j] - g) <= tg: j += 1
            elif j + 2 < len(ys) and abs(ys[j + 2] - ys[j] - g) <= tg: j += 2
            else: break
            bd.append(ys[j])
        c = cols(bd[0], bd[-1]) if g > 2 * tol else []
        while len(c) < 4 and len(bd) > 3:   # title-block rows of the same height below the table: their columns differ
            bd.pop(); c = cols(bd[0], bd[-1])
        if len(bd) < 3 or len(c) < 4: i += 1; continue
        nxt = ys.index(bd[-1])
        up = i > 0 and same_cols(ys[i - 1], ys[i], c)   # the table goes on above the equal rows
        if up and ys[i] - ys[i - 1] < 3 * g: bd.insert(0, ys[i - 1])   # e.g. a taller header row on top
        k = ys.index(bd[-1])
        if k + 1 < len(ys) and ys[k + 1] - ys[k] < 3 * g and same_cols(ys[k], ys[k + 1], c):   # a taller header row below
            t = text(fitz.Rect(c[0], ys[k], c[-1], ys[k + 1]))
            if any(k_ in t for k_ in HEAD_KW) or (not t and ys[k + 1] - ys[k] <= 1.5 * g): bd.append(ys[k + 1])
        rows = [fitz.Rect(c[0], y0, c[-1], y1) for y0, y1 in zip(bd, bd[1:])]
        head = [k for k, r_ in enumerate(rows) if any(k_ in text(r_) for k_ in HEAD_KW)]
        if head and (head[0] > 0 or up):
            rows = rows[:head[0] + 1]   # header under the data rows: the rows below it are the title block's
        T = fitz.Rect(rows[0].x0, rows[0].y0, rows[-1].x1, rows[-1].y1); txt = text(T)
        full = 0
        for r_ in rows:
            r_ = fitz.Rect(r_.x0 + 0.5, r_.y0 + 0.5, r_.x1 - 0.5, r_.y1 - 0.5)
            if (any(r_.contains(fitz.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)) for w in words)
                    or any(r_.contains(d['rect']) for d in D)):   # vector text (cad2pdf) has no words
                full += 1
        rev = any(k_ in txt for k_ in REV_KW) and not any(k_ in txt for k_ in PART_KW)
        sure = T.y0 < R.y0 - tol
        if (T.y0 <= R.y0 + tol and R.y0 + tol < T.y1 < R.y1 - tol and full >= 2 and not rev
                and (sure or head or not txt)):
            out.append((T, len(rows), sure))
        i = max(nxt, i + 1)
    return out


def clear_tables(D, words, I, rects):
    """auto regions standing on the bottom frame (title block) must not swallow the part list above the title block:
    every such region whose top lies inside a table moves down to the table's bottom rule (sure tables only; `alt`
    also clears the guessed ones). Tables are looked for from the outermost regions.
    -> (regions, [(table Rect, rows, sure)], alt regions)"""
    tol = 1.5; found = []
    def inside(q, o): return o.x0 <= q.x0 + tol and o.y0 <= q.y0 + tol and o.x1 >= q.x1 - tol and o.y1 >= q.y1 - tol
    bottom = [r for r in rects if r.y1 >= I.y1 - tol]
    for k, r in enumerate(bottom):
        if any(inside(r, o) and (not inside(o, r) or m < k) for m, o in enumerate(bottom) if m != k): continue
        for T, n, sure in part_tables(D, words, r, I):
            if not any(abs(T.y1 - u.y1) < tol and abs(T.x0 - u.x0) < tol for u, _, _ in found): found.append((T, n, sure))
    def clear(tabs):
        out = []
        for r in rects:
            r = fitz.Rect(r)
            if r.y1 >= I.y1 - tol:
                for T in sorted(tabs, key=lambda t: t.y0):
                    if T.y0 - tol <= r.y0 < T.y1 - tol and min(r.x1, T.x1) - max(r.x0, T.x0) > 0.5 * min(r.width, T.width):
                        r.y0 = T.y1
            out.append(r)
        return out
    return clear([T for T, _, s_ in found if s_]), found, clear([T for T, _, _ in found])


def to_frac(rects, I):
    """page rects -> furniture_frac (0-1 of the inner frame), boxes inside other boxes dropped"""
    fr = []
    for r in rects:
        q = [round((r.x0 - I.x0) / I.width, 3), round((r.y0 - I.y0) / I.height, 3),
             round((r.x1 - I.x0) / I.width, 3), round((r.y1 - I.y0) / I.height, 3)]
        q = [min(1, max(0, v)) for v in q]
        if q not in fr: fr.append(q)
    fr.sort()
    return [q for q in fr if not any(o != q and o[0] <= q[0] and o[1] <= q[1] and o[2] >= q[2] and o[3] >= q[3] for o in fr)]


def corner_boxes(D, I):
    """small boxes hanging in a corner of the inner frame (RoHS stamp, customer-approval box, CAD-file note):
    a horizontal and a vertical rule that both end on the frame edges and close a box smaller than 25% x 15%"""
    H = long_lines(D, 'H', 0.04 * I.width); V = long_lines(D, 'V', 0.03 * I.height)
    tol = 1.5; out = []
    for x0, x1, y in H:
        for side in ('L', 'R'):
            if side == 'L' and abs(x0 - I.x0) > tol: continue
            if side == 'R' and abs(x1 - I.x1) > tol: continue
            for top in (True, False):
                d = (y - I.y0) if top else (I.y1 - y)
                if not (0.01 * I.height < d < 0.15 * I.height): continue
                w = (x1 - x0)
                if w > 0.25 * I.width: continue
                xe = x1 if side == 'L' else x0
                # a vertical rule at the free end that runs from this rule to the frame edge
                ok = any(abs(c - xe) < tol and (lo <= min(y, I.y0 if top else I.y1) + tol) and (hi >= max(y, I.y0 if top else I.y1) - tol)
                         for lo, hi, c in V)
                if ok:
                    r = fitz.Rect(min(x0, x1), I.y0 if top else y, max(x0, x1), y if top else I.y1)
                    out.append(r)
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('pdf'); ap.add_argument('name'); ap.add_argument('--desc', default='')
    ap.add_argument('--rotate', type=int, default=0); ap.add_argument('--frame-bottom'); ap.add_argument('--search', type=float, default=0.12)
    ap.add_argument('--extra', nargs='*', default=[]); ap.add_argument('--out'); ap.add_argument('--write', action='store_true')
    ap.add_argument('--no-auto', action='store_true', help='不用自动找的区域，只用 --extra')
    ap.add_argument('--opt', action='append', default=[], help='模板开关，如 round_joins=true、layout=sheet、frame_search=0.3')
    a = ap.parse_args()
    doc = fitz.open(a.pdf); p = doc[0]
    if a.rotate: p.set_rotation((p.rotation + a.rotate) % 360)
    if p.rotation: p.remove_rotation()
    try:
        D, bb, I, furn = analyse(p, None, a.frame_bottom, None, a.search)
    except SystemExit as e:
        if str(e) != 'TITLE_BLOCK_NOT_FOUND': raise
        D, bb, I, _ = analyse(p, [[0, 0, 0.01, 0.01]], a.frame_bottom, None, a.search); furn = []
    # title-block rows only in the bottom 30%: tables standing on the right frame edge higher up are product content
    furn = [r for r in furn if r.y0 >= I.y0 + 0.7 * I.height or r.y1 <= I.y0 + 0.2 * I.height]
    rects = [] if a.no_auto else list(furn) + corner_boxes(D, I)
    rects, tables, alt = clear_tables(D, p.get_text('words'), I, rects)
    extra = [[float(v) for v in e.split(',')] for e in a.extra]
    fr = to_frac(rects, I) + extra
    def cli(f): return '--no-auto --extra ' + ' '.join(','.join(f'{v:g}' for v in q) for q in f)
    for tb, n, sure in tables:
        y = round((tb.y1 - I.y0) / I.height, 3)
        if sure: print(f'零件表（{n} 行）被标题栏区域框进去了：区域上沿已收到表格底线 {y}（「去掉的区域」图上黄色）')
        else: print(f'红框上沿疑似零件表（{n} 行）：表格正好从红框上沿开始，也可能是标题栏自己的格子，没自动让开（图上橙框）')
    if any(not s_ for _, _, s_ in tables): print('  是零件表就改用：', cli(to_frac(alt, I) + extra))
    if fr: print('手改区域从这里起：', cli(fr))
    tpl = {'desc': a.desc or f'{a.name}（tools/new_template.py 起草）', 'furniture_frac': fr, 'fit_search': True}
    if a.frame_bottom: tpl['frame_bottom'] = a.frame_bottom
    if a.search != 0.12: tpl['frame_search'] = a.search
    for o in a.opt:
        k, v = o.split('=', 1)
        tpl[k] = json.loads(v) if v[:1] in '[{0123456789-' or v in ('true', 'false', 'null') else v
    print('inner frame', [round(v, 1) for v in I]); print(json.dumps(tpl, ensure_ascii=False))
    path = ROOT / 'families' / 'cad_templates.json'
    T = json.loads(path.read_text(encoding='utf-8'))
    if a.write:
        if a.name in T['templates'] and not tpl['desc']: tpl['desc'] = T['templates'][a.name].get('desc', '')
        T['templates'][a.name] = tpl
        path.write_text(json.dumps(T, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        print('写入', a.name)
        return
    # test render with the drafted template (temporary copy of the template file, repo untouched)
    out = Path(a.out or tempfile.mkdtemp()); out.mkdir(parents=True, exist_ok=True)
    # what will be removed: the source with every furniture region shaded red and numbered (check no product content is inside)
    ov = fitz.open(a.pdf); op = ov[0]
    if a.rotate: op.set_rotation((op.rotation + a.rotate) % 360)
    if op.rotation: op.remove_rotation()
    sh = op.new_shape()
    for k, (x0, y0, x1, y1) in enumerate(fr):
        R = fitz.Rect(I.x0 + x0 * I.width, I.y0 + y0 * I.height, I.x0 + x1 * I.width, I.y0 + y1 * I.height)
        sh.draw_rect(R); sh.finish(color=(1, 0, 0), fill=(1, 0, 0), fill_opacity=0.18, width=max(0.3, I.width / 800))
        op.insert_text((R.x0 + 1, R.y0 + max(4, I.width / 90)), str(k), fontsize=max(4, I.width / 90), color=(1, 0, 0))
    for tb, n, sure in tables:   # part lists the regions now step around (yellow) / might have to (orange outline)
        sh.draw_rect(tb)
        if sure: sh.finish(color=(0.9, 0.7, 0), fill=(1, 0.9, 0), fill_opacity=0.35, width=max(0.3, I.width / 800))
        else: sh.finish(color=(1, 0.5, 0), dashes='[4] 0', width=max(0.6, I.width / 400))
    sh.commit()
    z = 1800 / max(op.rect.width, op.rect.height)
    op.get_pixmap(matrix=fitz.Matrix(z, z)).save(out / '草稿-去掉的区域.png')
    import cad_family as CF
    orig = path.read_text(encoding='utf-8')
    T['templates'][a.name] = tpl
    try:
        path.write_text(json.dumps(T, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        job = {'source': str(Path(a.pdf).resolve()), 'model': '草稿', 'title': '连接器', 'template': a.name, 'rotate': a.rotate,
               'tolerance': {'linear_tolerances': [{'tier': 'X.', 'value': '±0.3'}], 'angular_tolerances': [], 'additional_tolerance_conditions': []}}
        import fonts as FT
        f, fi = FT.default_font()
        CF.run_layouts(job, out, f, fi)
    finally:
        path.write_text(orig, encoding='utf-8')
    print('测试图', out / '草稿-原图对照.png')


if __name__ == '__main__':
    main()
