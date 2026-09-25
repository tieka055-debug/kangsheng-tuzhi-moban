#!/usr/bin/env python3
"""Derive a Manifest v2 from an ORIGINAL supplier PDF with no
hand-written per-drawing coordinates.

Everything here is computed from the source page plus one family config
(relative rules: title-block labels, watermark words, block hints, layout rail).
The output is fed unchanged to the existing production engine (`draft`), whose
full-page zero-unplaced-ink gate, clip/text-cut, overlap, font and preservation
checks are the judge.  This script never writes review PASS values.
"""
from __future__ import annotations

import argparse, hashlib, json, math, re, sys, time, collections
from pathlib import Path

import numpy as np
import pymupdf as fitz

HERE = Path(__file__).resolve().parent
PAGE_W, PAGE_H = 841.89, 595.276
FRAME = fitz.Rect(22, 29, 820, 564)
TITLE_BOX = fitz.Rect(488, 450, 820, 564)
TOL_BOX = fitz.Rect(400, 493, 488, 564)
PROJ_BOX = fitz.Rect(775, 545.5, 820, 564)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def R(r):
    # multiples of 1/64 pt are exact in float32 (the engine compares float32 rects)
    return [round(float(v) * 64) / 64 for v in r]


# ----------------------------------------------------------------- geometry io
def normalized(path):
    """Rotation that makes text read left->right on a landscape page."""
    for rot in (0, 90, 180, 270):
        doc = fitz.open(path); pg = doc[0]
        pg.set_rotation(rot); pg.remove_rotation()
        c = collections.Counter()
        for b in pg.get_text('dict')['blocks']:
            for l in b.get('lines', []):
                c[tuple(round(v) for v in l['dir'])] += len(''.join(s['text'] for s in l['spans']).strip())
        if c and c.most_common(1)[0][0] == (1, 0) and pg.rect.width > pg.rect.height:
            return doc, pg, rot
    raise ValueError('NO_READING_ORIENTATION')


class Prim:
    __slots__ = ('rect', 'kind', 'text', 'orient', 'coord', 'span', 'width')

    def __init__(self, rect, kind, text='', orient=None, coord=None, span=None, width=0.0):
        self.rect = fitz.Rect(rect); self.kind = kind; self.text = text
        self.orient = orient; self.coord = coord; self.span = span; self.width = width


def primitives(page):
    words = [Prim(w[:4], 'word', str(w[4])) for w in page.get_text('words') if str(w[4]).strip()]
    items, lines = [], []
    for dr in page.get_drawings():
        lw = float(dr.get('width') or 0.0)
        pad = lw / 2 if dr.get('color') is not None else 0.0
        for it in dr['items']:
            op = it[0]
            if op == 'l':
                a, b = it[1], it[2]
                r = fitz.Rect(min(a.x, b.x) - pad, min(a.y, b.y) - pad, max(a.x, b.x) + pad, max(a.y, b.y) + pad)
                p = Prim(r, 'item', width=lw)
                if abs(a.y - b.y) < 0.3 and abs(a.x - b.x) > 1:
                    p.orient, p.coord, p.span = 'H', (a.y + b.y) / 2, (min(a.x, b.x), max(a.x, b.x))
                elif abs(a.x - b.x) < 0.3 and abs(a.y - b.y) > 1:
                    p.orient, p.coord, p.span = 'V', (a.x + b.x) / 2, (min(a.y, b.y), max(a.y, b.y))
                items.append(p)
                if p.orient: lines.append(p)
            elif op == 're':
                rr = fitz.Rect(it[1])
                items.append(Prim(fitz.Rect(rr.x0 - pad, rr.y0 - pad, rr.x1 + pad, rr.y1 + pad), 'item', width=lw))
                if dr.get('color') is not None:
                    for (o, c, s) in (('H', rr.y0, (rr.x0, rr.x1)), ('H', rr.y1, (rr.x0, rr.x1)),
                                      ('V', rr.x0, (rr.y0, rr.y1)), ('V', rr.x1, (rr.y0, rr.y1))):
                        if s[1] - s[0] > 1:
                            q = Prim(fitz.Rect(), 'item', orient=o, coord=c, span=s, width=lw)
                            lines.append(q)
            else:
                pts = [x for x in it[1:] if isinstance(x, fitz.Point)]
                if op == 'qu': pts = list(it[1])
                if not pts: continue
                xs = [p.x for p in pts]; ys = [p.y for p in pts]
                items.append(Prim(fitz.Rect(min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad), 'item', width=lw))
    return words, items, lines


# ----------------------------------------------------------------- rect algebra
def subtract(a, b):
    a, b = fitz.Rect(a), fitz.Rect(b)
    i = a & b
    if i.is_empty or i.width <= 0 or i.height <= 0:
        return [a]
    out = []
    if i.y0 > a.y0: out.append(fitz.Rect(a.x0, a.y0, a.x1, i.y0))
    if i.y1 < a.y1: out.append(fitz.Rect(a.x0, i.y1, a.x1, a.y1))
    if i.x0 > a.x0: out.append(fitz.Rect(a.x0, i.y0, i.x0, i.y1))
    if i.x1 < a.x1: out.append(fitz.Rect(i.x1, i.y0, a.x1, i.y1))
    return [r for r in out if r.width > 0.01 and r.height > 0.01]


def subtract_all(regions, cutters):
    out = [fitz.Rect(r) for r in regions]
    for c in cutters:
        nxt = []
        for r in out: nxt.extend(subtract(r, c))
        out = nxt
    return out


def pad(r, d):
    return fitz.Rect(r.x0 - d, r.y0 - d, r.x1 + d, r.y1 + d)


CLIP_MODE = 'multi'
RELAXED_RAIL = False
NO_FALLBACK = False


def near(a, b, gap):
    return not (a.x1 + gap < b.x0 or b.x1 + gap < a.x0 or a.y1 + gap < b.y0 or b.y1 + gap < a.y0)


