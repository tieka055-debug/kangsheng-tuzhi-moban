"""品牌配置加载：brands/<name>.json → 坐标 Rect、颜色元组。康生是默认品牌，值与原先写死的完全一致。"""
import json
from pathlib import Path
import pymupdf as fitz

ROOT = Path(__file__).resolve().parents[1]
BRANDS = ('kangsheng', 'runqing')


def load(name='kangsheng'):
    if name not in BRANDS:
        raise SystemExit(f'未知品牌 {name!r}，可选：{", ".join(BRANDS)}')
    cfg = json.loads((ROOT / 'brands' / f'{name}.json').read_text())
    b = dict(cfg)
    b['blue'] = tuple(v / 255 for v in cfg['blue_rgb255'])
    b['gold'] = tuple(v / 255 for v in cfg['gold_rgb255'])
    for k in ('frame', 'reserved', 'rail', 'views_area', 'slot', 'title_box', 'tolerance_box'):
        if k in cfg:
            b[k] = fitz.Rect(cfg[k])
    return b


def brand_model(b, model):
    """品牌图纸上用的型号：去掉配置里列出的供应商前缀（如润擎去 TF-/ND-/BG-），大小写不敏感，只去开头一个。康生不配置，原样返回。"""
    m = model.strip()
    for pre in b.get('model_strip_prefixes', []):
        if m.upper().startswith(pre.upper()) and len(m) > len(pre):
            return m[len(pre):].lstrip()
    return model
