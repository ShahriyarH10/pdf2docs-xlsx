"""Step 5: the table of contents - found automatically, read line by line with the dotted leaders erased."""
import json
import os
import re

from . import config
from .config import BN_RE, C_TOC_HEADER, CHAPTER_RE, TAIL_DOT_RE, TAIL_NUM_RE
from .images import extract_images
from .text import clean_toc_name


def detect_toc_pages(pdf, img_dir, out_dir, n_pages):
    """PDF pages that are a table of contents = pages full of dotted leaders ("title ........ 27"). [] if none."""
    cache = os.path.join(out_dir, "toc_pages.json")
    if os.path.exists(cache):
        return json.load(open(cache))
    first = list(range(1, min(n_pages, 60) + 1))
    paths = extract_images(pdf, img_dir, first)
    hits = []
    for p in first:
        ys = sorted(y for y, _, cnt in _strip_leaders(paths[p])[1] if cnt >= 8)
        rows = sum(1 for i, y in enumerate(ys) if i == 0 or y - ys[i - 1] > 15)
        if rows >= 8:
            hits.append(p)
    best, run = [], []
    for p in hits:                                            # longest run of consecutive pages
        run = run + [p] if run and p == run[-1] + 1 else [p]
        if len(run) > len(best):
            best = run
    json.dump(best, open(cache, "w"))
    return best


def _strip_leaders(path):
    """Grey image with the dotted leaders ("......") of a TOC page erased + the (y, left x, dot count) of each leader.
    Leaders confuse Tesseract (it garbles the words next to them). Only EVENLY SPACED runs of >=5 small dots are
    removed, so the dot inside Bangla letters such as 'র' is kept."""
    import cv2
    import numpy as np
    g = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    _, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n, lab, st, cen = cv2.connectedComponentsWithStats(bw, connectivity=8)
    dots = sorted((i for i in range(1, n) if 3 <= st[i, 2] <= 16 and 3 <= st[i, 3] <= 16
                   and 0.5 < st[i, 2] / st[i, 3] < 2.0), key=lambda i: cen[i][1])
    groups, cur = [], []
    for i in dots:                                        # same baseline (+-3 px)
        if cur and abs(cen[i][1] - np.mean([cen[j][1] for j in cur])) > 3:
            groups.append(cur)
            cur = []
        cur.append(i)
    if cur:
        groups.append(cur)
    remove, leader_y = np.zeros(n, bool), []
    for gp in groups:
        gp.sort(key=lambda i: cen[i][0])
        runs, run = [], [gp[0]]
        for i in gp[1:]:
            if cen[i][0] - cen[run[-1]][0] <= 20:
                run.append(i)
            else:
                runs.append(run)
                run = [i]
        runs.append(run)
        for r in runs:
            if len(r) >= 5:
                leader_y.append((float(np.mean([cen[i][1] for i in r])), float(min(cen[i][0] for i in r)), len(r)))
                remove[r] = True
    mask = cv2.dilate(remove[lab].astype(np.uint8), np.ones((7, 7), np.uint8)) > 0     # also wipe the faint halo
    g[mask] = 255
    return g, leader_y


