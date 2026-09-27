#!/usr/bin/env python3
"""一个 DWG 里画了多张图（横排或竖排）时，按空白间隔拆成单张矢量 PDF，再逐张交给 cad_family。
  python pipeline/split_sheets.py 原图.pdf 输出目录 [--axis x|y] [--min-gap 4]
输出：输出目录/sheet_00.pdf …（按从左到右 / 从上到下编号）。图框上方的绿色图名标签会被裁掉。
两张图框紧贴（中间没有空白）时拆不开，会报 WIDE_SEGMENT，需人工看一眼再定切分位置。"""
import argparse, json
from pathlib import Path
import fitz, numpy as np


def pts(it):
    k = it[0]
    if k == 'l': return [it[1], it[2]]
    if k == 'c': return [it[1], it[2], it[3], it[4]]
    if k == 're': return [it[1].tl, it[1].br]
    if k == 'qu': return [it[1].ul, it[1].lr]
    return []


def segments(page, axis, min_gap):
    pix = page.get_pixmap(dpi=144, alpha=False)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].min(2) < 200
    prof = a.sum(0 if axis == 'x' else 1); k = pix.w / page.rect.width
    out, s = [], None
    for i, v in enumerate(prof > 0):
        if v and s is None: s = i
        if not v and s is not None: out.append((s, i)); s = None
    if s is not None: out.append((s, len(prof)))
    m = []
    for s, e in out:
        if m and (s - m[-1][1]) / k < min_gap: m[-1] = (m[-1][0], e)
        else: m.append((s, e))
    segs = [(s / k, e / k) for s, e in m if (e - s) / k > 20]
    return segs


def cut(page, clip, out):
    D = page.get_drawings()
    ys = [min(q.y for q in pts(it)) for d in D for it in d['items'] if it[0] == 'l'
          and abs(it[1].y - it[2].y) < 0.3 and abs(it[1].x - it[2].x) > 0.6 * clip.width
          and clip.contains(it[1]) and clip.contains(it[2])]
    if ys: clip.y0 = min(ys) - 0.8      # start at the frame: drops a sheet-name label drawn above it
    m = fitz.Matrix(1, 0, 0, 1, -clip.x0, -clip.y0)
    doc = fitz.open(); p = doc.new_page(width=clip.width, height=clip.height); sh = p.new_shape()
    for d in D:
        its = [it for it in d['items'] if pts(it) and all(clip.contains(q) for q in pts(it))]
        if not its: continue
        for it in its:
            k = it[0]
            if k == 'l': sh.draw_line(it[1] * m, it[2] * m)
            elif k == 'c': sh.draw_bezier(it[1] * m, it[2] * m, it[3] * m, it[4] * m)
            elif k == 're': sh.draw_rect(it[1] * m)
            elif k == 'qu': sh.draw_quad(it[1] * m)
        t = d['type']
        sh.finish(color=d.get('color') if t in ('s', 'fs') else None, fill=d.get('fill') if t in ('f', 'fs') else None,
                  width=d.get('width') or 0, closePath=d.get('closePath', False), even_odd=d.get('even_odd', False))
    sh.commit(); doc.save(out)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('pdf'); ap.add_argument('out')
    ap.add_argument('--axis', choices=['x', 'y']); ap.add_argument('--min-gap', type=float, default=4.0)
    a = ap.parse_args()
    page = fitz.open(a.pdf)[0]; R = page.rect
    axis = a.axis or ('x' if R.width > R.height else 'y')
    segs = segments(page, axis, a.min_gap)
    sizes = sorted(e - s for s, e in segs); med = sizes[len(sizes) // 2]
    Path(a.out).mkdir(parents=True, exist_ok=True); rep = []
    for i, (s, e) in enumerate(segs):
        clip = fitz.Rect(s - 1, R.y0, e + 1, R.y1) if axis == 'x' else fitz.Rect(R.x0, s - 1, R.x1, e + 1)
        f = Path(a.out) / f'sheet_{i:02d}.pdf'; cut(page, clip, str(f))
        rep.append({'file': str(f), 'span': [round(s, 1), round(e, 1)],
                    'flag': 'WIDE_SEGMENT' if len(segs) > 1 and (e - s) > 1.5 * med else None})
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
