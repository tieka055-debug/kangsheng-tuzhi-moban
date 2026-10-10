#!/usr/bin/env python3
"""新供应商图框：自动起草模板（量标题栏/修订栏/角上小框的比例），出一张测试图给人看。
  python tools/new_template.py 原图.pdf 模板名 --desc "供应商+图框描述" [--rotate 270] [--frame-bottom title_top] [--search 0.3]
                               [--extra x0,y0,x1,y1 ...] [--out 目录] [--write]
不加 --write：只打印起草的 furniture_frac 并出测试图（<out>/草稿-原图对照.png），不改仓库。
看过对照图合格后加 --write 写进 families/cad_templates.json，再用 frame_match.py --learn 登记样本。
--extra 用来补自动没找到的区域（比例 0-1，相对内框），例如左上的 RoHS 框、四边水印带。"""
import argparse, os, time, bisect, collections, json, re, sys, tempfile
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


class Rules:
    """the horizontal rules of a page, looked up by height"""
    def __init__(self, D, tol=1.5):
        self.tol = tol
        self.H = sorted((y, lo, hi) for lo, hi, y in segments(D, 'H') if hi - lo > tol)
        self.ys = [h[0] for h in self.H]

    def spans(self, y):
        """[lo, hi] runs of the rules at height y (gaps up to tol closed)"""
        out = []
        for lo, hi in sorted(h[1:] for h in self.H[bisect.bisect_left(self.ys, y - self.tol):bisect.bisect_right(self.ys, y + self.tol)]):
            if out and lo <= out[-1][1] + self.tol: out[-1][1] = max(out[-1][1], hi)
            else: out.append([lo, hi])
        return out

    def covers(self, y, a, b):
        return any(lo <= a + self.tol and hi >= b - self.tol for lo, hi in self.spans(y))

    def across(self, a, b, y0, y1):
        """heights of the rules running all the way across a..b between y0 and y1 (double rules -> one)"""
        out = []
        for y in self.ys[bisect.bisect_left(self.ys, y0):bisect.bisect_right(self.ys, y1)]:
            if (not out or y - out[-1] > self.tol) and self.covers(y, a, b): out.append(y)
        return out


Table = collections.namedtuple('Table', 'rect rows sure side ys')   # side: only some of the region's columns (side by side with the title block); ys: the row rules


def _meet(A, B):
    """intersection of two lists of [lo, hi] runs"""
    return [[max(a, c), min(b, d)] for a, b in A for c, d in B if min(b, d) > max(a, c)]