def _toc_rows(img_path, lang, tess_cmd, tessdata):
    """One dict per printed TOC line: text, vertical position, and whether a dotted leader was on that line.
    Lines WITH a leader are cut out around the leader's baseline and OCRed as a single line
    (--psm 7, so no layout analysis; the dots were erased beforehand). Lines without a leader (the first line of a
    wrapped title) are found by ink projection. Whole-page OCR kept dropping or merging bold chapter lines."""
    import pytesseract
    from PIL import Image, ImageOps
    if tess_cmd:
        pytesseract.pytesseract.tesseract_cmd = tess_cmd
    cfg = "--psm 7" + (f' --tessdata-dir "{tessdata}"' if tessdata else "")
    g, leaders = _strip_leaders(img_path)
    H, W = g.shape
    sc = W / 1781.0                                       # line geometry below was measured on a 1781 px wide scan

    def ocr(top, bot, x_end):
        crop = ImageOps.expand(Image.fromarray(g[max(0, top):min(H, bot), :max(40, x_end)]), border=30, fill=255)
        text = pytesseract.image_to_string(crop, lang=lang, config=cfg).strip()
        if not text:                                      # bold lines sometimes need to be a bit larger
            text = pytesseract.image_to_string(crop.resize((int(crop.width * 1.5), int(crop.height * 1.5))),
                                               lang=lang, config=cfg).strip()
        return text

    rows, wins = [], []
    clusters = []                                         # one leader per printed line: the longest dot run
    for y, x, n in sorted(leaders):
        if clusters and y - clusters[-1][0] <= 30 * sc:   # same printed line (lines are ~70 px apart)
            if n > clusters[-1][2]:
                clusters[-1] = (y, x, n)
        else:
            clusters.append((y, x, n))
    for y, x, n in clusters:
        top, bot = int(y - 50 * sc), int(y + 18 * sc)
        wins.append((top, bot))
        text = ocr(top, bot, W)
        # nothing readable on a leader line = a bold chapter line (they are the ones Tesseract gives up on);
        # keep a placeholder so the chapter COUNT and the sections under it stay right (name comes from chapters.txt)
        rows.append(dict(top=top, cy=(top + bot) / 2 / H, text=text or "অধ্যায় : ?", leader=True))
    rest = g.copy()                                       # lines without a leader: erase the ones done, find the rest
    for top, bot in wins:
        rest[max(0, top):bot] = 255
    ink = (rest < 128).sum(axis=1) > 3
    bands, start, gap = [], None, 0
    for y in range(H):
        if ink[y]:
            if start is None:
                start = y
            gap = 0
        elif start is not None:
            gap += 1
            if gap > 8:
                bands.append((start, y - gap))
                start = None
    for t, bt in bands:
        if bt - t < 25 * sc:                              # specks
            continue
        text = ocr(t - 8, bt + 9, W)
        if text:
            rows.append(dict(top=t, cy=(t + bt) / 2 / H, text=text, leader=False))
    rows.sort(key=lambda r: r["top"])
    return rows


def read_toc(img_dir, pages, out_dir, a):
    """-> (chapters, sections, section_chapter, warnings); ids are assigned in reading order.
    section_chapter[i] = index of the chapter that section i is printed under."""
    cache = os.path.join(out_dir, "toc.json")
    if os.path.exists(cache):
        rows_by_page = {int(k): v for k, v in json.load(open(cache, encoding="utf8")).items()}
    else:
        rows_by_page = {}
    todo = [p for p in pages if p not in rows_by_page]
    if todo:
        paths = extract_images(a.pdf, img_dir, todo)
        for p in todo:
            rows_by_page[p] = _toc_rows(paths[p], "ben", a.tesseract_cmd, a.tessdata)
        json.dump(rows_by_page, open(cache, "w", encoding="utf8"), ensure_ascii=False)
    chapters, sections, sec_chapter, warns, parts = [], [], [], [], []
    for p in pages:
        for r in rows_by_page[p]:
            txt = r["text"].strip()
            if r["cy"] < C_TOC_HEADER or "চিপত্র" in txt or any(s in txt for s in config.TOC_SKIP):  # header / "সূচিপত্র"
                continue
            ends = r["leader"] or bool(TAIL_NUM_RE.search(txt))     # a leader or a trailing page number ends an entry
            body = TAIL_DOT_RE.sub("", TAIL_NUM_RE.sub("", txt)).strip() if ends else txt
            if CHAPTER_RE.match(txt):
                if parts:
                    warns.append(f"TOC page {p}: dropped unterminated text {' '.join(parts)!r}")
                    parts = []
                chapters.append(clean_toc_name(TAIL_DOT_RE.sub("", CHAPTER_RE.sub("", body))))
                continue
            if body:
                parts.append(body)
            if ends and parts:
                full = " ".join(parts)
                # a page number in the MIDDLE means two entries ran together (a wrapped title + the next one)
                for k, piece in enumerate(re.split(r"\s+[০-৯]{2,4}\s+", full)):
                    if k:
                        piece = re.sub(r"^\S{1,2}\s+", "", piece)      # speck in front of the second title
                    name = clean_toc_name(piece)
                    if len(BN_RE.findall(name)) < 4:
                        warns.append(f"TOC page {p}: dropped junk {name!r}")
                    else:
                        sections.append(name)
                        sec_chapter.append(max(0, len(chapters) - 1))
                parts = []
        if parts:
            warns.append(f"TOC page {p}: dropped unterminated text {' '.join(parts)!r}")
            parts = []
    return chapters, sections, sec_chapter, warns