def cluster(prims, gap):
    n = len(prims); parent = list(range(n))
    def f(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    order = sorted(range(n), key=lambda i: prims[i].rect.x0)
    for a in range(n):
        i = order[a]; ri = prims[i].rect
        for b in range(a + 1, n):
            j = order[b]; rj = prims[j].rect
            if rj.x0 > ri.x1 + gap: break
            if rj.y0 <= ri.y1 + gap and ri.y0 <= rj.y1 + gap:
                parent[f(i)] = f(j)
    groups = collections.defaultdict(list)
    for i in range(n): groups[f(i)].append(prims[i])
    return list(groups.values())


def bbox(prims):
    r = fitz.Rect(prims[0].rect)
    for p in prims[1:]: r |= p.rect
    return r


# ----------------------------------------------------------------- analysis
class Blocked(Exception):
    pass


def find_frame(lines, W, H):
    hs = [l for l in lines if l.orient == 'H' and l.span[1] - l.span[0] > 0.45 * W]
    vs = [l for l in lines if l.orient == 'V' and l.span[1] - l.span[0] > 0.45 * H]
    top = [l.coord for l in hs if l.coord < 45]; bot = [l.coord for l in hs if l.coord > H - 45]
    lef = [l.coord for l in vs if l.coord < 45]; rig = [l.coord for l in vs if l.coord > W - 45]
    ix0 = max(lef) if lef else 3.0; iy0 = max(top) if top else 3.0
    ix1 = min(rig) if rig else W - 3.0; iy1 = min(bot) if bot else H - 3.0
    return fitz.Rect(ix0 + 0.45, iy0 + 0.45, ix1 - 0.45, iy1 - 0.45)


def line_at(lines, orient, *, lo, hi, along, want):
    """Nearest line of `orient` whose coord is in [lo,hi] and whose span covers `along`.
    want='max' returns largest coord, 'min' smallest."""
    c = [l for l in lines if l.orient == orient and lo <= l.coord <= hi
         and l.span[0] - 0.8 <= along <= l.span[1] + 0.8]
    if not c: return None
    return (max if want == 'max' else min)(c, key=lambda l: l.coord)


def cell_of(lines, rect, bounds):
    cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
    L = line_at(lines, 'V', lo=bounds.x0 - 1, hi=rect.x0 + 0.8, along=cy, want='max')
    Rr = line_at(lines, 'V', lo=rect.x1 - 0.8, hi=bounds.x1 + 1, along=cy, want='min')
    T = line_at(lines, 'H', lo=bounds.y0 - 1, hi=rect.y0 + 0.8, along=cx, want='max')
    B = line_at(lines, 'H', lo=rect.y1 - 0.8, hi=bounds.y1 + 1, along=cx, want='min')
    x0 = L.coord if L else bounds.x0; x1 = Rr.coord if Rr else bounds.x1
    y0 = T.coord if T else bounds.y0; y1 = B.coord if B else bounds.y1
    return fitz.Rect(x0, y0, x1, y1)


def analyse(src_path, family, work_dir, derived_note=None):
    t0 = time.perf_counter()
    doc, page, rot = normalized(src_path)
    W, H = page.rect.width, page.rect.height
    words, items, lines = primitives(page)
    flags, notes = [], []
    tb_cfg = family['title_block']

    interior = find_frame(lines, W, H)

    # ---------------- title block (anchored on its own labels, never on coordinates)
    def has(w, label): return label in w.text
    tb_words = [w for w in words if any(has(w, l) for l in tb_cfg['labels'] if l != '第')]
    if not tb_words: raise Blocked('TITLE_BLOCK_NOT_FOUND')
    top_lab = [w for w in tb_words if has(w, tb_cfg['top_label'])] or tb_words
    top_lab = min(top_lab, key=lambda w: w.rect.y0)
    tl = line_at(lines, 'H', lo=top_lab.rect.y0 - 15, hi=top_lab.rect.y0 + 0.5,
                 along=(top_lab.rect.x0 + top_lab.rect.x1) / 2, want='max')
    if tl is None: raise Blocked('TITLE_BLOCK_TOP_RULE_NOT_FOUND')
    tb_minx = min(w.rect.x0 for w in tb_words if w.rect.y0 >= tl.coord - 1)
    ll = [l for l in lines if l.orient == 'V' and abs(l.coord - tb_minx) <= 8
          and l.span[0] - 1 <= tl.coord + 5 and l.span[1] >= tl.coord + 20]
    if not ll: raise Blocked('TITLE_BLOCK_LEFT_RULE_NOT_FOUND')
    left_rule = min(ll, key=lambda l: abs(l.coord - tb_minx))
    tb = fitz.Rect(left_rule.coord - left_rule.width / 2 - 0.3, tl.coord - tl.width / 2 - 0.3, W, H)
    tb_lines = [l for l in lines if (l.orient == 'H' and tb.y0 - 1 <= l.coord <= H and l.span[1] > tb.x0)
                or (l.orient == 'V' and tb.x0 - 1 <= l.coord <= W and l.span[1] > tb.y0)]

    def find(label):
        c = [w for w in words if has(w, label) and tb.contains(fitz.Rect(w.rect.x0 + 0.5, w.rect.y0 + 0.5, w.rect.x1 - 0.5, w.rect.y1 - 0.5))]
        return min(c, key=lambda w: w.rect.y0) if c else None

    # tolerance cell
    head = find(tb_cfg['tolerance_heading']); plab = find(tb_cfg['projection_label'])
    if head is None or plab is None: raise Blocked('TOLERANCE_OR_PROJECTION_LABEL_NOT_FOUND')
    hy = (head.rect.y0 + head.rect.y1) / 2
    row = [w for w in words if abs((w.rect.y0 + w.rect.y1) / 2 - hy) < 2 and head.rect.x0 <= w.rect.x0 < head.rect.x0 + 90]
    hx1 = max(w.rect.x1 for w in row)
    tlft = line_at(tb_lines, 'V', lo=tb.x0, hi=head.rect.x0 + 0.5, along=hy, want='max')
    trgt = line_at(tb_lines, 'V', lo=hx1 - 0.5, hi=W, along=hy, want='min')
    tbot = line_at(tb_lines, 'H', lo=head.rect.y1, hi=plab.rect.y0 + 0.5, along=head.rect.x0 + 2, want='max')
    if not (tlft and trgt and tbot): raise Blocked('TOLERANCE_CELL_RULES_NOT_FOUND')
    lw = max(tlft.width, 0.36)
    tol_clip = fitz.Rect(tlft.coord - lw / 2 - 0.05, tl.coord - tl.width / 2 - 0.05,
                         trgt.coord + trgt.width / 2 + 0.05, tbot.coord + tbot.width / 2 + 0.05)

    # projection symbol
    py = (plab.rect.y0 + plab.rect.y1) / 2
    lab_b = line_at(tb_lines, 'H', lo=plab.rect.y1 - 0.5, hi=H, along=plab.rect.x0 + 2, want='min')
    if lab_b is None: raise Blocked('PROJECTION_CELL_NOT_FOUND')
    cw_ = trgt.coord - tlft.coord
    full = [l for l in tb_lines if l.orient == 'H' and l.coord >= lab_b.coord + 3
            and l.span[0] <= tlft.coord + 0.1 * cw_ and l.span[1] >= trgt.coord - 0.1 * cw_]
    cell_b = min(full, key=lambda l: l.coord) if full else None
    pcell = fitz.Rect(tlft.coord, lab_b.coord, trgt.coord, cell_b.coord if cell_b else H)
    inner = fitz.Rect(pcell.x0 + 0.6, pcell.y0 + 0.6, pcell.x1 - 0.6, pcell.y1 - 0.6)
    sym = [p for p in items if inner.contains(p.rect) and p.rect.width < 0.8 * pcell.width]
    if not sym: raise Blocked('PROJECTION_SYMBOL_NOT_FOUND')
    proj_clip = bbox(sym); proj_clip = fitz.Rect(proj_clip.x0 - 0.5, proj_clip.y0 - 0.5, proj_clip.x1 + 0.5, proj_clip.y1 + 0.5) & inner

    # fields (value regions become replaced_title_field; values read from text when present)
    fields, field_regions, field_flags = {}, {}, {}
    for key, label in tb_cfg['fields'].items():
        lab = find(label)
        if lab is None:
            field_flags[key] = 'LABEL_NOT_FOUND'; continue
        cell = cell_of(tb_lines, lab.rect, tb)
        cin = fitz.Rect(cell.x0 + 0.5, cell.y0 + 0.5, cell.x1 - 0.5, cell.y1 - 0.5)
        cw = [w for w in words if cin.contains(fitz.Rect((w.rect.x0 + w.rect.x1) / 2, (w.rect.y0 + w.rect.y1) / 2,
                                                          (w.rect.x0 + w.rect.x1) / 2 + .01, (w.rect.y0 + w.rect.y1) / 2 + .01))]
        vals = [w for w in cw if w is not lab]
        curve = [p for p in items if cin.contains(p.rect) and not p.orient]
        if key in ('title', 'model'):
            text = ' '.join(w.text for w in sorted(vals, key=lambda w: (round(w.rect.y0), w.rect.x0)))
            reg_prims = vals + [c for c in curve if not lab.rect.intersects(c.rect)]
            fields[key] = text
            if not text: field_flags[key] = 'VALUE_NOT_TEXT_NEEDS_READ'
        else:
            joined = ''.join(w.text for w in sorted(cw, key=lambda w: w.rect.x0))
            val = re.split(r'[：:]', joined, maxsplit=1)
            raw = val[1].strip() if len(val) > 1 else ''
            if key == 'sheet':
                m = re.search(r'共\s*(\d+)\s*页.*?第\s*(\d+)\s*页', joined)
                raw = f'{m.group(2)}/{m.group(1)}' if m else ''
                if not m: field_flags[key] = 'SHEET_PATTERN_NOT_FOUND'
            if key == 'scale_text':
                raw = raw.replace('：', ':')
            fields[key] = raw
            reg_prims = cw + curve
            if curve and not raw: field_flags[key] = 'CURVE_VALUE_NEEDS_READ'
        if reg_prims:
            rr = bbox(reg_prims)
            field_regions[key] = fitz.Rect(rr.x0 - 0.6, rr.y0 - 0.6, rr.x1 + 0.6, rr.y1 + 0.6) & cin
        if key == 'model':
            # size cell is the next cell to the right on the model row
            my = (lab.rect.y0 + lab.rect.y1) / 2
            nxt = fitz.Rect(cell.x1 + 1, lab.rect.y0, cell.x1 + 2, lab.rect.y1)
            scell = cell_of(tb_lines, nxt, tb)
            sin = fitz.Rect(scell.x0 + 0.5, scell.y0 + 0.5, scell.x1 - 0.5, scell.y1 - 0.5)
            sw = [w for w in words if sin.contains(w.rect)]
            sc = [p for p in items if sin.contains(p.rect) and not p.orient]
            if sw or sc:
                sr = bbox(sw + sc)
                field_regions['size'] = fitz.Rect(sr.x0 - 0.6, sr.y0 - 0.6, sr.x1 + 0.6, sr.y1 + 0.6) & sin
                fields['size'] = ''.join(w.text for w in sw)
                if not sw: field_flags['size'] = 'CURVE_VALUE_NEEDS_READ'

    # ---------------- watermarks (text objects matched by family words)
    wm = [w for w in words if any(p in w.text for p in family['watermark_patterns'])]
    for w in words:
        if w.text in family['watermark_single_chars'] and any(near(w.rect, m.rect, 25) for m in wm):
            wm.append(w)
    wm_ids = {id(w) for w in wm}

    # ---------------- content blocks
    def in_tb(r):
        c = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
        return tb.contains(c)
    content = [p for p in words + items
               if id(p) not in wm_ids and interior.contains(fitz.Point((p.rect.x0 + p.rect.x1) / 2, (p.rect.y0 + p.rect.y1) / 2))
               and not in_tb(p.rect)]
    members = cluster(content, 5.0)
    # one PDF text span is one line of meaning: never let its words end up in different blocks
    _spans = [fitz.Rect(sp['bbox']) for bl in page.get_text('dict')['blocks'] for ln in bl.get('lines', [])
              for sp in ln['spans'] if sp['text'].strip()]
    _idx = {id(m): i for i, grp in enumerate(members) for m in grp}
    _par = list(range(len(members)))
    def _f(i):
        while _par[i] != i:
            _par[i] = _par[_par[i]]; i = _par[i]
        return i
    for sr in _spans:
        ids = {_idx[id(w)] for w in content if w.kind == 'word' and id(w) in _idx
               and sr.contains(fitz.Point((w.rect.x0 + w.rect.x1) / 2, (w.rect.y0 + w.rect.y1) / 2))}
        ids = sorted(ids)
        for j in ids[1:]: _par[_f(j)] = _f(ids[0])
    _m = collections.defaultdict(list)
    for i, grp in enumerate(members): _m[_f(i)].extend(grp)
    members = list(_m.values())
    cl = [bbox(m) for m in members]

    def area(r): return r.width * r.height
    def dist(a, b):
        dx = max(0, max(a.x0, b.x0) - min(a.x1, b.x1)); dy = max(0, max(a.y0, b.y0) - min(a.y1, b.y1))
        return math.hypot(dx, dy)
    def join(i, j):
        cl[j] = cl[j] | cl[i]; members[j] = members[j] + members[i]; del cl[i]; del members[i]

    # (1) small fragments (isolated dimension text / symbols) -> nearest real block
    changed = True
    while changed:
        changed = False
        for i in sorted(range(len(cl)), key=lambda i: area(cl[i])):
            if area(cl[i]) >= 900 or len(cl) == 1: continue
            others = [j for j in range(len(cl)) if j != i and area(cl[j]) >= 900]
            if not others: continue
            j = min(others, key=lambda j: dist(cl[i], cl[j]))
            if dist(cl[i], cl[j]) <= 30:
                join(i, j); changed = True; break

    # (2) captions (text lines + underline rules) -> the drawing directly above, else nearest
    hints0 = family['block_hints']
    def is_caption(mem, box):
        ws = [m for m in mem if m.kind == 'word']; nonline = [m for m in mem if m.kind == 'item' and not m.orient]
        t = ' '.join(w.text for w in ws)
        if any(h in t for h in hints0['table_header'] + hints0['performance']): return False
        if ws and len(nonline) <= 2 and box.height < 40: return True   # two-line captions are ~32pt tall
        # captions drawn as outlined glyphs: a short, wide strip of small curve items and no big graphics
        return (not ws and box.height < 30 and box.width >= 4 * box.height and nonline
                and max(m.rect.height for m in nonline) < 14)
    def is_text_block(mem):
        t = ' '.join(m.text for m in mem if m.kind == 'word')
        return any(h in t for h in hints0['table_header'] + hints0['performance'])
    changed = True
    while changed:
        changed = False
        for i in range(len(cl)):
            if not is_caption(members[i], cl[i]): continue
            c = cl[i]
            above = [j for j in range(len(cl)) if j != i and not is_text_block(members[j])
                     and cl[j].y1 <= c.y0 + 2 and c.y0 - cl[j].y1 <= 40
                     and min(c.x1, cl[j].x1) - max(c.x0, cl[j].x0) >= 0.5 * min(c.width, cl[j].width)]
            if above:
                j = min(above, key=lambda j: c.y0 - cl[j].y1); join(i, j); changed = True; break
            others = [j for j in range(len(cl)) if j != i and not is_text_block(members[j])]
            if others:
                j = min(others, key=lambda j: dist(c, cl[j]))
                if dist(c, cl[j]) <= 25:
                    join(i, j); changed = True; break

    hints = family['block_hints']; part_re = re.compile(hints['part_regex'])
    def classify(mem):
        ws = [m for m in mem if m.kind == 'word']; its = [m for m in mem if m.kind == 'item']
        txt = ' '.join(w.text for w in ws); parts = [w.text for w in ws if part_re.search(w.text)]
        if any(h in txt for h in hints['table_header']) or len(parts) >= 2: return 'table', parts
        if any(h in txt for h in hints['pcb']): return 'pcb', parts
        if any(h in txt for h in hints['performance']) and len([i for i in its if not i.orient]) <= 12: return 'performance', parts
        return 'view', parts
    blocks = []
    for box, mem in zip(cl, members):
        kind, parts = classify(mem)
        blocks.append({'kind': kind, 'box': fitz.Rect(box), 'parts': parts, 'mem': mem})

    # NOTES / performance drawn as glyph outlines: one text block, even if its lines are far apart.
    def outlined(b):
        its_ = [m for m in b['mem'] if m.kind == 'item']
        if not its_ or any(m.kind == 'word' for m in b['mem']): return False
        small_ = sum(1 for m in its_ if max(m.rect.width, m.rect.height) < 9) / len(its_)
        long_ = sum(1 for m in its_ if max(m.rect.width, m.rect.height) >= 30)
        return b['kind'] == 'view' and small_ >= 0.98 and long_ == 0 and len(its_) > 40
    changed = True
    while changed:
        changed = False
        ol = [b for b in blocks if outlined(b)]
        for i, a in enumerate(ol):
            for c2 in ol[i + 1:]:
                gy = max(0, max(a['box'].y0, c2['box'].y0) - min(a['box'].y1, c2['box'].y1))
                ox = min(a['box'].x1, c2['box'].x1) - max(a['box'].x0, c2['box'].x0)
                if gy <= 25 and ox > 0:
                    a['box'] = a['box'] | c2['box']; a['mem'] = a['mem'] + c2['mem']
                    blocks.remove(c2); changed = True; break
            if changed: break
    if not any(b['kind'] == 'performance' for b in blocks):
        ol = [b for b in blocks if outlined(b)]
        if len(ol) == 1:
            ol[0]['kind'] = 'performance'; ol[0]['outlined_perf'] = True

    # (3) table borders that coincide with frame / title-block rules
    def collinear_cover(orient, coord, lo, hi, tol=0.35):
        segs = sorted((l.span for l in lines if l.orient == orient and abs(l.coord - coord) <= tol), key=lambda s: s[0])
        cov, cur = 0.0, lo
        for a, b in segs:
            a, b = max(a, cur), min(b, hi)
            if b > a: cov += b - a; cur = b
        return cov / max(hi - lo, 1e-6)
    restored = {}
    for b in blocks:
        if b['kind'] != 'table': continue
        r = b['box']
        for side in ('x0', 'x1', 'y0', 'y1'):
            orient = 'V' if side in ('x0', 'x1') else 'H'
            lo, hi = (r.y0, r.y1) if orient == 'V' else (r.x0, r.x1)
            edge = getattr(r, side); outward = -1 if side in ('x0', 'y0') else 1
            cand = [l for l in lines if l.orient == orient and 0 <= (l.coord - edge) * outward <= 3
                    and min(l.span[1], hi) - max(l.span[0], lo) >= 0.3 * (hi - lo)
                    and (abs(l.span[0] - lo) <= 1.5 or abs(l.span[1] - hi) <= 1.5 or (l.span[0] <= lo and l.span[1] >= hi))]
            if cand:
                l = max(cand, key=lambda l: (l.coord - edge) * outward)
                setattr(r, side, l.coord + outward * (l.width / 2 + 0.05))
        if r.intersects(tb):
            if r.x0 < tb.x0 < r.x1 and r.x1 - tb.x0 <= 2.5:
                x = left_rule.coord
                if collinear_cover('V', x, max(r.y0, 0), r.y1) >= 0.9:
                    lw_ = max(l.width for l in lines if l.orient == 'V' and abs(l.coord - x) <= 0.35
                              and min(l.span[1], r.y1) - max(l.span[0], r.y0) > 1)
                    restored[id(b)] = {'orientation': 'vertical', 'x': round(x, 3), 'y0': round(r.y0 + 0.05, 3),
                                       'y1': round(r.y1 - 0.05, 3), 'width': round(lw_, 3), 'color': 'blue'}
                    b['rextent'] = fitz.Rect(r.x0, r.y0, x + lw_ / 2, r.y1); r.x1 = min(tb.x0, x - lw_ / 2 - 0.02)
                else:
                    flags.append('TABLE_TITLE_RULE_NOT_COLLINEAR')
            elif r.y0 < tb.y0 < r.y1 and r.y1 - tb.y0 <= 2.5:
                y = tl.coord
                if collinear_cover('H', y, r.x0, r.x1) >= 0.9:
                    lw_ = max(l.width for l in lines if l.orient == 'H' and abs(l.coord - y) <= 0.35
                              and min(l.span[1], r.x1) - max(l.span[0], r.x0) > 1)
                    restored[id(b)] = {'orientation': 'horizontal', 'y': round(y, 3), 'x0': round(r.x0 + 0.05, 3),
                                       'x1': round(r.x1 - 0.05, 3), 'width': round(lw_, 3), 'color': 'blue'}
                    b['rextent'] = fitz.Rect(r.x0, r.y0, r.x1, y + lw_ / 2); r.y1 = min(tb.y0, y - lw_ / 2 - 0.02)
                else:
                    flags.append('TABLE_TITLE_RULE_NOT_COLLINEAR')
            else:
                flags.append('TABLE_OVERLAPS_TITLE_BLOCK')

    # (4) clips: block rectangle minus foreign objects inside it (multi-rect clips);
    #     true interleaving with another block => merge; with title block/frame => flag.
    _pix = page.get_pixmap(matrix=fitz.Matrix(4, 4), alpha=False)
    _dark = np.frombuffer(_pix.samples, np.uint8).reshape(_pix.height, _pix.width, _pix.n)[:, :, :3].min(2) < 150
    def dark_in(k):
        # the engine trims 0.5pt at every piece edge before counting expected ink
        return bool(_dark[int(math.floor(k.y0 * 4)):int(math.ceil(k.y1 * 4)),
                          int(math.floor(k.x0 * 4)):int(math.ceil(k.x1 * 4))].any())
    changed = True
    while changed:
        changed = False
        for i in range(len(blocks)):
            for j in range(i + 1, len(blocks)):
                a, c2 = blocks[i]['box'], blocks[j]['box']
                inter = a & c2
                if inter.is_empty or inter.width <= 0 or inter.height <= 0: continue
                if 'table' in (blocks[i]['kind'], blocks[j]['kind']): continue
                small = min(area(a), area(c2))
                if area(inter) > 0.2 * small:
                    K, D = blocks[i], blocks[j]
                    K['box'] = a | c2; K['mem'] = K['mem'] + D['mem']; K['kind'], K['parts'] = classify(K['mem'])
                    del blocks[j]; changed = True; break
            if changed: break
    if CLIP_MODE == 'single':
        changed = True
        while changed:
            changed = False
            for i in range(len(blocks)):
                for j in range(i + 1, len(blocks)):
                    if pad(blocks[i]['box'], 0.8).intersects(pad(blocks[j]['box'], 0.8)):
                        if 'table' in (blocks[i]['kind'], blocks[j]['kind']) and blocks[i]['kind'] != blocks[j]['kind']:
                            raise Blocked('SINGLE_MODE_TABLE_ENTANGLED')
                        K, D = blocks[i], blocks[j]
                        K['box'] = K['box'] | D['box']; K['mem'] = K['mem'] + D['mem']; K['kind'], K['parts'] = classify(K['mem'])
                        if id(D) in restored: restored[id(K)] = restored.pop(id(D)); K['rextent'] = D.get('rextent')
                        del blocks[j]; changed = True; break
                if changed: break
    owner = {}
    for bi, b in enumerate(blocks):
        for m in b['mem']: owner[id(m)] = bi
    furniture = [p for p in words + items if id(p) not in owner and id(p) not in wm_ids]
    page_rect = fitz.Rect(0, 0, W, H)
    for guard in range(200):
        restart = False
        for bi, b in enumerate(blocks):
            r = b['box']; c = pad(r, 0.6) & page_rect
            if id(b) in restored:
                if restored[id(b)]['orientation'] == 'vertical': c.x1 = r.x1
                else: c.y1 = r.y1
            own = b['mem']; cuts = []
            for p in content + furniture:
                oi = owner.get(id(p))
                if oi == bi or not p.rect.intersects(c): continue
                hit = [m for m in own if pad(m.rect, 0.2).intersects(p.rect)]
                if hit:
                    if p.orient:  # a shared rule: carried with this block, not cut
                        if oi is None and b['kind'] != 'table': flags.append(f'SHARES_RULE_WITH_FURNITURE:{b["kind"]}')
                        continue
                    if oi is not None:
                        # interleaved ink: one block
                        o = blocks[oi]; keep, drop = (bi, oi) if bi < oi else (oi, bi)
                        K, D = blocks[keep], blocks[drop]
                        K['box'] = K['box'] | D['box']; K['mem'] = K['mem'] + D['mem']
                        K['kind'], K['parts'] = classify(K['mem'])
                        if id(D) in restored: restored[id(K)] = restored.pop(id(D)); K['rextent'] = D.get('rextent')
                        del blocks[drop]
                        owner = {id(m): i for i, bb in enumerate(blocks) for m in bb['mem']}
                        restart = True; break
                    flags.append(f'INTERLEAVES_FURNITURE:{b["kind"]}')
                    continue
                cuts.append(pad(p.rect, 0.35))
            if restart: break
            clips = subtract_all([c], cuts) if (cuts and CLIP_MODE == 'multi') else [c]
            # keep only pieces that actually carry this block's objects; drop empty slivers
            clips = [k for k in clips if any(k.intersects(m.rect) for m in own) and dark_in(k)] or [c]
            if len(clips) > 1:
                # the engine trims 0.5pt at each piece edge before counting ink: a sliver whose ink sits only
                # on its edges fails. Absorb such slivers into a neighbour when the grown rectangle stays
                # clear of every cut-out and every other piece.
                def interior_ink(k):
                    return bool(_dark[int(math.ceil(k.y0 * 4)) + 2:int(math.floor(k.y1 * 4)) - 2,
                                      int(math.ceil(k.x0 * 4)) + 2:int(math.floor(k.x1 * 4)) - 2].any())
                def area_(r):
                    return max(0.0, r.width) * max(0.0, r.height)
                changed_ = True
                while changed_:
                    changed_ = False
                    for k in list(clips):
                        if interior_ink(k): continue
                        for q in clips:
                            if q is k: continue
                            g = fitz.Rect(q) | k
                            if any(area_(g & x) > 1e-6 for x in cuts): continue
                            if any(area_(g & o) > 1e-6 for o in clips if o is not q and o is not k): continue
                            clips = [o for o in clips if o is not q and o is not k] + [g]
                            changed_ = True; break
                        if changed_: break
            if len(clips) > 24:
                flags.append(f'FRAGMENTED_CLIP:{b["kind"]}')
            b['clips'] = clips
            b['clip'] = bbox([Prim(k, 'x') for k in clips])
            u = b['clip']; e = b.get('rextent', u)
            b['extent'] = fitz.Rect(min(e.x0, u.x0), min(e.y0, u.y0), max(e.x1, u.x1), max(e.y1, u.y1))
        if not restart: break
    for b in blocks:
        if b['kind'] == 'table':
            curves = [m for m in b['mem'] if m.kind == 'item' and not m.orient and m.rect.width * m.rect.height > 4]
            if len(curves) > 40: flags.append('TABLE_CONTAINS_GRAPHICS')
        if b['kind'] == 'view':
            ws_ = [m for m in b['mem'] if m.kind == 'word']; gr_ = [m for m in b['mem'] if m.kind == 'item' and not m.orient]
            if len(ws_) >= 3 and not gr_:
                flags.append('TEXT_ONLY_BLOCK')   # e.g. part of a NOTES/performance block split off
            its_ = [m for m in b['mem'] if m.kind == 'item']
            if len(its_) > 150:
                small_ = sum(1 for m in its_ if max(m.rect.width, m.rect.height) < 9) / len(its_)
                long_ = sum(1 for m in its_ if max(m.rect.width, m.rect.height) >= 30)
                if small_ >= 0.98 and long_ == 0:
                    flags.append('OUTLINED_TEXT_BLOCK')   # notes/performance drawn as glyph outlines
        if b['kind'] != 'table' and any(k.intersects(tb) for k in b['clips']):
            flags.append(f'BLOCK_ENTERS_TITLE_BLOCK:{b["kind"]}')
    for b in blocks:
        if b['kind'] in ('view', 'pcb'):
            f = min((sp['size'] for bl in page.get_text('dict', clip=b['clip'])['blocks'] for ln in bl.get('lines', [])
                     for sp in ln['spans'] if sp['text'].strip()), default=None)
            if f is not None and f < family['layout']['min_font_pt']:
                flags.append('SOURCE_TEXT_BELOW_MIN_FONT_AT_1TO1')
    if any(h in ' '.join(w.text for w in words) for h in hints['performance']) and not any(b['kind'] == 'performance' for b in blocks):
        flags.append('PERFORMANCE_MERGED_WITH_GRAPHICS')

    # watermark words inside the technical area
    wm_ex, wm_conflict = [], []
    for w in wm:
        r = fitz.Rect(w.rect.x0 - 0.3, w.rect.y0 - 0.3, w.rect.x1 + 0.3, w.rect.y1 + 0.3)
        if any(r.intersects(k) for b in blocks for k in b['clips']) or r.intersects(tol_clip):
            wm_conflict.append(w.text)
        wm_ex.append(r)

    # ---------------- exclusions: (frame band + title block + watermark) minus everything carried
    band = subtract(fitz.Rect(0, 0, W, H), interior)
    carried = [k for b in blocks for k in b['clips']] + [tol_clip, proj_clip]
    field_boxes = list(field_regions.values())
    excl = []
    for r in subtract_all([x & fitz.Rect(0, 0, W, H) for x in band], carried + field_boxes + [tb]):
        excl.append({'box': R(r), 'kind': 'outer_frame', 'reason': 'Frame band outside the inner frame rule (family rule); zone labels only.', 'review_status': 'AUTO_PROPOSED'})
    for r in subtract_all([tb], carried + field_boxes):
        excl.append({'box': R(r), 'kind': 'supplier_title_block', 'reason': 'Supplier title-block furniture located from its own labels (family rule).', 'review_status': 'AUTO_PROPOSED'})
    for k, r in field_regions.items():
        excl.append({'box': R(r), 'kind': 'replaced_title_field', 'field': k, 'reason': f'Source {k} cell value; re-set in the Kangsheng title frame.', 'review_status': 'AUTO_PROPOSED'})
    for r in wm_ex:
        if interior.intersects(r) and not tb.contains(r):
            for piece in subtract_all([r & interior], carried + field_boxes + [tb]):
                excl.append({'box': R(piece), 'kind': 'watermark', 'reason': 'Autodesk education watermark text object (family word list).', 'review_status': 'AUTO_PROPOSED'})

    # ---------------- vocabulary check of everything we exclude (text is data, never silently dropped)
    allowed_tb = tb_cfg['labels'] + family['watermark_patterns'] + family['watermark_single_chars']
    name_rows = [find(l) for l in tb_cfg['name_row_labels']]
    unknown = []
    carried_or_field = carried + field_boxes
    for w in words:
        c = fitz.Rect(w.rect.x0 + .3, w.rect.y0 + .3, w.rect.x1 - .3, w.rect.y1 - .3)
        covered = sum(max(0.0, (cb & c).width) * max(0.0, (cb & c).height) for cb in carried_or_field if cb.intersects(c))
        if covered + 0.05 >= c.width * c.height: continue
        if id(w) in wm_ids: continue
        if tb.contains(c):
            if any(a in w.text for a in allowed_tb): continue
            if any(n and abs((n.rect.y0 + n.rect.y1) / 2 - (w.rect.y0 + w.rect.y1) / 2) < 3 and n.rect.x1 < w.rect.x0 < tlft.coord for n in name_rows):
                continue  # signature names
            unknown.append({'where': 'title_block', 'text': w.text, 'box': R(w.rect)})
        elif not interior.contains(c):
            if re.fullmatch(r'[0-9A-Z]', w.text): continue
            unknown.append({'where': 'frame_band', 'text': w.text, 'box': R(w.rect)})
        else:
            unknown.append({'where': 'content_unassigned', 'text': w.text, 'box': R(w.rect)})
    if unknown: flags.append('EXCLUDED_TEXT_NOT_IN_VOCABULARY')
    if wm_conflict: flags.append('WATERMARK_OVERLAPS_TECHNICAL_CLIP')

    return dict(doc=doc, page=page, rot=rot, W=W, H=H, interior=interior, tb=tb, tol_clip=tol_clip,
                proj_clip=proj_clip, fields=fields, field_flags=field_flags, field_regions=field_regions,
                blocks=blocks, restored=restored, exclusions=excl, unknown=unknown, flags=flags,
                wm=[w.text for w in wm], wm_conflict=wm_conflict, seconds=time.perf_counter() - t0)


# ----------------------------------------------------------------- layout
def est_font(page, b):
    f = span_fonts(page, b['clip'])
    if f is None and b.get('outlined_perf'):
        hs = sorted(m.rect.height for m in b['mem'] if m.kind == 'item' and 1 < m.rect.height < 9)
        if hs: f = hs[int(len(hs) * 0.9)] / 0.72   # ~cap height of outlined glyphs -> point size
    return f


def span_fonts(page, clip):
    sizes = []
    for b in page.get_text('dict', clip=clip)['blocks']:
        for l in b.get('lines', []):
            for s in l['spans']:
                if s['text'].strip() and clip.contains(fitz.Rect(s['bbox'])): sizes.append(s['size'])
    return min(sizes) if sizes else None


def layout(A, family):
    """Right rail for table/performance; views & PCB at 1:1 near their source arrangement.
    If views do not fit: shrink the performance block (not below its font floor), then allow a
    tighter ink clearance.  Never scales views/PCB."""
    L = family['layout']; page = A['page']
    perfs = [b for b in A['blocks'] if b['kind'] == 'performance']
    done = False; trial = []
    tables = [b for b in A['blocks'] if b['kind'] == 'table']
    # tier 0: normal fonts; tier 1 (user rule): content completeness beats rail font size, so
    # shrink the part table and performance text further (never below the engine floor)
    tiers = [(L.get('performance_min_font_pt', L['min_font_pt']), 1.0)]
    if L.get('fallback_performance_min_font_pt') and not NO_FALLBACK:
        tiers += [(L['fallback_performance_min_font_pt'], t) for t in (1.0, 0.85, 0.7, 0.6)]
    A['table_shrink'] = 1.0
    for tier, (perf_floor, tshrink) in enumerate(tiers):
        A['table_shrink'] = tshrink
        if tshrink < 1.0 and tables:
            tf = min((span_fonts(page, b['clip']) or 99) * min(1.0, fitz.Rect(L['rail']).width / b['clip'].width) * tshrink for b in tables)
            if tf < L['min_font_pt']: break
        for gap in (L['block_gap'], 3, 2):
            for step in range(0, 60):
                trial = []; shrink = 1.0 - 0.025 * step
                if shrink <= 0.1: break
                if _layout_once(A, family, shrink, trial, gap):
                    done = True; break
                if not perfs: break
                f = min((est_font(page, b) or 99) * b['scale'] for b in perfs)
                if f < perf_floor + 0.3: break
            if done: break
        if done:
            if tier: trial.append('RAIL_SHRUNK_TO_FIT_CONTENT')
            break
    A['flags'].extend(trial)
    A['layout_perf_shrink'] = round(shrink, 3); A['layout_gap'] = gap


def _layout_once(A, family, shrink, flags, gap):
    L = family['layout']; rail = fitz.Rect(L['rail'])
    page = A['page']
    tables = [b for b in A['blocks'] if b['kind'] == 'table']
    perfs = [b for b in A['blocks'] if b['kind'] == 'performance']
    y = rail.y0
    for b in sorted(tables, key=lambda b: b['clip'].y0):
        c = b['clip']; s = min(1.0, rail.width / c.width)
        min_s = L.get('table_min_scale', 0.0)
        if s < min_s and L.get('rail_max_x0') is not None:
            # widen the right rail leftward (engine allows table x0 >= 0.62*page width)
            s = min(1.0, (rail.x1 - L['rail_max_x0']) / c.width)
            flags.append('RAIL_WIDENED_FOR_TABLE')
        if s < min_s: flags.append('TABLE_TOO_WIDE_FOR_RAIL')
        f = span_fonts(page, c)
        s *= A.get('table_shrink', 1.0)
        if f and f * s < L['min_font_pt']: flags.append('TABLE_FONT_TOO_SMALL_IN_RAIL')
        b['dst'] = (rail.x1 - c.width * s, y); b['scale'] = s
        y += c.height * s + L['rail_gap']
    y = max(y, L['performance_min_y'])
    for b in sorted(perfs, key=lambda b: b['clip'].y0):
        c = b['clip']; s = min(1.0, rail.width / c.width, (rail.y1 - y) / c.height) * shrink
        f = span_fonts(page, c)
        if s <= 0 or (f and f * s < L['min_font_pt']):
            flags.append('PERFORMANCE_DOES_NOT_FIT_RAIL'); s = max(s, 0.3)
        b['dst'] = (rail.x1 - c.width * s, y); b['scale'] = s
        y += c.height * s + L['rail_gap']

    occ = np.zeros((int(PAGE_H) + 2, int(PAGE_W) + 2), bool)
    def mark(r, g):
        x0 = max(0, int(math.floor(r.x0 - g))); y0 = max(0, int(math.floor(r.y0 - g)))
        x1 = min(occ.shape[1], int(math.ceil(r.x1 + g))); y1 = min(occ.shape[0], int(math.ceil(r.y1 + g)))
        occ[y0:y1, x0:x1] = True
    frame_in = fitz.Rect(FRAME.x0 + 3, FRAME.y0 + 3, FRAME.x1 - 3, FRAME.y1 - 3)
    occ[:, :int(frame_in.x0)] = True; occ[:int(frame_in.y0), :] = True
    occ[:, int(math.ceil(frame_in.x1)):] = True; occ[int(math.ceil(frame_in.y1)):, :] = True
    mark(TITLE_BOX, gap); mark(TOL_BOX, gap)
    A['occ_base'] = occ.copy()
    for b in tables + perfs:
        c = b['clip']; s = b['scale']; x, y0 = b['dst']
        mark(fitz.Rect(x, y0, x + c.width * s, y0 + c.height * s), gap)
    others = [b for b in A['blocks'] if b['kind'] not in ('table', 'performance')]
    dx = FRAME.x0 + 6 - A['interior'].x0; dy = FRAME.y0 + 6 - A['interior'].y0
    from scipy.signal import fftconvolve
    from scipy.ndimage import binary_dilation
    if 'ink1' not in A:
        pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
        arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
        A['ink1'] = arr[:, :, :3].min(2) < 245
    ink1 = A['ink1']
    occ_rect = np.zeros_like(occ); occ_ink = A['occ_base'].copy()
    from scipy.ndimage import zoom as _zoom
    for rb in ([] if RELAXED_RAIL else tables + perfs):   # rail blocks: their scaled INK may not sit inside a view's clip rectangle
        c = rb['clip']; sc = rb['scale']
        m0 = ink1[int(math.floor(c.y0)):int(math.ceil(c.y1)), int(math.floor(c.x0)):int(math.ceil(c.x1))]
        if not m0.any(): continue
        mz = _zoom(m0.astype(np.uint8), sc, order=0).astype(bool)
        mz = binary_dilation(mz, iterations=3)
        X, Y = int(round(rb['dst'][0])), int(round(rb['dst'][1]))
        hh = min(mz.shape[0], occ_ink.shape[0] - Y); ww = min(mz.shape[1], occ_ink.shape[1] - X)
        occ_ink[Y:Y + hh, X:X + ww] |= mz[:hh, :ww]
    ok = True
    for b in sorted(others, key=lambda b: -b['clip'].width * b['clip'].height):
        c = b['clip']
        x0, y0 = int(math.floor(c.x0)), int(math.floor(c.y0)); x1, y1 = int(math.ceil(c.x1)), int(math.ceil(c.y1))
        sel = np.zeros((y1 - y0, x1 - x0), bool)
        for k in b['clips']:
            sel[int(math.floor(k.y0)) - y0:int(math.ceil(k.y1)) - y0, int(math.floor(k.x0)) - x0:int(math.ceil(k.x1)) - x0] = True
        m = ink1[y0:y1, x0:x1] & sel
        md = binary_dilation(m, iterations=gap) if m.any() else m
        h, w = m.shape
        px, py = c.x0 + dx, c.y0 + dy
        b['dst'] = (px, py); b['scale'] = 1.0; b.pop('moved_pt', None)
        # positions (integer top-left of the rasterised clip) whose dilated ink hits nothing occupied
        corr = fftconvolve(occ.astype(np.float32), md[::-1, ::-1].astype(np.float32), mode='valid')
        # two-sided: already placed INK must not fall inside this block's clip rectangles either
        corr2 = fftconvolve(occ_ink.astype(np.float32), sel[::-1, ::-1].astype(np.float32), mode='valid')
        free = (corr < 0.5) & (corr2 < 0.5)
        # the whole clip rectangle must stay inside the drawing frame
        ys, xs = np.mgrid[0:free.shape[0], 0:free.shape[1]]
        inside = (xs >= frame_in.x0) & (ys >= frame_in.y0) & (xs + w <= frame_in.x1) & (ys + h <= frame_in.y1)
        cand = np.argwhere(free & inside)
        if cand.size == 0:
            flags.append(f'NO_FREE_SPACE_FOR_{b["kind"].upper()}'); ok = False; continue
        d = (cand[:, 1] - px) ** 2 + (cand[:, 0] - py) ** 2
        yy, xx = cand[int(np.argmin(d))]
        off_x, off_y = c.x0 - x0, c.y0 - y0
        b['dst'] = (float(xx) + off_x, float(yy) + off_y)
        occ[yy:yy + h, xx:xx + w] |= (m | (sel & False))
        occ_rect[yy:yy + h, xx:xx + w] |= sel
        occ_ink[yy:yy + h, xx:xx + w] |= binary_dilation(m, iterations=gap) if m.any() else m
        occ[yy:yy + h, xx:xx + w] |= sel
        b['moved_pt'] = round(math.hypot(xx + off_x - px, yy + off_y - py), 1)

    t = A['tol_clip']; s = min(1.0, (TOL_BOX.width - 3) / t.width, (TOL_BOX.height - 3) / t.height)
    A['tol'] = {'dst': (TOL_BOX.x0 + (TOL_BOX.width - t.width * s) / 2, TOL_BOX.y0 + 1.5), 'scale': s}
    p = A['proj_clip']; s = min(0.55, (PROJ_BOX.width - 2) / p.width, (PROJ_BOX.height - 2) / p.height)
    A['proj'] = {'dst': (PROJ_BOX.x0 + (PROJ_BOX.width - p.width * s) / 2,
                         PROJ_BOX.y0 + (PROJ_BOX.height - p.height * s) / 2), 'scale': s}
    return ok


# ----------------------------------------------------------------- manifest
def to_manifest(A, src_path, family, out_dir, font, assets_dir):
    groups = []; counts = collections.Counter()
    for b in A['blocks']:
        counts[b['kind']] += 1
        kind = {'view': 'view', 'pcb': 'pcb', 'table': 'table', 'performance': 'performance'}[b['kind']]
        g = {'id': f'{b["kind"]}_{counts[b["kind"]]}', 'kind': kind, 'clips': [R(k) for k in b['clips']],
             'reviewed_source_extent': R(b['extent']), 'dst': [round(b['dst'][0], 3), round(b['dst'][1], 3)],
             'scale': round(b['scale'], 6)}
        if id(b) in A['restored']: g['restored_source_rules'] = [A['restored'][id(b)]]
        groups.append(g)
    groups.append({'id': 'tolerance', 'kind': 'tolerance', 'clips': [R(A['tol_clip'])], 'reviewed_source_extent': R(A['tol_clip']),
                   'dst': [round(v, 3) for v in A['tol']['dst']], 'scale': round(A['tol']['scale'], 6)})
    groups.append({'id': 'projection', 'kind': 'projection', 'clips': [R(A['proj_clip'])], 'reviewed_source_extent': R(A['proj_clip']),
                   'dst': [round(v, 3) for v in A['proj']['dst']], 'scale': round(A['proj']['scale'], 6)})
    parts = [p for b in A['blocks'] if b['kind'] == 'table' for p in b['parts']]
    parts = list(dict.fromkeys(parts))
    f = A['fields']; model = f.get('model', '')
    ident = {'record_id': 'probe-' + sha(src_path)[:12], 'expected_model': model, 'observed_model': model,
             'model_evidence': 'AUTO-READ from the source title-block MODEL cell text (probe; not a review).'}
    if parts:
        ident['observed_parts'] = parts; ident['part_pattern'] = '^(?:' + '|'.join(re.escape(p) for p in parts) + ')$'
    else:
        ident['no_part_table_reason'] = 'AUTO: no part-number table detected'
    return {
        'schema_version': 2,
        'source': {'path': str(Path(src_path).resolve()), 'sha256': sha(src_path), 'page': 1,
                   'rotation': A['rot'], 'expected_pages': 1},
        'renderer': 'auto', 'identity': ident,
        'fields': {'model': model, 'title': f.get('title', ''), 'unit': f.get('unit', ''), 'sheet': f.get('sheet', ''),
                   'scale_text': f.get('scale_text', ''), 'size': f.get('size', ''), 'tolerances': [],
                   'no_tolerance_block_reason': ''},
        'assets': {'background': str(assets_dir / 'background.png'), 'brand_strip': str(assets_dir / 'brand-strip.png'),
                   'font': str(font)},
        'groups': groups,
        'coverage': {'mode': 'full-page-minus-exclusions', 'exclude': A['exclusions']},
        'color_profile': family['color_profile'], 'stroke_profile': 'source',
        'probe': {'generator': 'auto_manifest.py phase-0', 'family': family['family_id'],
                  'not_a_review': True, 'supplier_id_omitted_reason': 'engine requires a reviewed source_fields ledger for zhiyuan; probe carries raw tolerance cell instead'},
    }


def _cff_to_ttf(font):
    """Convert a (CID-keyed) CFF subset to a quadratic glyf TTF so PDF embedding keeps a
    simple glyph/cmap relation (probe-only substitute for the production Heiti subset)."""
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib import newTable
    gs = font.getGlyphSet(); order = font.getGlyphOrder(); glyf = {}
    for name in order:
        pen = TTGlyphPen(gs); gs[name].draw(Cu2QuPen(pen, 1.0, reverse_direction=True)); glyf[name] = pen.glyph()
    font['loca'] = newTable('loca'); g = font['glyf'] = newTable('glyf')
    g.glyphOrder = order; g.glyphs = glyf
    del font['CFF ']
    if 'VORG' in font: del font['VORG']
    font['maxp'] = maxp = newTable('maxp'); maxp.tableVersion = 0x00010000
    for k in ('maxZones','maxTwilightPoints','maxStorage','maxFunctionDefs','maxInstructionDefs','maxStackElements','maxSizeOfInstructions','maxComponentElements'):
        setattr(maxp, k, 0)
    maxp.maxZones = 1
    font['head'].glyphDataFormat = 0; font['head'].indexToLocFormat = 0
    font.sfntVersion = '\x00\x01\x00\x00'
    post = font['post']; post.formatType = 2.0; post.extraNames = []; post.mapping = {}; post.glyphOrder = order
    return font


def derive_without_watermark(src_path, family, out):
    """Remove ONLY the watermark text-show operators from the content stream.
    Watermark operators are identified by the (font resource, size) signature of spans whose
    text matches the family watermark words; then every other word and every vector drawing
    must be byte-for-byte unchanged in position/text (verified), otherwise BLOCKED."""
    import pikepdf
    doc = fitz.open(src_path); page = doc[0]
    pats = family['watermark_patterns']
    sig_fonts = set()
    for b in page.get_text('dict')['blocks']:
        for l in b.get('lines', []):
            for sp in l['spans']:
                if any(p in sp['text'] for p in pats):
                    sig_fonts.add((sp['font'], round(sp['size'], 1)))
    res = {}
    for xref, ext, typ, base, name, enc in page.get_fonts():
        res.setdefault(base.split('+')[-1], set()).add(name)
    sig = {(n, sz) for f, sz in sig_fonts for n in res.get(f, ())}
    def words_of(pg):
        return sorted((round(w[0], 1), round(w[1], 1), round(w[2], 1), round(w[3], 1), w[4]) for w in pg.get_text('words'))
    def draws_of(pg):
        return [(tuple(round(v, 3) for v in d['rect']), d.get('color'), d.get('fill')) for d in pg.get_drawings()]
    before_words = [w for w in words_of(page) if not any(p in w[4] for p in pats) and w[4] not in family['watermark_single_chars']]
    before_draw = draws_of(page)
    pdf = pikepdf.open(src_path); pg = pdf.pages[0]
    ops = pikepdf.parse_content_stream(pg)
    keep, removed, cur = [], 0, None
    for operands, op in ops:
        o = str(op)
        if o == 'Tf': cur = (str(operands[0]).lstrip('/'), round(float(operands[1]), 1))
        if o in ('Tj', 'TJ', "'", '"') and cur in sig:
            removed += 1; continue
        keep.append((operands, op))
    pg.Contents = pdf.make_stream(pikepdf.unparse_content_stream(keep))
    derived = out / 'derived-no-watermark.pdf'
    pdf.save(derived, deterministic_id=True)
    p2 = fitz.open(derived)[0]
    after_words = words_of(p2)
    left = [w for w in after_words if any(p in w[4] for p in pats)]
    after_non = [w for w in after_words if w[4] not in family['watermark_single_chars'] or w in before_words]
    ver = {'method': 'content-stream text-show operators with watermark font/size signature',
           'signature': sorted(map(list, sig)), 'operators_removed': removed,
           'original_sha256': sha(src_path), 'derived_sha256': sha(derived),
           'watermark_words_remaining': len(left),
           'other_words_identical': before_words == after_non, 'other_words': len(before_words),
           'vector_drawings_identical': before_draw == draws_of(p2), 'vector_drawings': len(before_draw)}
    ver['pass'] = bool(removed) and ver['other_words_identical'] and ver['vector_drawings_identical'] and not left
    return derived, ver


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('source'); ap.add_argument('--out', required=True)
    ap.add_argument('--family', default=str(HERE / 'zhiyuan-family.json'))
    ap.add_argument('--font', required=True); ap.add_argument('--assets', required=True)
    ap.add_argument('--clip-mode', choices=['single', 'multi'], default='single')
    ap.add_argument('--relaxed-rail', action='store_true')
    ap.add_argument('--no-fallback', action='store_true', help='do not shrink the rail below normal fonts')
    ap.add_argument('--font-index', type=int, default=0, help='face index when --font is a .ttc collection')
    a = ap.parse_args()
    global CLIP_MODE, RELAXED_RAIL, NO_FALLBACK; CLIP_MODE = a.clip_mode; RELAXED_RAIL = a.relaxed_rail; NO_FALLBACK = a.no_fallback
    family = json.loads(Path(a.family).read_text())
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    report = {'source': a.source, 'source_sha256': sha(a.source), 'clip_mode': CLIP_MODE, 'relaxed_rail': RELAXED_RAIL}
    try:
        src = a.source
        A = analyse(src, family, out)
        if A['wm_conflict']:
            derived, ver = derive_without_watermark(src, family, out)
            report['watermark_object_removal'] = ver
            if not ver['pass']: raise Blocked('WATERMARK_REMOVAL_NOT_VERIFIED')
            src = str(derived); A = analyse(src, family, out)
            if A['wm_conflict']: raise Blocked('WATERMARK_STILL_CONFLICTS')
        layout(A, family)
        # source-bound title glyph subset (same method as production prepare-title-font)
        from fontTools import subset as ft_subset
        from fontTools.ttLib import TTFont
        text = ''.join(A['fields'].get(k, '') for k in ('model', 'title'))
        tf = TTFont(a.font, fontNumber=a.font_index); sub = ft_subset.Subsetter(); sub.populate(text=text); sub.subset(tf)
        font_out = (out / 'title-font.ttf').resolve()
        (_cff_to_ttf(tf) if 'CFF ' in tf else tf).save(font_out)
        m = to_manifest(A, src, family, out, font_out, Path(a.assets))
        (out / 'manifest.json').write_text(json.dumps(m, ensure_ascii=False, indent=1))
        report.update(status='MANIFEST_WRITTEN', rotation=A['rot'], seconds=round(A['seconds'], 3),
                      fields=A['fields'], field_flags=A['field_flags'], flags=sorted(set(A['flags'])),
                      unknown_excluded_text=A['unknown'], watermark_words=A['wm'], watermark_conflicts=A['wm_conflict'],
                      groups=[{'id': g['id'], 'kind': g['kind'], 'clip': g['clips'][0], 'dst': g['dst'], 'scale': g['scale']} for g in m['groups']],
                      moved=[{'kind': b['kind'], 'moved_pt': b.get('moved_pt')} for b in A['blocks'] if 'moved_pt' in b],
                      exclusions=len(m['coverage']['exclude']))
    except Blocked as e:
        report.update(status='BLOCKED', reason=str(e))
    (out / 'auto-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps({k: report.get(k) for k in ('status', 'reason', 'flags', 'fields', 'field_flags')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
