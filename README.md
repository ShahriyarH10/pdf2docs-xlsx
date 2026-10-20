# pdf2xlsx: scanned hadith book (PDF) to Excel workbook and Word file

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

- **In the browser** (`app.py`): upload a PDF, watch the conversion, then check the flagged hadiths next to the scanned page, correct them, and export. Runs on your own computer only (`localhost`).
- **On the command line** (`pdf2xlsx.py`): one command, PDF in, files out.

## What is in this repository

```
app.py              the web app (runs pdf2xlsx.py and adds the review screen)
web/                the web app's page (HTML, CSS, JS; no build step)
pdf2xlsx.py         the converter (command line)
requirements.txt    the Python packages both need
chapters.txt        the 12 chapter names of Al-Adab al-Mufrad part 1 (optional, see "Notes")
tessdata_best/      Tesseract's high-accuracy Bangla and Arabic models
pages.pdf           a 26-page excerpt of the book (chapter 1, hadiths 1-46), for a quick test
```

The full scanned book and the conversion results (`<name> - output` folders, with cached page images and OCR) are not in the repository: they are large, and the results are re-created by running the tool.

## Setup

You need Python 3.10 or newer, the Tesseract OCR program, and the Python packages in `requirements.txt`. The Bangla and Arabic OCR models are already in `tessdata_best`, so no extra Tesseract language packs are needed.

### macOS

```
brew install python tesseract
git clone https://github.com/ShahriyarH10/pdf2docs-xlsx.git pdf2xlsx
cd pdf2xlsx
python3 -m venv ~/pdf2xlsx-venv
~/pdf2xlsx-venv/bin/pip install -r requirements.txt
```

The virtual environment is created in the home folder on purpose: Python refuses to create one inside a folder whose path contains a colon (`:`).

### Windows (PowerShell)

