"""The review edits (bookocr.review) and the per-book settings (bookocr.settings) on a small hand-made book."""
import json

import pytest

from bookocr import config, review as rv, settings
from bookocr.text import clean_toc_name


def secs(book):
    return [h["section"] for h in book["hadiths"]]


def chaps(book):
    return [h["chapter"] for h in book["hadiths"]]


# ----------------------------------------------------------------------------------------------- hadiths
def test_split_makes_next_number_and_new_uid(book):
    first, second = rv.split(book, "h0", 11)
    assert first["text"] == "আবু হুরাইরা"
    assert second["text"] == "বলেন তার কথা শোনো।"
    assert second["no"] == 2 and second["uid"] == "h6" and second["flags"] == ["manual_split"]
    assert [h["uid"] for h in book["hadiths"]][:3] == ["h0", "h6", "h1"]
    assert book["next_uid"] == 7


def test_split_needs_text_on_both_sides(book):
    with pytest.raises(rv.EditError):
        rv.split(book, "h0", 0)
    with pytest.raises(rv.EditError):
        rv.split(book, "h0", len(book["hadiths"][0]["text"]))


def test_merge_appends_to_previous(book):
    prev = rv.merge(book, "h1")
    assert prev["uid"] == "h0" and "শোনো। ইবনু উমর" in prev["text"]
    assert prev["page_end"] == 2 and len(book["hadiths"]) == 5
    with pytest.raises(rv.EditError):
        rv.merge(book, "h0")


def test_delete_and_missing_uid(book):
    rv.delete(book, "h2")
    assert [h["uid"] for h in book["hadiths"]] == ["h0", "h1", "h3", "h4", "h5"]
    with pytest.raises(rv.EditError):
        rv.delete(book, "h2")


# ----------------------------------------------------------------------------------------------- find & replace
def test_whole_word_bangla(book):
    pat = rv.pattern("তার", word=True)
    matches, total = rv.search(book, pat)
    assert total == 3                                     # "তারা" is a different word
    assert [m["uid"] for m in matches] == ["h0", "h3", "h5"]
    assert rv.search(book, rv.pattern("তার"))[1] == 4      # as plain text it is inside "তারা" too


def test_replace_plain_regex_and_subset(book):
    assert rv.replace(book, rv.pattern("সন্তান"), "সন্তান-সন্ততি") == (2, 2)
    assert "সন্তান-সন্ততি ভালো" in book["hadiths"][2]["text"]
    pat = rv.pattern(r"(\S+) বলেন", regex=True)
    assert rv.replace(book, pat, r"\1 বর্ণনা করেন", regex=True, uids=["h5"]) == (1, 1)
    assert book["hadiths"][5]["text"].startswith("উমর বর্ণনা করেন")
    assert book["hadiths"][4]["text"].startswith("আবু বকর বলেন")
    assert rv.replace(book, rv.pattern("x.y"), "z") == (0, 0)     # plain text: the dot is not a wildcard


def test_bad_regex_is_an_edit_error(book):
    with pytest.raises(rv.EditError):
        rv.pattern("(", regex=True)
    with pytest.raises(rv.EditError):
        rv.pattern("")


# ----------------------------------------------------------------------------------------------- structure
def test_new_section_mid_section(book):
    si = rv.new_section(book, "h1", "নতুন")
    assert si == 1
    assert book["sections"] == ["ক", "নতুন", "খ", "গ"]
    assert book["section_chapter"] == [0, 0, 0, 1]
    assert secs(book) == [0, 1, 2, 2, 3, 3]
    assert chaps(book) == [0, 0, 0, 0, 1, 1]


def test_new_section_where_one_begins_is_refused(book):
    with pytest.raises(rv.EditError):
        rv.new_section(book, "h2", "নতুন")
    with pytest.raises(rv.EditError):
        rv.new_section(book, "h1", "  ")


def test_new_section_takes_its_chapter(book):
    rv.new_section(book, "h5", "শেষ")
    assert secs(book) == [0, 0, 1, 1, 2, 3]
    assert book["section_chapter"] == [0, 0, 1, 1]
    assert chaps(book) == [0, 0, 0, 0, 1, 1]


def test_delete_section_joins_previous_or_next(book):
    rv.delete_section(book, 1)
    assert book["sections"] == ["ক", "গ"] and secs(book) == [0, 0, 0, 0, 1, 1]
    rv.delete_section(book, 0)
    assert book["sections"] == ["গ"] and secs(book) == [0] * 6
    assert chaps(book) == [1] * 6                        # they now sit in section গ, which is in chapter 2
    rv.delete_section(book, 0)
    assert book["sections"] == [] and secs(book) == [-1] * 6


