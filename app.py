#!/usr/bin/env python3
"""
app.py - pdf2xlsx in the browser: upload a scanned book, watch the OCR run, review and correct the hadiths,
export book.xlsx / book.docx / review.csv.

  python app.py                    # opens http://localhost:8000 in the browser
  python app.py --port 8080 --no-browser
  python app.py --library ~/Books  # where uploaded PDFs and their "<name> - output" folders are kept
                                   # (default: this folder, so earlier command-line results show up too)

The conversion itself is pdf2xlsx.py, run as a separate process (exactly as on the command line), so the
command-line tool keeps working on its own. The review edits a copy of book.json (book.review.json); the
original OCR result is never changed.
"""
import argparse
import asyncio
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.parse import unquote

try:
    from fastapi import Body, FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles
    import uvicorn
except ImportError:
    sys.exit("The web app needs FastAPI and Uvicorn:  pip install -r requirements.txt")

from bookocr import review as rv, settings
from bookocr.export import export_book

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
SUFFIX = " - output"
LIBRARY = HERE                                   # set from --library in main()
DOWNLOADS = ("book.xlsx", "book.docx", "review.csv")

app = FastAPI(title="pdf2xlsx", docs_url=None, redoc_url=None)
lock = threading.RLock()                         # guards book files and the job


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Refuse requests whose Host is not this machine (a web page elsewhere cannot reach the app via DNS tricks)."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    if host not in ("localhost", "127.0.0.1", "::1"):
        return JSONResponse({"detail": "local access only"}, status_code=403)
    return await call_next(request)


# =============================================================================== projects
def out_dir(pid):
    """Output folder of a project; the id is the folder name without " - output". 404 for anything else."""
    pid = unquote(pid)
    if not pid or "/" in pid or "\\" in pid or pid.startswith("."):
        raise HTTPException(404, "no such book")
    path = os.path.join(LIBRARY, pid + SUFFIX)
    if not os.path.isfile(os.path.join(path, "source.json")):
        raise HTTPException(404, "no such book")
    return path


