"""Per-book settings: "<PDF name>.settings.json" next to the PDF.

Everything that belongs to one book rather than to the tool lives there, so a new book never needs code changes:

  {
    "title": "...",                  title on the first page of book.docx ("" = from the page headers)
    "pages": "",                     pages to convert ("" = all)
    "toc_pages": "auto",             "auto", "none" or e.g. "17-25"
    "body_start": null,              first PDF page with hadiths (null = after the table of contents)
    "no_docx": false,
    "chapters": ["...", ...],        chapter names, used instead of the OCR'd ones (decorative titles read badly)
    "fixes": [{"find": "...", "replace": "...", "regex": false, "word": false}, ...],
                                     corrections applied to all of this book's text (the web app's find & replace
                                     adds to this list when "remember" is ticked)
    "toc_fixes": [...],              same format, applied to table-of-contents titles only
    "toc_skip": ["..."]              TOC lines containing one of these strings are a running header
  }

Every key is optional. Options given on the command line win over the file.
"""
import json
import os

from . import config

DEFAULTS = dict(title="", pages="", toc_pages="auto", body_start=None, no_docx=False,
                chapters=[], fixes=[], toc_fixes=[], toc_skip=[])


def path_for(pdf):
    return os.path.splitext(pdf)[0] + ".settings.json"


def load(path):
    """The settings in `path` merged over the defaults (just the defaults when the file does not exist)."""
    s = json.loads(json.dumps(DEFAULTS))
    if path and os.path.isfile(path):
        with open(path, encoding="utf8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected a JSON object")
        s.update({k: v for k, v in data.items() if not k.startswith("_")})
    return s


def save(path, s):
    """Write the settings, leaving out keys that are still at their default (keeps the file short to read)."""
    out = {k: v for k, v in s.items() if k not in DEFAULTS or v != DEFAULTS[k]}
    if os.path.isfile(path):                              # keep the file's own notes ("_about": ...)
        with open(path, encoding="utf8") as f:
            out = {k: v for k, v in json.load(f).items() if k.startswith("_")} | out
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def compile_fix(fix):
    """{"find", "replace", "regex", "word"} (or a [pattern, replacement] pair, a regex) -> (pattern, replacement)."""
    if isinstance(fix, (list, tuple)):
        fix = dict(find=fix[0], replace=fix[1], regex=True)
    pat = config.fix_pattern(fix["find"], fix.get("regex", False), fix.get("word", False))
    rep = fix.get("replace", "")
    return pat, (rep if fix.get("regex") else (lambda m, r=rep: r))


def apply(s):
    """Install the book's corrections in bookocr.config, where the text cleaning reads them."""
    config.USER_FIXES[:] = [compile_fix(f) for f in s.get("fixes", [])]
    config.TOC_FIXES[:] = [compile_fix(f) for f in s.get("toc_fixes", [])]
    config.TOC_SKIP[:] = list(s.get("toc_skip", []))


def add_fix(path, find, replace, regex=False, word=False):
    """Remember a correction for the next conversions of this book (no duplicate entries)."""
    s = load(path)
    fix = dict(find=find, replace=replace, regex=bool(regex), word=bool(word))
    if fix not in s["fixes"]:
        s["fixes"].append(fix)
        save(path, s)
