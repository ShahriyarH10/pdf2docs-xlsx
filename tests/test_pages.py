"""End to end: OCR of pages.pdf (26 pages of Al-Adab al-Mufrad, checked by hand) must keep giving the same book.
Needs Tesseract; takes about 30 seconds on 8 cores."""
import json
import os
import shutil
import subprocess
import sys

import pytest

from conftest import ROOT

pytestmark = pytest.mark.skipif(not shutil.which("tesseract"), reason="Tesseract is not installed")


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    out = tmp_path_factory.mktemp("pages") / "out"
    r = subprocess.run([sys.executable, os.path.join(ROOT, "pdf2xlsx.py"), os.path.join(ROOT, "pages.pdf"),
                        "--out", str(out)], capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    return out, json.loads((out / "book.json").read_text(encoding="utf8")), r.stdout


def test_all_46_hadiths_in_order(result):
    _, book, _ = result
    assert [h["no"] for h in book["hadiths"]] == list(range(1, 47))
    assert not any(h["flags"] for h in book["hadiths"])
    assert all(len(h["text"]) >= 40 for h in book["hadiths"])


def test_chapter_and_sections(result):
    _, book, out = result
    assert "Settings: " in out                               # pages.settings.json next to the PDF was used
    assert book["chapters"] == ["পিতা-মাতার সাথে সদ্ব্যবহার"]
    assert len(book["sections"]) == 23
    assert book["sections"][1] == "মায়ের সাথে সদাচরণ"
    assert book["sections"][9].endswith("আয়ু বৃদ্ধি করেন")        # a toc_fixes correction from the settings file
    assert all(h["chapter"] == 0 and 0 <= h["section"] < 23 for h in book["hadiths"])


def test_review_data(result):
    _, book, _ = result
    assert book["word_conf"] is True
    assert all(h["anchor"] for h in book["hadiths"])
    doubts = [d for h in book["hadiths"] for d in h["doubt"]]
    assert doubts and all(d["w"] in h["text"] for h in book["hadiths"] for d in h["doubt"])
    assert all(0 <= v <= 1 for d in doubts for v in d["box"])


def test_files_written(result):
    out, _, _ = result
    for name in ("book.xlsx", "book.docx", "review.csv"):
        assert (out / name).stat().st_size > 0