def read_json(path, default=None):
    try:
        with open(path, encoding="utf8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


_page_counts = {}


def pdf_pages(pdf):
    if not os.path.isfile(pdf):
        return None
    key = (pdf, os.path.getmtime(pdf))
    if key not in _page_counts:
        import pymupdf
        _page_counts[key] = len(pymupdf.open(pdf))
    return _page_counts[key]


def pdf_path(out):
    src = read_json(os.path.join(out, "source.json"), {})
    return os.path.join(LIBRARY, src.get("name", ""))


def project_info(pid):
    out = out_dir(pid)
    pdf = pdf_path(out)
    ocr = os.path.join(out, "ocr")
    n_ocr = len([f for f in os.listdir(ocr) if f.endswith(".json")]) if os.path.isdir(ocr) else 0
    has_book = os.path.isfile(os.path.join(out, "book.json"))
    has_edits = os.path.isfile(os.path.join(out, "book.review.json"))
    hs = []
    if has_book or has_edits:
        b = get_book(pid)
        hs = b["hadiths"]
    files = []
    for name in DOWNLOADS:
        p = os.path.join(out, name)
        if os.path.isfile(p):
            files.append(dict(name=name, size=os.path.getsize(p), mtime=os.path.getmtime(p)))
    with lock:
        running = job is not None and job.project == pid and job.status == "running"
        failed = job is not None and job.project == pid and job.status in ("failed", "cancelled")
    if running:
        status = "running"
    elif has_book:
        status = "ready"
    elif files:
        status = "legacy"                      # converted by an older version: no book.json yet
    elif failed:
        status = job.status
    elif n_ocr:
        status = "paused"
    else:
        status = "new"
    edited_at = os.path.getmtime(os.path.join(out, "book.review.json")) if has_edits else None
    exported_at = max((f["mtime"] for f in files), default=None)
    return dict(
        id=pid, name=pid, pdf=os.path.basename(pdf), pdf_found=os.path.isfile(pdf), pages=pdf_pages(pdf),
        pages_read=n_ocr, status=status, hadiths=len(hs),
        flagged=sum(1 for h in hs if h["flags"]), reviewed=sum(1 for h in hs if h.get("reviewed")),
        open_flags=sum(1 for h in hs if h["flags"] and not h.get("reviewed")),
        has_edits=has_edits, unexported=bool(edited_at and (exported_at is None or edited_at > exported_at)),
        files=files, updated=max([os.path.getmtime(out), edited_at or 0, exported_at or 0]),
        settings_file=os.path.basename(settings.path_for(pdf)), undo=undo_label(pid),
        word_conf=bool(hs) and bool((_books.get(pid) or {}).get("word_conf")))


@app.get("/api/projects")
def list_projects():
    out = []
    for d in os.listdir(LIBRARY):
        if d.endswith(SUFFIX) and os.path.isfile(os.path.join(LIBRARY, d, "source.json")):
            try:
                out.append(project_info(d[:-len(SUFFIX)]))
            except Exception as e:                # one broken folder must not hide the others
                out.append(dict(id=d[:-len(SUFFIX)], name=d[:-len(SUFFIX)], status="broken", error=str(e)))
    out.sort(key=lambda p: -p.get("updated", 0))
    return dict(projects=out, library=LIBRARY, cpus=os.cpu_count() or 2)


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return project_info(pid)


@app.post("/api/upload")
async def upload(request: Request):
    """Raw PDF body (no multipart, so the browser can show upload progress); file name in X-Filename."""
    name = os.path.basename(unquote(request.headers.get("x-filename", ""))).strip()
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files can be converted.")
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name[:-4]).strip(" .") or "book"
    tmp = os.path.join(LIBRARY, f".upload-{os.getpid()}-{time.time_ns()}.part")
    size, head = 0, b""
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                if len(head) < 5:
                    head += chunk[:5]
                f.write(chunk)
                size += len(chunk)
        if not head.startswith(b"%PDF"):
            raise HTTPException(400, "This file is not a PDF.")
        with lock:
            i, base = 1, stem
            while True:                           # same name: reuse if it is the same file, else "name (2)"
                pdf = os.path.join(LIBRARY, stem + ".pdf")
                out = os.path.join(LIBRARY, stem + SUFFIX)
                src = read_json(os.path.join(out, "source.json"))
                pdf_ok = not os.path.exists(pdf) or os.path.getsize(pdf) == size
                out_ok = src is None and not os.path.exists(out) or src == dict(name=stem + ".pdf", size=size)
                if pdf_ok and out_ok:
                    break
                i += 1
                stem = f"{base} ({i})"
            if os.path.exists(pdf):
                os.remove(tmp)
            else:
                os.replace(tmp, pdf)
            os.makedirs(out, exist_ok=True)
            write_json(os.path.join(out, "source.json"), dict(name=stem + ".pdf", size=size))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return project_info(stem)


