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
import os, argparse, collections, json, math, sys
from pathlib import Path
import pymupdf as fitz

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / 'engine'))
import frame as KF                      # noqa: E402
import dynamic_tolerance as DT          # noqa: E402
import brand as BR                      # noqa: E402

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


def analyse(page, furniture_frac=None, frame_bottom=None, clip=None, search=0.12):
    D = page.get_drawings()
    R_ = page.rect
    mx, my = 0.1 * R_.width, 0.1 * R_.height   # frames are often drawn slightly past the page edge: only drop objects far outside
    D = [d for d in D if R_.x0 - mx <= (d['rect'].x0 + d['rect'].x1) / 2 <= R_.x1 + mx and R_.y0 - my <= (d['rect'].y0 + d['rect'].y1) / 2 <= R_.y1 + my] or D   # stray objects far outside the page (editor stamps) must not stretch the frame search
    if clip:   # several sheets on one page: work on one of them
        C = fitz.Rect(clip); D = [d for d in D if C.contains(d['rect'])]
    bb = fitz.Rect()
    for d in D: bb |= d['rect']
    # inner frame: the innermost of the long border lines on each side
    H = long_lines(D, 'H', 0.6 * bb.width); V = long_lines(D, 'V', 0.6 * bb.height)
    tops = [y for _, _, y in H if y < bb.y0 + search * bb.height]
    bots = [y for _, _, y in H if y > bb.y1 - search * bb.height]
    lefs = [x for _, _, x in V if x < bb.x0 + search * bb.width]
    rigs = [x for _, _, x in V if x > bb.x1 - search * bb.width]
    if not (tops and bots and lefs and rigs): raise SystemExit('FRAME_NOT_FOUND')
    y1 = min(bots)
    if frame_bottom == 'title_top':
        # sheets whose title block spans the full width: stop the drawing area at the title block's top rule
        y1 = min([y for _, _, y in long_lines(D, 'H', 0.9 * bb.width) if y > bb.y1 - 0.2 * bb.height] or [y1])
    if frame_bottom == 'inner_ring':
        # sheets whose title-block rules also run full width: the inner frame is the second line from the outer edge
        cl = []
        for y in sorted(bots, reverse=True):
            if not cl or cl[-1] - y > 1.0: cl.append(y)
        if len(cl) >= 2: y1 = cl[1]
    I = fitz.Rect(max(lefs), max(tops), min(rigs), y1)
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


def _fix_encoding(o):
    """部分供应商 CAD 字体把 ± 和 ° 编成了 GBK 乱码（显示为 ¡À / ¡ã）；公差文字照抄时还原成 ± 和 °（只改符号，不改数值）"""
    if isinstance(o, str): return o.replace('¡À', '±').replace('¡ã', '°')
    if isinstance(o, list): return [_fix_encoding(x) for x in o]
    if isinstance(o, dict): return {k: _fix_encoding(v) for k, v in o.items()}
    return o


