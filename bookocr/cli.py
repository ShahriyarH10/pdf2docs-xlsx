"""The command line: pdf2xlsx.py <book.pdf> [options]. See the README."""
import argparse
import json
import os
import sys

from . import settings
from .config import CONFIG
from .export import export_book, make_book, write_debug
from .hadiths import assign_sections, build_entries, repair_numbers, structure_from_headings
from .images import extract_images, find_rule, parse_pages
from .layout import apply_toc_headings, classify_page, mark_chapters, reread_headings, running_title, structural_headings
from .ocr import run_ocr
from .toc import detect_toc_pages, read_toc


def pick_pdf():
    """No PDF on the command line: let the user choose one (macOS dialog, else Tk dialog, else a typed/dragged path)."""
    import subprocess
    if sys.platform == "darwin":
        r = subprocess.run(["osascript", "-e",
                            'POSIX path of (choose file of type {"pdf"} with prompt "Choose the scanned book (PDF)")'],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
        if "User canceled" in r.stderr:
            sys.exit("No PDF chosen.")
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        path = filedialog.askopenfilename(title="Choose the scanned book (PDF)", filetypes=[("PDF", "*.pdf")])
        root.destroy()
        if path:
            return path
    except Exception:
        pass
    path = input("Drag the PDF into this window and press Enter: ").strip()
    if len(path) > 1 and path[0] == path[-1] and path[0] in "'\"":
        path = path[1:-1]
    return path.replace("\\ ", " ")


def preflight(a):
    """Stop with a readable message (instead of a traceback) when something needed is not installed."""
    import importlib.util
    import shutil
    missing = [pip for mod, pip in (("pymupdf", "pymupdf"), ("pytesseract", "pytesseract"), ("PIL", "pillow"),
                                     ("openpyxl", "openpyxl"), ("docx", "python-docx"), ("numpy", "numpy"),
                                     ("cv2", "opencv-python-headless")) if importlib.util.find_spec(mod) is None]
    if missing:
        sys.exit("Missing Python packages: " + " ".join(missing) + "\n  install them with:  pip install " + " ".join(missing))
    if not (a.tesseract_cmd or shutil.which("tesseract")):
        sys.exit("Tesseract is not installed (macOS: brew install tesseract tesseract-lang; "
                 "Windows: winget install UB-Mannheim.TesseractOCR) or pass --tesseract-cmd <path to tesseract>")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", nargs="?", help="the scanned book; leave it out to choose the file in a dialog")
    ap.add_argument("--out", help='output folder (default: "<PDF name> - output" next to the PDF)')
    ap.add_argument("--settings", help='the book\'s settings file (default: "<PDF name>.settings.json" next to the PDF, '
                                       'if present); options given here win over it')
    ap.add_argument("--title", help="title on the first page of book.docx (default: from the page headers)")
    ap.add_argument("--pages", help='e.g. "195-205" or "1,5,9-12" (default: all)')
    ap.add_argument("--lang", default="ara+ben")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2)))
    ap.add_argument("--tesseract-cmd")
    ap.add_argument("--tessdata", help="folder holding ben.traineddata / ara.traineddata")
    ap.add_argument("--debug", action="store_true", help="write annotated page images to <out>/debug")
    ap.add_argument("--keep-arabic", action="store_true",
                    help="also write the Arabic OCR into the outputs (default: dropped; the Arabic OCR is error-prone)")
    ap.add_argument("--no-docx", action="store_true", help="do not write book.docx")
    ap.add_argument("--toc-pages",
                    help='PDF pages of the table of contents, e.g. "17-25"; "auto" (default) finds them, '
                         '"none" = the PDF has no table of contents (chapters/sections come from the headings)')
    ap.add_argument("--chapters-file", help="text file, one chapter name per line, used instead of the OCR'd "
                                            "chapter names (bold chapter lines OCR badly); default: \"chapters\" "
                                            "in the settings file")
    ap.add_argument("--body-start", type=int, help="first PDF page with hadiths (default: page after the TOC)")
    ap.add_argument("--rebuild-only", action="store_true", help="skip image extraction and OCR; reuse cached JSON")
    ap.add_argument("--re-ocr", action="store_true", help="read the pages again even if their OCR is cached")
    a = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):               # the Windows console is not UTF-8: Bangla must not crash it
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if not a.tesseract_cmd and sys.platform == "win32":   # the Windows installer does not put tesseract on PATH
        import shutil
        if not shutil.which("tesseract"):
            for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                         os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
                exe = os.path.join(base or "", "Tesseract-OCR", "tesseract.exe")
                if os.path.isfile(exe):
                    a.tesseract_cmd = exe
                    break
    preflight(a)
    picked = not a.pdf
    if picked:
        a.pdf = pick_pdf()
    a.pdf = os.path.abspath(os.path.expanduser(a.pdf))
    if not os.path.isfile(a.pdf):
        sys.exit(f"PDF not found: {a.pdf}")
    if not a.out:                                         # one folder per PDF: the page cache must never be shared
        a.out = os.path.join(os.path.dirname(a.pdf), os.path.splitext(os.path.basename(a.pdf))[0] + " - output")
    print(f"PDF:    {a.pdf}\nOutput: {a.out}")
    os.makedirs(a.out, exist_ok=True)
    stamp, me = os.path.join(a.out, "source.json"), dict(name=os.path.basename(a.pdf), size=os.path.getsize(a.pdf))
    if os.path.exists(stamp):
        if json.load(open(stamp, encoding="utf8")) != me:
            sys.exit(f"{a.out}\n  holds the results of a different PDF. Use another --out folder, or delete this one.")
    elif os.path.isdir(os.path.join(a.out, "ocr")) and os.listdir(os.path.join(a.out, "ocr")):
        sys.exit(f"{a.out}\n  already holds cached pages of unknown origin. Use another --out folder, or delete this one.")
    else:
        json.dump(me, open(stamp, "w", encoding="utf8"), ensure_ascii=False)

    sf = a.settings or settings.path_for(a.pdf)
    if a.settings and not os.path.isfile(sf):
        sys.exit(f"Settings file not found: {sf}")
    s = settings.load(sf)
    settings.apply(s)
    if os.path.isfile(sf):
        print(f"Settings: {sf}")
    a.title = a.title or s["title"]
    a.pages = a.pages or s["pages"]
    a.toc_pages = a.toc_pages or s["toc_pages"] or "auto"
    a.body_start = a.body_start or s["body_start"]
    a.no_docx = a.no_docx or s["no_docx"]

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the folder holding pdf2xlsx.py
    if not a.tessdata and os.path.exists(os.path.join(here, "tessdata_best", "ben.traineddata")):
        a.tessdata = os.path.join(here, "tessdata_best")             # more accurate models, used automatically
    print(f"Tesseract models: {a.tessdata or 'system default (fast models)'}")
    img_dir, ocr_dir = os.path.join(a.out, "images"), os.path.join(a.out, "ocr")
    os.makedirs(a.out, exist_ok=True)

    import pymupdf
    n = len(pymupdf.open(a.pdf))
    if a.toc_pages == "auto":
        toc_pages = detect_toc_pages(a.pdf, img_dir, a.out, n)
    else:
        toc_pages = [] if a.toc_pages == "none" else parse_pages(a.toc_pages, n)
    body_start = a.body_start or (max(toc_pages) + 1 if toc_pages else 1)

    if not a.rebuild_only:
        pages = parse_pages(a.pages, n)
        print(f"{n} pages in PDF, processing {len(pages)}")
        paths = extract_images(a.pdf, img_dir, pages)
        run_ocr(paths, ocr_dir, a)
        wanted = set(pages)
    else:
        wanted = parse_pages(a.pages, 10 ** 6) if a.pages else None

    page_data = []
    for f in sorted(os.listdir(ocr_dir)):
        if f.endswith(".json"):
            d = json.load(open(os.path.join(ocr_dir, f), encoding="utf8"))
            if (wanted is None or d["page"] in wanted) and d["page"] >= body_start:
                page_data.append(d)
    page_data.sort(key=lambda d: d["page"])
    for d in page_data:
        d["rule_y"] = find_rule(os.path.join(img_dir, d["image"]))
    if a.chapters_file:
        cf, known = a.chapters_file, [l.strip() for l in open(a.chapters_file, encoding="utf8") if l.strip()]
    else:
        cf, known = os.path.basename(sf), [c.strip() for c in s["chapters"] if c.strip()]
    warns = []
    for d in page_data:
        classify_page(d, CONFIG)
    sec_chapter = None
    if toc_pages:
        print(f"Table of contents: pages {toc_pages[0]}-{toc_pages[-1]}")
        chapters, sections, sec_chapter, warns = read_toc(img_dir, toc_pages, a.out, a)
        print(f"Headings confirmed against the TOC: {apply_toc_headings(page_data, sections, CONFIG)}")
        structural_headings(page_data, CONFIG)
        mark_chapters(page_data, img_dir, a, known, reread=False)
        entries, unnumbered = build_entries(page_data, CONFIG)
        repair_numbers(entries)
        if known and (len(known) == len(chapters) or a.chapters_file):
            print(f"Chapter names taken from {cf} ({len(known)})")
            chapters = known
        elif known:                                           # the names belong to another book or edition
            warns.append(f"{os.path.basename(cf)} has {len(known)} chapter names but the table of contents has "
                         f"{len(chapters)} chapters: ignored, OCR'd chapter names used")
        sec_of, weak = assign_sections(entries, sections)
        if weak:
            print(f"{weak} heading-like line(s) matched no TOC section and were ignored")
        chap_of = [sec_chapter[si] if si < len(sec_chapter) else -1 for si in sec_of]
    else:
        print("No table of contents in this PDF: chapters and sections are taken from the headings in the text")
        structural_headings(page_data, CONFIG)
        mark_chapters(page_data, img_dir, a, known, reread=True)
        reread_headings(page_data, img_dir, a)
        for d in page_data:
            for ln in d["lines"]:
                if ln.get("chapter_unsure"):
                    warns.append(f"page {d['page']}: chapter title read as {ln['chapter_name']!r} - check it")
        entries, unnumbered = build_entries(page_data, CONFIG)
        repair_numbers(entries)
        chapters, sections, chap_of, sec_of = structure_from_headings(entries)
    title = a.title or running_title(page_data) or os.path.splitext(os.path.basename(a.pdf))[0]
    word_conf = any("words" in ln for d in page_data for ln in d["lines"])     # False for OCR cached by old versions
    book = make_book(title, chapters, sections, entries, sec_of, chap_of, sec_chapter, word_conf)
    with open(os.path.join(a.out, "book.json"), "w", encoding="utf8") as fh:
        json.dump(book, fh, ensure_ascii=False)
    made = export_book(book, a.out, docx=not a.no_docx)
    for old in ("chapters.xlsx", "sections.xlsx", "hadith.xlsx"):      # files of earlier versions: now sheets of book.xlsx
        if os.path.exists(os.path.join(a.out, old)):
            os.remove(os.path.join(a.out, old))
    if a.debug:
        write_debug(page_data, img_dir, os.path.join(a.out, "debug"))

    flagged = sum(1 for e in entries if e["flags"])
    print(f"\nDone. {len(chapters)} chapters, {len(sections)} sections, {len(entries)} hadiths "
          f"({flagged} need review -> review.csv), {len(unnumbered)} orphan lines.")
    for w in warns:
        print("  warning:", w)
    print("Created:")
    for name in made:
        print("  " + os.path.join(a.out, name))
    if picked and sys.platform == "darwin":               # chosen in a dialog -> show the results folder
        import subprocess
        subprocess.run(["open", a.out])
    elif picked and sys.platform == "win32":
        os.startfile(a.out)
    if a.debug:
        print(f"Debug images: {os.path.join(a.out, 'debug')}  (red=Arabic, green=heading, blue=footnote, gray=dropped)")
