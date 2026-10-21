"""Step 4: the body text cut into numbered hadiths, numbers repaired, hadiths put in sections."""
import re
from collections import defaultdict

from .config import BN_RE, C_MAX_JUMP, C_MIN_KEEP, EMBED_RE, FOOT_START_RE, MARKER_RE, bn_int
from .text import _norm, clean_ar, clean_bn, clean_toc_name, trim_tail


def build_entries(page_data, C):
    entries, unnumbered, foot = [], [], defaultdict(list)
    cur = None
    pending_ar, pending_ar_pages, pending_conf = [], [], []
    heading, prev_heading_line, chapter = "", False, ""
    fblocks, foot_open = [], False         # footnote blocks in reading order

    def new_entry(no, page, flag=None):
        e = dict(no=no, heading=heading, chapter=chapter, arabic=[], bangla=[], pages=[page], conf=[], flags=[],
                 words=[])
        if flag:
            e["flags"].append(flag)
        if pending_ar:
            e["arabic"] = list(pending_ar)
            e["pages"] = sorted(set(pending_ar_pages + [page]))
            e["conf"] += pending_conf
        entries.append(e)
        return e

    for pd in page_data:
        pg = pd["page"]
        for ln in pd["lines"]:
            k, txt = ln["kind"], ln["text"].strip()
            if k in ("header", "noise"):
                continue
            if k == "footnote":
                frag = re.fullmatch(r"\W{0,3}([০-৯0-9]{1,4})\W{0,3}", txt)
                if frag:                                   # tiny superscript = start of a new footnote
                    fblocks.append(dict(no=bn_int(frag.group(1)), page=pg, parts=[]))
                    foot_open = True
                    continue
                m = FOOT_START_RE.match(txt)
                if m:
                    fblocks.append(dict(no=bn_int(m.group(1)), page=pg, parts=[txt[m.end():]]))
                    foot_open = True
                elif foot_open and fblocks:
                    fblocks[-1]["parts"].append(txt)
                else:                                      # superscript was merged into the text: number lost
                    fblocks.append(dict(no=None, page=pg, parts=[txt]))
                    foot_open = True
                if re.search(r"হাদিসের মান\s*[:ঃ]?\s*[ঀ-৿]", " ".join(fblocks[-1]["parts"])):
                    foot_open = False                      # grade word present = footnote complete
                    # (if the grade word wrapped onto the next line, the block stays open until it arrives)
                continue
            foot_open = False
            if k == "chapter":                         # "অধ্যায় : ..." printed in the text itself
                chapter, prev_heading_line = ln.get("chapter_name") or txt, False
                continue
            if k == "heading":
                heading = (heading + " " + txt) if prev_heading_line else txt
                prev_heading_line = True
                continue
            prev_heading_line = False
            if k == "arabic":
                # Arabic after a translation is either the NEXT entry's Arabic (a [number] marker follows)
                # or a quote inside the current translation (more Bangla follows) - decided at the next body line.
                pending_ar.append(txt)
                pending_ar_pages.append(pg)
                pending_conf.append(ln["conf"])
                continue
            # body line
            m = MARKER_RE.match(txt)
            if not m and pending_ar and (len(txt) <= 4 or (ln["conf"] < 45 and len(txt) < 25)):
                continue                            # OCR speck after an Arabic block, not real translation text
            if m:
                cur = new_entry(bn_int(m.group(1) or m.group(2)), pg)
                cur["anchor"] = dict(page=pg, y0=round(ln["top"] / pd["height"], 4),
                                     y1=round(ln["bottom"] / pd["height"], 4))   # where the hadith starts (web review)
                pending_ar, pending_ar_pages, pending_conf = [], [], []
                txt = txt[m.end():]
            elif cur is None:
                if pending_ar and (len(txt) < 12 or ln["conf"] < 50):
                    continue                        # speck / junk between Arabic and its marker
                if pending_ar:                      # Arabic followed by Bangla with no marker
                    cur = new_entry(None, pg, "no_marker")
                    cur["anchor"] = dict(page=pg, y0=round(ln["top"] / pd["height"], 4),
                                         y1=round(ln["bottom"] / pd["height"], 4))
                    pending_ar, pending_ar_pages, pending_conf = [], [], []
                else:                               # front matter / intro text
                    unnumbered.append((pg, "text", txt))
                    continue
            elif pending_ar:                        # Arabic quote inside the current translation
                cur["arabic"] += pending_ar
                pending_ar, pending_ar_pages, pending_conf = [], [], []
            cur["bangla"].append(txt)
            cur["conf"].append(ln["conf"])
            W, H = pd["width"], pd["height"]
            cur["words"] += [dict(w=w[0], conf=w[1], page=pg, box=[round(w[2] / W, 4), round(w[3] / H, 4),
                                                                   round(w[4] / W, 4), round(w[5] / H, 4)])
                             for w in ln.get("words", []) if w[1] < C["word_low_conf"]]
            if pg not in cur["pages"]:
                cur["pages"].append(pg)

    # footnotes: drop junk blocks, fill lost numbers (footnote numbers run consecutively), index by number
    prev_no = None
    for b in fblocks:
        b["text"] = clean_bn(" ".join(b["parts"]), strip_lead=True)
        if len(BN_RE.findall(b["text"])) < 6:
            b["junk"] = True
            continue
        if b["no"] is None and prev_no is not None:
            b["no"] = prev_no + 1
            b["guessed"] = True
        if b["no"] is not None:
            prev_no = b["no"]
    good = [b for b in fblocks if not b.get("junk")]
    nxt_no = None
    for b in reversed(good):                               # leading blocks with no predecessor: count back
        if b["no"] is None and nxt_no is not None and nxt_no > 1:
            b["no"], b["guessed"] = nxt_no - 1, True
        if b["no"] is not None:
            nxt_no = b["no"]
    for b in good:
        foot[b["no"]].append(b)

    # finalise
    prev = None
    for e in entries:
        e["arabic_text"] = clean_ar(" ".join(e["arabic"]))
        e["bangla_text"] = trim_tail(clean_bn(" ".join(e["bangla"])))
        e["conf_mean"] = round(sum(e["conf"]) / len(e["conf"]), 1) if e["conf"] else 0
        e["page_start"], e["page_end"] = min(e["pages"]), max(e["pages"])
        e["foot_text"] = ""
        if e["no"] is not None and e["no"] in foot:
            blocks = foot.pop(e["no"])
            e["foot_text"] = " ".join(b["text"] for b in blocks)
        if e["no"] is not None:
            if prev is not None and e["no"] != prev + 1:
                e["flags"].append(f"seq_gap(expected {prev + 1})")
            prev = e["no"]
        if e["conf_mean"] < C["low_conf"]:
            e["flags"].append("low_conf")
        if len(e["bangla_text"]) < C["min_text_chars"]:
            e["flags"].append("short_text")
        if not e["arabic_text"]:
            e["flags"].append("no_arabic")
    for no, blocks in foot.items():
        for b in blocks:
            unnumbered.append((b["page"], f"footnote {no if no is not None else '?'} (unmatched)", b["text"]))
    return entries, unnumbered


