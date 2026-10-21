"""Editing a book (the dict saved as book.json) during review: split / merge / delete hadiths, find & replace,
sections and chapters, and the spelling check. Pure functions on the dict, used by app.py and the tests.

A hadith's section is an index into book["sections"]; section_chapter[i] is the chapter of section i. Sections are
runs of consecutive hadiths in book order, so they are added by starting one at a hadith ("a new section begins
here") and deleted by joining their hadiths to the section before.
"""
import re
from collections import Counter, defaultdict

from .config import fix_pattern


class EditError(ValueError):
    """An edit that cannot be done; the message is meant for the person editing."""


def find(book, uid):
    for i, h in enumerate(book["hadiths"]):
        if h["uid"] == uid:
            return i, h
    raise EditError("This hadith no longer exists (it was merged or deleted).")


def resync(book):
    """Every hadith in a section takes that section's chapter."""
    sc = book["section_chapter"]
    for h in book["hadiths"]:
        if 0 <= h["section"] < len(sc):
            h["chapter"] = sc[h["section"]]


def _new_uid(book):
    n = book.get("next_uid", len(book["hadiths"]))
    book["next_uid"] = n + 1
    return f"h{n}"


# ----------------------------------------------------------------------------------------------- hadiths
def split(book, uid, offset, no=None):
    """Cut a hadith in two at a character offset (two hadiths the OCR ran together). -> (first, second)"""
    i, h = find(book, uid)
    head, tail = h["text"][:offset].strip(), h["text"][offset:].strip()
    if not head or not tail:
        raise EditError("Put the cursor inside the text, where the second hadith begins.")
    if no is None and h["no"] is not None:
        no = h["no"] + 1
    new = dict(h, uid=_new_uid(book), no=no, text=tail, ocr_text=tail, arabic="", foot="", flags=["manual_split"],
               anchor=None, reviewed=False, page_start=h["page_end"],
               doubt=[d for d in h.get("doubt", []) if d["w"] in tail])
    h["text"] = head
    h["doubt"] = [d for d in h.get("doubt", []) if d["w"] in head]
    book["hadiths"].insert(i + 1, new)
    return h, new


def merge(book, uid):
    """Append a hadith to the one before it (a stray marker cut one hadith in two). -> the hadith before"""
    i, h = find(book, uid)
    if i == 0:
        raise EditError("The first hadith has nothing before it to merge into.")
    prev = book["hadiths"][i - 1]
    prev["text"] = (prev["text"].rstrip() + " " + h["text"].lstrip()).strip()
    prev["ocr_text"] = (prev["ocr_text"] + " " + h["ocr_text"]).strip()
    prev["page_end"] = max(prev["page_end"], h["page_end"])
    prev["doubt"] = prev.get("doubt", []) + h.get("doubt", [])
    if h.get("foot") and not prev.get("foot"):
        prev["foot"] = h["foot"]
    del book["hadiths"][i]
    return prev


def delete(book, uid):
    i, _ = find(book, uid)
    del book["hadiths"][i]


# ----------------------------------------------------------------------------------------------- find & replace
def pattern(text, regex=False, word=False):
    if not text:
        raise EditError("Type what to find.")
    try:
        return fix_pattern(text, regex, word)
    except re.error as e:
        raise EditError(f"Not a valid regular expression: {e}")


def search(book, pat, limit=300):
    """-> (matches, total): per hadith with a match, its uid, number, count and up to 3 snippets
    (before, match, after)."""
    out, total = [], 0
    for h in book["hadiths"]:
        ms = list(pat.finditer(h["text"]))
        if not ms:
            continue
        total += len(ms)
        if len(out) < limit:
            snips = [(h["text"][max(0, m.start() - 40):m.start()], m.group(0), h["text"][m.end():m.end() + 40])
                     for m in ms[:3]]
            out.append(dict(uid=h["uid"], no=h["no"], count=len(ms), snippets=snips))
    return out, total


def replace(book, pat, repl, regex=False, uids=None):
    """Replace in every hadith (or only in `uids`). -> (replacements, hadiths changed)"""
    rep = repl if regex else (lambda m: repl)
    n = changed = 0
    only = set(uids) if uids is not None else None
    for h in book["hadiths"]:
        if only is not None and h["uid"] not in only:
            continue
        try:
            new, k = pat.subn(rep, h["text"])
        except (re.error, IndexError) as e:
            raise EditError(f"The replacement does not fit the pattern: {e}")
        if k:
            h["text"] = new
            n += k
            changed += 1
    return n, changed


# ----------------------------------------------------------------------------------------------- structure
def rename(book, title=None, chapters=None, sections=None):
    for key, names in (("chapters", chapters), ("sections", sections)):
        if names is not None:
            names = [str(t).strip() for t in names]
            if len(names) != len(book[key]):
                raise EditError(f"Expected {len(book[key])} {key}.")
            book[key] = [n or old for n, old in zip(names, book[key])]
    if title is not None and str(title).strip():
        book["title"] = str(title).strip()


def _shift_sections(book, at, by):
    for h in book["hadiths"]:
        if h["section"] >= at:
            h["section"] += by