def run(job, out, font, font_index=0, brand='kangsheng'):
    if 'tolerance' in job: job = dict(job, tolerance=_fix_encoding(job['tolerance']))
    B = BR.load(brand)   # 品牌配置（默认康生，值与原先写死的完全一致）
    BLUE, GOLD, FRAME, RESERVED = B['blue'], B['gold'], B['frame'], B['reserved']
    model_in_job = job['model']
    if B.get('model_strip_prefixes'):   # 润擎：供应商自己的型号前缀（TF-/ND-/BG-）不上图、不进文件名
        job = dict(job, model=BR.brand_model(B, job['model']))
    KEEPOUT = [RESERVED] + [fitz.Rect(k) for k in B.get('keepout', [])]   # 视图必须避开的品牌图框元素（康生只有标题栏+公差栏）
    src_path = job['source']
    auto = None
    if not job.get('template') or job.get('template') == 'auto':
        # no template named: match the sheet's frame against every frame we have already made (see frame_match.py)
        import frame_match as fm
        auto = fm.match(src_path, job.get('clip'))
        if auto['status'] != 'OK':
            raise SystemExit(f"{auto['status']}: 图框和已登记的模板对不上或不唯一（最像 {auto['template']}，分数 {auto['score']}，次像 {auto['runner_up']}）。"
                             "按 SKILL.md「新建模板」做模板，再用 frame_match.py --learn 登记；不要硬做。")
        job = dict(job, template=auto['template'])
        if auto.get('search') and 'frame_search' not in job: job['frame_search'] = auto['search']
        if 'rotate' not in job: job['rotate'] = auto['rotate']
    probe = fitz.open(src_path)
    if probe[0].get_text('words'):
        # live text: convert glyphs to outlines so every character is carried as vector ink
        import subprocess, tempfile
        tmpd = Path(tempfile.mkdtemp()); ol = tmpd / 'outlined.pdf'
        pg0 = probe[0]; pr = pg0.rect
        # (a hairline's rect has zero width/height and Rect |= would skip it: test the corners instead)
        over = max([0] + [max(pr.x0 - q.x, q.x - pr.x1, pr.y0 - q.y, q.y - pr.y1)
                          for d in pg0.get_drawings() for q in (d['rect'].tl, d['rect'].br)])
        if 0.01 < over <= 12:   # only a frame drawn just past the edge; far-away stray objects are not widened for (keeps old outputs)
            # frame lines drawn just past the page edge would be clipped away by Ghostscript: widen the page first
            m = 12; big = fitz.open(src_path); bp = big[0]
            bp.set_mediabox(fitz.Rect(bp.mediabox.x0 - m, bp.mediabox.y0 - m, bp.mediabox.x1 + m, bp.mediabox.y1 + m))
            src_path = str(tmpd / 'widened.pdf'); big.save(src_path)
        subprocess.run(['gs', '-q', '-dNOPAUSE', '-dBATCH', '-dNoOutputFonts', '-sDEVICE=pdfwrite',
                        '-dFirstPage=1', '-dLastPage=1', f'-sOutputFile={ol}', src_path], check=True)
        src_path = str(ol)
    src = fitz.open(src_path); page = src[0]
    if job.get('rotate'):
        page.set_rotation((page.rotation + int(job['rotate'])) % 360)
    if page.rotation:
        page.remove_rotation()   # work in the upright drawing orientation
    ff = job.get('furniture_frac'); tpl = {}
    if job.get('template'):
        tpl = json.loads((ROOT / 'families' / 'cad_templates.json').read_text())['templates'][job['template']]
        ff = job.get('furniture_frac') or tpl['furniture_frac']   # a job may override the template for one sheet (SKILL: furniture_frac 临时覆盖)
    D, bb, I, furn = analyse(page, ff, job.get('frame_bottom') or tpl.get('frame_bottom'), job.get('clip'), job.get('frame_search') or tpl.get('frame_search', 0.12))
    keep, dropped = [], collections.Counter()
    def trim(it):
        # an axis-aligned rule running from the drawing into a removed supplier area stops at that area's edge
        if it[0] != 'l' or not furn: return it
        a_, b_ = it[1], it[2]
        for q in furn:
            qa, qb = fitz.Rect(q.x0 - 0.6, q.y0 - 0.6, q.x1 + 0.6, q.y1 + 0.6).contains(a_), fitz.Rect(q.x0 - 0.6, q.y0 - 0.6, q.x1 + 0.6, q.y1 + 0.6).contains(b_)
            if qa == qb: continue
            inn, out = (a_, b_) if qa else (b_, a_)
            if abs(a_.y - b_.y) < 0.3 and q.y0 + 0.6 < inn.y:   # horizontal, entering through a side edge
                x = q.x0 if out.x < q.x0 else q.x1
                if abs(inn.x - x) > 1: return ('l', out, fitz.Point(x, inn.y))
            elif abs(a_.x - b_.x) < 0.3 and q.x0 + 0.6 < inn.x < q.x1 - 0.6:   # vertical, entering through top/bottom
                y = q.y0 if out.y < q.y0 else q.y1
                if abs(inn.y - y) > 1: return ('l', out, fitz.Point(inn.x, y))
        return it
    for d in D:
        if furn and any(it[0] == 'l' for it in d['items']):
            its = [trim(it) for it in d['items']]
            if any(a is not b for a, b in zip(its, d['items'])):
                P_ = [q for it in its for q in it[1:] if isinstance(q, fitz.Point)]
                d = dict(d); d['items'] = its; dropped['rule_trimmed'] += 1
                if P_: d['rect'] = fitz.Rect(min(q.x for q in P_), min(q.y for q in P_), max(q.x for q in P_), max(q.y for q in P_))
        r = d['rect']
        fp = tpl.get('frame_pad', 0.5)   # tables drawn up to the outer frame line need a little more slack
        if not fitz.Rect(I.x0 - fp, I.y0 - fp, I.x1 + fp, I.y1 + fp).contains(r):
            dropped['frame_band'] += 1; continue
        if r.width > 0.9 * I.width or r.height > 0.9 * I.height:
            dropped['frame_rule'] += 1; continue
        # a long straight rule lying on the inner-frame edge (e.g. the title-block top line) is frame, not drawing
        if all(it[0] == 'l' for it in d['items']) and (
                (r.height < 0.6 and r.width > 2.0 and min(abs(r.y0 - I.y0), abs(r.y1 - I.y1)) < 1.0) or
                (r.width < 0.6 and r.height > 2.0 and min(abs(r.x0 - I.x0), abs(r.x1 - I.x1)) < 1.0)):
            dropped['frame_rule'] += 1; continue
        if inside_any(r, furn):
            dropped['title_or_rev'] += 1; continue
        # a path may mix drawing strokes with supplier-frame strokes: drop the individual items that
        # lie wholly inside a supplier title/revision area (both ends of a line inside)
        if furn and len(d['items']) > 1:
            def pts(it):
                if it[0] == 'l': return [it[1], it[2]]
                if it[0] == 'c': return [it[1], it[4]]
                if it[0] == 're': return [it[1].tl, it[1].br]
                if it[0] == 'qu': return [it[1].ul, it[1].lr]
                return []
            def inside(pt):
                return any(fitz.Rect(q.x0 - 0.6, q.y0 - 0.6, q.x1 + 0.6, q.y1 + 0.6).contains(pt) for q in furn)
            its = [it for it in d['items'] if not (pts(it) and all(inside(p_) for p_ in pts(it)))]
            if not its:
                dropped['title_or_rev'] += 1; continue
            if len(its) != len(d['items']):
                d = dict(d); d['items'] = its
                P_ = [p_ for it in its for p_ in pts(it)]
                d['rect'] = fitz.Rect(min(q.x for q in P_), min(q.y for q in P_), max(q.x for q in P_), max(q.y for q in P_))
                dropped['title_items'] += 1
        elif furn and len(d['items']) == 1 and d['items'][0][0] == 'l':
            a_, b_ = d['items'][0][1], d['items'][0][2]
            if any(fitz.Rect(q.x0 - 0.6, q.y0 - 0.6, q.x1 + 0.6, q.y1 + 0.6).contains(a_) and
                   fitz.Rect(q.x0 - 0.6, q.y0 - 0.6, q.x1 + 0.6, q.y1 + 0.6).contains(b_) for q in furn):
                dropped['title_or_rev'] += 1; continue
        keep.append(d)
    if not keep: raise SystemExit('NOTHING_TO_PLACE')
    # a table standing on the supplier title block shares its bottom rule with the block: when three or more
    # kept vertical rules end on a removed area's top edge, redraw the bottom rule between them
    # (the inner frame's bottom edge counts too: a parts table may stand on the frame itself)
    u_ = max(1.0, max(I.width, I.height) / 1190)   # row-spacing windows grow with sheets drawn in large units (A3 and smaller: 1)
    for q in list(furn) + [fitz.Rect(I.x0, I.y1, I.x1, I.y1 + 1)]:
        ends = []
        for d in keep:
            for it in d['items']:
                if it[0] != 'l' or abs(it[1].x - it[2].x) > 0.3: continue
                yb = max(it[1].y, it[2].y)
                if abs(yb - q.y0) < 1.0 and q.x0 - 0.6 <= it[1].x <= q.x1 + 0.6 and abs(it[1].y - it[2].y) > 2: ends.append((it[1].x, yb, d))
        hrules = [(min(it[1].x, it[2].x), max(it[1].x, it[2].x), it[1].y) for d in keep for it in d['items']
                  if it[0] == 'l' and abs(it[1].y - it[2].y) < 0.3]
        groups = []
        for yb in sorted({round(e[1] * 2) / 2 for e in ends}):
            row = sorted((e for e in ends if abs(e[1] - yb) < 0.6), key=lambda e: e[0])
            cur = row[:1]
            for a_, b_ in zip(row, row[1:]):   # neighbouring columns must be tied together by a row rule of the table
                if any(h[0] <= a_[0] + 0.6 and h[1] >= b_[0] - 0.6 and yb - 60 * u_ < h[2] < yb - 1 for h in hrules): cur.append(b_)
                else: groups.append(cur); cur = [b_]
            if cur: groups.append(cur)
        for grp in groups:
            if len(grp) < 3: continue
            x0 = min(e[0] for e in grp); x1 = max(e[0] for e in grp); src_ = grp[0][2]; yb_ = grp[0][1]
            # the table's row rules may run past the outer columns (their border was the supplier frame): follow them
            rows = [(min(it[1].x, it[2].x), max(it[1].x, it[2].x), it[1].y) for d in keep for it in d['items']
                    if it[0] == 'l' and abs(it[1].y - it[2].y) < 0.3 and yb_ - 60 * u_ < it[1].y < yb_ - 1
                    and min(it[1].x, it[2].x) <= x0 + 0.6 and max(it[1].x, it[2].x) >= x1 - 0.6]   # full-width row rules only
            new_items = []
            for side in (0, 1):
                ends = [r_[side] for r_ in rows]
                if not ends: continue
                far = min(ends) if side == 0 else max(ends)
                same = [r_ for r_ in rows if abs(r_[side] - far) < 0.6]
                if len(same) >= 2 and (far < x0 - 1 if side == 0 else far > x1 + 1):
                    new_items.append(('l', fitz.Point(far, min(r_[2] for r_ in same)), fitz.Point(far, yb_)))
                    if side == 0: x0 = far
                    else: x1 = far
            new_items.append(('l', fitz.Point(x0, yb_), fitz.Point(x1, yb_)))
            keep.append({'items': new_items, 'type': 's', 'color': src_.get('color'), 'width': src_.get('width'),
                         'closePath': False, 'rect': fitz.Rect(x0, min(min(i_[1].y, i_[2].y) for i_ in new_items) - 0.01, x1, yb_ + 0.01)})
            dropped['table_base_restored'] += 1
    # a table drawn against the supplier frame's left/right edge uses the frame line as its outer border (which is
    # removed with the frame): when three or more row rules sharing one start end on that edge, redraw the border
    for xe, side in ((I.x1, 1), (I.x0, 0)):
        rows = [(min(it[1].x, it[2].x), max(it[1].x, it[2].x), it[1].y, d) for d in keep for it in d['items']
                if it[0] == 'l' and abs(it[1].y - it[2].y) < 0.3 and abs(it[1].x - it[2].x) > 2]
        rows = [r_ for r_ in rows if abs(r_[side] - xe) < 1.0]
        by_start = collections.defaultdict(list)
        for r_ in rows: by_start[round(r_[1 - side] / 0.6)].append(r_)
        for grp in by_start.values():
            grp.sort(key=lambda r_: r_[2]); cl = [grp[:1]]
            for a_, b_ in zip(grp, grp[1:]):
                if b_[2] - a_[2] < 40 * u_: cl[-1].append(b_)
                else: cl.append([b_])
            for c_ in cl:
                if len(c_) < 3: continue
                y0_, y1_ = c_[0][2], c_[-1][2]; x_ = sum(r_[side] for r_ in c_) / len(c_)
                vs = [(it[1].x, it[1].y, it[2].y) for d in keep for it in d['items'] if it[0] == 'l' and abs(it[1].x - it[2].x) < 0.3]
                vs += [(xx, q.y0, q.y1) for d in keep for it in d['items'] if it[0] in ('re', 'qu')
                       for q in [it[1] if it[0] == 're' else it[1].rect] for xx in (q.x0, q.x1)]   # rectangle edges are borders too
                cov = sum(max(0, min(y1_, max(a, b)) - max(y0_, min(a, b))) for x, a, b in vs if abs(x - x_) < 0.6)
                if cov >= 0.95 * (y1_ - y0_): continue
                src_ = c_[0][3]
                keep.append({'items': [('l', fitz.Point(x_, y0_), fitz.Point(x_, y1_))], 'type': 's', 'color': src_.get('color'),
                             'width': src_.get('width'), 'closePath': False, 'rect': fitz.Rect(x_ - 0.01, y0_, x_ + 0.01, y1_)})
                dropped['table_side_restored'] += 1
    # colours: neutral + the dominant annotation colour -> blue; every other colour -> gold
    cnt = collections.Counter()
    for d in keep:
        c = d.get('color') if d['type'] != 'f' else d.get('fill')
        if c is not None and not is_neutral(c): cnt[ckey(c)] += 1
    dominant = cnt.most_common(1)[0][0] if cnt else None
    if job.get('dominant_colour'):   # a series keeps one colour mapping across all its sheets
        dominant = ckey(tuple(job['dominant_colour']))
    GREEN = (0.0, 1.0, 0.0)
    def mapc(c):
        if c is None: return None
        if is_neutral(c) or ckey(c) in (dominant, GREEN): return BLUE
        return GOLD
    cb = fitz.Rect()
    for d in keep: cb |= d['rect']
    # ---- blocks: cluster paths by proximity
    g = 0.015 * cb.width
    if tpl.get('split_paths'):
        # some exports bundle unrelated strokes (a leader line and a table rule far apart) into one path:
        # split such paths into their spatially separate pieces so they do not glue blocks together
        def ibox(it):
            P_ = [q for q in it[1:] if isinstance(q, fitz.Point)]
            if not P_ and it[0] in ('re', 'qu'): return fitz.Rect(it[1].rect if it[0] == 'qu' else it[1])
            return fitz.Rect(min(q.x for q in P_), min(q.y for q in P_), max(q.x for q in P_), max(q.y for q in P_))
        split_ = []
        for d in keep:
            its = d['items']
            if len(its) < 2 or (d['rect'].width < 6 * g and d['rect'].height < 6 * g):
                split_.append(d); continue
            bx = [ibox(it) for it in its]; pa = list(range(len(its)))
            def fr(i):
                while pa[i] != i: pa[i] = pa[pa[i]]; i = pa[i]
                return i
            for i in range(len(its)):
                for j in range(i + 1, len(its)):
                    if bx[j].x0 <= bx[i].x1 + g and bx[i].x0 <= bx[j].x1 + g and bx[j].y0 <= bx[i].y1 + g and bx[i].y0 <= bx[j].y1 + g:
                        pa[fr(j)] = fr(i)
            grp = collections.defaultdict(list)
            for i in range(len(its)): grp[fr(i)].append(i)
            if len(grp) == 1: split_.append(d); continue
            for idx in grp.values():
                e = dict(d); e['items'] = [its[i] for i in idx]; e['closePath'] = False
                e['rect'] = fitz.Rect(min(bx[i].x0 for i in idx), min(bx[i].y0 for i in idx),
                                      max(bx[i].x1 for i in idx), max(bx[i].y1 for i in idx)); split_.append(e)
            dropped['paths_split'] += 1
        keep = split_
        cb = fitz.Rect()
        for d in keep: cb |= d['rect']
    rects = [fitz.Rect(d['rect']) for d in keep]
    lfix = tpl.get('line_extent_fix') or B.get('line_extent_fix')   # 品牌也可打开（润擎：右栏顶上有修订栏，范围不能算小）
    for r in (rects if lfix else []):   # a straight line has a zero-width box, which Rect unions silently ignore: give it a hair of size
        if r.width < 0.02: r.x0 -= 0.01; r.x1 += 0.01
        if r.height < 0.02: r.y0 -= 0.01; r.y1 += 0.01
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
        def gap(q, r=b_['r']):   # edge-to-edge distance first (0 when touching), centre distance breaks ties
            dx = max(q['r'].x0 - r.x1, r.x0 - q['r'].x1, 0); dy = max(q['r'].y0 - r.y1, r.y0 - q['r'].y1, 0)
            return (dx + dy, abs((q['r'].x0 + q['r'].x1) / 2 - (r.x0 + r.x1) / 2)
                    + abs((q['r'].y0 + q['r'].y1) / 2 - (r.y0 + r.y1) / 2))
        near = min(big, key=gap)
        near['idx'] += b_['idx']; near['r'] |= b_['r']
    blocks = big or blocks
    # a block made only of one or two straight lines is a stray piece of the supplier frame
    def stray(b_):
        its = [it for i in b_['idx'] for it in keep[i]['items']]
        return all(it[0] == 'l' for it in its) and (len(its) <= 2 or (len(its) <= 6 and min(b_['r'].width, b_['r'].height) < 2.0))
    if len(blocks) > 1:
        blocks = [b_ for b_ in blocks if not stray(b_)]
        cb = fitz.Rect()
        for b_ in blocks: cb |= b_['r']
    # a caption (short, flat block) sitting right under a drawing belongs to it, e.g. "P.C.B LAYOUT / TOLERANCE"
    if len(blocks) > 1:
        for b_ in sorted(blocks, key=lambda q: q['r'].height):
            r = b_['r']
            if r.height > tpl.get('caption_h', 0.06) * cb.height or b_ not in blocks: continue
            def close_above(q):   # some path of q sits just above the caption and overlaps it horizontally
                if tpl.get('caption_block') and 0 <= r.y0 - q['r'].y1 < tpl.get('caption_gap', 0.03) * cb.height \
                        and min(r.x1, q['r'].x1) - max(r.x0, q['r'].x0) > 0.3 * r.width:
                    return True   # judged on the whole block: a PCB pattern ends in many tiny pads, none wide enough by itself
                return any(0 <= r.y0 - rects[i].y1 < tpl.get('caption_gap', 0.03) * cb.height and min(r.x1, rects[i].x1) - max(r.x0, rects[i].x0) > 0.3 * r.width
                           for i in q['idx'])
            above = [q for q in blocks if q is not b_ and close_above(q)]
            if above:
                q = above[0]
                if tpl.get('caption_block'):   # several drawings above: the caption belongs to the one it sits under most
                    q = max(above, key=lambda q: min(r.x1, q['r'].x1) - max(r.x0, q['r'].x0))
                q['idx'] += b_['idx']; q['r'] |= r; blocks.remove(b_)
    # a drawing's caption that ended up glued to a table just below it (e.g. "RECOMMENDED PCB LAYOUT / TOP VIEW" a hair
    # closer to a parts table than the clustering gap): the flat strip above the table's top rule goes back to the drawing
    if len(blocks) > 1 and B.get('caption_unglue'):   # brand option (RunQing): on Kangsheng sheets it shrinks the views to clear the title block
        for b_ in list(blocks):
            hr = sorted((min(it[1].x, it[2].x), max(it[1].x, it[2].x), it[1].y) for i in b_['idx'] for it in keep[i]['items']
                        if it[0] == 'l' and abs(it[1].y - it[2].y) < 0.3 and abs(it[1].x - it[2].x) > 0.3 * b_['r'].width)
            if len(hr) < 3: continue
            ytop = min(h[2] for h in hr)
            cap = [i for i in b_['idx'] if rects[i].y1 < ytop - 0.5]
            if not cap or len(cap) == len(b_['idx']): continue
            cr = fitz.Rect()
            for i in cap: cr |= rects[i]
            if cr.is_empty or cr.height > 0.06 * cb.height or any(rects[i].y0 < cr.y1 and rects[i].y1 > cr.y0 for i in b_['idx'] if i not in cap):
                continue   # not a flat strip clear of the table
            ups = [q for q in blocks if q is not b_ and 0 <= cr.y0 - q['r'].y1 < 0.06 * cb.height
                   and min(cr.x1, q['r'].x1) - max(cr.x0, q['r'].x0) > 0.3 * cr.width]
            if not ups: continue
            q = max(ups, key=lambda q: min(cr.x1, q['r'].x1) - max(cr.x0, q['r'].x0))
            b_['idx'] = [i for i in b_['idx'] if i not in cap]; b_['r'] = fitz.Rect()
            for i in b_['idx']: b_['r'] |= rects[i]
            q['idx'] += cap; q['r'] |= cr
            dropped['caption_unglued'] += 1
    if os.environ.get('CAD_DEBUG'):
        for b_ in blocks:
            print('BLOCK', [round(v, 1) for v in b_['r']], len(b_['idx']), file=sys.stderr)
            if os.environ.get('CAD_DEBUG') == '2':
                for i in b_['idx']:
                    if rects[i].width > 150 or rects[i].height > 150 or rects[i].y1 > 555: print('   ', [round(v, 1) for v in rects[i]], len(keep[i]['items']), keep[i]['items'][:2], file=sys.stderr)
        print('CB', [round(v, 1) for v in cb], file=sys.stderr)
        for d in keep:
            P_ = [q for it in d['items'] for q in it[1:] if isinstance(q, fitz.Point)]
            if P_ and max(q.x for q in P_) > d['rect'].x1 + 1: print('STALE', [round(v, 1) for v in d['rect']], d['items'][:2], file=sys.stderr)
        for d in keep:
            if len(d['items']) == 2 and all(it[0] == 'l' for it in d['items']) and d['rect'].width > 20 and d['rect'].height > 20:
                print('  L', [round(v, 1) for v in d['rect']], file=sys.stderr)
        print('I', [round(v, 1) for v in I], [[round(v, 1) for v in q] for q in furn], file=sys.stderr)
    # a small piece sitting on the same line right next to a bigger block (e.g. the end of a note line) joins it
    if len(blocks) > 1 and tpl.get('join_line_pieces'):
        for b_ in sorted(blocks, key=lambda q: q['r'].width * q['r'].height):
            r = b_['r']
            if b_ not in blocks or r.width * r.height > 0.01 * cb.width * cb.height: continue
            side = [q for q in blocks if q is not b_ and q['r'].width * q['r'].height > r.width * r.height
                    and any(min(r.y1, rects[i].y1) - max(r.y0, rects[i].y0) > 0.5 * r.height
                            and min(abs(r.x0 - rects[i].x1), abs(rects[i].x0 - r.x1)) < 0.04 * cb.width for i in q['idx'])]
            if os.environ.get('CAD_DEBUG'): print('SMALL', [round(v, 1) for v in r], bool(side), file=sys.stderr)
            if side:
                q = side[0]; q['idx'] += b_['idx']; q['r'] |= r; blocks.remove(b_)
    for b_ in (blocks if lfix else []):   # block extents follow their paths
        b_['r'] = fitz.Rect()
        for i in b_['idx']: b_['r'] |= rects[i]
    if lfix:
        cb = fitz.Rect()
        for b_ in blocks: cb |= b_['r']
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
    # tables standing at the bottom of the source sheet (under the views, left of its title block) go to the free
    # bottom-left slot beside the Kangsheng title block instead of forcing the whole view group to shrink
    slot = []
    if tpl.get('bottom_slot') or job.get('bottom_slot'):
        # a parts table standing on the bottom edge beside the supplier title block often touches the views
        # above it: split it off when the part below the title-block top is a real table (several full rules)
        ycut = I.y0 + tpl.get('table_band_top', 0.815) * I.height - 2
        for b_ in list(views):
            low = [i for i in b_['idx'] if rects[i].y0 >= ycut]
            if not low or len(low) == len(b_['idx']): continue
            lr = fitz.Rect()
            for i in low: lr |= rects[i]
            rules = sum(1 for i in low for it in keep[i]['items'] if it[0] == 'l' and abs(it[1].y - it[2].y) < 0.3
                        and abs(it[1].x - it[2].x) > 0.6 * lr.width)
            if rules >= 3:
                # the table may start above the cut (header rows): its vertical rules crossing the cut mark its top
                cross = [rects[i].y0 for i in b_['idx'] if i not in low and rects[i].width < 0.6 and rects[i].y1 > ycut
                         and lr.x0 - 3 <= rects[i].x0 <= lr.x1 + 3]
                if cross:
                    top = min(cross) - 1
                    low = [i for i in b_["idx"] if fitz.Rect(lr.x0 - 3, top, lr.x1 + 3, lr.y1 + 1).contains(rects[i])]
                    lr = fitz.Rect()
                    for i in low: lr |= rects[i]
                tr = fitz.Rect(lr.x0 - 3, lr.y0 - 1, lr.x1 + 3, lr.y1 + 1)
                for q in views:   # pieces of the table that clustered into other blocks come along
                    if q is b_: continue
                    mv = [i for i in q['idx'] if tr.contains(rects[i])]
                    if mv and len(mv) < len(q['idx']):
                        q['idx'] = [i for i in q['idx'] if i not in mv]; q['r'] = fitz.Rect()
                        for i in q['idx']: q['r'] |= rects[i]
                        low += mv
                b_['idx'] = [i for i in b_['idx'] if i not in low]; b_['r'] = fitz.Rect()
                for i in b_['idx']: b_['r'] |= rects[i]
                views.append({'idx': low, 'r': lr})
    if os.environ.get('CAD_DEBUG') == '4':
        for b_ in views:
            for i in b_['idx']:
                if rects[i].width > 100 and rects[i].height < 2: print('VH', [round(v, 1) for v in b_['r']], [round(v, 1) for v in rects[i]], keep[i]['items'], file=sys.stderr)
    if (tpl.get('bottom_slot') or job.get('bottom_slot')) and len(views) > 1:
        slot = [b_ for b_ in views if b_['r'].y0 >= cb.y0 + 0.72 * cb.height]
        if len(slot) == len(views): slot = []
        views = [b_ for b_ in views if b_ not in slot]
    pullset = set()
    if tpl.get('rail_pull'):
        # house rule for sheets that draw the pin table beside the views: it joins the right column, on top
        for a_, b2, c_, d_ in tpl['rail_pull']:
            pr = fitz.Rect(I.x0 + a_ * I.width, I.y0 + b2 * I.height, I.x0 + c_ * I.width, I.y0 + d_ * I.height)
            mv = []
            for q in list(views) + list(rail) + list(slot):
                got = [i for i in q['idx'] if rects[i].x0 >= pr.x0 and rects[i].x1 <= pr.x1 and rects[i].y0 >= pr.y0 and rects[i].y1 <= pr.y1]   # (a line's box is empty: Rect.contains would skip it)
                if got:
                    q['idx'] = [i for i in q['idx'] if i not in got]; mv += got
            if mv:
                pullset |= set(mv)
                rail.append({'idx': mv, 'r': fitz.Rect(min(rects[i].x0 for i in mv), min(rects[i].y0 for i in mv),
                                                        max(rects[i].x1 for i in mv), max(rects[i].y1 for i in mv))})
        for lst in (views, rail, slot):
            lst[:] = [q for q in lst if q['idx']]
            for q in lst:
                q['r'] = fitz.Rect()
                for i in q['idx']: q['r'] |= rects[i]
    mats = {}
    RAIL = fitz.Rect(B['rail'].x0, B['rail'].y0, B['rail'].x1 - tpl.get('rail_margin', 0), B['rail'].y1)
    if rail:
        # the right column moves as ONE unit (notes, tables and ordering diagrams keep their relative layout)
        rb = fitz.Rect()
        for b_ in rail: rb |= b_['r']
        rw = RAIL.x1 - B['rail_min_x']          # the rail may widen leftwards up to x=524 (same limit as the Zhiyuan rule)
        sc = min(rw / rb.width, RAIL.height / rb.height)
        uni = min((FRAME.width - 20) / cb.width, (FRAME.height - 20) / cb.height)
        if tpl.get('rail_cap'): sc = min(sc, tpl['rail_cap'] * uni)   # notes never blow up far beyond the drawing's scale
        if sc < 0.6 * uni or (job.get('layout') or tpl.get('layout')) == 'sheet':   # rail would shrink the notes/tables more than the whole sheet would: keep sheet layout
            views, rail = views + rail, []
        x = RAIL.x1 - rb.width * sc; y = RAIL.y0
        if os.environ.get("CAD_DEBUG"): print("RAIL", [round(v, 1) for v in rb], sc, [(round(rects[i].x1,1), keep[i]["items"][:1]) for b_ in rail for i in b_["idx"] if rects[i].x1 > 233], file=sys.stderr)
        m_ = fitz.Matrix(sc, 0, 0, sc, x - rb.x0 * sc, y - rb.y0 * sc)
        for b_ in rail:
            for i in b_['idx']: mats[i] = (m_, sc)
        if rail and tpl.get('rail_stack') and len(rail) > 1:
            # house rule: pin/dimension table top-right, performance notes stacked directly below it
            gap = 8.0
            def is_table(b_):
                return sum(1 for i in b_['idx'] for it in keep[i]['items'] if it[0] == 'l' and abs(it[1].y - it[2].y) < 0.3
                           and abs(it[1].x - it[2].x) > 0.6 * b_['r'].width) >= 4
            rbp = fitz.Rect(rb.x0 - 1, rb.y0 - 1, rb.x1 + 1, rb.y1 + 1)
            for b_ in views:   # pieces of the table (grid lines) that were merged into a view block travel with the rail
                mv = [i for i in b_['idx'] if rbp.contains(rects[i])]
                if mv:
                    b_['idx'] = [i for i in b_['idx'] if i not in mv]
                    rail.append({'idx': mv, 'r': fitz.Rect(min(rects[i].x0 for i in mv), min(rects[i].y0 for i in mv),
                                                            max(rects[i].x1 for i in mv), max(rects[i].y1 for i in mv))})
            views = [b_ for b_ in views if b_['idx']]
            for b_ in views:
                b_['r'] = fitz.Rect()
                for i in b_['idx']: b_['r'] |= rects[i]
            units = []   # blocks whose boxes overlap (a table's grid and its text) move together
            for b_ in sorted(rail, key=lambda q: -q['r'].width * q['r'].height):
                hit = next((u for u in units if fitz.Rect(u['r'].x0 - 2, u['r'].y0 - 2, u['r'].x1 + 2, u['r'].y1 + 2).intersects(b_['r'])), None)
                if hit: hit['idx'] = hit['idx'] + b_['idx']; hit['r'] = hit['r'] | b_['r']
                else: units.append({'idx': list(b_['idx']), 'r': fitz.Rect(b_['r'])})
            grew = True
            while grew:
                grew = False
                for u in units:
                    for v in units:
                        if u is not v and u['r'].intersects(v['r']):
                            u['idx'] += v['idx']; u['r'] |= v['r']; units.remove(v); grew = True; break
                    if grew: break
            order = sorted(units, key=lambda b_: (not any(i in pullset for i in b_['idx']), not is_table(b_), b_['r'].y0))
            wmax = max(b_['r'].width for b_ in order); htot = sum(b_['r'].height for b_ in order) + gap * (len(order) - 1)
            sc = min(rw / wmax, RAIL.height / htot)
            if tpl.get('rail_cap'): sc = min(sc, tpl['rail_cap'] * uni)
            if os.environ.get('CAD_DEBUG'): print('STACK', [[round(v, 1) for v in u['r']] for u in order], file=sys.stderr)
            y = RAIL.y0; xl = RAIL.x1
            for b_ in order:
                x_ = RAIL.x1 - b_['r'].width * sc; xl = min(xl, x_)
                mm = fitz.Matrix(sc, 0, 0, sc, x_ - b_['r'].x0 * sc, y - b_['r'].y0 * sc)
                for i in b_['idx']: mats[i] = (mm, sc)
                y += b_['r'].height * sc + gap
            x = xl
        rail_x0 = x - 10 if rail else B['views_area'].x1
    else:
        rail_x0 = B['views_area'].x1
    # views keep their arrangement, enlarged uniformly into the left area, clear of the title block
    vb = fitz.Rect()
    for b_ in views: vb |= b_['r']
    area = fitz.Rect(B['views_area'].x0, B['views_area'].y0, rail_x0, B['views_area_bottom_with_slot'] if slot else B['views_area'].y1)
    s = min(area.width / vb.width, area.height / vb.height)
    placed = None
    s_floor = min(0.2, 0.2 * s)   # sources drawn at 1:1 in metres/large units start far below 1
    def _fit_search(placed):
        # sheets whose views fill the whole page: instead of shrinking around the centre until the drawing clears the
        # Kangsheng title block, look for the largest scale at which SOME position clears it (real line geometry, not path boxes)
        import numpy as np
        cs = 2.0
        Wc = int(vb.width / cs) + 3; Hc = int(vb.height / cs) + 3
        occ = np.zeros((Hc, Wc), dtype=np.int32)
        for b_ in views:
            for i in b_['idx']:
                for it in keep[i]['items']:
                    if it[0] == 'l': pts = [it[1], it[2]]
                    elif it[0] == 'c': pts = it[1:5]
                    elif it[0] == 're': pts = [it[1].tl, it[1].br]
                    elif it[0] == 'qu': pts = [it[1].ul, it[1].lr, it[1].ur, it[1].ll]
                    else: continue
                    x0_ = min(q.x for q in pts); x1_ = max(q.x for q in pts); y0_ = min(q.y for q in pts); y1_ = max(q.y for q in pts)
                    occ[max(0, int((y0_ - vb.y0) / cs)):int((y1_ - vb.y0) / cs) + 1, max(0, int((x0_ - vb.x0) / cs)):int((x1_ - vb.x0) / cs) + 1] = 1
        P = np.zeros((Hc + 1, Wc + 1), dtype=np.int64); P[1:, 1:] = occ.cumsum(0).cumsum(1)
        def busy(sx0, sy0, sx1, sy1):
            gx0 = max(0, int(np.floor((sx0 - vb.x0) / cs))); gy0 = max(0, int(np.floor((sy0 - vb.y0) / cs)))
            gx1 = min(Wc, int(np.ceil((sx1 - vb.x0) / cs))); gy1 = min(Hc, int(np.ceil((sy1 - vb.y0) / cs)))
            if gx1 <= gx0 or gy1 <= gy0: return False
            return (P[gy1, gx1] - P[gy0, gx1] - P[gy1, gx0] + P[gy0, gx0]) > 0
        KS = [fitz.Rect(q.x0 - 3, q.y0 - 3, q.x1 + 3, q.y1 + 3) for q in KEEPOUT]
        s_try = s
        while s_try > s_floor and placed is None:
            w_, h_ = vb.width * s_try, vb.height * s_try
            xs = np.linspace(area.x0, max(area.x0, area.x1 - w_), 25); ys = np.linspace(area.y0, max(area.y0, area.y1 - h_), 25)
            cx_, cy_ = area.x0 + (area.width - w_) / 2, area.y0 + max(0, (area.height - h_) / 2)
            cand = sorted(((ox_ - cx_) ** 2 + (oy_ - cy_) ** 2, ox_, oy_) for ox_ in xs for oy_ in ys)
            for _, ox_, oy_ in cand:
                if not any(busy((RS.x0 - ox_) / s_try + vb.x0, (RS.y0 - oy_) / s_try + vb.y0, (RS.x1 - ox_) / s_try + vb.x0, (RS.y1 - oy_) / s_try + vb.y0) for RS in KS):
                    placed = (s_try, fitz.Matrix(s_try, 0, 0, s_try, ox_ - vb.x0 * s_try, oy_ - vb.y0 * s_try)); break
            s_try *= 0.99
        return placed
    if tpl.get('fit_search'): placed = _fit_search(placed)
    while placed is None and s > s_floor:
        ox = area.x0 + (area.width - vb.width * s) / 2; oy = area.y0 + max(0, (area.height - vb.height * s) / 2)
        if tpl.get('views_top'):   # tall view stacks: hang from the top so only the bottom has to clear the title block
            oy = area.y0
        m = fitz.Matrix(s, 0, 0, s, ox - vb.x0 * s, oy - vb.y0 * s)
        if not any((rects[i] * m).intersects(K) for K in KEEPOUT for b_ in views for i in b_['idx']):
            placed = (s, m); break
        s *= 0.98
    if placed is None and not tpl.get('fit_search'):   # views fill the sheet and the plain shrink-around-centre cannot clear the title block: search positions instead of giving up
        placed = _fit_search(placed)
    if placed is None:
        if os.environ.get('CAD_DEBUG'): print('NOROOM', vb, area, s, RESERVED, len(views), file=sys.stderr)
        raise SystemExit('NO_ROOM')
    s, m = placed
    for b_ in views:
        for i in b_['idx']: mats[i] = (m, s)
    if slot:
        sb = fitz.Rect()
        for b_ in slot: sb |= b_['r']
        SLOT = fitz.Rect(B['slot'])
        ss = min(SLOT.width / sb.width, SLOT.height / sb.height, s)
        ms = fitz.Matrix(ss, 0, 0, ss, SLOT.x0 + (SLOT.width - sb.width * ss) / 2 - sb.x0 * ss,
                         SLOT.y0 + (SLOT.height - sb.height * ss) / 2 - sb.y0 * ss)
        for b_ in slot:
            for i in b_['idx']: mats[i] = (ms, ss)
    doc = fitz.open(); pg = doc.new_page(width=KF.PAGE[0], height=KF.PAGE[1])
    if B.get('background'): pg.insert_image(pg.rect, filename=str(ROOT / B['background']))
    # embedded raster images inside the drawing area (e.g. a moulded marking drawn as a picture) are technical content:
    # each travels with the block it overlaps most (or the nearest one), under the vector strokes
    images_placed = 0
    placed_blocks = [b_ for lst in (views, rail, slot) for b_ in lst if any(i in mats for i in b_['idx'])]
    for info in (page.get_image_info(xrefs=True) if placed_blocks else []):
        ir = fitz.Rect(info['bbox'])
        if ir.is_empty or not info.get('xref') or not fitz.Rect(I.x0 - 1, I.y0 - 1, I.x1 + 1, I.y1 + 1).contains(ir) or inside_any(ir, furn):
            continue
        def _ov(b_):
            o_ = b_['r'] & ir
            return o_.width * o_.height if not o_.is_empty else 0
        def _dist(b_):
            r_ = b_['r']
            return max(r_.x0 - ir.x1, ir.x0 - r_.x1, 0) + max(r_.y0 - ir.y1, ir.y0 - r_.y1, 0)
        best = max(placed_blocks, key=_ov)
        if _ov(best) == 0: best = min(placed_blocks, key=_dist)
        m_ = mats[next(i for i in best['idx'] if i in mats)][0]
        pix = fitz.Pixmap(src, info['xref'])
        smask = next((im[1] for im in page.get_images(full=True) if im[0] == info['xref']), 0)
        if smask:
            pix = fitz.Pixmap(pix, fitz.Pixmap(src, smask))
        if pix.n - pix.alpha > 3: pix = fitz.Pixmap(fitz.csRGB, pix)
        pg.insert_image(ir * m_, pixmap=pix, keep_proportion=False)
        images_placed += 1
    sh = pg.new_shape()
    for n_, d in enumerate(keep):
        if n_ not in mats: continue
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
        if tpl.get('width_cap'): w = min(w, tpl['width_cap'])   # some CAD exports draw leaders/table frames 4-7x heavier than the rest
        # text outlined as tiny stroked triangles (e.g. Foxit-edited PDFs) needs round joins/caps: mitred joins grow spikes
        lj = lc = 1 if tpl.get('round_joins') else 0
        # glyphs drawn as tiny stroked triangles whose source already uses round joins: keep them round, otherwise the
        # 0.4pt minimum width with mitred joins grows long spikes on every acute corner
        if not lj and d.get('lineJoin') == 1 and t in ('s', 'fs') and max(d['rect'].width, d['rect'].height) < 8 \
                and all(it[0] == 'l' for it in d['items']):
            lj = lc = 1
        # brand option (RunQing): every stroke gets round joins -- the source's miter limit is not carried over, so mitred
        # glyph corners (even large dimension text on enlarged sheets) would spike out at the default limit; on drawing
        # geometry a 0.2pt corner radius is invisible
        if not lj and B.get('round_small_glyphs') and t in ('s', 'fs'):
            lj = lc = 1
        sh.finish(color=col, fill=fil, width=w, closePath=d.get('closePath', False),
                  even_odd=d.get('even_odd', False), lineCap=lc, lineJoin=lj)
    sh.commit()
    # Kangsheng frame + title
    ff = Path(font)
    if font_index or ff.suffix.lower() in ('.ttc', '.otc', '.otf'):
        sys.path.insert(0, str(HERE)); import auto_manifest as am
        text = job['title'] + job['model'] + B.get('font_extra_text', '')
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
    if brand == 'runqing':
        KF.draw_runqing_frame_and_title(pg, fields, {'font': font, 'logo': str(ROOT / B['logo'])}, B, job['tolerance'])
    else:
        KF.draw_frame_and_title(pg, fields, {'font': font, 'brand_strip': str(ROOT / 'assets' / 'brand-strip.png')},
                                tolerance_mode='source')
        DT.render_dynamic_tolerance(pg, job['tolerance'], KF.TOLERANCE_BOX)
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    pdf = out / f"{job['model'].replace('/', '_')}-{B['output_suffix']}.pdf"
    doc.save(pdf, garbage=3, deflate=True)
    rep = {'source': job['source'], 'inner_frame': list(I), 'supplier_furniture_rects': [list(r) for r in furn],
           'paths_total': len(D), 'paths_placed': len(keep), 'dropped': dict(dropped),
           'dominant_colour_to_blue': dominant, 'other_colours_to_gold': [k for k in cnt if k != dominant],
           'scale': round(s, 4), 'output': str(pdf), 'auto_match': auto,
           **({'images_placed': images_placed} if images_placed else {}),
           **({'brand': brand, 'model_in_job': model_in_job, 'model_on_sheet': job['model']} if brand != 'kangsheng' else {}),
           'warnings': (['SMALL_SCALE: 缩放 < 0.55，视图会偏小，请看对照图'] if s < 0.55 else [])}
    (out / 'report.json').write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    # side-by-side review image
    rv = fitz.open(); r = rv.new_page(width=1700, height=640)
    r.show_pdf_page(fitz.Rect(5, 20, 845, 635), src, 0, clip=bb)
    r.show_pdf_page(fitz.Rect(855, 20, 1695, 635), doc, 0)
    r.get_pixmap(dpi=110).save(out / f"{job['model'].replace('/', '_')}-{B['compare_suffix']}.png")
    print(json.dumps({k: rep[k] for k in ('paths_total', 'paths_placed', 'dropped', 'scale', 'warnings')}, ensure_ascii=False))
    return rep


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('job'); ap.add_argument('--out', required=True)
    ap.add_argument('--font', required=True); ap.add_argument('--font-index', type=int, default=0)
    ap.add_argument('--brand', choices=BR.BRANDS, default='kangsheng', help='目标品牌；不传 = 康生（行为与以前完全一致）')
    a = ap.parse_args()
    run(json.loads(Path(a.job).read_text()), a.out, a.font, a.font_index, a.brand)


if __name__ == '__main__':
    main()
