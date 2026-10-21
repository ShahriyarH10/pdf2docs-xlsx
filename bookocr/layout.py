"""Step 3: what each OCR line is - header, Arabic, heading, chapter title, footnote, body or noise."""
import os
import re
import statistics

from .config import AR_RE, BN_RE, CHAPTER_GARBLE_RE, CHAPTER_RE, MARKER_RE
from .text import _norm, clean_toc_name


def _clearly_bangla(ln):
    """Readable Bangla with not one Arabic letter in it: never part of an Arabic block, however light it looks."""
    return not AR_RE.search(ln["text"]) and len(BN_RE.findall(ln["text"])) >= 6 and ln["conf"] >= 62


def classify_page(data, C):
    W, H = data["width"], data["height"]
    lines = data["lines"]
    for ln in lines:
        txt = ln["text"]
        a, b = len(AR_RE.findall(txt)), len(BN_RE.findall(txt))
        ln["ar_ratio"] = a / (a + b) if (a + b) else 0.0
        ln["h"] = ln["bottom"] - ln["top"]
        ln["cy"] = (ln["top"] + ln["bottom"]) / 2 / H
        ln["kind"] = None
    # reference statistics from clean, confident Bangla body lines (no Arabic characters at all)
    ref = [l for l in lines if l["cy"] >= C["header_zone"] and l["cy"] < C["footnote_zone"]
           and not AR_RE.search(l["text"]) and l["conf"] >= 75 and len(l["text"]) >= 25]
    med_h = statistics.median([l["h"] for l in ref]) if ref else 0
    med_ink = statistics.median([l["ink"] for l in ref]) if ref else 0
    # header / noise / arabic
    for ln in lines:
        txt = ln["text"].strip()
        n_ar = len(AR_RE.findall(txt))
        light = med_ink and ln["ink"] < C["arabic_ink_ratio"] * med_ink      # Arabic strokes are lighter
        if ln["cy"] < C["header_zone"]:
            ln["kind"] = "header"
        elif ln["cy"] > C["ornament_zone"] and (len(txt) <= 4 or ln["conf"] < 40):
            ln["kind"] = "noise"
        elif ln["ar_ratio"] >= C["arabic_ratio"] and n_ar >= 3:
            ln["kind"] = "arabic"
        elif light and ln["ar_ratio"] >= C["arabic_ratio_if_light"] and n_ar >= 3:
            ln["kind"] = "arabic"
        elif len(txt) <= 3 and ln["cy"] < C["footnote_zone"]:
            ln["kind"] = "noise"                                              # stray specks / superscripts
        elif ln["conf"] < 15 and len(txt) < 4:
            ln["kind"] = "noise"
        elif med_h and ln["h"] < 0.25 * med_h and len(txt) <= 3:
            ln["kind"] = "noise"                                              # 1-2 px specks anywhere on the page
    # smoothing: a line between two Arabic lines is Arabic too (translation never sits inside an Arabic block)
    seq = [l for l in lines if l["kind"] not in ("header", "noise")]
    for i in range(1, len(seq)):
        cur, prv = seq[i], seq[i - 1]
        if cur["kind"] is not None or prv["kind"] != "arabic":
            continue
        nxt_ar = i + 1 < len(seq) and seq[i + 1]["kind"] == "arabic"
        light = med_ink and cur["ink"] < C["arabic_ink_ratio"] * med_ink
        if _clearly_bangla(cur):
            continue
        # between two Arabic lines, or the short last line of an Arabic block (light strokes, shaky OCR)
        if nxt_ar or (light and cur["conf"] < 70 and not MARKER_RE.match(cur["text"].strip())):
            cur["kind"] = "arabic"
    for i in range(len(seq) - 1):                  # first line of an Arabic block misread as Bangla
        cur = seq[i]
        if cur["kind"] is None and seq[i + 1]["kind"] == "arabic" and not MARKER_RE.match(cur["text"].strip()):
            light = med_ink and cur["ink"] < C["arabic_ink_ratio"] * med_ink
            if light and cur["conf"] < 70 and not _clearly_bangla(cur):
                cur["kind"] = "arabic"
    # footnotes: everything below the printed footnote rule; without a rule only unmistakable footnote lines count
    start = None
    rule = data.get("rule_y")
    for i, ln in enumerate(lines):
        if ln["kind"] is not None:
            continue
        txt = ln["text"].strip()
        if rule is not None:
            if ln["top"] >= rule - 5:
                start = i
                break
        elif ln["cy"] > C["footnote_zone"] and not MARKER_RE.match(txt):
            tiny_number = ln["h"] < 0.6 * med_h and re.fullmatch(r"\W*[০-৯0-9]{1,4}\W*", txt)
            foot_like = re.match(r"^\W{0,4}[০-৯0-9]{0,4}\s*[.।]\s+\S", txt) and "হাদিসের মান" in txt
            if tiny_number or foot_like or txt.startswith("হাদিসের মান"):
                start = i
                break
    if start is not None:
        for ln in lines[start:]:
            if ln["kind"] is None:
                ln["kind"] = "footnote"
    # headings (bold = much more ink per pixel) vs body
    for ln in lines:
        if ln["kind"] is not None:
            continue
        txt = ln["text"].strip()
        bold = med_ink and ln["ink"] >= C["heading_ink_ratio"] * med_ink
        huge = med_h and ln["h"] >= C["heading_huge_ratio"] * med_h and ln["ink"] >= 1.1 * med_ink   # tall AND not lighter than text
        cx = (ln["left"] + ln["right"]) / 2 / W
        wide = (ln["right"] - ln["left"]) / W >= 0.6
        centered = abs(cx - 0.5) < C["heading_center_tol"]      # headings are centred; short last body lines are not
        ln["ink_r"] = ln["ink"] / med_ink if med_ink else 0.0
        ln["h_r"] = ln["h"] / med_h if med_h else 0.0
        if (bold or huge) and (wide or centered) and len(txt) <= C["heading_max_chars"] \
                and not MARKER_RE.match(txt):
            ln["kind"] = "heading"
        else:
            ln["kind"] = "body"
    return lines


