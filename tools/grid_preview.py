#!/usr/bin/env python3
"""新供应商图框：在原图内框上画 5% 网格，用来量出供应商标题栏/修订栏的比例，写进 families/cad_templates.json。
  python tools/grid_preview.py 原图.pdf 输出.png [--rotate 270] [--clip x0,y0,x1,y1] [--frame-bottom title_top|inner_ring]"""
import argparse, sys
from pathlib import Path
import fitz
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
from cad_family import analyse

ap = argparse.ArgumentParser(); ap.add_argument('pdf'); ap.add_argument('png')
ap.add_argument('--rotate', type=int, default=0); ap.add_argument('--clip'); ap.add_argument('--frame-bottom'); ap.add_argument('--search', type=float, default=0.12)
a = ap.parse_args()
p = fitz.open(a.pdf)[0]
if a.rotate: p.set_rotation((p.rotation + a.rotate) % 360)
if p.rotation: p.remove_rotation()
clip = [float(v) for v in a.clip.split(',')] if a.clip else None
D, bb, I, _ = analyse(p, [[0, 0, 0.01, 0.01]], a.frame_bottom, clip, a.search)
print('inner frame', I)
sh = p.new_shape()
for k in range(1, 20):
    x = I.x0 + k / 20 * I.width; y = I.y0 + k / 20 * I.height
    sh.draw_line((x, I.y0), (x, I.y1)); sh.draw_line((I.x0, y), (I.x1, y))
sh.finish(color=(1, 0, 0), width=max(0.1, I.width / 3000)); sh.commit()
fs = max(2, I.width / 120)
for k in range(1, 20):
    p.insert_text((I.x0 + k / 20 * I.width + 0.3, I.y0 + fs), f'{k*5}', fontsize=fs, color=(1, 0, 0))
    p.insert_text((I.x0 + 0.5, I.y0 + k / 20 * I.height - 0.3), f'{k*5}', fontsize=fs, color=(1, 0, 0))
z = 1400 / max(I.width, I.height)
p.get_pixmap(matrix=fitz.Matrix(z, z), clip=fitz.Rect(clip) if clip else None).save(a.png)