def test_move_section_and_chapters(book):
    rv.move_section(book, 1, 1)
    assert chaps(book) == [0, 0, 1, 1, 1, 1]
    ci = rv.add_chapter(book, 0, "ভূমিকা")
    assert ci == 0 and book["chapters"][0] == "ভূমিকা"
    assert book["section_chapter"] == [1, 2, 2] and chaps(book) == [1, 1, 2, 2, 2, 2]
    rv.delete_chapter(book, 1)                           # its section joins the chapter before (ভূমিকা)
    assert book["chapters"] == ["ভূমিকা", "অধ্যায় দুই"]
    assert book["section_chapter"] == [0, 1, 1] and chaps(book) == [0, 0, 1, 1, 1, 1]
    rv.delete_chapter(book, 0)                           # the first chapter's sections join the next one
    assert book["section_chapter"] == [0, 0, 0] and chaps(book) == [0] * 6
    with pytest.raises(rv.EditError):
        rv.add_chapter(book, 0, "")


def test_rename_keeps_old_name_for_empty_field(book):
    rv.rename(book, title="নতুন বই", chapters=["এক", ""], sections=None)
    assert book["title"] == "নতুন বই" and book["chapters"] == ["এক", "অধ্যায় দুই"]
    with pytest.raises(rv.EditError):
        rv.rename(book, sections=["এক"])


# ----------------------------------------------------------------------------------------------- spelling
def test_spelling_suggests_the_common_twin(book):
    common = "রাসুলুল্লাহ সাল্লাল্লাহু আলাইহি ওয়াসাল্লাম বলেছেন। "
    for h in book["hadiths"][1:]:
        h["text"] = common * 2
    book["hadiths"][0]["text"] = "বাসুলুল্লাহ বলেছেন।"                   # র read as ব: an OCR slip
    sp = rv.spelling(book)
    assert sp == {"h0": [dict(w="বাসুলুল্লাহ", s="রাসুলুল্লাহ", n=10)]}
    rv.ignore_word(book, "বাসুলুল্লাহ")
    assert rv.spelling(book) == {}


def test_spelling_leaves_word_endings_alone(book):
    for h in book["hadiths"][1:]:
        h["text"] = "তোমাদের " * 3
    book["hadiths"][0]["text"] = "তোমাদেরও"          # longer: not a same-length swap
    assert rv.spelling(book) == {}
    book["hadiths"][0]["text"] = "তোমাদেবে"          # change in the last two characters: an ending, not considered
    assert rv.spelling(book) == {}


# ----------------------------------------------------------------------------------------------- settings
def test_settings_roundtrip_keeps_notes(tmp_path):
    p = tmp_path / "x.settings.json"
    p.write_text(json.dumps({"_about": "note", "chapters": ["এক"]}, ensure_ascii=False), encoding="utf8")
    s = settings.load(str(p))
    assert s["chapters"] == ["এক"] and s["toc_pages"] == "auto" and "_about" not in s
    s["title"] = "বই"
    settings.save(str(p), s)
    saved = json.loads(p.read_text(encoding="utf8"))
    assert saved == {"_about": "note", "title": "বই", "chapters": ["এক"]}
    settings.add_fix(str(p), "তালো", "ভালো")
    settings.add_fix(str(p), "তালো", "ভালো")
    assert json.loads(p.read_text(encoding="utf8"))["fixes"] == [
        dict(find="তালো", replace="ভালো", regex=False, word=False)]


def test_settings_apply_fixes_to_text_and_toc():
    try:
        settings.apply(dict(fixes=[dict(find="ফাতেমা", replace="ফাতিমা", word=True)],
                            toc_fixes=[dict(find=r"\sনে$", replace="", regex=True)], toc_skip=["আদাবুল"]))
        assert config.common_fixes("ফাতেমা ও ফাতেমার") == "ফাতিমা ও ফাতেমার"
        assert clean_toc_name("মায়ের সাথে সদাচরণ নে") == "মায়ের সাথে সদাচরণ"
        assert config.TOC_SKIP == ["আদাবুল"]
    finally:
        settings.apply({})
    assert config.common_fixes("ফাতেমা") == "ফাতেমা"


def test_old_style_fix_pairs_are_regexes():
    pat, rep = settings.compile_fix([r"আয় (বৃদ্ধি)", r"আয়ু \1"])
    assert pat.sub(rep, "আয় বৃদ্ধি") == "আয়ু বৃদ্ধি"