def apply_toc_headings(page_data, titles, C):
    """Body-page headings are only slightly bolder than the text, so the ink test misses some and they leak into
    the previous hadith. A bold-ish body line that matches a TOC section title (or a wrapped piece of one) IS a heading."""
    from difflib import SequenceMatcher
    tn = [_norm(t) for t in titles if len(_norm(t)) >= 5]
    n = 0
    for pd in page_data:
        for ln in pd["lines"]:
            if ln["kind"] != "body" or not (ln.get("ink_r", 0) >= C["toc_head_ink"] or ln.get("h_r", 0) >= C["toc_head_h"]):
                continue
            txt = ln["text"].strip()
            if MARKER_RE.match(txt):
                continue
            ln_n = _norm(txt)
            if len(ln_n) < 5:
                continue
            for t in tn:
                sm = SequenceMatcher(None, ln_n, t, autojunk=False)
                if sm.quick_ratio() < 0.6:
                    continue
                m = sm.find_longest_match(0, len(ln_n), 0, len(t))
                if m.size >= 0.75 * len(ln_n) and m.size >= 5 or sm.ratio() >= 0.8:
                    ln["kind"] = "heading"
                    n += 1
                    break
    return n


def structural_headings(page_data, C):
    """Headings the ink test missed, found by LAYOUT: a centred line that is indented on both sides, a little bolder
    than the text, and directly followed by the Arabic of the next hadith (or by another heading line)."""
    n = 0
    for pd in page_data:
        W = pd["width"]
        wide = [l for l in pd["lines"] if l["kind"] == "body" and (l["right"] - l["left"]) / W > 0.6]
        if len(wide) < 3:
            continue
        ml = statistics.median([l["left"] for l in wide])
        mr = statistics.median([l["right"] for l in wide])
        seq = [l for l in pd["lines"] if l["kind"] not in ("header", "noise")]
        for i in range(len(seq) - 2, -1, -1):
            ln, nxt = seq[i], seq[i + 1]
            txt = ln["text"].strip()
            if ln["kind"] != "body" or nxt["kind"] not in ("arabic", "heading") or MARKER_RE.match(txt):
                continue
            cx = (ln["left"] + ln["right"]) / 2
            if ln.get("ink_r", 0) >= C["struct_head_ink"] and ln["left"] >= ml + 0.02 * W \
                    and ln["right"] <= mr - 0.02 * W and abs(cx - (ml + mr) / 2) <= 0.04 * W \
                    and 4 <= len(txt) <= C["heading_max_chars"]:
                ln["kind"] = "heading"
                n += 1
        # the short last line of a wrapped heading, taken for Arabic only because it is light and badly read
        for i in range(1, len(seq)):
            ln = seq[i]
            cx = (ln["left"] + ln["right"]) / 2
            if ln["kind"] == "arabic" and seq[i - 1]["kind"] == "heading" and not AR_RE.search(ln["text"]) \
                    and len(BN_RE.findall(ln["text"])) >= 4 and ln["left"] >= ml + 0.02 * W \
                    and ln["right"] <= mr - 0.02 * W and abs(cx - (ml + mr) / 2) <= 0.04 * W:
                ln["kind"] = "heading"
                n += 1
    return n