def new_section(book, uid, name):
    """A new section begins at this hadith: it and the following hadiths of the same section move into it.
    -> index of the new section"""
    name = str(name).strip()
    if not name:
        raise EditError("Give the new section a name.")
    i, h = find(book, uid)
    old = h["section"]
    hs = book["hadiths"]
    if old >= 0 and i > 0 and hs[i - 1]["section"] != old:
        raise EditError("A section already begins at this hadith. Rename it on the Chapters & sections tab.")
    at = old + 1 if old >= 0 else 1 + max([x["section"] for x in hs[:i]] + [-1])
    chapter = book["section_chapter"][old] if old >= 0 else max(h["chapter"], 0 if book["chapters"] else -1)
    _shift_sections(book, at, 1)
    book["sections"].insert(at, name)
    book["section_chapter"].insert(at, chapter)
    for x in hs[i:]:                                       # (old < at, so the shift left `old` alone)
        if x["section"] != old:
            break
        x["section"] = at
    resync(book)
    return at


def delete_section(book, si):
    """Remove a section; its hadiths join the section before it (the one after it, for the first section)."""
    if not 0 <= si < len(book["sections"]):
        raise EditError("No such section.")
    dest = si - 1 if si > 0 else (1 if len(book["sections"]) > 1 else -1)    # index before the removal
    for h in book["hadiths"]:
        if h["section"] == si:
            h["section"] = dest
    _shift_sections(book, si + 1, -1)
    del book["sections"][si]
    del book["section_chapter"][si]
    resync(book)


def move_section(book, si, ci):
    """Put a section under another chapter."""
    if not 0 <= si < len(book["sections"]) or not -1 <= ci < len(book["chapters"]):
        raise EditError("No such section or chapter.")
    book["section_chapter"][si] = ci
    resync(book)


def add_chapter(book, at, name):
    """Insert a chapter at index `at` (0 = first). Sections are moved into it with move_section. -> its index"""
    name = str(name).strip()
    if not name:
        raise EditError("Give the new chapter a name.")
    at = max(0, min(int(at), len(book["chapters"])))
    book["chapters"].insert(at, name)
    book["section_chapter"] = [c + 1 if c >= at else c for c in book["section_chapter"]]
    for h in book["hadiths"]:
        if h["chapter"] >= at:
            h["chapter"] += 1
    return at


def delete_chapter(book, ci):
    """Remove a chapter; its sections join the chapter before it (the one after it, for the first chapter)."""
    if not 0 <= ci < len(book["chapters"]):
        raise EditError("No such chapter.")
    n = len(book["chapters"])
    target = ci - 1 if ci > 0 else (0 if n > 1 else -1)    # index after the removal

    def remap(c):
        if c == ci:
            return target
        return c - 1 if c > ci else c
    book["section_chapter"] = [remap(c) for c in book["section_chapter"]]
    for h in book["hadiths"]:
        if h["chapter"] >= 0:
            h["chapter"] = remap(h["chapter"])
    del book["chapters"][ci]
    resync(book)


# ----------------------------------------------------------------------------------------------- spelling
WORD_RE = re.compile(r"[ঀ-৥ৰ-৿]+")     # Bangla letters and signs, no digits


def spelling(book, rare=2, common=8, ratio=8, min_len=5, keep_end=2):
    """Words that are probably OCR slips, found without a dictionary: the book is its own word list. A word seen
    at most `rare` times that differs by ONE letter or sign (ত/ভ, ছ/হ, ন/স, ু/ূ ...) from a word seen at least
    `common` (and `ratio` x as many) times is suspect, with the common word as the suggestion.
    Measured on Al-Adab al-Mufrad, these limits keep about half of the suggestions real: Bangla is full of
    one-letter pairs of real words, mostly short ones and word endings (তার/তারা, করে/করো), so words shorter than
    `min_len` and changes in the last `keep_end` characters are not considered, and only same-length swaps count.
    Words in book["spell_ignore"] are never suspect.
    -> {uid: [{w, s, n}]}  (word, suggestion, how often the suggestion occurs in the book)"""
    words_of = {h["uid"]: WORD_RE.findall(h["text"]) for h in book["hadiths"]}
    counts = Counter(w for ws in words_of.values() for w in ws)
    ignore = set(book.get("spell_ignore", []))
    index = defaultdict(list)                              # (length, position, word with that position blanked)
    for w, c in counts.items():
        if c >= common and len(w) >= min_len:
            for i in range(len(w) - keep_end):
                index[(len(w), i, w[:i] + w[i + 1:])].append(w)
    suggest = {}
    for w, c in counts.items():
        if c > rare or len(w) < min_len or w in ignore:
            continue
        best = None
        for i in range(len(w) - keep_end):
            for cand in index.get((len(w), i, w[:i] + w[i + 1:]), ()):
                if cand != w and counts[cand] >= ratio * c and (best is None or counts[cand] > counts[best]):
                    best = cand
        if best:
            suggest[w] = best
    out = {}
    for uid, ws in words_of.items():
        seen = []
        for w in ws:
            if w in suggest and w not in seen:
                seen.append(w)
        if seen:
            out[uid] = [dict(w=w, s=suggest[w], n=counts[suggest[w]]) for w in seen]
    return out


def ignore_word(book, word):
    """Never suggest a correction for this word again (it is a real word, not an OCR slip)."""
    book.setdefault("spell_ignore", [])
    if word not in book["spell_ignore"]:
        book["spell_ignore"].append(word)
