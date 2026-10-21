"""Step 2: OCR of every page (Tesseract, pages in parallel), with a second look at weak lines."""
import json
import os
import re
import statistics
import time
from collections import defaultdict

from .config import AR_RE, BN_RE


def _fix_tail(img, d, idx, tess_cmd, tessdata):
    """A footnote superscript glued to the last word often wrecks it: 'আরো দান করো।⁶³' comes out as 'আরো দান 37'.
    The last token of a Bangla line is re-read from a crop without its right-hand 20% (where the superscript sits)."""
    import pytesseract
    from PIL import ImageOps
    if len(idx) < 3 or any(AR_RE.search(d["text"][i]) for i in idx[:-1]):
        return
    last = idx[-1]
    tok = d["text"][last].strip()
    # the wrecked token shows up as a bare number - or as a scrap of "Arabic" at the end of a Bangla line
    digit_tail = re.fullmatch(r"[0-9০-৯]{1,4}\W*", tok) is not None or bool(AR_RE.search(tok))
    short_end = d["left"][last] + d["width"][last] < 0.85 * img.size[0] and not tok.endswith(tuple("।?!”’\"»)"))
    if not (digit_tail or short_end):
        return
    if tess_cmd:
        pytesseract.pytesseract.tesseract_cmd = tess_cmd
    l, t, w, h = d["left"][last], d["top"][last], d["width"][last], d["height"][last]
    crop = ImageOps.expand(img.crop((l, max(0, t - 4), l + int(w * 0.8), t + h + 8)), border=20, fill=255)
    cfg = "--psm 7" + (f' --tessdata-dir "{tessdata}"' if tessdata else "")
    new = pytesseract.image_to_string(crop, lang="ben", config=cfg).strip()
    core = new.rstrip("।|!?”’\"*")
    if not BN_RE.search(new) or not core:
        return
    # a word that was read is only replaced when the re-read KEEPS it whole and adds the lost sentence end
    if digit_tail or (core.startswith(tok) and new != tok and new[-1] in "।|!?”’"):
        d["text"][last] = new


def _read_line(img, box, tess_cmd, tessdata):
    """OCR one line crop on its own with the Bangla model -> (text, mean confidence, words).
    words: [text, confidence, left, top, right, bottom] per word, in page coordinates."""
    import pytesseract
    from PIL import ImageOps
    if tess_cmd:
        pytesseract.pytesseract.tesseract_cmd = tess_cmd
    cfg = "--psm 7" + (f' --tessdata-dir "{tessdata}"' if tessdata else "")
    d = pytesseract.image_to_data(ImageOps.expand(img.crop(box), border=30, fill=255), lang="ben", config=cfg,
                                  output_type=pytesseract.Output.DICT)
    idx = [i for i, t in enumerate(d["text"]) if t.strip()]
    if not idx:
        return "", 0.0, []
    x0, y0 = box[0] - 30, box[1] - 30                     # crop origin, minus the white border added around it
    words = [[d["text"][i], round(float(d["conf"][i])), x0 + d["left"][i], y0 + d["top"][i],
              x0 + d["left"][i] + d["width"][i], y0 + d["top"][i] + d["height"][i]] for i in idx]
    return " ".join(d["text"][i] for i in idx), sum(float(d["conf"][i]) for i in idx) / len(idx), words


