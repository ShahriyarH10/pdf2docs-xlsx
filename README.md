# pdf2xlsx: scanned hadith book (PDF) to Excel workbook and Word file

[![tests](https://github.com/ShahriyarH10/pdf2docs-xlsx/actions/workflows/tests.yml/badge.svg)](https://github.com/ShahriyarH10/pdf2docs-xlsx/actions/workflows/tests.yml)

pdf2xlsx reads a scanned Bangla/Arabic hadith book (a PDF whose pages are images), runs OCR on it, splits the text into numbered hadiths, sorts them into chapters and sections, and writes:

| File | Contents |
|---|---|
| `book.xlsx` | one workbook with three sheets (below) |
| `book.docx` | the book as a Word file: chapter (Heading 1), section (Heading 2), numbered hadiths |
| `review.csv` | the hadiths it is unsure about (low OCR confidence, repaired number, gap in the numbering) |
| `book.json` | everything above as data; the web app's review works on a copy of it |

| Sheet | Columns |
|---|---|
| `chapters` | id, name |
| `sections` | section_id, name |
| `hadiths` | id, hadith (id is the hadith's own number in the book) |

Text is set in the Kalpurush font and justified.

It can be used in two ways:

- **In the browser** (`app.py`): upload a PDF, watch the conversion, then check the doubtful hadiths and words next to the scanned page, correct them, and export. Runs on your own computer only (`localhost`).
- **On the command line** (`pdf2xlsx.py`): one command, PDF in, files out.

## Quick start

| | |
|---|---|
| **macOS** | Double-click `start.command` (or run `./start.command`). |
| **Windows** | Double-click `start.bat`. |
| **Linux** | Run `./start.command`. |
| **Docker** | `docker build -t pdf2xlsx .` then `docker run --rm -p 127.0.0.1:8000:8000 -v "$PWD/books:/books" pdf2xlsx` and open http://localhost:8000. |

The start scripts set everything up on the first run, which takes a few minutes. Later runs open the app straight away.

- **macOS and Linux:** `start.command` installs Tesseract with Homebrew if it is missing (on Linux it gives the `apt` command instead). It also creates a Python environment in `~/pdf2xlsx-venv` with the packages.
- **Windows:** `start.bat` tells you the `winget` command for anything missing. It creates the environment in `venv` inside this folder.

With Docker, nothing else needs installing. Uploaded books and their results are kept in `./books`.

## What is in this repository

```
app.py                    the web app: runs pdf2xlsx.py and adds the review screen
web/                      the web app's page (HTML, CSS, JS; no build step)
pdf2xlsx.py               the command line (a thin wrapper around bookocr)
bookocr/                  the converter, one module per step:
  images.py                 page images out of the PDF, the footnote rule
  ocr.py                    Tesseract, pages in parallel, second look at weak lines, per-word confidence
  layout.py                 what each line is: header, Arabic, heading, chapter, footnote, body, noise
  text.py                   cleaning OCR text
  hadiths.py                cutting the text into numbered hadiths, repairing numbers, sections
  toc.py                    reading the table of contents
  export.py                 book.json, book.xlsx, book.docx, review.csv
  review.py                 the review edits: split, merge, find & replace, sections, spelling check
  settings.py               per-book settings files
  config.py                 thresholds, patterns, generic OCR corrections
  cli.py                    command-line options
tessdata_best/            Tesseract's high-accuracy Bangla and Arabic models
pages.pdf                 a 26-page excerpt of Al-Adab al-Mufrad part 1, for a quick test
pages.settings.json       its settings (chapter names, title corrections)
AAM book.settings.json    the settings of the full book (the PDF itself is not in the repository)
start.command, start.bat  one-click start
Dockerfile                the web app with Tesseract, in a container
tests/                    automated tests (run on every push by GitHub Actions)
```

The full scanned book and the conversion results (`<name> - output` folders, with cached page images and OCR) are not in the repository: they are large, and the results are re-created by running the tool.

## Manual setup

The start scripts do this for you. You need Python 3.10 or newer, the Tesseract OCR program, and the packages in `requirements.txt`. The Bangla and Arabic models are already in `tessdata_best`, so no extra Tesseract language packs are needed.

macOS:

```
brew install python tesseract
git clone https://github.com/ShahriyarH10/pdf2docs-xlsx.git pdf2xlsx
cd pdf2xlsx
python3 -m venv ~/pdf2xlsx-venv
~/pdf2xlsx-venv/bin/pip install -r requirements.txt
```

The environment is created in the home folder on purpose: Python refuses to create one inside a folder whose path contains a colon (`:`).

Windows (PowerShell). Close and reopen PowerShell after the `winget` lines.

```
winget install Python.Python.3.12
winget install UB-Mannheim.TesseractOCR
winget install Git.Git
git clone https://github.com/ShahriyarH10/pdf2docs-xlsx.git pdf2xlsx
cd pdf2xlsx
py -m venv venv
.\venv\Scripts\python -m pip install -r requirements.txt
```

Tesseract is found automatically in `C:\Program Files\Tesseract-OCR`. If it is installed somewhere else, add `--tesseract-cmd "C:\path\to\tesseract.exe"` when running `pdf2xlsx.py`.

## Web app

```
~/pdf2xlsx-venv/bin/python app.py          # macOS / Linux: opens http://localhost:8000
.\venv\Scripts\python app.py              # Windows
```

1. **Upload.** Drop a PDF on the start page. It is saved next to `app.py`, and its results go to `<PDF name> - output`, as on the command line. Books converted earlier on the command line are listed too.
2. **Convert.** Choose the options (page range, table-of-contents pages, chapter names, title) and watch the progress: stage, page count, time left and the log. The options are saved in the book's settings file (below). A conversion can be cancelled; the pages already read are kept, so the next run continues where it stopped. Closing the browser tab does not stop it.
3. **Review.** The flagged hadiths are listed, each next to its scanned page with the line where it starts highlighted. Clicking a line of the scan opens the hadith printed there.
   - **Words to check.** Words that Tesseract read with low confidence are shaded in the text and boxed on the scan. Clicking one selects it in the text and shows it on the page.
   - **Possible misspellings.** These are underlined in red with a suggestion, which you can fix in this hadith, fix in the whole book, or ignore. The **Spelling** filter lists every hadith that has one. How they are found is explained under "How it works".
   - **Editing.** Correct the text (saved as you type) or revert it to the OCR text. Change the hadith number or its section, or start a new section at this hadith. Split two hadiths that were run together (at the cursor), merge one that was cut in two, or delete junk.
   - **Mark reviewed** with Cmd/Ctrl+Enter, which moves on to the next one.

   Hadith numbers are checked live: a gap or a duplicate shows up as soon as it appears. Keyboard: Alt+↑/↓ (or `k`/`j`) previous/next, `/` search, Cmd/Ctrl+Shift+F find & replace, Cmd/Ctrl+Z undo (outside a text field).
4. **Find & replace.** Works across the whole book: plain text, whole words or a regular expression. You see every match first and can untick some. "Also fix it in future conversions" saves the correction in the book's settings file, so the next conversion applies it by itself.
5. **Chapters & sections.** Fix names and the book title, move sections to another chapter, and add or delete chapters and sections. A deleted section's hadiths join the section before it.
6. **Undo.** The undo button at the top takes back splits, merges, deletions, replacements, structure changes and "Discard edits", up to 30 steps.
7. **Export.** Writes `book.xlsx`, `book.docx` and `review.csv` again with your corrections. Hadiths marked reviewed are left out of `review.csv`. Download the files from the Files tab or open the output folder.

**Where the edits are stored.**

- **The review copy:** the review edits a copy, `book.review.json` in the output folder. The OCR result (`book.json`) is never changed, and "Discard edits" on the Files tab goes back to it.
- **Converting again:** this starts a fresh review and keeps the old edits in a dated `book.review.<date>.json`.
- **Books converted by an older version:**
  - A book with no `book.json` shows a "Prepare for review" button. It redoes the hadith splitting from the cached OCR in a few seconds.
  - A book read before word confidence was recorded gets no "OCR unsure" highlights. To add them, use Convert again → Advanced → "Read all pages again".

Options: `--port 8080`, `--no-browser`, `--library <folder>` (where uploaded PDFs and their output folders are kept; default: the folder of `app.py`), `--host` (only for Docker).

## Command line

```
~/pdf2xlsx-venv/bin/python pdf2xlsx.py                    # a file dialog asks for the PDF
~/pdf2xlsx-venv/bin/python pdf2xlsx.py pages.pdf          # or give the PDF directly (26 pages, about 30 seconds)
~/pdf2xlsx-venv/bin/python pdf2xlsx.py "my book.pdf"      # a full book
```

On Windows use `.\venv\Scripts\python` instead of `~/pdf2xlsx-venv/bin/python`.

Useful options (they win over the settings file):

- `--pages 100-109`: process only some pages (a quick test).
- `--rebuild-only`: redo everything except the OCR, in a few seconds (uses the cached pages).
- `--re-ocr`: read the pages again even though their OCR is cached.
- `--toc-pages 17-25` or `--toc-pages none`: override the automatic detection of the table of contents.
- `--settings file.json`: use another settings file than `<PDF name>.settings.json`.
- `--chapters-file names.txt`: chapter names, one per line, used instead of the settings file's.
- `--title "..."`: title on the first page of `book.docx`.
- `--no-docx`: skip the Word file.
- `--debug`: also write page images showing how each line was classified.

## Per-book settings

Everything that belongs to one book rather than to the tool is in `<PDF name>.settings.json` next to the PDF. That way, a new book never needs code changes. Every key is optional:

```json
{
  "title": "আদাবুল মুফরাদ",
  "pages": "",
  "toc_pages": "auto",
  "body_start": null,
  "no_docx": false,
  "chapters": ["পিতা-মাতার সাথে সদ্ব্যবহার", "আত্মীয়তার বন্ধন ঠিক রাখা"],
  "fixes": [{"find": "তালো", "replace": "ভালো", "regex": false, "word": true}],
  "toc_fixes": [{"find": "\\sনে$", "replace": "", "regex": true}],
  "toc_skip": ["আদাবুল"]
}
```

- **`title`:** the title on the first page of `book.docx`. Leave it empty to take it from the page headers.
- **`pages`:** the pages to convert. Leave it empty for all pages.
- **`toc_pages`:** where the table of contents is: `auto`, `none`, or a range such as `17-25`.
- **`body_start`:** the first PDF page with hadiths. `null` means the page after the table of contents.
- **`chapters`:** Tesseract reads decorative chapter titles badly, so they can be given here. When the PDF has a table of contents, the names are used only if their number matches its chapters. Without one, each chapter title found in the text is matched against them.
- **`fixes`:** corrections applied to all of the book's text. Find & replace adds to this list when "Also fix it in future conversions" is ticked.
- **`toc_fixes`:** the same, for table-of-contents titles only.
- **`toc_skip`:** table-of-contents lines containing one of these strings are the running header, not an entry.

The web app's convert dialog edits the same file. See `bookocr/settings.py` for details.

## Notes

- **Where the results go.** A folder named `<PDF name> - output` next to the PDF.
- **How long it takes.** About 10 minutes for a 368-page book on an 8-core laptop. Page images and OCR results are cached in the output folder, so a second run is fast.
- **Font.** Install Kalpurush on the computer that opens the files; without it another Bangla font is substituted. The web app also uses Kalpurush when it is installed (otherwise Noto Sans Bengali, loaded from Google Fonts when online).
- **Missing packages or Tesseract.** The script stops with a message giving the exact install command.

## How it works

1. **Page images.** The original scanned JPEGs are taken out of the PDF untouched.
2. **OCR.** Each page is read with Tesseract (Arabic + Bangla), pages in parallel. The confidence and position of every word are kept for the review.
3. **Second look.** The short last line of each paragraph is read again on its own (a footnote superscript glued to the last word often wrecks it), and strips of text the first pass skipped are found and read.
4. **Line classification.** Every line becomes header, Arabic, heading, chapter, footnote, body or noise, using its position, ink density (bold headings), script, and the printed rule above the footnotes.
5. **Hadith splitting.** The body text of the whole book is joined and cut at the `[number]` markers. The numbers run 1, 2, 3..., so a misread marker is repaired from its neighbours.
6. **Chapters and sections.** Taken from the table of contents when the PDF has one (found automatically; the dotted leader lines are erased before OCR). Otherwise taken from the headings printed in the text.
7. **Output.** `book.json`, then the workbook, the Word file and `review.csv` from it.

The web app runs `pdf2xlsx.py` as a separate process, exactly as on the command line, and reads its progress from the output. Review edits are applied to a copy of `book.json`, and export writes the three files from that copy with the same code.

**The spelling check** works without a dictionary: the book is its own word list. A word that appears at most twice gets a suggestion when another word appears at least 8 times (and 8× as often) and differs from it by one letter or vowel sign in the same position (ছ/হ, ন/স, ু/ূ...). Short words and the last two characters of a word are left out, because Bangla has many real one-letter word pairs there (তার/তারা, করে/করো). On the full book this gives 85 suggestions, and about half are real OCR slips (পর্যস্ত → পর্যন্ত, আনছুমা → আনহুমা, বাসুলুল্লাহ → রাসুলুল্লাহ). The rest are real words, which "Ignore" silences for the book.

## Development

```
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

- **`tests/test_review.py` and `tests/test_app.py`:** the review edits, settings and web API, on a small hand-made book. They take under a second.
- **`tests/test_pages.py`:** converts `pages.pdf` end to end and checks it against the hand-verified result: hadiths 1-46 in order, 23 sections, the chapter name. It needs Tesseract and takes about 30 seconds.

GitHub Actions runs all of them on every push (`.github/workflows/tests.yml`).

## Results

| | `pages.pdf` (26 pages, included) | Al-Adab al-Mufrad part 1 (368 pages) |
|---|---|---|
| Chapters | 1 | 12 |
| Sections | 23 | 257 |
| Hadiths extracted | 46 of 46 | 682 of 685 |
| Hadiths flagged for review | 0 | 44 |

`pages.pdf` has no table of contents, so its chapter and sections come from the headings in the text. Its 23 section names and its hadith numbering were checked by hand against the pages.

## Known limitations

- **OCR errors remain in the text** until they are corrected in the review screen. The word highlights and spelling suggestions point at likely errors; nothing is corrected automatically except the corrections in `bookocr/config.py` and the book's settings file.
- **Three hadiths of the full book are missing** (116, 378, 470): their number markers were unreadable, so their text is merged into a neighbouring hadith. They are flagged, and can be separated with "Split" in the review screen.
- **About 105 of the 682 hadiths do not end with a sentence terminator**, where the OCR lost the final "।" and in some cases the last word.
- **Arabic text and footnotes are not exported.** Arabic OCR on this scan is too unreliable to be useful, so only the Bangla translation is kept. The review screen shows both for reference.
- **Undo history is kept while the app runs.** Restarting `app.py` clears it; the edits themselves are saved.
- **Windows and Docker.** The tool was written and tested on macOS. `start.bat`, the Windows-specific handling (finding Tesseract, console encoding, stopping a conversion) and the Docker image have had less testing.
