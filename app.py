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

import pdf2xlsx as core

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
        chapters_txt=os.path.isfile(os.path.join(out, "chapters.txt")))


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
            _books.pop(self.project, None)

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
    global job
    out = out_dir(pid)
    pdf = pdf_path(out)
    if not os.path.isfile(pdf):
        raise HTTPException(400, f"The PDF ({os.path.basename(pdf)}) is not in {LIBRARY} any more.")
    cmd = [sys.executable, "-u", os.path.join(HERE, "pdf2xlsx.py"), pdf, "--out", out]
    for key, flag in (("title", "--title"), ("pages", "--pages"), ("body_start", "--body-start"),
                      ("workers", "--workers")):
        v = str(opts.get(key) or "").strip()
        if v:
            cmd += [flag, v]
    toc = str(opts.get("toc_pages") or "auto").strip()
    if toc != "auto":
        cmd += ["--toc-pages", toc]
    for key, flag in (("keep_arabic", "--keep-arabic"), ("no_docx", "--no-docx"), ("debug", "--debug"),
                      ("rebuild_only", "--rebuild-only")):
        if opts.get(key):
            cmd.append(flag)
    chapters = [l.strip() for l in str(opts.get("chapters") or "").splitlines() if l.strip()]
    cf = os.path.join(out, "chapters.txt")
    if chapters:
        with open(cf, "w", encoding="utf8") as f:
            f.write("\n".join(chapters) + "\n")
    elif os.path.exists(cf) and opts.get("chapters") is not None:
        os.remove(cf)                              # the field was emptied: back to the default chapters.txt
    if os.path.exists(cf):
        cmd += ["--chapters-file", cf]
    with lock:
        if job is not None and job.status == "running":
            raise HTTPException(409, f"“{job.project}” is still being converted. Wait for it or cancel it first.")
        job = Job(pid, cmd, out)
        write_json(os.path.join(out, "run_options.json"), opts)
        return job.state()


@app.get("/api/projects/{pid}/options")
def run_options(pid: str):
    out = out_dir(pid)
    opts = read_json(os.path.join(out, "run_options.json"), {})
    cf = os.path.join(out, "chapters.txt")
    if os.path.exists(cf):
        opts["chapters"] = open(cf, encoding="utf8").read()
    return opts


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


def get_book(pid):
    """The reviewed copy (book.review.json) if there is one, else the OCR result (book.json)."""
    with lock:
        if pid in _books:
            return _books[pid]
        out = out_dir(pid)
        book = read_json(os.path.join(out, "book.review.json")) or read_json(os.path.join(out, "book.json"))
        if book is None:
            raise HTTPException(409, "This book has no review data yet. Run the conversion first.")
        _books[pid] = book
        return book


def save_book(pid, book):
    with lock:
        write_json(os.path.join(out_dir(pid), "book.review.json"), book)
        _books[pid] = book


def find(book, uid):
    for i, h in enumerate(book["hadiths"]):
        if h["uid"] == uid:
            return i, h
    raise HTTPException(404, "This hadith no longer exists (it was merged or deleted).")


@app.get("/api/projects/{pid}/book")
def book_data(pid: str):
    return get_book(pid)


@app.patch("/api/projects/{pid}/hadiths/{uid}")
def edit_hadith(pid: str, uid: str, change: dict = Body(...)):
    with lock:
        book = get_book(pid)
        _, h = find(book, uid)
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