def _same_number(a, b):
    """Equal, or equal up to the classic Bangla OCR confusion of ৪ (4) and ৮ (8)."""
    sa, sb = str(a), str(b)
    return len(sa) == len(sb) and all(x == y or {x, y} == {"4", "8"} for x, y in zip(sa, sb))


def repair_numbers(entries):
    """Hadith numbers are 1,2,3,... so the previous number predicts the next one. Fix what OCR got wrong:
      * ৪/৮ mix-ups ([৮০০] for [৪০০]) are corrected silently;
      * a marker that does not fit but whose NEXT neighbour proves it is the expected number is corrected
        (flag no_repaired);
      * a marker that jumps forward a little is kept and flagged seq_gap (a hadith in between was not detected);
        a huge jump is a misread and gets the expected number (flag no_repaired);
      * a marker that jumps backward is not a hadith start ("[৭]" inside a list...): its text is appended to
        the previous hadith (flag merged_marker)."""
    out, prev = [], None
    for i, e in enumerate(entries):
        e["flags"] = [f for f in e["flags"] if not f.startswith("seq_gap")]
        o = e["no"]
        if prev is None:
            out.append(e)
            prev = _split_embedded(e, out)["no"]
            continue
        exp = prev + 1
        nxt = entries[i + 1]["no"] if i + 1 < len(entries) else None
        if o == exp:
            pass
        elif o is not None and _same_number(o, exp):
            e["no"] = exp
        elif o is None or o > exp + C_MAX_JUMP or (nxt is not None and (nxt == exp + 1 or _same_number(nxt, exp + 1))):
            e["flags"].append(f"no_repaired(read {o})")
            e["no"] = exp
        elif o > exp:
            e["flags"].append(f"seq_gap(expected {exp})")
        else:                                                 # backwards: not a new hadith
            if len(e["bangla_text"]) >= C_MIN_KEEP:
                out[-1]["bangla_text"] = (out[-1]["bangla_text"] + " " + e["bangla_text"]).strip()
                out[-1]["page_end"] = max(out[-1]["page_end"], e["page_end"])
                out[-1]["flags"].append(f"merged_marker(read {o})")
                out[-1]["words"] = out[-1].get("words", []) + e.get("words", [])
            continue
        out.append(e)
        e = _split_embedded(e, out)
        prev = e["no"]
    entries[:] = out