def reread_headings(page_data, img_dir, a):
    """Section names when there is no table of contents to copy them from: every heading line is read several
    more times on its own (Bangla model, different sizes) and the reading whose words actually occur in the book's
    running text wins - the running text is read far more reliably than the bold headings."""
    import cv2
    import pytesseract
    from collections import Counter
    from PIL import Image, ImageOps
    if a.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = a.tesseract_cmd
    cfg = "--psm 7" + (f' --tessdata-dir "{a.tessdata}"' if a.tessdata else "")
    word_re = re.compile(r"[\u0980-\u09FF]{2,}")
    vocab = {w for pd in page_data for l in pd["lines"] if l["kind"] == "body" and l["conf"] >= 80
             for w in word_re.findall(l["text"])}

    def score(t):
        words = word_re.findall(t)
        return sum(w in vocab for w in words) / len(words) if words else 0.0

    for pd in page_data:
        heads = [l for l in pd["lines"] if l["kind"] == "heading"]
        if not heads:
            continue
        g = cv2.imread(os.path.join(img_dir, pd["image"]), cv2.IMREAD_GRAYSCALE)
        H, W = g.shape
        for ln in heads:
            x0, x1 = max(0, ln["left"] - 25), min(W, ln["right"] + 25)
            y0, y1 = max(0, ln["top"] - 10), min(H, ln["bottom"] + 10)
            rows = (g[y0:y1, x0:x1] < 128).sum(axis=1) > 0
            runs, start, gap = [], None, 0                # the line's box may catch the top of the next line:
            for i, on in enumerate(list(rows) + [False] * 6):   # keep the tallest band of ink only
                if on:
                    start, gap = (i if start is None else start), 0
                elif start is not None:
                    gap += 1
                    if gap >= 5:
                        runs.append((start, i - gap + 1))
                        start = None
            if runs:
                t, b = max(runs, key=lambda r: r[1] - r[0])
                y0, y1 = max(0, y0 + t - 8), min(H, y0 + b + 8)
            band = g[y0:y1, x0:x1]
            cands = [ln["text"].strip()]
            for sc in (1.0, 0.85, 0.75, 0.6):
                im = band if sc == 1 else cv2.resize(band, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
                t = pytesseract.image_to_string(ImageOps.expand(Image.fromarray(im), border=30, fill=255),
                                                lang="ben", config=cfg).strip()
                if t and not AR_RE.search(t):
                    cands.append(t)
            longest = max(len(BN_RE.findall(c)) for c in cands)
            cands = [c for c in cands if len(BN_RE.findall(c)) >= 0.7 * longest]     # no clipped readings
            votes = Counter(cands)
            ln["text"] = max(cands, key=lambda c: (round(score(c), 3), votes[c], c == cands[0]))


def mark_chapters(page_data, img_dir, a, known, reread):
    """Heading lines that are a chapter title ("অধ্যায় : ...") become kind 'chapter'. With reread=True (no table of
    contents to take the name from) the line is OCRed again on its own and matched against the known chapter names."""
    from difflib import SequenceMatcher
    names = []
    for pd in page_data:
        for ln in pd["lines"]:
            txt = ln["text"].strip()
            if not (CHAPTER_RE.match(txt) or CHAPTER_GARBLE_RE.match(txt)):
                continue
            if not (ln["kind"] == "heading" or (ln["kind"] == "body" and ln.get("ink_r", 0) >= 1.25)):
                continue
            ln["kind"] = "chapter"
            if not reread:
                continue
            import cv2
            import pytesseract
            from PIL import Image, ImageOps
            if a.tesseract_cmd:
                pytesseract.pytesseract.tesseract_cmd = a.tesseract_cmd
            tdir = f' --tessdata-dir "{a.tessdata}"' if a.tessdata else ""
            g = cv2.imread(os.path.join(img_dir, pd["image"]), cv2.IMREAD_GRAYSCALE)
            band = g[max(0, ln["top"] - 12):ln["bottom"] + 12]
            cands = []
            for scale in (0.6, 1.0):                           # the decorative type reads best a bit smaller
                im = cv2.resize(band, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale != 1 else band
                im = ImageOps.expand(Image.fromarray(im), border=30, fill=255)
                for psm in (13, 7):
                    t = pytesseract.image_to_string(im, lang="ben", config=f"--psm {psm}" + tdir).strip()
                    t = clean_toc_name(re.sub(r"^[^:：ঃ]{0,14}[:：ঃ]\s*", "", t))
                    if len(BN_RE.findall(t)) >= 4:
                        cands.append(t)
            best = (0.0, None)
            for t in cands:
                for k in known:
                    best = max(best, (SequenceMatcher(None, _norm(t), _norm(k), autojunk=False).ratio(), k))
            if best[0] >= 0.5:
                ln["chapter_name"] = best[1]
            else:
                ln["chapter_name"] = max(cands, key=len) if cands else "অধ্যায়"
                ln["chapter_unsure"] = True
            names.append(ln["chapter_name"])
    return names


def running_title(page_data):
    """The book title as printed in the running header of the pages (None if there is no consistent header)."""
    from collections import Counter
    c = Counter(re.sub(r"\s+", " ", l["text"]).strip() for pd in page_data for l in pd["lines"]
                if l["kind"] == "header" and len(BN_RE.findall(l["text"])) >= 4)
    if c:
        text, n = c.most_common(1)[0]
        if n >= max(2, len(page_data) // 4):
            return text
    return None