def _second_look(img, lines, tess_cmd, tessdata):
    """Two repairs of the page-level OCR, both by reading a strip of the page as ONE line:
      * the short last line of a paragraph (where the footnote superscript sits) is read again; the new reading is
        kept when it is at least as confident and loses nothing ('দয়া করছে' -> 'দয়া করছো।');
      * a strip with ink that the page OCR skipped completely (a lone 'হাঁ।' under a paragraph) is read and added."""
    W, H = img.size
    if not lines:
        return
    med_h = statistics.median([l["bottom"] - l["top"] for l in lines])
    for ln in lines:
        txt = ln["text"]
        if AR_RE.search(txt) or len(BN_RE.findall(txt)) < 4 or ln["right"] >= 0.85 * W \
                or not 0.5 * med_h <= ln["bottom"] - ln["top"] <= 1.8 * med_h:
            continue
        new, conf, words = _read_line(img, (max(0, ln["left"] - 15), max(0, ln["top"] - 8), min(W, ln["right"] + 60),
                                            min(H, ln["bottom"] + 8)), tess_cmd, tessdata)
        if conf >= ln["conf"] - 2 and len(BN_RE.findall(new)) >= len(BN_RE.findall(txt)) and not AR_RE.search(new):
            ln["text"], ln["conf"], ln["words"] = new, max(conf, ln["conf"]), words
    covered = [False] * H
    for ln in lines:
        for y in range(max(0, ln["top"] - 4), min(H, ln["bottom"] + 4)):
            covered[y] = True
    grey = img.load()
    step = max(1, W // 600)                               # sample columns: fast enough in pure Python
    xs = range(0, W, step)
    bands, start = [], None
    for y in range(int(0.08 * H), int(0.92 * H)):
        dark = (not covered[y]) and sum(1 for x in xs if grey[x, y] < 128) > 2
        if dark and start is None:
            start = y
        elif not dark and start is not None:
            bands.append((start, y))
            start = None
    for t, b in bands:
        if not 0.4 * med_h <= b - t <= 1.6 * med_h:
            continue
        cols = [x for x in xs if any(grey[x, y] < 128 for y in range(t, b, 2))]
        if not cols or cols[-1] - cols[0] > 0.6 * W:      # a whole missed line is Arabic or a rule, not a stray word
            continue
        box = (max(0, cols[0] - 20), max(0, t - 8), min(W, cols[-1] + 20), min(H, b + 8))
        text, conf, words = _read_line(img, box, tess_cmd, tessdata)
        if conf >= 75 and len(BN_RE.findall(text)) >= 2 and not AR_RE.search(text):
            crop = img.crop(box)
            hist = crop.histogram()
            lines.append(dict(text=text, left=box[0], top=t, right=box[2], bottom=b, conf=conf,
                              ink=sum(hist[:128]) / max(1, sum(hist)), words=words))


def _ocr_worker(args):
    page, img_path, json_path, lang, tess_cmd, tessdata = args
    import pytesseract
    from PIL import Image
    if tess_cmd:
        pytesseract.pytesseract.tesseract_cmd = tess_cmd
    cfg = "--psm 4"
    if tessdata:
        cfg += f' --tessdata-dir "{tessdata}"'
    img = Image.open(img_path).convert("L")
    W, H = img.size
    d = pytesseract.image_to_data(img, lang=lang, config=cfg, output_type=pytesseract.Output.DICT)
    groups = defaultdict(list)
    for i, t in enumerate(d["text"]):
        if t.strip() and d["level"][i] == 5:
            groups[(d["block_num"][i], d["par_num"][i], d["line_num"][i])].append(i)
    for idx in groups.values():
        _fix_tail(img, d, idx, tess_cmd, tessdata)
    lines = []
    for idx in groups.values():
        l = min(d["left"][i] for i in idx)
        t = min(d["top"][i] for i in idx)
        r = max(d["left"][i] + d["width"][i] for i in idx)
        b = max(d["top"][i] + d["height"][i] for i in idx)
        confs = [float(d["conf"][i]) for i in idx if float(d["conf"][i]) >= 0]
        crop = img.crop((l, t, r, b))
        hist = crop.histogram()                      # 256 grey levels; "ink" = share of dark pixels
        ink = sum(hist[:128]) / max(1, sum(hist))
        words = [[d["text"][i], round(float(d["conf"][i])), d["left"][i], d["top"][i],
                  d["left"][i] + d["width"][i], d["top"][i] + d["height"][i]] for i in idx]   # for the web review
        lines.append(dict(text=" ".join(d["text"][i] for i in idx), left=l, top=t, right=r, bottom=b,
                          conf=sum(confs) / len(confs) if confs else 0.0, ink=ink, words=words))
    _second_look(img, lines, tess_cmd, tessdata)
    lines.sort(key=lambda x: x["top"])
    with open(json_path, "w", encoding="utf8") as f:
        json.dump(dict(page=page, width=W, height=H, image=os.path.basename(img_path), lines=lines), f,
                  ensure_ascii=False)
    return page


def run_ocr(paths, ocr_dir, args):
    from concurrent.futures import ProcessPoolExecutor, as_completed
    os.makedirs(ocr_dir, exist_ok=True)
    todo = []
    for p, ip in sorted(paths.items()):
        jp = os.path.join(ocr_dir, f"page_{p:03d}.json")
        if args.re_ocr or not os.path.exists(jp):
            todo.append((p, ip, jp, args.lang, args.tesseract_cmd, args.tessdata))
    print(f"OCR: {len(paths) - len(todo)} pages cached, {len(todo)} to do, {args.workers} workers")
    if not todo:
        return
    t0 = time.time()
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(_ocr_worker, t) for t in todo]
        for fu in as_completed(futs):
            fu.result()
            done += 1
            el = time.time() - t0
            eta = el / done * (len(todo) - done)
            print(f"  page done {done}/{len(todo)}  elapsed {el/60:.1f} min  ETA {eta/60:.1f} min", flush=True)
