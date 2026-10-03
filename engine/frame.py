"""Approved Kangsheng frame only; technical values always supplied by reviewed manifest."""
from pathlib import Path
import pymupdf as fitz
BLUE = (6/255,66/255,168/255)
PAGE = (841.89,595.276)
FRAME = [22,29,820,564]
TITLE_BOX = [488,450,820,564]
TOLERANCE_BOX = [400,493,488,564]
PROJECTION_BOX = [775,545.5,820,564]
def resolve(base, value):
    p=Path(value).expanduser()
    return p if p.is_absolute() else (base/p).resolve()

def draw_frame_and_title(page: fitz.Page, fields: dict, assets: dict,
                         tolerance_mode: str = 'legacy') -> None:
    if tolerance_mode not in {'legacy', 'source'}:
        raise ValueError('Unknown tolerance mode')
    cjk_font_name = "china-s"
    cjk_font_file = assets["font"]
    if cjk_font_file:
        cjk_font_name = "ks-title-font"
        page.insert_font(fontname=cjk_font_name, fontfile=str(Path(cjk_font_file).expanduser()))
    def line(a, b, width=.65):
        page.draw_line(fitz.Point(*a), fitz.Point(*b), color=BLUE, width=width)
    def rect(box, width=.65):
        page.draw_rect(fitz.Rect(box), color=BLUE, width=width)
    def text(x, y, value, size=7, font="helv"):
        page.insert_text((x, y), value, fontname=font, fontsize=size, color=BLUE)
    def center(box, value, size=7, font="helv"):
        if font == "china-s":
            font = cjk_font_name
            bounds = fitz.Rect(box)
            font_obj = (fitz.Font(fontfile=str(Path(cjk_font_file).expanduser()))
                        if cjk_font_file and font == cjk_font_name else fitz.Font(fontname=font))
            width = font_obj.text_length(value, fontsize=size)
            x = bounds.x0 + (bounds.width - width) / 2
            y = bounds.y0 + (bounds.height + size * .72) / 2
            # Use the configured typeface's native weight. PDF stroke widths
            # can scale with text size and close the counters of CJK glyphs.
            page.insert_text((x, y), value, fontname=font, fontsize=size, color=BLUE)
            return
        result = page.insert_textbox(fitz.Rect(box), value, fontname=font, fontsize=size, color=BLUE, align=1)
        if result < 0:
            raise ValueError(f"title text overflow: {value!r}")
    def center_mixed(box, value, size=9):
        bounds = fitz.Rect(box)
        if cjk_font_file:
            font_obj = fitz.Font(fontfile=str(Path(cjk_font_file).expanduser()))
            width = font_obj.text_length(value, fontsize=size)
            if width > bounds.width - 4:
                size *= (bounds.width - 4) / width
                width = font_obj.text_length(value, fontsize=size)
            x = bounds.x0 + (bounds.width - width) / 2
            y = bounds.y0 + (bounds.height + size * .72) / 2
            page.insert_text((x, y), value, fontname=cjk_font_name, fontsize=size, color=BLUE)
            return
        runs = []
        for char in value:
            font = "helv" if char.isascii() else "china-s"
            if runs and runs[-1][0] == font:
                runs[-1] = (font, runs[-1][1] + char)
            else:
                runs.append((font, char))
        def widths(font_size):
            return [fitz.Font(fontname=font).text_length(text, fontsize=font_size) for font, text in runs]
        run_widths = widths(size)
        total = sum(run_widths)
        if total > bounds.width - 4:
            size *= (bounds.width - 4) / total
            run_widths = widths(size); total = sum(run_widths)
        x = bounds.x0 + (bounds.width - total) / 2
        y = bounds.y0 + (bounds.height + size * .72) / 2
        for (font, text_value), width in zip(runs, run_widths):
            page.insert_text((x, y), text_value, fontname=font, fontsize=size, color=BLUE)
            x += width

    rect(FRAME, 1.2)
    for i, x in enumerate([75, 181, 280, 396, 503, 610, 716], 1):
        text(x - 2, 21, str(i), 9); text(x - 2, 580, str(i), 9)
    for x in [113.5, 224.4, 331.8, 447.8, 554.5, 661.8, 770.8]:
        line((x, 14), (x, 29), .8); line((x, 564), (x, 580), .8)
    for x in [67.8, 155.9, 267.7, 384, 491.3, 598, 705.4]:
        line((x, 22), (x, 29), .7); line((x, 564), (x, 573), .7)
    for i, y in enumerate([80, 184, 290, 397, 503]):
        text(11, y, chr(65 + i), 9); text(824, y, chr(65 + i), 9)
    for y in [132.5, 236.9, 342.9, 450.6]:
        line((10, y), (22, y), .8); line((820, y), (832, y), .8)
    for y in [78.4, 181, 286.5, 390.8, 498.6]:
        line((15, y), (22, y), .7); line((820, y), (827, y), .7)

    brand = fitz.open(resolve(Path.cwd(), assets["brand_strip"]))
    brand_pdf = fitz.open("pdf", brand.convert_to_pdf())
    br = brand_pdf[0].rect
    page.show_pdf_page(fitz.Rect(489, 452, 819, 491), brand_pdf, 0,
                       clip=fitz.Rect(br.width * .012, br.height * .12, br.width * .988, br.height * .91))
    brand_pdf.close(); brand.close()

    rect(TITLE_BOX, 1)
    line((488, 492), (820, 492)); line((488, 527), (820, 527))
    line((541, 492), (541, 527)); line((600, 492), (600, 527)); line((625, 492), (625, 527))
    line((688, 492), (688, 527)); line((488, 509.5), (688, 509.5))
    for x, y, value in [(492, 502, "DESIGN"), (492, 519, "APPROVED"), (603, 502, "DATE"),
                        (603, 519, "DATE"), (692, 501, "TITLE:"), (492, 537, "MODEL:")]:
        text(x, y, value, 6.5)
    center([696, 505, 813, 524], fields["title"], 11, "china-s")
    line((638, 527), (638, 564)); line((638, 545.5), (820, 545.5))
    for x in [679, 759]: line((x, 527), (x, 545.5))
    for x in [701, 775]: line((x, 545.5), (x, 564))
    text(643, 538, "REV: " + fields.get("revision", ""), 6.5)
    text(683, 538, "SCALE: " + fields.get("scale_text", ""), 6.5)
    text(764, 538, "UNIT: " + fields.get("unit", "mm"), 6.5)
    text(643, 557, "SIZE: " + fields.get("size", "A4"), 6.5)
    text(706, 557, "SHEET: " + fields.get("sheet", "1/1"), 6.5)
    center_mixed([491, 541, 635, 563], fields["model"], 9)

    # A v2 source block brings its own labels, values and rules. The reserved
    # rectangle is its container, not another table to draw over the original.
    if tolerance_mode == 'legacy':
        rect(TOLERANCE_BOX, .8)
        text(404, 503, "UNLESS OTHERWISE", 6)
        text(404, 511, "SPECIFIED, TOLERANCE:", 5.6)
        y = 523
        for value in fields.get("tolerances", []):
            text(408, y, value, 7); y += 10


