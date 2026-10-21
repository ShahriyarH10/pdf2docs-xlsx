"""Step 6: the outputs - book.json, book.xlsx, book.docx, review.csv (and debug images)."""
import csv
import os
import re

from .config import CONFIG, TO_BN


def hide_unused_columns(ws, used):
    """Hide every column to the right of the data, so the sheet shows only the filled columns
    (a spreadsheet grid otherwise always draws empty columns C, D, E ... next to them)."""
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.dimensions import ColumnDimension
    first = get_column_letter(used + 1)
    ws.column_dimensions[first] = ColumnDimension(ws, index=first, min=used + 1, max=16384, hidden=True)


def fit_rows(ws, text_col, width, line_pt=21.0):
    """Give every row the height its wrapped text needs. Without this a spreadsheet program shows long hadiths
    cut off; Excel's hard limit is 409 points per row, so the text column is made wide enough for the longest one.
    -> ids (column A) of rows that still do not fit."""
    import math
    per_line = max(20, int(width * 0.85))                 # characters that fit on one line of the column
    too_long = []
    for row in ws.iter_rows(min_row=2):
        text = row[text_col - 1].value
        if not isinstance(text, str):
            continue
        lines = sum(max(1, math.ceil(len(seg) / per_line)) for seg in text.split("\n"))
        need = lines * line_pt + 4
        if need > 409:
            too_long.append(row[0].value)
        ws.row_dimensions[row[0].row].height = min(409, need)
    return too_long


def write_workbook(path, chapters, sections, entries, font):
    """book.xlsx with three sheets -  chapters: id, name   sections: section_id, name   hadiths: id, hadith."""
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    wb.remove(wb.active)
    for title, header, rows, widths in (
            ("chapters", ["id", "name"], [(i, t) for i, t in enumerate(chapters, 1)], [8, 60]),
            ("sections", ["section_id", "name"], [(i, t) for i, t in enumerate(sections, 1)], [12, 70]),
            ("hadiths", ["id", "hadith"], [(e["no"], e["bangla_text"]) for e in entries], [8, 180])):
        ws = wb.create_sheet(title)
        ws.append(header)
        for r in rows:
            ws.append([ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v for v in r])
        for row in ws.iter_rows():
            for c in row:
                head = c.row == 1
                c.font = Font(name=font, bold=head)
                c.alignment = Alignment(horizontal="center" if head else "justify" if c.column == 2 else None,
                                        vertical="top", wrap_text=True)
                if head:
                    c.fill = PatternFill("solid", fgColor="DDDDDD")
        ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = widths
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        hide_unused_columns(ws, 2)
        fit_rows(ws, 2, widths[1])
    wb.save(path)


def write_review(entries, path):
    """CSV of every hadith that needs a human look (UTF-8 with BOM so Excel opens Bangla correctly)."""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "pages", "ocr_conf", "flags", "text_start"])
        for e in entries:
            if e["flags"]:
                w.writerow([e["no"], f"{e['page_start']}-{e['page_end']}", e["conf_mean"],
                            "; ".join(e["flags"]), e["bangla_text"][:80]])


