#!/usr/bin/env python3
"""DWG → 矢量 PDF（LibreDWG dwg2dxf + ezdxf 渲染）。修复 LibreDWG 导出的多行文字把换行写进值里的问题。
  python pipeline/dwg_to_pdf.py 原图.dwg 输出.pdf"""
import subprocess, sys, tempfile
from pathlib import Path
FONT = 'NotoSansCJK-Regular.ttc'


def fix_dxf(raw: str) -> str:
    lines = raw.split('\n'); out = []; i = 0
    while i < len(lines):
        code = lines[i].strip()
        if not code.lstrip('-').isdigit():
            out[-1] = out[-1].rstrip('\r') + lines[i]; i += 1; continue   # stray line: part of previous value
        out.append(lines[i]); 
        if i + 1 < len(lines): out.append(lines[i + 1])
        i += 2
    return '\n'.join(out)


def convert(dwg, pdf):
    from ezdxf import recover
    from ezdxf.addons.drawing import RenderContext, Frontend, layout, config
    from ezdxf.addons.drawing import pymupdf as _pm
    from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend
    for _c in vars(_pm).values():   # no PDF layers: layer names may not be valid text
        if isinstance(_c, type) and 'get_optional_content_group' in vars(_c):
            _c.get_optional_content_group = lambda self, layer: 0
    with tempfile.TemporaryDirectory() as t:
        dxf = Path(t) / 'a.dxf'
        subprocess.run(['dwg2dxf', '-y', '--as', 'r2004', '-o', str(dxf), str(dwg)], capture_output=True, check=False)
        b = dxf.read_bytes()
        raw = b.decode('utf-8', 'replace')   # LibreDWG writes UTF-8 text
        import re
        raw = re.sub(r'(\$ACADVER\s*\r?\n\s*1\s*\r?\n)AC10\d\d', r'\1AC1021', fix_dxf(raw), count=1)  # R2007+: UTF-8
        dxf.write_text(raw, encoding='utf-8')
        doc, _ = recover.readfile(str(dxf))
    # SHX fonts are not available: render every text style with one CJK font that also has Ω, °, ±
    for st in doc.styles:
        st.dxf.font = FONT
        if st.dxf.hasattr('bigfont'): st.dxf.discard('bigfont')
    be = PyMuPdfBackend()
    cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE, color_policy=config.ColorPolicy.COLOR,
                               lineweight_policy=config.LineweightPolicy.RELATIVE)
    Frontend(RenderContext(doc), be, config=cfg).draw_layout(doc.modelspace())
    Path(pdf).write_bytes(be.get_pdf_bytes(layout.Page(0, 0, layout.Units.mm, margins=layout.Margins.all(2))))


if __name__ == '__main__':
    convert(sys.argv[1], sys.argv[2])