def draw_runqing_frame_and_title(page: fitz.Page, fields: dict, assets: dict, brand: dict, tolerance: dict) -> None:
    """润擎（RunQing）图框：橘色外框 + 青蓝内框、格号 1–7/A–E、左上标题、右上修订栏、右下 logo+标题栏+GENERAL TOLERANCE。
    只画图框和标题栏；产品技术内容（规格/材料/零件表）一律是原图矢量搬运，这里不写任何型号的技术值。
    坐标全部来自 brands/runqing.json（样张实测）。公差只写 tolerance 里传入的（本图自己读出的）值。"""
    import dynamic_tolerance as DT
    blue, orange = brand['blue'], brand['gold']
    grid_c = tuple(v / 255 for v in brand['grid_color_rgb255'])
    font_file = str(Path(assets['font']).expanduser())
    page.insert_font(fontname='rq-font', fontfile=font_file)
    cjk = fitz.Font(fontfile=font_file)
    helv, hebo = fitz.Font('helv'), fitz.Font('hebo')

    def line(a, b, width=.5, color=blue):
        page.draw_line(fitz.Point(*a), fitz.Point(*b), color=color, width=width)

    def put(x, y0, value, size, bold=False, maxw=None, k=1.09):
        """y0 = 文字框顶（样张 bbox 的 y0）；基线 = y0 + k*字号（k 由样张字形实测）"""
        ascii_ = value.isascii()
        font_obj = (hebo if bold else helv) if ascii_ else cjk
        w = font_obj.text_length(value, fontsize=size)
        if maxw and w > maxw:
            size *= maxw / w
        name = ('hebo' if bold else 'helv') if ascii_ else 'rq-font'
        page.insert_text((x, y0 + k * size), value, fontname=name, fontsize=size, color=blue)

    # ---- 外框 / 内框 / 格号
    page.draw_rect(fitz.Rect(brand['outer_frame']), color=orange, width=1.1)
    fr = brand['frame']
    page.draw_rect(fitz.Rect(fr), color=blue, width=.7)
    for x in brand['grid_x']:
        line((x, fr.y0), (x, fr.y0 + 5), .4, grid_c); line((x, fr.y1 - 5), (x, fr.y1), .4, grid_c)
    for y in brand['grid_y']:
        line((fr.x0, y), (fr.x0 + 5, y), .4, grid_c); line((fr.x1 - 5, y), (fr.x1, y), .4, grid_c)
    for i, x in enumerate(brand['grid_label_x'], 1):
        put(x, 15.2, str(i), 6.3); put(x, 571.5, str(i), 6.3)
    for i, y in enumerate(brand['grid_label_y']):
        put(18.0, y, chr(65 + i), 6.2); put(818.9, y, chr(65 + i), 6.2)

    # ---- 左上标题 + 型号
    put(38, 41.6, 'CONNECTOR ENGINEERING DRAWING', 9.0, bold=True)
    line((38, 56.28), (526, 56.28), 1.05, orange)
    put(39, 61.3, fields['model'], 7.4, maxw=480)

    # ---- 右上修订栏（只画空表头，原图的修订内容不搬）
    rb = fitz.Rect(brand['rev_box'])
    page.draw_rect(rb, color=blue, width=.65)
    for x in brand['rev_vlines']: line((x, rb.y0), (x, rb.y1), .45)
    line((rb.x0, brand['rev_hline']), (rb.x1, brand['rev_hline']), .45)
    for x, t in zip([552.0, 581.0, 700.0, 760.0], ['REV', 'DESCRIPTION', 'DRAW', 'DATE']):
        put(x, 46.6, t, 6.2, bold=True)

    # ---- 右下标题栏
    tb = fitz.Rect(brand['title_box'])
    page.draw_rect(tb, color=blue, width=.65)
    for y, w in zip(brand['title_hlines'], (.6, .5, .5)): line((tb.x0, y), (tb.x1, y), w)
    vx, vy0, vy1 = brand['title_vline']; line((vx, vy0), (vx, vy1), .45)
    page.insert_image(fitz.Rect(brand['logo_box']), filename=assets['logo'], keep_proportion=True)
    page.insert_text((640, 431.7 + .8 * 11.1), '东莞市润擎电子科技有限公司', fontname='rq-font', fontsize=11.1, color=blue)
    put(641, 446, 'Dongguan Runqing Electronics Technology Co., Ltd.', 5.8)
    put(552, 470.7, 'PART NAME', 6.1, bold=True)
    put(552, 478.8, fields['title'], 7.9, bold=True, maxw=106)
    put(667, 470.7, 'SOURCE REF. P/N', 6.1, bold=True)
    put(667, 479.9, fields['model'], 6.9, maxw=142)
    put(552, 495.6, 'UNIT  ' + fields.get('unit', 'mm'), 6.2)
    put(650, 495.6, 'SCALE  ' + fields.get('scale_text', ''), 6.2)
    put(732, 495.6, 'REV  ' + fields.get('revision', ''), 6.2, bold=True)
    put(552, 504.6, 'SOURCE DATE  ' + fields.get('source_date', ''), 6.2)
    put(732, 504.6, 'PAGE  ' + fields.get('sheet', '1/1').replace('/', ' OF '), 6.2)
    # GENERAL TOLERANCE / 默认公差
    page.insert_text((552, 519.2 + .81 * 6.5), 'GENERAL TOLERANCE / ', fontname='helv', fontsize=6.5, color=blue)
    page.insert_text((552 + helv.text_length('GENERAL TOLERANCE / ', fontsize=6.5), 519.2 + .81 * 6.5), '默认公差',
                     fontname='rq-font', fontsize=6.5, color=blue)
    schema = DT.normalize_tolerance_schema(tolerance)
    if any(not helv.has_glyph(ord(c)) for key in DT.SCHEMA_KEYS for row in schema[key] for c in DT._line(row)):
        raise ValueError('Tolerance contains a glyph unsupported by the chosen font')
    ta = fitz.Rect(brand['tolerance_area']); xs = brand['tolerance_cols_x']
    first_base, last_base = 538.5, ta.y1 - 2
    flat = [("t", DT._line(r)) for k in ('linear_tolerances', 'angular_tolerances') for r in schema[k]]
    cond = [("c", DT._line(r)) for r in schema['additional_tolerance_conditions']]
    # 样张排法：档位按行从左到右排成两列（≤5 / >5-30 ；>30 / ANGLE）；放不下再按类别分列；档位很多（如 4 线性 + 5 角度）时排三列
    xs3 = brand.get('tolerance_cols3_x', [])
    arrangements = ([([flat[0::2], flat[1::2]], xs, cond)] if len(flat) > 1 else [])
    arrangements += [([left, right] if right else [left], xs, conditions) for _, left, right, conditions in DT._body_arrangements(schema)]
    if xs3 and len(flat) > 2:
        arrangements.append(([flat[i::3] for i in range(3)], xs3, cond))
    for columns, cx, conditions in arrangements:
        col_w = [(cx[c + 1] if c + 1 < len(columns) else ta.x1) - cx[c] - 4 for c in range(len(columns))]
        rows = max(len(c_) for c_ in columns) + len(conditions)
        for size in (6.5, 6.0, 5.5, 5.0, DT.MIN_FONT_PT):
            step = 13.0 if rows < 3 else min(13.0, (last_base - first_base) / (rows - 1))
            if step < size * 1.25 or first_base + (rows - 1) * step > last_base + .01: continue
            if any(helv.text_length(t, fontsize=size) > col_w[c] for c, rs in enumerate(columns) for _, t in rs): continue
            if any(helv.text_length(t, fontsize=size) > ta.x1 - xs[0] - 4 for _, t in conditions): continue
            n = 0
            for c, rs in enumerate(columns):
                for r, (_, t) in enumerate(rs):
                    page.insert_text((cx[c], first_base + r * step), t, fontname='helv', fontsize=size, color=blue); n += 1
            base = max(len(c_) for c_ in columns)
            for r, (_, t) in enumerate(conditions):
                page.insert_text((xs[0], first_base + (base + r) * step), t, fontname='helv', fontsize=size, color=blue); n += 1
            assert n == sum(len(schema[k]) for k in DT.SCHEMA_KEYS)
            break
        else:
            continue
        break
    else:
        raise ValueError('Complete tolerance data overflows its fixed footer cell')

    # ---- 左下版权 + 投影符号
    put(39, 550.1, 'COPYRIGHT RESERVED, PLEASE DO NOT COPY.', 5.5)
    cx, cy = 499.2, 542.7
    page.draw_circle(fitz.Point(cx, cy), 4.9, color=blue, width=.45)
    page.draw_circle(fitz.Point(cx, cy), 2.3, color=blue, width=.45)
    line((cx - 6.9, cy), (cx - 5.2, cy), .4); line((cx, cy - 6.5), (cx, cy + 6.5), .4)
    line((cx + 5.2, cy), (cx + 8.5, cy), .4); line((cx - .6, cy), (cx + .6, cy), .4)
    trap = [fitz.Point(507.7, 541.1), fitz.Point(516.7, 537.7), fitz.Point(516.7, 547.2), fitz.Point(507.7, 544.3)]
    page.draw_polyline(trap + [trap[0]], color=blue, width=.45)
    line((507.7, cy), (519, cy), .4)
