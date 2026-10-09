#!/usr/bin/env python3
"""多页 PDF（承认书/规格书：封面、目录、测试报告 + 图纸页）：找出图纸页，各存成单页 PDF，再走正常流程。
  python tools/drawing_pages.py 原图.pdf 输出目录      -> 输出目录/<原名>-p<页号>.pdf，并打印每页判断
判断：页内能找到图框（四边长线）且矢量线条 >= 300 条才算图纸页；封面/文字页/扫描页跳过。
单页 PDF 原样复制。只读原图，矢量原样搬运（insert_pdf）。"""
import sys
from pathlib import Path
import fitz
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
from cad_family import analyse   # noqa: E402


def is_drawing(page):
    n = len(page.get_drawings())
    if n < 300: return False, n, 'few_lines'
    for rot in (0, 90):
        try:
            p = page
            analyse(p, [[0, 0, 0.01, 0.01]], None, None, 0.12)
            return True, n, 'frame'
        except SystemExit:
            pass
    return False, n, 'no_frame'


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(src); kept = []
    for i, p in enumerate(doc):
        ok, n, why = is_drawing(p)
        print(f'p{i + 1}: {"图纸" if ok else "跳过"} ({n} 条线, {why})')
        if ok: kept.append(i)
    for i in kept:
        d = fitz.open(); d.insert_pdf(doc, from_page=i, to_page=i)
        name = f'{src.stem}-p{i + 1}.pdf' if len(doc) > 1 else src.name
        d.save(out / name, garbage=3, deflate=True)
    print(f'{len(kept)} 页图纸 -> {out}')


if __name__ == '__main__':
    main()
