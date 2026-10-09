"""Default title font, so colleagues on Mac / Windows / Linux need not pass --font.

The Kangsheng title block uses a Heiti (黑体) face: STHeiti Medium on Mac (what every sheet so far was made with),
SimHei on Windows (the same 黑体 style), Noto Sans CJK on Linux. `KS_FONT` (path, optional `KS_FONT_INDEX`) overrides.
"""
import os
from pathlib import Path

CANDIDATES = [
    ('/System/Library/Fonts/STHeiti Medium.ttc', 0),            # Mac (all existing Kangsheng sheets)
    ('/System/Library/Fonts/STHeiti Light.ttc', 0),
    (r'C:\Windows\Fonts\simhei.ttf', 0),                         # Windows 黑体
    (r'C:\Windows\Fonts\msyh.ttc', 0),                           # Windows 微软雅黑
    ('/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc', 2),  # Linux (index 2 = SC)
    ('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', 2),
    ('/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc', 2),
]


def default_font():
    """(path, index) of the first title font found; raises SystemExit with a hint when none is."""
    if os.environ.get('KS_FONT'):
        return os.environ['KS_FONT'], int(os.environ.get('KS_FONT_INDEX', '0'))
    for p, i in CANDIDATES:
        if Path(p).exists(): return p, i
    raise SystemExit('FONT_NOT_FOUND: 找不到中文黑体字体，请用 --font 指定（Mac: STHeiti Medium.ttc；Windows: C:\\Windows\\Fonts\\simhei.ttf），'
                     '或设置环境变量 KS_FONT')


def resolve(font, font_index):
    """--font as given, or the default when it was left out."""
    if font: return font, font_index
    return default_font()
