#!/usr/bin/env python3
"""DWG → 矢量 PDF（LibreDWG dwg2dxf + ezdxf 渲染）。修复 LibreDWG 导出的多行文字把换行写进值里的问题。
  python pipeline/dwg_to_pdf.py 原图.dwg 输出.pdf"""
import subprocess, sys, tempfile
from pathlib import Path
FONT = 'NotoSansCJK-Regular.ttc'


def fix_text(s):
    """Old DWGs store GBK text; LibreDWG hands it over as Latin-1 (sometimes UTF-8 re-encoded once more)."""
    if not s or all(ord(c) < 128 or c in '±°ΩΦφ×²³µ' for c in s): return s   # plain text with drawing symbols
    def gbk(t):
        try: return t.encode('latin-1').decode('gbk')
        except Exception: return None
    for cand in (s,):
        pass
    try: once = s.encode('cp1252').decode('utf-8')
    except Exception: once = None
    for t in ([once] if once else []) + [s]:
        r = gbk(t)
        if r is not None and r != t:
            return r.replace('∴', '±')   # A1E0 is used for ± by these drawings' SHX big font
    return s


def fix_dxf(raw: str) -> str:
    lines = raw.split('\n'); out = []; i = 0
    while i < len(lines):
        code = lines[i].strip()
        if not code.lstrip('-').isdigit():
            out[-1] = out[-1].rstrip('\r') + lines[i]; i += 1; continue   # stray line: part of previous value
        out.append(lines[i]); 
        if i + 1 < len(lines): out.append(lines[i + 1])
        i += 2
    # LibreDWG writes an MTEXT's first chunk (code 1) before its continuation chunks (code 3); DXF readers
    # append code 1 last, which scrambles long notes: re-emit the chunks in file order, last one as code 1
    pairs = [(out[k], out[k + 1] if k + 1 < len(out) else '') for k in range(0, len(out), 2)]
    res = []; k = 0
    while k < len(pairs):
        c, v = pairs[k]
        if c.strip() == '0' and v.strip() == 'MTEXT':
            e = k + 1
            while e < len(pairs) and pairs[e][0].strip() != '0': e += 1
            ent = pairs[k:e]
            idx = [n for n, (cc, _) in enumerate(ent) if cc.strip() in ('1', '3')]
            codes = [ent[n][0].strip() for n in idx]
            if '3' in codes and codes.index('1') < len(codes) - 1:
                vals = [ent[n][1] for n in idx]
                for m_, n in enumerate(idx):
                    ent[n] = (ent[n][0].replace(ent[n][0].strip(), '1' if m_ == len(idx) - 1 else '3'), vals[m_])
            res += ent; k = e; continue
        res.append(pairs[k]); k += 1
    return '\n'.join(x for pr in res for x in pr)