def part_tables(D, words, R, I, rules=None):
    """part lists whose lower rows were caught in the top of a title-block region R: >= 3 nearly equally spaced rules
    spanning over half of R's width, crossed by >= 4 verticals running through all of them (>= 3 columns), content in
    >= 2 rows (an empty BOM, header only, stays furniture; so does a revision table). The table must end inside R (a grid
    reaching R's bottom is the title block's own); a header row (QTY/数量/序号) under data rows ends it. Sure only when the
    table goes on above R's top (R cut through it); one that just starts at R's top is a guess (title blocks have equal
    rows too), and is dropped when its words have no part-list header.
    Side by side: a part list and the title block next to it often share the same row rules, so the equal rows run
    across both. The table is only as wide as its rules run all the way across (merged title-block cells break them),
    and in those columns it may go on above/below on rules of its own (to R's bottom: header row last). When the rules
    leave two groups of columns, the one going on further (else the one with a part-list header / item numbers in the
    first column) is the part list. Such a table is also sure with a header or item numbers, or (vector text, no words)
    when its own rows run down to R's bottom. -> [Table]"""
    tol = 1.5; rules = rules or Rules(D, tol)
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
    def same_box(a, b, ref):   # the outer columns too
        c = cols(a, b)
        return same_cols(a, b, ref) and all(any(abs(x - e) < tol for x in c) for e in (ref[0], ref[-1]))
    def text(T):
        return ''.join(w[4] for w in words if T.contains(fitz.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))).upper().replace(' ', '')
    def grow(bd, c, g, tg):   # rows of the same height going on above / below bd on rules across the columns c only
        ry = rules.across(c[0], c[-1], I.y0 - tol, R.y1 + tol)
        dn = [y for y in ry if y > bd[-1] + tol]; up = [y for y in ry if y < bd[0] - tol][::-1]
        out = list(bd); k = 0
        while k < len(dn):
            h = dn[k] - out[-1]
            if abs(h - g) <= tg and same_box(out[-1], dn[k], c): out.append(dn[k]); k += 1; continue
            if h < g - tg and k + 1 < len(dn) and abs(dn[k + 1] - out[-1] - g) <= tg: k += 1; continue   # a stray rule splitting one row
            t = text(fitz.Rect(c[0], out[-1], c[-1], dn[k]))
            if h < 3 * g and same_box(out[-1], dn[k], c) and (any(k_ in t for k_ in HEAD_KW) or not t and (h <= 1.5 * g or dn[k] >= R.y1 - tol)):
                out.append(dn[k])   # a taller header row last
            break
        for y in up:
            h = out[0] - y
            if abs(h - g) <= tg and same_box(y, out[0], c): out.insert(0, y); continue
            if h < 3 * g and same_box(y, out[0], c): out.insert(0, y)   # a taller header row on top
            break
        return out
    def items(rows, c):   # item numbers (1, 2, ... / A, B, ...) down the first column
        return sum(bool(re.fullmatch(r'\d{1,3}|[A-Z]', text(fitz.Rect(c[0], a, c[1], b)))) for a, b in zip(rows, rows[1:])) >= 2
    def score(t):   # which group of columns is the part list: more rows, then a header / item numbers, then wider
        c, rows = t
        return (len(rows), any(k_ in text(fitz.Rect(c[0], a, c[-1], b)) for a, b in zip(rows, rows[1:]) for k_ in HEAD_KW)
                or items(rows, c), c[-1] - c[0])
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
        run = rules.spans(bd[0])
        for y in bd[1:]: run = _meet(run, rules.spans(y))
        cand = [cc for cc in ([x for x in c if lo - tol <= x <= hi + tol] for lo, hi in run) if len(cc) >= 4]
        def judge(c, bd, side):
            if side:
                up = bd[0] < band[0] - tol
            else:
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
            low = T.y1 >= R.y1 - tol
            if side:
                mark = bool(head) or items([r_.y0 for r_ in rows] + [T.y1], c)
                sure = (sure or mark   # vector text: own rows down to R's bottom
                        or not txt and low and sum(y > band[-1] + tol for y in bd) >= 2)
                if low and not mark and (txt or len(rows) < 6):   # down to R's bottom: header / item numbers, or (no words) a long list
                    return None   # else the title block's own grid
            if (T.y0 <= R.y0 + tol and R.y0 + tol < T.y1 and (not low or side) and full >= 2 and not rev
                    and (sure or head or not txt) and (sure or not side)):   # side by side: only when sure, else as before
                return Table(T, len(rows), sure, side, [r_.y0 for r_ in rows] + [T.y1])
        band = list(bd); t = None
        if cand and cand != [c]:   # the rules do not run across all the columns: pick the part list's
            grown = sorted(((cc, grow(bd, cc, g, tg)) for cc in cand), key=score, reverse=True)
            if ((len(grown) == 1 or score(grown[0])[:2] != score(grown[1])[:2])   # else nothing tells them apart
                    and (grown[0][0][0] > R.x0 + tol or grown[0][0][-1] < R.x1 - tol)):
                t = judge(grown[0][0], grown[0][1], True)
        t = t or judge(c, list(band), False)
        if t: out.append(t)
        i = max(nxt, i + 1)
    return out


