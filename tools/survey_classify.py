#!/usr/bin/env python3
"""批量给一个目录里的原图 PDF 判图框：对每张跑 frame_match.match，写 JSON（状态/最像模板/旋转/分数）。
  python tools/survey_classify.py PDF目录 输出.json
状态：OK=可自动出图；AMBIGUOUS/UNKNOWN_FRAME=要先做模板；NO_FRAME=没找到图框（多半不是连接器图纸）。
只读线条几何，不改任何文件；已有结果的文件会跳过，可断点续跑。"""
import json, sys, os, glob
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
import fitz, frame_match as fm

src, out_path = sys.argv[1], sys.argv[2]
out = json.load(open(out_path)) if os.path.exists(out_path) else {}
sigs = fm.load()
for pdf in sorted(glob.glob(os.path.join(src, '*.pdf'))):
    k = Path(pdf).stem
    if k in out: continue
    try:
        d = fitz.open(pdf); npg = len(d); nd = len(d[0].get_drawings()); d.close()
        r = fm.match(pdf, None, 0, sigs); r['pages'] = npg; r['drawings'] = nd
    except BaseException as e:
        r = {'status': 'ERR', 'err': str(e)[:80]}
    out[k] = r
    json.dump(out, open(out_path, 'w'), ensure_ascii=False)
    print(k, r.get('status'), r.get('template'), r.get('score'), flush=True)