# =============================================================================== conversion job
class Job:
    STAGES = ("prepare", "images", "ocr", "structure", "done")

    def __init__(self, project, cmd, out):
        self.project, self.cmd, self.out = project, cmd, out
        self.id = time.time_ns()
        self.lines, self.warnings = [], []
        self.stage, self.status = "prepare", "running"
        self.ocr_done = self.ocr_total = 0
        self.eta = None
        self.summary = None
        self.started, self.ended = time.time(), None
        self.backup = None
        self.cancelled = False
        kw = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                  cwd=HERE, env=dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8"))
        if os.name == "nt":
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True     # own process group: cancel also stops the OCR workers
        self.proc = subprocess.Popen(cmd, **kw)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for raw in self.proc.stdout:
            line = raw.rstrip("\n")
            with lock:
                self.lines.append(line)
                self._parse(line)
        code = self.proc.wait()
        with lock:
            self.ended = time.time()
            if self.cancelled:
                self.status = "cancelled"
            elif code == 0:
                self.status, self.stage = "done", "done"
                review = os.path.join(self.out, "book.review.json")
                if os.path.exists(review):         # the new OCR result replaces the reviewed copy: keep the old one
                    self.backup = f"book.review.{time.strftime('%Y%m%d-%H%M%S')}.json"
                    os.replace(review, os.path.join(self.out, self.backup))
            else:
                self.status = "failed"
            forget(self.project)
            _history.pop(self.project, None)                # the old snapshots belong to the replaced book

    def _parse(self, line):
        if re.match(r"\d+ pages in PDF, processing", line):
            self.stage = "images"
        elif m := re.match(r"OCR: (\d+) pages cached, (\d+) to do", line):
            self.stage = "ocr" if int(m.group(2)) else "structure"
            self.ocr_total, self.ocr_done = int(m.group(2)), 0
        elif m := re.search(r"page done (\d+)/(\d+)\s+elapsed [\d.]+ min\s+ETA ([\d.]+) min", line):
            self.ocr_done, self.ocr_total, self.eta = int(m.group(1)), int(m.group(2)), float(m.group(3)) * 60
            if self.ocr_done == self.ocr_total:
                self.stage = "structure"
        elif line.startswith(("Table of contents", "No table of contents")):
            self.stage = "structure"
        elif m := re.match(r"Done\. (\d+) chapters, (\d+) sections, (\d+) hadiths \((\d+) need review", line):
            self.summary = dict(zip(("chapters", "sections", "hadiths", "flagged"), map(int, m.groups())))
        elif line.strip().startswith("warning:"):
            self.warnings.append(line.strip()[len("warning:"):].strip())

    def percent(self):
        if self.status == "done":
            return 100
        base = dict(prepare=1, images=4, ocr=8, structure=93, done=100)[self.stage]
        if self.stage == "ocr" and self.ocr_total:
            base += 85 * self.ocr_done / self.ocr_total
        return round(base, 1)

    def cancel(self):
        self.cancelled = True
        if self.proc.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True)
        else:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

            def hard_kill():
                time.sleep(3)
                if self.proc.poll() is None:
                    try:
                        os.killpg(self.proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            threading.Thread(target=hard_kill, daemon=True).start()

    def state(self, since=0):
        return dict(id=self.id, project=self.project, status=self.status, stage=self.stage, percent=self.percent(),
                    ocr_done=self.ocr_done, ocr_total=self.ocr_total, eta=self.eta, summary=self.summary,
                    warnings=self.warnings, backup=self.backup, started=self.started, ended=self.ended,
                    elapsed=(self.ended or time.time()) - self.started,
                    log_from=since, log=self.lines[since:], log_len=len(self.lines))


job = None


@app.post("/api/projects/{pid}/run")
def run(pid: str, opts: dict = Body(default={})):
    """Start pdf2xlsx.py on the book. The book's own settings (title, pages, table of contents, chapter names) are
    saved in its settings file first, so the command line and later runs use them too."""
    global job
    out = out_dir(pid)
    pdf = pdf_path(out)
    if not os.path.isfile(pdf):
        raise HTTPException(400, f"The PDF ({os.path.basename(pdf)}) is not in {LIBRARY} any more.")
    sf = settings.path_for(pdf)
    s = settings.load(sf)
    for key in ("title", "pages", "toc_pages"):
        if key in opts:
            s[key] = str(opts[key] or "").strip() or settings.DEFAULTS[key]
    if "body_start" in opts:
        s["body_start"] = int(opts["body_start"]) if str(opts["body_start"] or "").strip() else None
    if "no_docx" in opts:
        s["no_docx"] = bool(opts["no_docx"])
    if "chapters" in opts:
        s["chapters"] = [l.strip() for l in str(opts["chapters"] or "").splitlines() if l.strip()]
    cmd = [sys.executable, "-u", os.path.join(HERE, "pdf2xlsx.py"), pdf, "--out", out, "--settings", sf]
    v = str(opts.get("workers") or "").strip()
    if v:
        cmd += ["--workers", v]
    for key, flag in (("debug", "--debug"), ("rebuild_only", "--rebuild-only"), ("re_ocr", "--re-ocr")):
        if opts.get(key):
            cmd.append(flag)
    with lock:
        if job is not None and job.status == "running":
            raise HTTPException(409, f"“{job.project}” is still being converted. Wait for it or cancel it first.")
        settings.save(sf, s)
        job = Job(pid, cmd, out)
        return job.state()


@app.get("/api/projects/{pid}/options")
def run_options(pid: str):
    """The book's settings, as the convert dialog shows them."""
    s = settings.load(settings.path_for(pdf_path(out_dir(pid))))
    s["chapters"] = "\n".join(s["chapters"])
    return s


@app.post("/api/job/cancel")
def cancel():
    with lock:
        if job is None or job.status != "running":
            raise HTTPException(409, "Nothing is running.")
        job.cancel()
        return job.state()


@app.get("/api/job")
def job_state(since: int = 0):
    with lock:
        return job.state(since) if job else None


@app.get("/api/job/events")
async def job_events(request: Request, since: int = 0):
    """Server-sent events: the job's state (with new log lines) twice a second until it ends."""
    async def stream():
        cursor, last = since, None
        while not await request.is_disconnected():
            with lock:
                st = job.state(cursor) if job else None
            if st is not None:
                cursor = st["log_len"]
            payload = json.dumps(st, ensure_ascii=False)
            if payload != last:
                yield f"data: {payload}\n\n"
                last = payload
            if st is None or st["status"] != "running":
                break
            await asyncio.sleep(0.5)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# =============================================================================== review
_books = {}
_history = {}                                    # pid -> [(label, book as JSON)]: what Undo restores, newest last
_spell = {}                                      # pid -> (book version, result)
HISTORY = 30


def get_book(pid):
    """The reviewed copy (book.review.json) if there is one, else the OCR result (book.json)."""
    with lock:
        if pid in _books:
            return _books[pid]
        out = out_dir(pid)
        book = read_json(os.path.join(out, "book.review.json")) or read_json(os.path.join(out, "book.json"))
        if book is None:
            raise HTTPException(409, "This book has no review data yet. Run the conversion first.")
        book["_v"] = 0
        _books[pid] = book
        return book


def save_book(pid, book):
    with lock:
        book["_v"] = book.get("_v", 0) + 1
        write_json(os.path.join(out_dir(pid), "book.review.json"), {k: v for k, v in book.items() if k != "_v"})
        _books[pid] = book


def forget(pid):
    """The book on disk was replaced (new conversion, edits discarded): drop what is cached about it."""
    _books.pop(pid, None)
    _spell.pop(pid, None)


def remember(pid, label):
    """Snapshot the book before an edit that Undo can take back."""
    stack = _history.setdefault(pid, [])
    stack.append((label, json.dumps(get_book(pid), ensure_ascii=False)))
    del stack[:-HISTORY]


def undo_label(pid):
    stack = _history.get(pid)
    return stack[-1][0] if stack else None


def edit(pid, label, fn, *args):
    """Run one Undo-able edit (a bookocr.review function) on the book and save it.
    -> {"book", "undo", "result"} - the whole book, since an edit can touch many hadiths."""
    with lock:
        book = get_book(pid)
        remember(pid, label)
        try:
            result = fn(book, *args)
        except rv.EditError as e:
            _history[pid].pop()
            raise HTTPException(400, str(e))
        save_book(pid, book)
        return dict(book=book, undo=undo_label(pid), result=result)


@app.get("/api/projects/{pid}/book")
def book_data(pid: str):
    return dict(book=get_book(pid), undo=undo_label(pid))


@app.patch("/api/projects/{pid}/hadiths/{uid}")
def edit_hadith(pid: str, uid: str, change: dict = Body(...)):
    """Small edits of one hadith (typing, number, section, reviewed): saved at once, not Undo steps."""
    with lock:
        book = get_book(pid)
        try:
            _, h = rv.find(book, uid)
        except rv.EditError as e:
            raise HTTPException(404, str(e))
        if "text" in change:
            h["text"] = str(change["text"])           # as typed; export_book trims it
        if "no" in change:
            no = change["no"]
            h["no"] = int(no) if no not in (None, "") else None
        if "section" in change:
            si = int(change["section"])
            h["section"] = si
            if 0 <= si < len(book["section_chapter"]):
                h["chapter"] = book["section_chapter"][si]
        if "reviewed" in change:
            h["reviewed"] = bool(change["reviewed"])
        save_book(pid, book)
        return h


def _no(pid, uid):
    """The hadith's number, for Undo labels ("Split hadith 12"); 404 if it is gone."""
    try:
        h = rv.find(get_book(pid), uid)[1]
    except rv.EditError as e:
        raise HTTPException(404, str(e))
    return h["no"] if h["no"] is not None else "?"


@app.post("/api/projects/{pid}/hadiths/{uid}/split")
def split_hadith(pid: str, uid: str, body: dict = Body(...)):
    """Cut a hadith in two at a character offset (two hadiths the OCR ran together)."""
    r = edit(pid, f"Split hadith {_no(pid, uid)}", rv.split, uid, int(body.get("offset", 0)), body.get("no"))
    r["result"] = r["result"][1]["uid"]                 # the new hadith
    return r


@app.post("/api/projects/{pid}/hadiths/{uid}/merge")
def merge_hadith(pid: str, uid: str):
    r = edit(pid, f"Merge hadith {_no(pid, uid)}", rv.merge, uid)
    r["result"] = r["result"]["uid"]                    # the hadith it was merged into
    return r


@app.delete("/api/projects/{pid}/hadiths/{uid}")
def delete_hadith(pid: str, uid: str):
    return edit(pid, f"Delete hadith {_no(pid, uid)}", rv.delete, uid)


@app.post("/api/projects/{pid}/hadiths/{uid}/new-section")
def new_section(pid: str, uid: str, body: dict = Body(...)):
    return edit(pid, f"New section at hadith {_no(pid, uid)}", rv.new_section, uid, body.get("name", ""))


@app.post("/api/projects/{pid}/search")
def search(pid: str, body: dict = Body(...)):
    try:
        pat = rv.pattern(body.get("find", ""), body.get("regex"), body.get("word"))
    except rv.EditError as e:
        raise HTTPException(400, str(e))
    matches, total = rv.search(get_book(pid), pat)
    return dict(matches=matches, total=total, hadiths=len(matches))


@app.post("/api/projects/{pid}/replace")
def replace(pid: str, body: dict = Body(...)):
    """Replace in the whole book (or the hadiths in body["uids"]); body["remember"] also saves the correction in the
    book's settings file, so the next conversions of this book apply it by themselves."""
    find_text, rep = body.get("find", ""), body.get("replace", "")
    try:
        pat = rv.pattern(find_text, body.get("regex"), body.get("word"))
    except rv.EditError as e:
        raise HTTPException(400, str(e))
    r = edit(pid, f"Replace “{find_text}”", rv.replace, pat, rep, bool(body.get("regex")), body.get("uids"))
    if body.get("remember"):
        settings.add_fix(settings.path_for(pdf_path(out_dir(pid))), find_text, rep, body.get("regex"), body.get("word"))
    return r


@app.get("/api/projects/{pid}/spelling")
def spelling(pid: str):
    with lock:
        book = get_book(pid)
        cached = _spell.get(pid)
        if not cached or cached[0] != book.get("_v"):
            cached = _spell[pid] = (book.get("_v"), rv.spelling(book))
        return cached[1]


@app.post("/api/projects/{pid}/spelling/ignore")
def ignore_word(pid: str, body: dict = Body(...)):
    return edit(pid, f"Ignore “{body.get('word', '')}”", rv.ignore_word, str(body.get("word", "")))


@app.put("/api/projects/{pid}/structure")
def edit_structure(pid: str, body: dict = Body(...)):
    """Rename the book, its chapters and sections."""
    return edit(pid, "Rename chapters and sections", rv.rename, body.get("title"), body.get("chapters"),
                body.get("sections"))


@app.post("/api/projects/{pid}/structure/{action}")
def structure_action(pid: str, action: str, body: dict = Body(...)):
    """delete-section {section} · move-section {section, chapter} · add-chapter {at, name} · delete-chapter {chapter}"""
    book = get_book(pid)
    try:
        if action == "delete-section":
            si = int(body["section"])
            return edit(pid, f"Delete section {si + 1}", rv.delete_section, si)
        if action == "move-section":
            si, ci = int(body["section"]), int(body["chapter"])
            return edit(pid, f"Move section {si + 1}", rv.move_section, si, ci)
        if action == "add-chapter":
            return edit(pid, "Add chapter", rv.add_chapter, int(body.get("at", len(book["chapters"]))),
                        body.get("name", ""))
        if action == "delete-chapter":
            ci = int(body["chapter"])
            return edit(pid, f"Delete chapter {ci + 1}", rv.delete_chapter, ci)
    except (KeyError, ValueError, TypeError):
        raise HTTPException(400, "Incomplete request.")
    raise HTTPException(404, "Unknown action.")


@app.post("/api/projects/{pid}/undo")
def undo(pid: str):
    with lock:
        stack = _history.get(pid)
        if not stack:
            raise HTTPException(409, "Nothing to undo.")
        label, snap = stack.pop()
        book = json.loads(snap)
        book["_v"] = get_book(pid).get("_v", 0)
        save_book(pid, book)
        return dict(book=book, undo=undo_label(pid), undone=label)


@app.post("/api/projects/{pid}/export")
def export(pid: str, body: dict = Body(default={})):
    with lock:
        book = get_book(pid)
        out = out_dir(pid)
        try:
            made = export_book(book, out, docx=body.get("docx", True))
        except PermissionError:
            raise HTTPException(409, "A file could not be written. Is book.xlsx or book.docx open in Excel or Word? "
                                     "Close it and try again.")
    return dict(files=made, project=project_info(pid))


@app.post("/api/projects/{pid}/discard")
def discard_edits(pid: str):
    """Back to the OCR result. Undo brings the edits back (while the app keeps running)."""
    with lock:
        p = os.path.join(out_dir(pid), "book.review.json")
        if os.path.exists(p):
            remember(pid, "Discard edits")
            os.remove(p)
        forget(pid)
    return project_info(pid)


# =============================================================================== files
@app.get("/api/projects/{pid}/files/{name}")
def download(pid: str, name: str):
    if name not in DOWNLOADS:
        raise HTTPException(404)
    path = os.path.join(out_dir(pid), name)
    if not os.path.isfile(path):
        raise HTTPException(404, "Not exported yet.")
    return FileResponse(path, filename=f"{pid} - {name}")


@app.get("/api/projects/{pid}/pages/{page}")
def page_image(pid: str, page: int):
    img_dir = os.path.join(out_dir(pid), "images")
    for ext in ("jpg", "png", "jpeg", "jp2", "tif", "tiff"):
        p = os.path.join(img_dir, f"page_{page:03d}.{ext}")
        if os.path.isfile(p):
            return FileResponse(p, headers={"Cache-Control": "max-age=3600"})
    raise HTTPException(404, "No image for this page.")


@app.post("/api/projects/{pid}/reveal")
def reveal(pid: str):
    """Show the output folder in Finder / Explorer."""
    out = out_dir(pid)
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", out], check=True)
        elif os.name == "nt":
            os.startfile(out)
        else:
            subprocess.run(["xdg-open", out], check=True)
    except (OSError, subprocess.CalledProcessError):
        raise HTTPException(400, f"Could not open a folder window here. The files are in {out}")
    return dict(ok=True)


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")


# =============================================================================== main
def free_port(host, start):
    for port in range(start, start + 20):
        with socket.socket() as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    sys.exit(f"No free port between {start} and {start + 19}.")


def main():
    global LIBRARY
    ap = argparse.ArgumentParser(description="pdf2xlsx web app (localhost only)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--library", default=HERE, help="folder for uploaded PDFs and their output folders")
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    ap.add_argument("--host", default="127.0.0.1", help="address to listen on (0.0.0.0 inside Docker; the app "
                                                       "still answers only requests addressed to localhost)")
    a = ap.parse_args()
    LIBRARY = os.path.abspath(os.path.expanduser(a.library))
    os.makedirs(LIBRARY, exist_ok=True)
    port = free_port(a.host, a.port)
    url = f"http://localhost:{port}"
    print(f"pdf2xlsx is running at {url}   (library: {LIBRARY})\nPress Ctrl+C to stop.", flush=True)
    if not a.no_browser:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    try:
        uvicorn.run(app, host=a.host, port=port, log_level="warning")
    finally:
        if job is not None and job.status == "running":
            job.cancel()


if __name__ == "__main__":
    main()