@app.post("/api/projects/{pid}/hadiths/{uid}/split")
def split_hadith(pid: str, uid: str, body: dict = Body(...)):
    """Cut a hadith in two at a character offset (two hadiths the OCR ran together)."""
    with lock:
        book = get_book(pid)
        i, h = find(book, uid)
        at = int(body.get("offset", 0))
        head, tail = h["text"][:at].strip(), h["text"][at:].strip()
        if not head or not tail:
            raise HTTPException(400, "Put the cursor inside the text, where the second hadith begins.")
        uid2 = f"h{book.get('next_uid', len(book['hadiths']))}"
        book["next_uid"] = book.get("next_uid", len(book["hadiths"])) + 1
        no = body.get("no", h["no"] + 1 if h["no"] is not None else None)
        new = dict(h, uid=uid2, no=no, text=tail, ocr_text=tail, arabic="", foot="", flags=["manual_split"],
                   anchor=None, reviewed=False, page_start=h["page_end"])
        h["text"] = head
        book["hadiths"].insert(i + 1, new)
        save_book(pid, book)
        return dict(first=h, second=new)


@app.post("/api/projects/{pid}/hadiths/{uid}/merge")
def merge_hadith(pid: str, uid: str):
    """Append a hadith to the one before it (a stray marker split one hadith in two)."""
    with lock:
        book = get_book(pid)
        i, h = find(book, uid)
        if i == 0:
            raise HTTPException(400, "The first hadith has nothing before it to merge into.")
        prev = book["hadiths"][i - 1]
        prev["text"] = (prev["text"] + " " + h["text"]).strip()
        prev["ocr_text"] = (prev["ocr_text"] + " " + h["ocr_text"]).strip()
        prev["page_end"] = max(prev["page_end"], h["page_end"])
        if h["foot"] and not prev["foot"]:
            prev["foot"] = h["foot"]
        del book["hadiths"][i]
        save_book(pid, book)
        return prev


@app.delete("/api/projects/{pid}/hadiths/{uid}")
def delete_hadith(pid: str, uid: str):
    with lock:
        book = get_book(pid)
        i, _ = find(book, uid)
        del book["hadiths"][i]
        save_book(pid, book)
        return dict(ok=True)


@app.put("/api/projects/{pid}/structure")
def edit_structure(pid: str, body: dict = Body(...)):
    """Rename the book, its chapters and sections (the number of each stays as the conversion found it)."""
    with lock:
        book = get_book(pid)
        for key in ("chapters", "sections"):
            if key in body:
                names = [str(t).strip() for t in body[key]]
                if len(names) != len(book[key]):
                    raise HTTPException(400, f"Expected {len(book[key])} {key}.")
                book[key] = names
        if "title" in body and str(body["title"]).strip():
            book["title"] = str(body["title"]).strip()
        save_book(pid, book)
        return book


@app.post("/api/projects/{pid}/export")
def export(pid: str, body: dict = Body(default={})):
    with lock:
        book = get_book(pid)
        out = out_dir(pid)
        try:
            made = core.export_book(book, out, docx=body.get("docx", True))
        except PermissionError:
            raise HTTPException(409, "A file could not be written. Is book.xlsx or book.docx open in Excel or Word? "
                                     "Close it and try again.")
    return dict(files=made, project=project_info(pid))


@app.post("/api/projects/{pid}/discard")
def discard_edits(pid: str):
    with lock:
        p = os.path.join(out_dir(pid), "book.review.json")
        if os.path.exists(p):
            os.remove(p)
        _books.pop(pid, None)
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
    if sys.platform == "darwin":
        subprocess.run(["open", out])
    elif os.name == "nt":
        os.startfile(out)
    else:
        subprocess.run(["xdg-open", out])
    return dict(ok=True)


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")


# =============================================================================== main
def free_port(start):
    for port in range(start, start + 20):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
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
    a = ap.parse_args()
    LIBRARY = os.path.abspath(os.path.expanduser(a.library))
    os.makedirs(LIBRARY, exist_ok=True)
    port = free_port(a.port)
    url = f"http://localhost:{port}"
    print(f"pdf2xlsx is running at {url}   (library: {LIBRARY})\nPress Ctrl+C to stop.", flush=True)
    if not a.no_browser:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    finally:
        if job is not None and job.status == "running":
            job.cancel()


if __name__ == "__main__":
    main()