def convert(dwg, pdf):
    from ezdxf import recover, audit as _audit
    def _safe_all(self):   # LibreDWG output can contain broken owner handles: skip entities whose audit crashes
        for e in list(self.entitydb.values()):
            if not e.is_alive: continue
            try: e.audit(self)
            except Exception: pass
    _audit.Auditor.audit_all_database_entities = _safe_all
    from ezdxf.layouts import base as _lb
    _ro = _lb.BaseLayout.get_redraw_order
    def _safe_ro(self):   # a broken extension dictionary must not stop the drawing: fall back to file order
        try: return _ro(self)
        except Exception: return {}
    _lb.BaseLayout.get_redraw_order = _safe_ro
    from ezdxf.addons.drawing import RenderContext, Frontend, layout, config
    from ezdxf.addons.drawing import pymupdf as _pm
    from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend
    for _c in vars(_pm).values():   # no PDF layers: layer names may not be valid text
        if isinstance(_c, type) and 'get_optional_content_group' in vars(_c):
            _c.get_optional_content_group = lambda self, layer: 0
    def dump(extra, t, name):
        dxf = Path(t) / name
        subprocess.run(['dwg2dxf', '-y', *extra, '-o', str(dxf), str(dwg)], capture_output=True, check=False)
        if not dxf.exists(): return None
        out = []
        for ln in dxf.read_bytes().split(b'\n'):   # UTF-8, but old DWGs leak raw GBK bytes (e.g. Ω = A6 B8)
            try: out.append(ln.decode('utf-8'))
            except UnicodeDecodeError: out.append(ln.decode('gbk', 'replace'))
        return '\n'.join(out)
    def load(extra):
        with tempfile.TemporaryDirectory() as t:
            dxf = Path(t) / 'a.dxf'
            if extra == 'merge':
                # LibreDWG sometimes stops after BLOCKS: keep header/tables/blocks of the full dump and
                # take the ENTITIES section from the entities-only (-m) dump
                full = dump(['--as', 'r2004'], t, 'f.dxf'); mini = dump(['-m'], t, 'm.dxf')
                if not full or not mini or '\nENTITIES' not in mini: return None
                import re as _re
                m = _re.search(r'  0\r?\nSECTION\r?\n  2\r?\nENTITIES\r?\n.*?  0\r?\nENDSEC\r?\n', mini, _re.S)
                if not m: return None
                raw = full.rstrip() + '\r\n' + m.group(0) + '  0\r\nEOF\r\n'
            else:
                raw = dump(extra, t, 'a.dxf')
                if raw is None: return None
            import re
            raw = re.sub(r'(\$ACADVER\s*\r?\n\s*1\s*\r?\n)AC10\d\d', r'\1AC1021', fix_dxf(raw), count=1)  # R2007+: UTF-8
            dxf.write_text(raw, encoding='utf-8')
            try:
                d, _ = recover.readfile(str(dxf))
            except Exception:
                return None
            return d if len(d.modelspace()) else None
    # full DXF first; LibreDWG sometimes aborts before ENTITIES -> fall back to entities-only (-m)
    doc = load(['--as', 'r2004']) or load('merge') or load(['-m'])
    if doc is None: raise SystemExit('DWG_CONVERT_FAILED')
    # SHX fonts are not available: render every text style with one CJK font that also has Ω, °, ±
    for st in doc.styles:
        st.dxf.font = FONT
        if st.dxf.hasattr('bigfont'): st.dxf.discard('bigfont')
    be = PyMuPdfBackend()
    cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE, color_policy=config.ColorPolicy.COLOR,
                               lineweight_policy=config.LineweightPolicy.RELATIVE)
    # drop entities with broken (NaN/huge) extents and far outliers (e.g. stray points far from the sheet)
    from ezdxf import bbox as _bbox
    import math, statistics
    msp = doc.modelspace(); ok = {}
    for e in doc.entitydb.values():   # repair GBK text everywhere (model space and blocks)
        try:
            t = e.dxftype()
            if t in ('TEXT', 'ATTRIB', 'ATTDEF'): e.dxf.text = fix_text(e.dxf.text)
            elif t == 'MTEXT':   # inline font switches point to SHX/Windows fonts we do not have: use the style font
                import re as _r
                e.text = fix_text(_r.sub(r'\\[Ff][^;]*;', '', e.text))
            elif t == 'DIMENSION' and e.dxf.get('text'): e.dxf.text = fix_text(e.dxf.text)
        except Exception: pass
    for e in msp:   # LibreDWG -m dumps can drop the layer name: such entities would be invisible
        try:
            if not e.dxf.get('layer'): e.dxf.layer = '0'
        except Exception: pass
    for e in msp:
        try:
            if e.dxftype() == 'LWPOLYLINE':   # LibreDWG can emit polylines with broken vertex data
                pts = list(e.get_points())
                if len(pts) < 2: continue
            b = _bbox.extents([e], fast=True)
            if b.has_data and all(math.isfinite(v) for v in (*b.extmin, *b.extmax)): ok[e.dxf.handle] = b
        except Exception: pass
    if ok:
        cx = statistics.median((b.extmin.x + b.extmax.x) / 2 for b in ok.values())
        cy = statistics.median((b.extmin.y + b.extmax.y) / 2 for b in ok.values())
        sz = sorted(max(b.size.x, b.size.y) for b in ok.values())[int(len(ok) * 0.98)] or 1.0
        lim = 20 * sz
        keep = {h for h, b in ok.items() if abs((b.extmin.x + b.extmax.x) / 2 - cx) < lim and abs((b.extmin.y + b.extmax.y) / 2 - cy) < lim}
    else:
        keep = set()
    Frontend(RenderContext(doc), be, config=cfg).draw_layout(msp, filter_func=lambda e: e.dxf.handle in keep)
    Path(pdf).write_bytes(be.get_pdf_bytes(layout.Page(0, 0, layout.Units.mm, margins=layout.Margins.all(2))))


if __name__ == '__main__':
    convert(sys.argv[1], sys.argv[2])