def _split_embedded(e, out):
    """The previous hadith ended in the middle of a printed line, so the next marker sits mid-line: cut there."""
    while e["no"] is not None:
        m = EMBED_RE.search(e["bangla_text"], 10)
        n = bn_int(m.group(1)) if m else None
        if n is None or not (n == e["no"] + 1 or _same_number(n, e["no"] + 1)):
            break
        new = dict(e, no=e["no"] + 1, bangla_text=e["bangla_text"][m.end():].strip(),
                   page_start=e["page_end"], flags=["split_marker"], anchor=None)
        e["bangla_text"] = e["bangla_text"][:m.start()].strip()
        out.append(new)
        e = new
    return e


def structure_from_headings(entries):
    """No table of contents: chapters and sections are the headings met in the text, in order.
    -> chapters, sections, chap_of (per hadith, -1 = none), sec_of (per hadith, -1 = none)."""
    chapters, sections, chap_of, sec_of = [], [], [], []
    prev = None
    for e in entries:
        key = (e.get("chapter", ""), e["heading"])
        if key != prev:
            if e.get("chapter") and (prev is None or prev[0] != e["chapter"]):
                chapters.append(e["chapter"])
            name = clean_toc_name(e["heading"])
            if name:
                sections.append(name)
            prev = key
            cur_sec = len(sections) - 1 if name else -1
        chap_of.append(len(chapters) - 1)
        sec_of.append(cur_sec)
    return chapters, sections, chap_of, sec_of


def assign_sections(entries, sections):
    """Index of the TOC section each hadith sits under. Headings and the TOC list are both in book order, so each
    heading change is matched against the next few sections only (a heading the OCR missed does not derail it)."""
    from difflib import SequenceMatcher
    norm = [_norm(t) for t in sections]
    cur, prev_heading, out, weak = -1, None, [], 0
    for e in entries:
        h = _norm(e["heading"])
        if e["heading"] != prev_heading:
            prev_heading = e["heading"]
            if cur == -1:
                cur = 0
            if h:
                lo = cur
                cand = [(SequenceMatcher(None, h, norm[i], autojunk=False).ratio(), i)
                        for i in range(lo, min(len(sections), lo + 7))]
                best, bi = max(cand) if cand else (0, cur)
                if best >= 0.45:
                    cur = bi
                else:                                         # not a section title (a body line taken for a heading)
                    weak += 1
        out.append(max(cur, 0))
    return out, weak