def write_docx(entries, sec_of, chap_of, chapters, sections, path, font_name, title):
    """Formatted Word file: title, chapter (Heading 1), section (Heading 2), then the numbered hadiths, justified."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)                 # A4
    sec.left_margin = sec.right_margin = Cm(2.2)
    sec.top_margin = sec.bottom_margin = Cm(2.0)

    def style_font(st, size, bold=False, color=None):
        st.font.name, st.font.size, st.font.bold = font_name, Pt(size), bold
        st.font.color.rgb = color or RGBColor(0, 0, 0)
        rpr = st.element.get_or_add_rPr()
        rf = rpr.find(qn("w:rFonts"))
        if rf is None:
            rf = OxmlElement("w:rFonts")
            rpr.append(rf)
        for att in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            rf.set(qn(att), font_name)
        for att in ("w:asciiTheme", "w:hAnsiTheme", "w:cstheme", "w:eastAsiaTheme"):
            if rf.get(qn(att)) is not None:
                del rf.attrib[qn(att)]
        szcs = OxmlElement("w:szCs")
        szcs.set(qn("w:val"), str(int(size * 2)))
        rpr.append(szcs)
        lang = OxmlElement("w:lang")
        lang.set(qn("w:val"), "bn-BD")
        lang.set(qn("w:bidi"), "bn-BD")
        rpr.append(lang)

    normal = doc.styles["Normal"]
    style_font(normal, 12)
    normal.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.3
    style_font(doc.styles["Title"], 26, True)
    style_font(doc.styles["Heading 1"], 18, True, RGBColor(0x1F, 0x3A, 0x5F))
    style_font(doc.styles["Heading 2"], 14, True, RGBColor(0x2E, 0x2E, 0x2E))
    for name, before, after in (("Heading 1", 22, 8), ("Heading 2", 14, 4)):
        pf = doc.styles[name].paragraph_format
        pf.space_before, pf.space_after, pf.keep_with_next = Pt(before), Pt(after), True
        pf.alignment = WD_ALIGN_PARAGRAPH.LEFT
    doc.styles["Title"].paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # centred title block
    for _ in range(6):
        doc.add_paragraph()
    doc.add_paragraph(title, style="Title")
    note = doc.add_paragraph("বাংলা অনুবাদ (OCR থেকে নিষ্কাশিত)")
    note.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # page number in the footer
    fp = sec.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = fp.add_run()
    for kind, txt in (("begin", None), (None, " PAGE "), ("end", None)):
        if kind:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
        else:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = txt
        run._r.append(el)

    doc.add_paragraph().paragraph_format.page_break_before = True      # the text starts on a new page
    last_chapter = last_section = None
    for e, si, ci in zip(entries, sec_of, chap_of):
        if ci != last_chapter and 0 <= ci < len(chapters):
            doc.add_paragraph(f"অধ্যায় : {chapters[ci]}", style="Heading 1")
            last_chapter = ci
        if si != last_section and 0 <= si < len(sections):
            doc.add_paragraph(sections[si], style="Heading 2")
            last_section = si
        para = doc.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        r1 = para.add_run(f"[{str(e['no']).translate(TO_BN)}] " if e["no"] is not None else "")
        r1.bold = True
        para.add_run(e["bangla_text"])
    doc.core_properties.title = title
    doc.core_properties.language = "bn-BD"
    doc.save(path)


def doubtful_words(e):
    """The hadith's low-confidence words that are still in its cleaned text (OCR junk the cleaning removed is
    not worth a look), as {w, conf, page, box} with the box in fractions of the page size."""
    out, seen = [], set()
    for d in e.get("words", []):
        w = re.sub(r"[^\u0980-\u09E5\u09F0-\u09FF]", "", d["w"])        # Bangla letters only (no digits/punctuation)
        key = (w, d["page"], tuple(d["box"]))
        if len(w) >= 2 and w in e["bangla_text"] and key not in seen:
            seen.add(key)
            out.append(dict(d, w=w))
    return out


def make_book(title, chapters, sections, entries, sec_of, chap_of, sec_chapter=None, word_conf=False):
    """Everything the outputs are made from, as one JSON-ready dict (saved as book.json; the web review edits a copy).
    section_chapter = chapter index of each section, so a hadith moved to another section follows its chapter."""
    if sec_chapter is None:                               # no TOC: a section belongs to the chapter it first appears in
        sec_chapter = [-1] * len(sections)
        for si, ci in zip(sec_of, chap_of):
            if 0 <= si < len(sections) and sec_chapter[si] == -1:
                sec_chapter[si] = ci
    hadiths = [dict(uid=f"h{i}", no=e["no"], text=e["bangla_text"], ocr_text=e["bangla_text"],
                    arabic=e.get("arabic_text", ""), foot=e.get("foot_text", ""), heading=e.get("heading", ""),
                    flags=list(e["flags"]), conf=e["conf_mean"], page_start=e["page_start"], page_end=e["page_end"],
                    anchor=e.get("anchor"), section=si, chapter=ci, reviewed=False, doubt=doubtful_words(e))
               for i, (e, si, ci) in enumerate(zip(entries, sec_of, chap_of))]
    return dict(version=1, title=title, chapters=list(chapters), sections=list(sections),
                section_chapter=list(sec_chapter), hadiths=hadiths, next_uid=len(hadiths), word_conf=word_conf)


def export_book(book, out_dir, docx=True, font=None):
    """book.xlsx, review.csv and (unless docx=False) book.docx from a book dict. Hadiths marked reviewed are left
    out of review.csv. -> names of the files written."""
    font = font or CONFIG["font"]
    hs = book["hadiths"]
    entries = [dict(no=h["no"], bangla_text=re.sub(r"[ \t]+", " ", h["text"]).strip(), flags=[] if h.get("reviewed") else h["flags"],
                    page_start=h["page_start"], page_end=h["page_end"], conf_mean=h["conf"]) for h in hs]
    write_workbook(os.path.join(out_dir, "book.xlsx"), book["chapters"], book["sections"], entries, font)
    write_review(entries, os.path.join(out_dir, "review.csv"))
    made = ["book.xlsx", "review.csv"]
    if docx:
        write_docx(entries, [h["section"] for h in hs], [h["chapter"] for h in hs], book["chapters"],
                   book["sections"], os.path.join(out_dir, "book.docx"), font, book["title"])
        made.insert(1, "book.docx")
    return made


def write_debug(page_data, img_dir, dbg_dir):
    from PIL import Image, ImageDraw
    os.makedirs(dbg_dir, exist_ok=True)
    colors = dict(header="gray", noise="gray", arabic="red", heading="green", chapter="purple", footnote="blue",
                  body="black")
    for pd in page_data:
        img = Image.open(os.path.join(img_dir, pd["image"])).convert("RGB")
        dr = ImageDraw.Draw(img)
        for ln in pd["lines"]:
            dr.rectangle([ln["left"], ln["top"], ln["right"], ln["bottom"]], outline=colors[ln["kind"]], width=4)
            dr.text((max(0, ln["left"] - 80), ln["top"]), ln["kind"][:4], fill=colors[ln["kind"]])
        s = 1000 / img.width
        img.resize((1000, int(img.height * s))).save(os.path.join(dbg_dir, f"page_{pd['page']:03d}.jpg"), quality=70)