Close and reopen PowerShell after the `winget` lines.

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
~/pdf2xlsx-venv/bin/python app.py          # macOS: opens http://localhost:8000
.\venv\Scripts\python app.py              # Windows
```

1. **Upload.** Drop a PDF on the start page. It is saved next to `app.py`, and its results go to `<PDF name> - output`, as on the command line. Books converted earlier on the command line are listed too.
2. **Convert.** Choose the options (page range, table-of-contents pages, chapter names, title) and watch the progress: stage, page count, time left and the log. A conversion can be cancelled; the pages already read are kept, so the next run continues where it stopped. Closing the browser tab does not stop it.
3. **Review.** The flagged hadiths are listed, each next to its scanned page with the line where it starts highlighted. You can:
   - correct the text (saved as you type), or revert it to the OCR text;
   - change the hadith number or its section;
   - split two hadiths that were run together (at the cursor), merge one that was cut in two, delete junk;
   - mark it reviewed (Cmd/Ctrl+Enter, which moves on to the next one).

   Hadith numbers are checked live: a gap or a duplicate shows up as soon as it appears. Keyboard: Alt+↑/↓ (or `k`/`j`) previous/next, `/` search.
4. **Chapters & sections.** Fix chapter and section names and the book title.
5. **Export.** Writes `book.xlsx`, `book.docx` and `review.csv` again with your corrections; hadiths marked reviewed are left out of `review.csv`. Download them from the Files tab or open the output folder.

The review edits a copy (`book.review.json` in the output folder); the OCR result (`book.json`) is never changed, and "Discard edits" on the Files tab goes back to it. Converting a book again starts a fresh review and keeps the old edits in a dated `book.review.<date>.json`. A book converted with an older version of the tool (no `book.json`) shows a "Prepare for review" button, which redoes the hadith splitting from the cached OCR in a few seconds.

Options: `--port 8080`, `--no-browser`, `--library <folder>` (where uploaded PDFs and their output folders are kept; default: the folder of `app.py`).

## Command line

```
~/pdf2xlsx-venv/bin/python pdf2xlsx.py                    # a file dialog asks for the PDF
~/pdf2xlsx-venv/bin/python pdf2xlsx.py pages.pdf          # or give the PDF directly (26 pages, about 30 seconds)
~/pdf2xlsx-venv/bin/python pdf2xlsx.py "my book.pdf"      # a full book
```

On Windows use `.\venv\Scripts\python` instead of `~/pdf2xlsx-venv/bin/python`.

Useful options:

- `--pages 100-109`: process only some pages (a quick test).
- `--rebuild-only`: redo everything except the OCR, in a few seconds (uses the cached pages).
- `--toc-pages 17-25` or `--toc-pages none`: override the automatic detection of the table of contents.
- `--chapters-file names.txt`: chapter names to use instead of the OCR'd ones.
- `--title "..."`: title on the first page of `book.docx`.
- `--no-docx`: skip the Word file.
- `--debug`: also write page images showing how each line was classified.

## Notes

- **Where the results go.** A folder named `<PDF name> - output` next to the PDF.
- **How long it takes.** About 10 minutes for a 368-page book on an 8-core laptop. Page images and OCR results are cached in the output folder, so a second run is fast.
- **`chapters.txt`.** Tesseract reads this book's decorative chapter titles badly, so its 12 chapter names are supplied in this file, one per line. When the PDF has a table of contents, the names are used only if their number matches its chapters; without one, each chapter title found in the text is matched against them. For another book, edit or remove the file, or type the names in the web app's convert dialog.
- **Font.** Install Kalpurush on the computer that opens the files; without it another Bangla font is substituted. The web app also uses Kalpurush when it is installed (otherwise Noto Sans Bengali, loaded from Google Fonts when online).
- **Missing packages or Tesseract.** The script stops with a message giving the exact install command.

## How it works

1. **Page images.** The original scanned JPEGs are taken out of the PDF untouched.
2. **OCR.** Each page is read with Tesseract (Arabic + Bangla), pages in parallel.
3. **Second look.** The short last line of each paragraph is read again on its own (a footnote superscript glued to the last word often wrecks it), and strips of text the first pass skipped are found and read.
4. **Line classification.** Every line becomes header, Arabic, heading, chapter, footnote, body or noise, using its position, ink density (bold headings), script, and the printed rule above the footnotes.
5. **Hadith splitting.** The body text of the whole book is joined and cut at the `[number]` markers. The numbers run 1, 2, 3..., so a misread marker is repaired from its neighbours.
6. **Chapters and sections.** Taken from the table of contents when the PDF has one (found automatically; the dotted leader lines are erased before OCR). Otherwise taken from the headings printed in the text.
7. **Output.** `book.json`, then the workbook, the Word file and `review.csv` from it.

The web app runs `pdf2xlsx.py` as a separate process, exactly as on the command line, and reads its progress from the output. Review edits are applied to a copy of `book.json`, and export writes the three files from that copy with the same code.

## Results

| | `pages.pdf` (26 pages, included) | Al-Adab al-Mufrad part 1 (368 pages) |
|---|---|---|
| Chapters | 1 | 12 |
| Sections | 23 | 257 |
| Hadiths extracted | 46 of 46 | 682 of 685 |
| Hadiths flagged for review | 0 | 44 |

`pages.pdf` has no table of contents, so its chapter and sections come from the headings in the text. Its 23 section names and its hadith numbering were checked by hand against the pages.

## Known limitations

- **OCR errors remain in the text** until they are corrected in the review screen (or in `book.docx`). Spelling mistakes inside hadiths are not corrected automatically.
- **Three hadiths of the full book are missing** (116, 378, 470): their number markers were unreadable, so their text is merged into a neighbouring hadith. They are flagged, and can be separated with "Split" in the review screen.
- **About 105 of the 682 hadiths do not end with a sentence terminator**, where the OCR lost the final "।" and in some cases the last word.
- **Arabic text and footnotes are not exported.** Arabic OCR on this scan is too unreliable to be useful, so only the Bangla translation is kept. The review screen shows both for reference.
- **Review screen.** Chapters and sections can be renamed but not added or removed. A deleted hadith can only be brought back by discarding all edits.
- **Book-specific corrections.** A short list of misreadings found in this book's section names is corrected explicitly in the script (`COMMON_FIXES`, `TOC_FIXES`). Another book may have misreadings that list does not cover.
- **Windows.** Written and tested on macOS. The Windows-specific handling (finding Tesseract, console encoding, stopping a conversion) has had less testing.
