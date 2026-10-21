"""Cleaning OCR text: Bangla body text, Arabic, TOC titles."""
import re

from . import config
from .config import AR_RE, common_fixes


def clean_bn(text, strip_lead=False):
    """Remove OCR junk from Bangla text: stray Arabic letters, direction marks, short Latin specks."""
    text = AR_RE.sub("", text)
    text = re.sub(r"[‎‏‪-‮]", "", text)
    text = re.sub(r"(?<=[ঀ-৿])\s*[|Il](?=\s|$|[”’\"\)\]]|[০-৯0-9*])", "।", text)   # the danda is often read as a pipe
    text = re.sub(r"(?<=[।?!\"”’»])\s*[০-৯]{2,4}(?=\s|$)", "", text)   # footnote superscripts after sentence end
    text = re.sub(r"(?:(?<=\s)|^)[A-Za-z0-9°*&@#~|\\/_^]{1,3}(?=\s|$)", " ", text)  # short Latin/symbol specks
    text = re.sub(r"\*+[০-৯0-9]{0,3}", "", text)                         # footnote reference marks: "*" / "*২"
    text = re.sub(r"[-_–—]*_[-_–—]*|--+", "—", text)                       # the print's dash "—" is read as "-_" / "--" / "_"
    text = common_fixes(text)
    text = re.sub(r"\s+", " ", text).strip()
    if strip_lead:
        text = re.sub(r"^[^ঀ-৿]+", "", text)
    return text


def _closes_reference(text):
    """True when the text ends with the number that closes an open bracket: "(সুরা মুমতাহিনা : ৮)"."""
    m = re.search(r"[০-৯]{1,3}(?:\s*[-,]\s*[০-৯]{1,3})?\s*\)\W{0,2}$", text)
    return bool(m) and text[:m.start()].count("(") > text[:m.start()].count(")")


def trim_tail(text):
    """Drop OCR junk after the last sentence end ('।” ৬9 চল চট 0৪৪ উস 2০41'): a short tail holding Latin letters or
    digit mixtures cannot be Bangla text."""
    # a trailing footnote number - but not the number that closes a reference such as "(সুরা মুমতাহিনা : ৮)"
    if re.search(r"[ঀ-৿]\W{0,3}\s*[০-৯0-9]{1,4}\W{0,2}$", text) and not _closes_reference(text):
        text = re.sub(r"\s*[০-৯0-9]{1,4}\W{0,2}$", "", text)
    text = re.sub(r"(?<=[।?!”’)])['\"]+$", "", text)             # stray quote from the superscript
    while True:                                           # trailing tokens with digits/Latin and no Bangla letter
        m = re.search(r"\s+(\S+)$", text)
        if m and not re.search(r"[\u0985-\u09B9\u09DC-\u09DF]", m.group(1)) \
                and re.search(r"[0-9০-৯A-Za-z<>]", m.group(1)) and not _closes_reference(text):
            text = text[:m.start()]
        else:
            break
    m = None
    for m in re.finditer(r"[।?!”’\"»)]", text):
        pass
    if m and 0 < len(text) - m.end() <= 45:
        tail = text[m.end():]
        if re.search(r"[A-Za-z]|[০-৯][0-9]|[0-9][০-৯]|\d{3,}", tail):
            return text[:m.end()].rstrip()
    return text


def clean_ar(text):
    """Remove Bangla/Latin junk that OCR sometimes produces inside Arabic lines."""
    text = re.sub(r"[ঀ-৿A-Za-z]", "", text)
    text = re.sub(r"[‎‏‪-‮]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _norm(t):
    """Bangla letters + vowel signs only (re's \\W would also strip the vowel signs)."""
    return re.sub(r"[০-৯]+", "", re.sub(r"[^\u0980-\u09FF]+", "", t))


def clean_toc_name(t):
    t = t.replace("\u200c", "").replace("\u200d", "")
    t = re.sub(r"[-_–—]*_[-_–—]*|--+", "—", t)            # "-_" "__" "--" were a dash in the print
    t = re.sub(r"(?<=[ঀ-৿])_(?=[ঀ-৿])", "-", t)           # "আচার_ব্যবহার" -> "আচার-ব্যবহার"
    for pat, rep in config.TOC_FIXES:
        t = pat.sub(rep, t)
    t = common_fixes(t)
    t = re.sub(r"^[^ঀ-৿\"“‘(]+", "", t)                  # leading specks
    t = re.sub(r"[^ঀ-৿?!।\"”’)]+$", "", t)               # trailing specks / leader remains
    return re.sub(r"\s+", " ", t).strip()
