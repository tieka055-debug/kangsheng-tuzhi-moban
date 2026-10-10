#!/usr/bin/env python3
"""认不出的图，先拿最像的几个已有模板各出一张，拼成一张对照图给人挑（docs/NEW_SUPPLIER.md 第 2 步）。
  python tools/try_templates.py 原图.pdf [--out 目录] [--top 3] [--clip x0,y0,x1,y1]
用 frame_match 的打分取前 3 个不同的模板（各带自己最像的旋转、搜索带），各跑一遍 cad_family，输出：
  <out>/候选N-模板名/      各自的出图和 report.json
  <out>/试模板对照.png     三张「原图对照」上下拼在一起，标签 = 模板、rotate、相似度、警告
挑中干净的那个：job 里写 "template"/"rotate"，再按第 5 步登记样本；都不干净就用 tools/new_template.py 起草。
只读原图和模板，不改仓库。多页 PDF 先用 tools/drawing_pages.py 挑出图纸页（这里只看第 1 页）。"""
import argparse, json, sys, tempfile
from pathlib import Path
import fitz
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline')); sys.path.insert(0, str(ROOT / 'engine')); sys.path.insert(0, str(ROOT / 'tools'))
import frame_match as fm   # noqa: E402
from contact_sheet import contact_sheet, page_pixmap   # noqa: E402


def top_templates(best, n):
    """first n distinct templates of a frame_match.scored() list, each at its own best rotation (near ties -> rotate 0)"""
    out = []
    for b in best:
        if b[2] in [o[2] for o in out]: continue
        out.append(fm.pick([c for c in best if c[2] == b[2]]))
        if len(out) == n: break
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('pdf'); ap.add_argument('--out'); ap.add_argument('--top', type=int, default=3)
    ap.add_argument('--clip')
    a = ap.parse_args()
    pdf = str(Path(a.pdf).resolve()); clip = [float(v) for v in a.clip.split(',')] if a.clip else None
    out = Path(a.out or tempfile.mkdtemp()); out.mkdir(parents=True, exist_ok=True)
    if len(fitz.open(pdf)) > 1: print('多页 PDF：这里只看第 1 页；图纸不在第 1 页就先跑 tools/drawing_pages.py')
    best = fm.scored(pdf, clip)
    if not best: raise SystemExit('NO_FRAME：找不到图框，没法试模板')
    import cad_family as CF, fonts as FT
    font, fi = FT.default_font()
    cells = []
    for k, (sc, rot, tpl, se) in enumerate(top_templates(best, a.top), 1):
        d = out / f'候选{k}-{tpl}'; d.mkdir(exist_ok=True)
        job = {'source': pdf, 'model': Path(pdf).stem, 'title': '连接器', 'template': tpl, 'rotate': rot,
               'tolerance': {'linear_tolerances': [{'tier': 'X.', 'value': '±0.3'}], 'angular_tolerances': [],
                             'additional_tolerance_conditions': []}}
        if se != 0.12: job['frame_search'] = se
        if clip: job['clip'] = clip
        (d / 'job.json').write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding='utf-8')
        head = f'候选{k}  {tpl}  rotate {rot}  相似度 {sc:.3f}'
        try:
            rep = CF.run_layouts(job, d, font, fi)
            png = next(d.glob('*原图对照.png'))
            warn = rep.get('warnings') or []
            cells.append((str(png), head + (f"  警告: {'; '.join(w.split(':')[0] for w in warn)}" if warn else ''), False))
            print(head, '出图', png, *(['警告'] + warn if warn else []))
        except (SystemExit, Exception) as e:   # a template that does not fit this frame at all: show the source instead
            cells.append((page_pixmap(pdf, rot, size=1200), f'{head}  出图失败: {str(e)[:60]}', True))
            print(head, '出图失败', str(e)[:200])
    first = next((c[0] for c in cells if isinstance(c[0], str)), None)
    w, h = (fitz.Pixmap(first).width, fitz.Pixmap(first).height) if first else (2600, 980)
    cw = 1800; ch = int(cw * h / w) + 8
    png = contact_sheet(cells, out / '试模板对照.png', cols=1, cell_w=cw, cell_h=ch, label_h=30)
    print('对照图', png)
    print('挑中干净的：job 里写 "template"/"rotate"，再 frame_match.py --learn 登记；都不干净：tools/new_template.py 起草')


if __name__ == '__main__':
    main()
