"""Step 1: page images out of the PDF; the footnote rule on a page."""
import os


def parse_pages(spec, n):
    if not spec:
        return list(range(1, n + 1))
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += range(int(a), min(int(b), n) + 1)
        else:
            out.append(int(part))
    return sorted({p for p in out if 1 <= p <= n})


def extract_images(pdf, img_dir, pages):
    import pymupdf
    os.makedirs(img_dir, exist_ok=True)
    doc = pymupdf.open(pdf)
    paths = {}
    for p in parse_pages(pages, len(doc)) if isinstance(pages, str) or pages is None else pages:
        existing = [f for f in os.listdir(img_dir) if f.startswith(f"page_{p:03d}.")]
        if existing:
            paths[p] = os.path.join(img_dir, existing[0])
            continue
        page = doc[p - 1]
        imgs = page.get_images(full=True)
        if imgs:  # scanned page: take the largest embedded image untouched
            best = max(imgs, key=lambda i: i[2] * i[3])
            info = doc.extract_image(best[0])
            ext = "jpg" if info["ext"] in ("jpeg", "jpg") else info["ext"]
            path = os.path.join(img_dir, f"page_{p:03d}.{ext}")
            with open(path, "wb") as f:
                f.write(info["image"])
        else:     # digital page: render it
            path = os.path.join(img_dir, f"page_{p:03d}.png")
            page.get_pixmap(dpi=300).save(path)
        paths[p] = path
    return paths


def find_rule(img_path):
    """y of the short horizontal rule printed above the footnotes (None if the page has none).
    Bangla headlines are long thin strokes too, so a candidate must also be ISOLATED: blank just above and below."""
    import cv2
    import numpy as np
    g = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
    H, W = g.shape
    bw = (g < 128).astype(np.uint8)
    lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((1, max(10, int(0.15 * W))), np.uint8))
    rows = np.where(lines.sum(axis=1) >= 0.15 * W)[0]
    rows = rows[(rows > 0.5 * H) & (rows < 0.97 * H)]
    if not len(rows):
        return None
    groups, cur = [], [rows[0]]
    for y in rows[1:]:
        if y - cur[-1] <= 1:
            cur.append(y)
        else:
            groups.append(cur)
            cur = [y]
    groups.append(cur)
    for gp in groups:
        top, bot = gp[0], gp[-1]
        if bot - top > 12:                                # too thick for a rule
            continue
        xs = np.where(lines[top:bot + 1].sum(axis=0) > 0)[0]
        if not 0.15 * W <= xs[-1] - xs[0] <= 0.7 * W:
            continue
        above = bw[max(0, top - 14):max(0, top - 3), xs[0]:xs[-1] + 1].sum()
        below = bw[bot + 4:bot + 15, xs[0]:xs[-1] + 1].sum()
        if above < 0.02 * (xs[-1] - xs[0]) * 11 and below < 0.02 * (xs[-1] - xs[0]) * 11:
            return int(top)
    return None