def clear_tables(D, words, I, rects):
    """auto regions standing on the bottom frame (title block) must not swallow the part list above the title block:
    within the part list's columns every such region starts below the table's bottom rule (sure tables only; `alt`
    also clears the guessed ones); its columns beside a side-by-side part list stay (the title block), and go up
    with the rows they share with the part list above the region's top. Tables are looked for from the outermost
    regions. -> (regions, [Table], alt regions)"""
    tol = 1.5; found = []; rules = Rules(D, tol)
    def inside(q, o): return o.x0 <= q.x0 + tol and o.y0 <= q.y0 + tol and o.x1 >= q.x1 - tol and o.y1 >= q.y1 - tol
    bottom = [r for r in rects if r.y1 >= I.y1 - tol]
    for k, r in enumerate(bottom):
        if any(inside(r, o) and (not inside(o, r) or m < k) for m, o in enumerate(bottom) if m != k): continue
        for t in part_tables(D, words, r, I, rules):
            if not any(abs(t.rect.y1 - u.rect.y1) < tol and abs(t.rect.x0 - u.rect.x0) < tol for u in found): found.append(t)
    def clear(tabs):
        out = []
        for r in rects:
            if r.y1 < I.y1 - tol: out.append(fitz.Rect(r)); continue
            ps = [fitz.Rect(r)]
            for t in sorted(tabs, key=lambda t: t.rect.y0):
                T = t.rect; nx = []
                for q in ps:
                    if not t.side:   # as wide as the region: its top moves down to the table's bottom rule
                        if T.y0 - tol <= q.y0 < T.y1 - tol and min(q.x1, T.x1) - max(q.x0, T.x0) > 0.5 * min(q.width, T.width):
                            q = fitz.Rect(q.x0, T.y1, q.x1, q.y1)
                        nx.append(q); continue
                    if min(q.x1, T.x1) - max(q.x0, T.x0) <= tol or q.y0 >= T.y1 - tol: nx.append(q); continue
                    for s in ([q.x0, T.x0], [T.x1, q.x1]):   # beside the part list: title block
                        if s[1] - s[0] <= tol: continue
                        top = q.y0
                        for y in sorted((y for y in t.ys if y < top - tol), reverse=True):
                            if not rules.covers(y, *s): break
                            top = y
                        nx.append(fitz.Rect(s[0], top, s[1], q.y1))
                    if T.y1 < q.y1 - tol: nx.append(fitz.Rect(q.x0, T.y1, q.x1, q.y1))
                ps = nx
            out += ps
        return out
    return clear([t for t in found if t.sure]), found, clear(found)


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
    for t in tables:
        tb = t.rect; y = round((tb.y1 - I.y0) / I.height, 3)
        x = '–'.join(f'{min(1, max(0, (v - I.x0) / I.width)):.3f}' for v in (tb.x0, tb.x1))
        if t.sure and t.side: print(f'零件表（{t.rows} 行）和标题栏左右并排：只让开零件表那几列 x {x}（{"一直到底" if y >= 0.999 else f"到表格底线 {y}"}）；旁边的标题栏照样去掉（「去掉的区域」图上黄色）')
        elif t.sure: print(f'零件表（{t.rows} 行）被标题栏区域框进去了：区域上沿已收到表格底线 {y}（「去掉的区域」图上黄色）')
        else: print(f'红框上沿疑似零件表（{t.rows} 行，x {x}）：表格正好从红框上沿开始，也可能是标题栏自己的格子，没自动让开（图上橙框）')
    if any(not t.sure for t in tables): print('  是零件表就改用：', cli(to_frac(alt, I) + extra))
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
    for t in tables:   # part lists the regions now step around (yellow) / might have to (orange outline)
        sh.draw_rect(t.rect)
        if t.sure: sh.finish(color=(0.9, 0.7, 0), fill=(1, 0.9, 0), fill_opacity=0.35, width=max(0.3, I.width / 800))
        else: sh.finish(color=(1, 0.5, 0), dashes='[4] 0', width=max(0.6, I.width / 400))
    sh.commit()
    z = 1800 / max(op.rect.width, op.rect.height)
    op.get_pixmap(matrix=fitz.Matrix(z, z)).save(out / '草稿-去掉的区域.png')
    import cad_family as CF
    # the draft goes into the shared template file only while this render runs; other drafts may run at the same
    # time, so take a lock file around each edit and on the way out remove only our own entry (never rewrite the
    # whole file from an old copy -- that dropped or resurrected other runs' entries)
    lock = path.with_suffix('.lock')

    def edit(fn):
        for _ in range(600):
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY); break
            except FileExistsError:
                time.sleep(0.1)
        else:
            raise SystemExit(f'模板文件被锁住（{lock}），确认没有别的起草在跑后删掉它再试')
        try:
            T_ = json.loads(path.read_text(encoding='utf-8')); fn(T_['templates'])
            path.write_text(json.dumps(T_, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        finally:
            os.close(fd); os.remove(str(lock))
    had = T['templates'].get(a.name)
    try:
        edit(lambda t: t.__setitem__(a.name, tpl))
        job = {'source': str(Path(a.pdf).resolve()), 'model': '草稿', 'title': '连接器', 'template': a.name, 'rotate': a.rotate,
               'tolerance': {'linear_tolerances': [{'tier': 'X.', 'value': '±0.3'}], 'angular_tolerances': [], 'additional_tolerance_conditions': []}}
        import fonts as FT
        f, fi = FT.default_font()
        CF.run_layouts(job, out, f, fi)
    finally:
        edit(lambda t: t.__setitem__(a.name, had) if had is not None else t.pop(a.name, None))
    print('测试图', out / '草稿-原图对照.png')


if __name__ == '__main__':
    main()
