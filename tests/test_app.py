"""The web app's API (app.py) on a temporary library holding one converted book."""
import json

import pytest
from fastapi.testclient import TestClient

import app as webapp


@pytest.fixture
def client(tmp_path, book, monkeypatch):
    out = tmp_path / "বই - output"
    out.mkdir()
    (out / "source.json").write_text(json.dumps(dict(name="বই.pdf", size=1)), encoding="utf8")
    (out / "book.json").write_text(json.dumps(book, ensure_ascii=False), encoding="utf8")
    monkeypatch.setattr(webapp, "LIBRARY", str(tmp_path))
    for cache in (webapp._books, webapp._history, webapp._spell):
        cache.clear()
    return TestClient(webapp.app, base_url="http://localhost")


PID = "/api/projects/%E0%A6%AC%E0%A6%87"         # "বই"


def test_only_localhost(client):
    assert client.get("/api/projects", headers={"host": "example.com"}).status_code == 403
    r = client.get("/api/projects")
    assert r.status_code == 200 and r.json()["projects"][0]["hadiths"] == 6


def test_typing_is_saved_but_not_an_undo_step(client, tmp_path):
    r = client.patch(f"{PID}/hadiths/h0", json={"text": "নতুন লেখা ", "reviewed": True})
    assert r.json()["text"] == "নতুন লেখা " and r.json()["reviewed"] is True
    saved = json.loads((tmp_path / "বই - output" / "book.review.json").read_text(encoding="utf8"))
    assert saved["hadiths"][0]["text"] == "নতুন লেখা " and "_v" not in saved
    assert client.post(f"{PID}/undo").status_code == 409


def test_split_merge_delete_and_undo(client):
    r = client.post(f"{PID}/hadiths/h0/split", json={"offset": 11}).json()
    assert r["result"] == "h6" and len(r["book"]["hadiths"]) == 7 and r["undo"] == "Split hadith 1"
    r = client.delete(f"{PID}/hadiths/h3").json()
    assert len(r["book"]["hadiths"]) == 6 and r["undo"] == "Delete hadith 4"
    r = client.post(f"{PID}/undo").json()
    assert r["undone"] == "Delete hadith 4" and len(r["book"]["hadiths"]) == 7 and r["undo"] == "Split hadith 1"
    r = client.post(f"{PID}/undo").json()
    assert [h["uid"] for h in r["book"]["hadiths"]] == [f"h{i}" for i in range(6)] and r["undo"] is None
    assert client.post(f"{PID}/hadiths/h0/merge").status_code == 400
    assert client.post(f"{PID}/hadiths/h99/merge").status_code == 404
    assert client.post(f"{PID}/undo").status_code == 409          # a refused edit leaves no undo step


def test_replace_remembers_the_fix_in_the_settings_file(client, tmp_path):
    r = client.post(f"{PID}/search", json={"find": "তার", "word": True}).json()
    assert r["total"] == 3 and r["hadiths"] == 3
    r = client.post(f"{PID}/replace", json={"find": "তার", "replace": "তাহার", "word": True, "remember": True}).json()
    assert r["result"] == [3, 3]
    assert "তাহার কথা" in r["book"]["hadiths"][0]["text"] and "তারা" in r["book"]["hadiths"][1]["text"]
    s = json.loads((tmp_path / "বই.settings.json").read_text(encoding="utf8"))
    assert s["fixes"] == [dict(find="তার", replace="তাহার", regex=False, word=True)]
    assert client.post(f"{PID}/search", json={"find": "(", "regex": True}).status_code == 400


def test_structure_actions(client):
    r = client.post(f"{PID}/hadiths/h1/new-section", json={"name": "নতুন"}).json()
    assert r["book"]["sections"] == ["ক", "নতুন", "খ", "গ"]
    r = client.post(f"{PID}/structure/move-section", json={"section": 1, "chapter": 1}).json()
    assert r["book"]["section_chapter"] == [0, 1, 0, 1]
    r = client.post(f"{PID}/structure/add-chapter", json={"at": 2, "name": "তিন"}).json()
    assert r["book"]["chapters"] == ["অধ্যায় এক", "অধ্যায় দুই", "তিন"]
    r = client.post(f"{PID}/structure/delete-section", json={"section": 0}).json()
    assert r["book"]["sections"] == ["নতুন", "খ", "গ"]
    r = client.put(f"{PID}/structure", json={"title": "শিরোনাম"}).json()
    assert r["book"]["title"] == "শিরোনাম"
    assert client.post(f"{PID}/structure/nonsense", json={}).status_code == 404
    assert client.post(f"{PID}/structure/delete-chapter", json={}).status_code == 400


def test_spelling_and_ignore(client):
    assert client.get(f"{PID}/spelling").json() == {}
    r = client.post(f"{PID}/spelling/ignore", json={"word": "তারা"}).json()
    assert r["book"]["spell_ignore"] == ["তারা"]


def test_discard_can_be_undone(client, tmp_path):
    client.delete(f"{PID}/hadiths/h0")
    assert (tmp_path / "বই - output" / "book.review.json").exists()
    p = client.post(f"{PID}/discard").json()
    assert p["hadiths"] == 6 and not p["has_edits"]
    r = client.post(f"{PID}/undo").json()
    assert r["undone"] == "Discard edits" and len(r["book"]["hadiths"]) == 5


def test_export_and_download(client):
    r = client.post(f"{PID}/export", json={}).json()
    assert r["files"] == ["book.xlsx", "book.docx", "review.csv"]
    assert client.get(f"{PID}/files/book.xlsx").status_code == 200
    assert client.get(f"{PID}/files/source.json").status_code == 404
