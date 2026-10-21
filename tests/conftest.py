import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture
def book():
    """A small book: 2 chapters, 3 sections, 6 hadiths (sections 0,0,1,1,2,2; chapters 0,0,0,0,1,1)."""
    texts = ["আবু হুরাইরা বলেন তার কথা শোনো।", "ইবনু উমর বলেন তারা এসেছিল।", "আনাস বলেন সন্তান ভালো।",
             "আয়েশা বলেন তার ঘরে আলো।", "আবু বকর বলেন সন্তান ও পিতা।", "উমর বলেন তার দান।"]
    secs = [0, 0, 1, 1, 2, 2]
    sec_chapter = [0, 0, 1]
    hadiths = [dict(uid=f"h{i}", no=i + 1, text=t, ocr_text=t, arabic="", foot="", heading="", flags=[], conf=90,
                    page_start=i + 1, page_end=i + 1, anchor=None, section=s, chapter=sec_chapter[s], reviewed=False,
                    doubt=[])
               for i, (t, s) in enumerate(zip(texts, secs))]
    return dict(version=1, title="বই", chapters=["অধ্যায় এক", "অধ্যায় দুই"], sections=["ক", "খ", "গ"],
                section_chapter=sec_chapter, hadiths=hadiths, next_uid=6, word_conf=False)
