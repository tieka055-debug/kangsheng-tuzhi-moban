"""拼图小工具（cluster_frames.py、try_templates.py 共用）：把几张图排成格子存一张 PNG，每格上面一行标签。
只用 PyMuPDF（不用装 PIL）；标签用 PyMuPDF 自带的中文字体，Mac / Windows 一样。"""
import html
import fitz


def page_pixmap(pdf, rotate=0, page_no=0, size=700):
    """thumbnail of one PDF page, turned like frame_match does (rotate on top of the page's own /Rotate)"""
    doc = fitz.open(pdf); p = doc[page_no]
    if rotate: p.set_rotation((p.rotation + rotate) % 360)
    z = size / max(p.rect.width, p.rect.height)
    return p.get_pixmap(matrix=fitz.Matrix(z, z))


def contact_sheet(cells, out, cols=4, cell_w=400, cell_h=300, label_h=20):
    """cells: [(fitz.Pixmap or image path, label, highlight)] -> PNG at `out` (1 pt = 1 px); highlighted cells get a red box"""
    rows = max(1, (len(cells) + cols - 1) // cols)
    doc = fitz.open(); pg = doc.new_page(width=cols * cell_w, height=rows * (cell_h + label_h))
    pg.draw_rect(pg.rect, color=None, fill=(1, 1, 1))
    for k, (img, label, hi) in enumerate(cells):
        x = (k % cols) * cell_w; y = (k // cols) * (cell_h + label_h)
        r = fitz.Rect(x + 4, y + label_h, x + cell_w - 4, y + label_h + cell_h - 4)
        if isinstance(img, fitz.Pixmap): pg.insert_image(r, pixmap=img)
        else: pg.insert_image(r, filename=str(img))
        col = '#d90000' if hi else '#000099'   # html box: Latin and CJK each get a proper built-in font
        pg.insert_htmlbox(fitz.Rect(x + 4, y + 1, x + cell_w - 4, y + label_h), html.escape(label),
                          css=f'* {{font-family: sans-serif; font-size: {label_h * 0.6:.1f}px; color: {col}; white-space: pre}}')
        pg.draw_rect(r, color=(0.85, 0, 0) if hi else (0.8, 0.8, 0.8), width=2 if hi else 0.5)
    pg.get_pixmap().save(out)
    return out
