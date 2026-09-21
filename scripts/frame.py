"""Approved Kangsheng frame only; technical values always supplied by reviewed manifest."""
from pathlib import Path
import pymupdf as fitz
BLUE = (6/255,66/255,168/255)
PAGE = (841.89,595.276)
FRAME = [22,29,820,564]
def resolve(base, value):
    p=Path(value).expanduser()
    return p if p.is_absolute() else (base/p).resolve()

def draw_frame_and_title(page: fitz.Page, fields: dict, assets: dict) -> None:
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
            page.insert_text((x, y), value, fontname=font, fontsize=size, color=BLUE)
            if True and value == fields.get("title"):
                # Built-in CJK fonts have no bold face. A very small second pass
                # gives the title the same visual weight as the approved sample.
                page.insert_text((x + .28, y), value, fontname=font, fontsize=size, color=BLUE)
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
            if True:
                page.insert_text((x + .24, y), value, fontname=cjk_font_name, fontsize=size, color=BLUE)
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

    rect([488, 450, 820, 564], 1)
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

    rect([400, 493, 488, 564], .8)
    text(404, 503, "UNLESS OTHERWISE", 6)
    text(404, 511, "SPECIFIED, TOLERANCE:", 5.6)
    y = 523
    for value in fields.get("tolerances", []):
        text(408, y, value, 7); y += 10
