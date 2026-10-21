"""Settings, regular expressions and small helpers shared by every step."""
import re


CONFIG = dict(
    header_zone=0.075,        # lines whose centre is above this fraction of page height = running header
    ornament_zone=0.95,       # lines below this fraction, if tiny/low-confidence = bottom ornament
    footnote_zone=0.72,       # footnotes can only start below this fraction of page height
    footnote_height_ratio=0.82,   # footnote line is shorter than this x median body line height
    arabic_ratio=0.50,        # line is Arabic if Arabic chars / (Arabic+Bangla chars) >= this
    arabic_ink_ratio=0.75,    # ... or if it is lighter than this x body ink density ...
    arabic_ratio_if_light=0.10,   # ... and at least this Arabic share (rescues badly-OCRed Arabic lines)
    heading_ink_ratio=1.70,   # heading: bold, i.e. ink density above this x median body ink density
    heading_huge_ratio=1.60,  # ... or line taller than this x median body line height
    heading_max_chars=110,
    heading_center_tol=0.15,  # heading must be full-width, or centred within this fraction of page width
    low_conf=60,              # entry flagged if mean OCR confidence is below this
    word_low_conf=70,         # a word read with less confidence than this is highlighted in the web review
    min_text_chars=15,        # entry flagged if Bangla text shorter than this
    struct_head_ink=1.2,      # layout-detected heading (centred, indented both sides) must be this x bolder
    toc_head_ink=1.25,        # a body line must be at least this x bolder (ink) ...
    toc_head_h=1.15,          # ... or this x taller than the text to be tested against the TOC titles
    font="Kalpurush",         # font of the Bangla cells in the output workbooks
)


AR_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")


BN_RE = re.compile(r"[\u0980-\u09FF]")


BN_DIGITS = "০১২৩৪৫৬৭৮৯"


TO_ASCII = str.maketrans(BN_DIGITS, "0123456789")


TO_BN = str.maketrans("0123456789", BN_DIGITS)


# hadith marker at the start of a line: "[১৪৪]"  "[১৭২।"  "২৯৬]" (opener lost)  "و [২০৯]" (Arabic speck in front).
# NOT "(২)" - that is a numbered list inside a hadith.  group(1) = number with opener, group(2) = without.
MARKER_RE = re.compile(r"^[\s\u0600-\u06FF\u200e\u200f]*(?:[\[\{【\|]\s*([০-৯0-9]{1,4})\s*[\]\)\}】\|।]|([০-৯0-9]{1,4})\s*[\]\}】])\s*")


EMBED_RE = re.compile(r"[\[\{【]\s*([০-৯0-9]{1,4})\s*[\]\)\}】\|।]\s*")      # a marker in the MIDDLE of a line


FOOT_START_RE = re.compile(r"^\s*\W{0,3}([০-৯0-9]{1,4})\s*[\.।:]\s*")


CHAPTER_RE = re.compile(r"^\s*অধ্যা(?:\u09df|\u09af\u09bc?)?\s*[:：ঃ.]?\s*")


CHAPTER_GARBLE_RE = re.compile(r"^\W*[0-9]{4,}\W*[:：]")    # the decorative "অধ্যায়" is often read as digits


TAIL_NUM_RE = re.compile(r"[\s.…,·:\-_*]*[০-৯0-9]{1,4}[\s.]*$")


TAIL_DOT_RE = re.compile(r"[\s.…,·:_*]+$")


C_MAX_JUMP = 8             # forward jumps larger than this are misread digits, not missing hadiths
C_MIN_KEEP = 15            # a stray backwards marker shorter than this is junk and is dropped
C_TOC_HEADER = 0.075       # TOC lines above this fraction of the page height are its running header


# OCR misreadings that are not Bangla words at all, so replacing them is safe (bold type confuses ভ/ত, ু/ূ, ন্ন/র)
COMMON_FIXES = [
    (r"সম্[তভ]ান", "সন্তান"), (r"তালো", "ভালো"), (r"অতিশপ্ত", "অভিশপ্ত"), (r"জারাত", "জান্নাত"),
    (r"অমূসলিম", "অমুসলিম"), (r"দূআ", "দুআ"), (r"ঘে ব্যক্তি", "যে ব্যক্তি"), (r"সূরে", "সুরে"),
    (r"খিষ্টান", "খ্রিষ্টান"), (r"স্বর[ীা]প", "স্বরূপ"), (r"মসলমান", "মুসলমান"),
]

# Filled from the book's settings file (bookocr.settings.apply); empty when there is none.
USER_FIXES = []            # (compiled pattern, replacement): corrections for this book's text, applied everywhere
TOC_FIXES = []             # (compiled pattern, replacement): corrections for its table-of-contents titles only
TOC_SKIP = []              # TOC lines containing one of these strings are a running header, not an entry


def fix_pattern(find, regex=False, word=False):
    """A find-and-replace pattern: plain text unless regex=True; word=True matches whole Bangla words only
    (\\b does not work for Bangla: its vowel signs are not word characters to Python's re)."""
    pat = find if regex else re.escape(find)
    if word:
        pat = rf"(?<![\u0980-\u09FF\w])(?:{pat})(?![\u0980-\u09FF\w])"
    return re.compile(pat)


def common_fixes(text):
    for pat, rep_ in COMMON_FIXES:
        text = re.sub(pat, rep_, text)
    for pat, rep_ in USER_FIXES:
        text = pat.sub(rep_, text)
    return text


def bn_int(s):
    try:
        return int(s.translate(TO_ASCII))
    except ValueError:
        return None
