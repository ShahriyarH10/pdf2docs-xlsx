#!/usr/bin/env python3
"""
pdf2xlsx.py - scanned Bangla/Arabic book (PDF)  ->  one Excel workbook (chapters / sections / hadiths) + Word file.

  python pdf2xlsx.py                          # a dialog asks for the PDF; results go to "<PDF name> - output"
  python pdf2xlsx.py book.pdf                 # same, PDF given directly (resumes if interrupted)
  python pdf2xlsx.py book.pdf --pages 100-109 --debug   # small test first
  python pdf2xlsx.py book.pdf --rebuild-only  # redo everything except the OCR (seconds), after tuning

Every run writes book.json, book.xlsx (3 sheets), book.docx and review.csv. Book-specific settings (chapter names,
corrections, title) are read from "<PDF name>.settings.json" next to the PDF: see bookocr/settings.py.
The code is in the bookocr package (one module per step); python app.py runs the same thing in the browser.
"""
from bookocr.cli import main

if __name__ == "__main__":
    main()
