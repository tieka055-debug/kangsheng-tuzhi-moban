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
from cad_family import analyse, long_lines   # noqa: E402


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
    fr = []
    for r in rects:
        q = [round((r.x0 - I.x0) / I.width, 3), round((r.y0 - I.y0) / I.height, 3),
             round((r.x1 - I.x0) / I.width, 3), round((r.y1 - I.y0) / I.height, 3)]
        q = [min(1, max(0, v)) for v in q]
        if q not in fr: fr.append(q)
    fr.sort()
    fr = [q for q in fr if not any(o != q and o[0] <= q[0] and o[1] <= q[1] and o[2] >= q[2] and o[3] >= q[3] for o in fr)]   # drop boxes inside others
    for e in a.extra: fr.append([float(v) for v in e.split(',')])
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
