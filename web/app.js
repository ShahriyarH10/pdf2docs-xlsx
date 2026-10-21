/* pdf2xlsx web app: library, conversion progress, review editor, structure, files. No build step. */
"use strict";

// ------------------------------------------------------------------ helpers
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = (name, cls = "") => `<svg class="${cls}"><use href="#i-${name}"/></svg>`;
const fmt = (n) => (n ?? 0).toLocaleString("en");
const enc = encodeURIComponent;
const BN = "০১২৩৪৫৬৭৮৯";
const fromBn = (s) => String(s).replace(/[০-৯]/g, (d) => BN.indexOf(d));
const MAC = /Mac|iPhone|iPad/.test(navigator.platform);
const BN_CHAR = /[ঀ-৿\w]/;

function plural(n, one, many = one + "s") { return `${fmt(n)} ${n === 1 ? one : many}`; }
function duration(sec) {
  if (sec == null || !isFinite(sec)) return "";
  sec = Math.round(sec);
  if (sec < 60) return `${sec} s`;
  const m = Math.floor(sec / 60), s = sec % 60;
  if (m < 60) return `${m} min${s && m < 10 ? ` ${s} s` : ""}`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}
function ago(t) {
  const d = Date.now() / 1000 - t;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}
function size(b) {
  if (b < 1024) return `${b} B`;
  if (b < 1048576) return `${(b / 1024).toFixed(0)} KB`;
  return `${(b / 1048576).toFixed(1)} MB`;
}
function debounce(fn, ms) {
  let t;
  const d = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  d.cancel = () => clearTimeout(t);
  return d;
}

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const r = await fetch("/api" + path, init);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* not JSON */ }
    throw new Error(typeof msg === "string" ? msg : "The request was not understood.");
  }
  return r.json();
}
const papi = (path, opts) => api(`/projects/${enc(S.pid)}${path}`, opts);

/** toast(msg, kind, action): action = {label, fn} adds a button (e.g. Undo). */
function toast(msg, kind = "", action = null) {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = `${icon(kind === "err" ? "alert" : "check")}<span>${esc(msg)}</span>`;
  if (action) {
    const b = document.createElement("button");
    b.className = "toast-act";
    b.textContent = action.label;
    b.onclick = () => { el.remove(); action.fn(); };
    el.append(b);
  }
  const box = $("#toasts");
  $$(".toast:not(.err)", box).forEach((t) => t.remove());     // the newest message replaces the last one
  box.append(el);
  const life = kind === "err" ? 6000 : action ? 6000 : 3200;
  setTimeout(() => { el.style.transition = "opacity .3s"; el.style.opacity = "0"; }, life);
  setTimeout(() => el.remove(), life + 400);
}
const fail = (e) => toast(e.message || String(e), "err");

// ------------------------------------------------------------------ theme
function currentTheme() {
  return document.documentElement.dataset.theme ||
    (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}
document.documentElement.dataset.theme = currentTheme();
$("#themeBtn").onclick = () => {
  const t = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("theme", t); } catch (e) { /* private mode */ }
};

// ------------------------------------------------------------------ state
const S = {
  projects: [], library: "", cpus: 4,
  job: null, jobLog: [], es: null,
  pid: null, project: null, tab: "review",
  book: null, undo: null, spell: {},
  filter: "todo", query: "", current: null, page: null, zoom: 1, focusWord: null,
};

// ------------------------------------------------------------------ flags
const FLAG_INFO = {
  low_conf: (h) => [`Low OCR confidence (${Math.round(h.conf)}%)`, "Tesseract was unsure about many words. Compare the text with the page carefully."],
  short_text: () => ["Very short text", "This may be a fragment that belongs to the hadith before or after it."],
  no_arabic: () => ["No Arabic text found before it", "Usually harmless. Check that the hadith starts in the right place."],
  no_repaired: (h, arg) => ["Number was corrected", `The printed number was read as “${arg}” and corrected from its neighbours. Check the number against the page.`],
  seq_gap: (h, arg) => ["Number skips", `${arg ? arg[0].toUpperCase() + arg.slice(1) : "Another number was expected"}. A hadith may be missing, or merged into the one before.`],
  merged_marker: (h, arg) => ["Stray marker merged in", `A marker read as “${arg}” was found inside this text and joined to it. Split it if it is really a new hadith.`],
  split_marker: () => ["Started in the middle of a line", "It was cut off the hadith before it. Check where it begins."],
  no_marker: () => ["No [number] marker found", "Set the hadith number by hand."],
  manual_split: () => ["Created by splitting", "Check its number and where it begins."],
};
function describeFlag(h, f) {
  const m = /^(\w+)(?:\((.*)\))?$/.exec(f);
  const [key, arg] = m ? [m[1], (m[2] || "").replace(/^read /, "")] : [f, ""];
  const fn = FLAG_INFO[key];
  return fn ? fn(h, arg) : [key.replace(/_/g, " "), ""];
}

/** Live number check over the whole (edited) book: uid -> problem text. */
function sequenceIssues(hs) {
  const out = {}, seen = {};
  let prev = null;
  hs.forEach((h) => {
    if (h.no == null) out[h.uid] = "No number";
    else {
      if (seen[h.no]) out[h.uid] = `Number ${h.no} is used twice`;
      else if (prev != null && h.no !== prev + 1) {
        out[h.uid] = h.no <= prev ? `Comes after ${prev} but is numbered ${h.no}` : `Jumps from ${prev} to ${h.no}`;
      }
      seen[h.no] = true;
      prev = h.no;
    }
  });
  return out;
}

// ------------------------------------------------------------------ job (server-sent events)
function connectJob(since = 0) {
  if (S.es) S.es.close();
  const es = new EventSource(`/api/job/events?since=${since}`);
  S.es = es;
  es.onmessage = (ev) => {
    const st = JSON.parse(ev.data);
    if (!st) return;
    const fresh = !S.job || S.job.id !== st.id;
    if (fresh) S.jobLog = [];
    if (st.log_from === S.jobLog.length) S.jobLog.push(...st.log);
    const wasRunning = S.job && S.job.status === "running" && !fresh;
    S.job = st;
    onJobUpdate();
    if (st.status !== "running") {
      es.close();
      S.es = null;
      if (wasRunning) onJobEnd(st);
    }
  };
  es.onerror = () => {
    es.close();
    S.es = null;
    if (S.job && S.job.status === "running") setTimeout(() => connectJob(S.jobLog.length), 1500);
  };
}

function onJobUpdate() {
  const j = S.job, pill = $("#jobPill");
  if (j && j.status === "running") {
    pill.hidden = false;
    pill.href = `#/p/${enc(j.project)}`;
    pill.innerHTML = `<span class="spinner"></span><span>${esc(j.project)} · ${Math.floor(j.percent)}%</span>`;
  } else pill.hidden = true;
  if (S.pid && j && j.project === S.pid && $("#progress")) updateProgress();
  const card = j && $(`.book-card[data-id="${CSS.escape(j.project)}"] .pill`);
  if (card && j.status === "running") card.textContent = `Converting · ${Math.floor(j.percent)}%`;
}

async function onJobEnd(st) {
  if (st.status === "done") {
    const s = st.summary;
    toast(s ? `“${st.project}” converted: ${fmt(s.hadiths)} hadiths, ${fmt(s.flagged)} to review` : `“${st.project}” converted`);
    if (st.backup) toast(`Your earlier review edits were kept in ${st.backup}`);
  } else if (st.status === "failed") toast(`Converting “${st.project}” failed. See the log.`, "err");
  if (S.pid === st.project) {
    S.book = null;
    await loadProject(S.pid);
  }
  render();
}

// ------------------------------------------------------------------ router
function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  if (parts[0] === "p" && parts[1]) return { pid: parts[1], tab: parts[2] || "review" };
  return { pid: null };
}

async function route() {
  await flushSave();
  const r = parseRoute();
  if (r.pid !== S.pid) {
    Object.assign(S, { pid: r.pid, project: null, book: null, undo: null, spell: {}, current: null, filter: "todo", query: "" });
    if (r.pid) {
      try { await loadProject(r.pid); } catch (e) { fail(e); location.hash = "#/"; return; }
    }
  }
  S.tab = r.tab || "review";
  if (!S.pid) await loadLibrary();
  render();
}

async function loadLibrary() {
  try {
    const d = await api("/projects");
    Object.assign(S, { projects: d.projects, library: d.library, cpus: d.cpus });
  } catch (e) { fail(e); }
}

async function loadProject(pid) {
  S.project = await api(`/projects/${enc(pid)}`);
  if (S.project.status === "ready" || S.project.has_edits) {
    const r = await api(`/projects/${enc(pid)}/book`);
    S.book = r.book;
    S.undo = r.undo;
    loadSpelling();
  }
}

async function loadSpelling() {
  if (!S.book) return;
  try {
    S.spell = await papi("/spelling");
  } catch (e) { S.spell = {}; }
  if (S.tab === "review" && $("#items")) { renderList(); refreshWords(); }
}
const reloadSpelling = debounce(loadSpelling, 1500);

/** An edit came back from the server with the whole book: take it and redraw. */
function applyEdit(r, { current, msg } = {}) {
  S.book = r.book;
  S.undo = r.undo;
  S.project.unexported = true;
  S.project.has_edits = true;
  if (current) S.current = current;
  if (!S.book.hadiths.some((h) => h.uid === S.current)) S.current = (S.book.hadiths[0] || {}).uid;
  S.page = null;
  S.focusWord = null;
  updateUndoButton();
  if (msg) toast(msg, "", S.undo ? { label: "Undo", fn: undo } : null);
  loadSpelling();
}

async function undo() {
  if (!S.undo) return;
  await flushSave();
  try {
    const r = await papi("/undo", { method: "POST" });
    S.book = r.book;
    S.undo = r.undo;
    S.project.unexported = true;
    if (!S.book.hadiths.some((h) => h.uid === S.current)) S.current = (S.book.hadiths[0] || {}).uid;
    S.page = null;
    toast(`Undone: ${r.undone}`);
    S.project = await papi("");
    renderProject();
    loadSpelling();
  } catch (e) { fail(e); }
}

function updateUndoButton() {
  const b = $("#undoBtn");
  if (!b) return;
  b.disabled = !S.undo;
  b.title = S.undo ? `Undo: ${S.undo} (${MAC ? "⌘" : "Ctrl+"}Z)` : "Nothing to undo";
}

function render() {
  const crumbs = $("#crumbs");
  crumbs.innerHTML = S.pid ? `${icon("right")}<span>${esc(S.pid)}</span>` : "";
  if (!S.pid) return renderLibrary();
  renderProject();
}

// ------------------------------------------------------------------ library
function renderLibrary() {
  const v = $("#view");
  v.innerHTML = `
    <div class="page">
      <section class="hero">
        <div>
          <h1>Scanned hadith books to <span class="accent">Excel</span> and <span class="accent">Word</span></h1>
          <p>Drop a scanned Bangla/Arabic PDF. Every page is read with OCR, split into numbered hadiths and sorted into
             chapters and sections. Then you check the doubtful ones next to the printed page.</p>
          <div class="steps"><div><b>1</b>Upload</div><div><b>2</b>Convert</div><div><b>3</b>Review</div><div><b>4</b>Export</div></div>
        </div>
        <label class="dropzone" id="drop">
          <input type="file" accept="application/pdf,.pdf" id="fileIn">
          <div class="dz-idle">
            <div class="dz-icon" style="margin:0 auto 12px">${icon("upload")}</div>
            <strong>Drop a scanned PDF here</strong>
            <div class="muted">or click to choose a file</div>
          </div>
          <div class="dz-busy dz-progress" hidden>
            <strong id="upName"></strong>
            <div class="bar" style="margin:14px 0 8px"><div id="upBar" style="width:0"></div></div>
            <div class="muted small" id="upText">Uploading…</div>
          </div>
        </label>
      </section>
      <section>
        <div class="section-head"><h2>Books</h2><span class="muted" title="${esc(S.library)}">${esc(S.library)}</span></div>
        ${S.projects.length ? `<div class="cards">${S.projects.map(bookCard).join("")}</div>`
          : `<div class="card empty-lib">No books yet. Drop a PDF above to start.</div>`}
      </section>
    </div>`;
  const drop = $("#drop"), input = $("#fileIn");
  input.onchange = () => input.files[0] && upload(input.files[0]);
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) upload(f); });
}

const STATUS = {
  new: "Not converted", paused: "Paused", running: "Converting", ready: "Ready", legacy: "Older result",
  failed: "Failed", cancelled: "Cancelled", broken: "Unreadable",
};

function statusLabel(p) {
  if (p.status === "running" && S.job && S.job.project === p.id) return `Converting · ${Math.floor(S.job.percent)}%`;
  if (p.status === "paused" && p.pages) return `Paused · ${fmt(p.pages_read)}/${fmt(p.pages)} pages`;
  return STATUS[p.status] || p.status;
}

function bookCard(p) {
  const meta = [p.pages ? plural(p.pages, "page") : null, p.hadiths ? plural(p.hadiths, "hadith") : null].filter(Boolean).join(" · ");
  let foot = "";
  if (p.hadiths) {
    const done = p.flagged - p.open_flags, pct = p.flagged ? (100 * done) / p.flagged : 100;
    foot = `<div class="bar ${p.open_flags ? "" : "ok"}"><div style="width:${pct}%"></div></div>
      <div class="meta small">${p.flagged ? (p.open_flags ? `${fmt(done)} of ${plural(p.flagged, "flagged hadith")} reviewed` : "All flagged hadiths reviewed") : "Nothing flagged"}${p.unexported ? " · edits not exported" : ""}</div>`;
  } else if (!p.pdf_found && p.status !== "broken") foot = `<div class="meta small">PDF not found next to this folder</div>`;
  return `<a class="card book-card" href="#/p/${enc(p.id)}" data-id="${esc(p.id)}">
    <div class="top"><div class="book-icon">${icon("book")}</div><span class="pill ${p.status}">${esc(statusLabel(p))}</span></div>
    <h3>${esc(p.name)}</h3>
    <div class="meta">${esc(meta || p.pdf || "")}</div>
    ${p.updated ? `<div class="meta small">Updated ${ago(p.updated)}</div>` : ""}
    <div class="foot">${foot}</div>
  </a>`;
}

function upload(file) {
  if (!/\.pdf$/i.test(file.name)) return toast("Only PDF files can be converted.", "err");
  $(".dz-idle").hidden = true;
  $(".dz-busy").hidden = false;
  $("#upName").textContent = file.name;
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload");
  xhr.setRequestHeader("X-Filename", encodeURIComponent(file.name));
  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    $("#upBar").style.width = (100 * e.loaded) / e.total + "%";
    $("#upText").textContent = `${size(e.loaded)} of ${size(e.total)}`;
  };
  xhr.onload = () => {
    let d = {};
    try { d = JSON.parse(xhr.responseText); } catch (e) { /* ignore */ }
    if (xhr.status >= 300) {
      toast(d.detail || "Upload failed", "err");
      return renderLibrary();
    }
    if (d.status === "new") S.autoRun = d.id;          // renderStart opens the settings once the page is up
    location.hash = `#/p/${enc(d.id)}`;
  };
  xhr.onerror = () => { toast("Upload failed", "err"); renderLibrary(); };
  xhr.send(file);
}

// ------------------------------------------------------------------ project shell
function renderProject() {
  const p = S.project;
  if (!p) return;
  const running = S.job && S.job.status === "running" && S.job.project === p.id;
  const hasBook = !!S.book && !running;
  const todo = hasBook ? todoCount() : 0;
  const meta = [p.pdf, p.pages && plural(p.pages, "page"), S.book && plural(S.book.hadiths.length, "hadith")].filter(Boolean).join(" · ");
  const tabs = hasBook ? `<nav class="tabs">
      <a href="#/p/${enc(p.id)}" class="${S.tab === "review" ? "active" : ""}">Review <span class="count ${todo ? "" : "zero"}" id="todoCount">${todo || "✓"}</span></a>
      <a href="#/p/${enc(p.id)}/structure" class="${S.tab === "structure" ? "active" : ""}">Chapters &amp; sections</a>
      <a href="#/p/${enc(p.id)}/files" class="${S.tab === "files" ? "active" : ""}">Files${p.unexported ? ' <span class="count">!</span>' : ""}</a>
    </nav>` : "";
  $("#view").innerHTML = `
    <div class="proj">
      <div class="proj-head">
        <div class="proj-title">
          <h1>${esc(p.name)} <span class="pill ${running ? "running" : p.status}">${esc(running ? "Converting" : STATUS[p.status] || p.status)}</span></h1>
          <div class="meta">${esc(meta)}</div>
        </div>
        <div class="proj-actions">
          ${hasBook ? `<button class="btn icon-only" id="undoBtn" data-act="undo">${icon("undo")}<span class="sr">Undo</span></button>
                       <button class="btn" data-act="find" title="Find & replace in the whole book (${MAC ? "⌘" : "Ctrl+"}⇧F)">${icon("search")}<span class="hide-sm">Find &amp; replace</span></button>
                       <button class="btn" data-act="rerun">${icon("refresh")}<span class="hide-sm">Convert again</span></button>
                       <button class="btn primary" data-act="export">${icon("download")}Export files</button>` : ""}
        </div>
        ${tabs}
      </div>
      <div class="tab-body" id="tabBody"></div>
    </div>`;
  updateUndoButton();
  $(".proj-actions").onclick = (e) => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const act = b.dataset.act;
    if (act === "rerun") openRunDialog();
    if (act === "export") exportFiles();
    if (act === "undo") undo();
    if (act === "find") openFind();
  };
  const body = $("#tabBody");
  if (running) return renderProgress(body);
  if (!hasBook) return renderStart(body);
  if (S.tab === "structure") return renderStructure(body);
  if (S.tab === "files") return renderFiles(body);
  renderReview(body);
}

function renderStart(body) {
  const p = S.project, j = S.job && S.job.project === p.id ? S.job : null;
  let html;
  if (!p.pdf_found) {
    html = `<div class="card state-card bad"><div class="big-icon">${icon("alert")}</div><h2>The PDF is missing</h2>
      <p>${esc(p.pdf)} should be in ${esc(S.library)}, next to this book's output folder. Put it back (or drop it on the start page) to convert.</p></div>`;
  } else if (p.status === "legacy") {
    html = `<div class="card state-card"><div class="big-icon">${icon("refresh")}</div><h2>Prepare this book for review</h2>
      <p>It was converted with an older version of the tool. Preparing it reuses the pages already read, so it only takes a few seconds.</p>
      <button class="btn primary lg" data-go="legacy">${icon("play")}Prepare for review</button></div>`;
  } else if (j && (j.status === "failed" || j.status === "cancelled")) {
    const failed = j.status === "failed";
    html = `<div class="card state-card ${failed ? "bad" : ""}"><div class="big-icon">${icon(failed ? "alert" : "info")}</div>
      <h2>${failed ? "The conversion stopped with an error" : "Conversion cancelled"}</h2>
      <p>${failed ? "The last lines of the log are below." : `Pages already read are kept${p.pages ? ` (${fmt(p.pages_read)} of ${fmt(p.pages)})` : ""}, so starting again continues where it stopped.`}</p>
      ${failed ? `<div class="log-tail">${esc(S.jobLog.slice(-14).join("\n"))}</div>` : ""}
      <button class="btn primary lg" data-go="run">${icon("play")}${failed ? "Try again" : "Continue"}</button></div>`;
  } else {
    const paused = p.status === "paused";
    html = `<div class="card state-card"><div class="big-icon">${icon(paused ? "play" : "book")}</div>
      <h2>${paused ? "Conversion paused" : "Ready to convert"}</h2>
      <p>${paused ? `${fmt(p.pages_read)} of ${fmt(p.pages)} pages have been read. Continuing picks up from there.`
        : `${p.pages ? plural(p.pages, "page") + ". " : ""}OCR takes roughly 1.5 seconds per page on an 8-core computer, and you can watch it here.`}</p>
      <button class="btn primary lg" data-go="run">${icon("play")}${paused ? "Continue" : "Start conversion"}</button></div>`;
  }
  body.innerHTML = `<div class="center-state">${html}</div>`;
  body.onclick = (e) => {
    const b = e.target.closest("[data-go]");
    if (!b) return;
    if (b.dataset.go === "legacy") startRun({ rebuild_only: true });
    else openRunDialog();
  };
  if (S.autoRun === p.id && p.pdf_found) {
    S.autoRun = null;
    openRunDialog();
  }
}

// ------------------------------------------------------------------ run dialog + progress
const PAGES_RE = /^\s*\d+(\s*-\s*\d+)?(\s*,\s*\d+(\s*-\s*\d+)?)*\s*$/;

async function openRunDialog() {
  const p = S.project;
  let prev = {};
  try { prev = await papi("/options"); } catch (e) { /* first run */ }
  const toc = prev.toc_pages || "auto";
  const tocMode = toc === "auto" || toc === "none" ? toc : "pages";
  const m = modal(`
    <header><h2>Convert “${esc(p.name)}”</h2>
      <p>${p.pages ? plural(p.pages, "page") + ". " : ""}Pages that were already read are reused, so a second run is fast.
      These settings are saved in <code>${esc(p.settings_file)}</code> next to the PDF.</p></header>
    <form id="runForm">
      ${p.has_edits ? `<div class="note warn">${icon("alert")}<span>This book has review edits. Converting again starts the review from the new result; your edits are kept in a backup file in the output folder.</span></div>` : ""}
      <label class="field"><span>Title <em>(first page of the Word file)</em></span>
        <input class="input bn" name="title" value="${esc(prev.title || "")}" placeholder="Taken from the page headers"></label>
      <div class="row2">
        <label class="field"><span>Pages</span>
          <input class="input" name="pages" value="${esc(prev.pages || "")}" placeholder="All, or e.g. 1-30">
          <span class="hint">Try a few pages first on a new book.</span></label>
        <div class="field"><span>Table of contents</span>
          <select class="input" name="toc_mode">
            <option value="auto" ${tocMode === "auto" ? "selected" : ""}>Find automatically</option>
            <option value="none" ${tocMode === "none" ? "selected" : ""}>This PDF has none</option>
            <option value="pages" ${tocMode === "pages" ? "selected" : ""}>On these pages…</option>
          </select>
          <input class="input" name="toc_range" placeholder="e.g. 17-25" value="${tocMode === "pages" ? esc(toc) : ""}" ${tocMode === "pages" ? "" : "hidden"}>
        </div>
      </div>
      <label class="field"><span>Chapter names <em>(optional, one per line)</em></span>
        <textarea class="input bn" name="chapters" rows="5" placeholder="The decorative chapter titles are often misread. Names typed here are used instead.">${esc(prev.chapters || "")}</textarea></label>
      ${(prev.fixes || []).length ? `<p class="hint">${plural(prev.fixes.length, "remembered correction")} from find &amp; replace will be applied.</p>` : ""}
      <details class="advanced"><summary>Advanced options</summary><div class="inner">
        <div class="row2">
          <label class="field"><span>First page with hadiths</span><input class="input" name="body_start" type="number" min="1" value="${esc(prev.body_start || "")}" placeholder="After the contents"></label>
          <label class="field"><span>Parallel OCR workers</span><input class="input" name="workers" type="number" min="1" max="64" placeholder="${S.cpus} (all cores)"></label>
        </div>
        ${p.pages_read ? `<label class="check"><input type="checkbox" name="rebuild_only"><span><b>Skip OCR</b><small>Reuse the pages already read and only redo the hadith splitting (seconds).</small></span></label>
        <label class="check"><input type="checkbox" name="re_ocr"><span><b>Read all pages again</b><small>Needed once for books read by an older version, to get the word highlights in the review. Takes as long as the first time.</small></span></label>` : ""}
        <label class="check"><input type="checkbox" name="no_docx" ${prev.no_docx ? "checked" : ""}><span><b>Skip the Word file</b></span></label>
        <label class="check"><input type="checkbox" name="debug"><span><b>Debug images</b><small>Page images showing how each line was classified, in the output folder.</small></span></label>
      </div></details>
      <div class="modal-actions">
        <button type="button" class="btn ghost" data-close>Cancel</button>
        <button type="submit" class="btn primary">${icon("play")}Start</button>
      </div>
    </form>`);
  const f = $("#runForm", m);
  f.toc_mode.onchange = () => { f.toc_range.hidden = f.toc_mode.value !== "pages"; if (!f.toc_range.hidden) f.toc_range.focus(); };
  if (f.rebuild_only) {
    f.rebuild_only.onchange = () => { if (f.rebuild_only.checked) f.re_ocr.checked = false; };
    f.re_ocr.onchange = () => { if (f.re_ocr.checked) f.rebuild_only.checked = false; };
  }
  f.onsubmit = (e) => {
    e.preventDefault();
    const tm = f.toc_mode.value;
    if (tm === "pages" && !PAGES_RE.test(f.toc_range.value)) return toast("Give the table-of-contents pages like 17-25", "err");
    if (f.pages.value.trim() && !PAGES_RE.test(f.pages.value)) return toast("Give the pages like 1-30 or 1,5,9-12", "err");
    const opts = {
      title: f.title.value.trim(), pages: f.pages.value.replace(/\s/g, ""),
      toc_pages: tm === "pages" ? f.toc_range.value.replace(/\s/g, "") : tm,
      chapters: f.chapters.value, body_start: f.body_start.value, workers: f.workers.value,
      no_docx: f.no_docx.checked, debug: f.debug.checked,
      rebuild_only: !!(f.rebuild_only && f.rebuild_only.checked), re_ocr: !!(f.re_ocr && f.re_ocr.checked),
    };
    closeModal();
    startRun(opts);
  };
}

async function startRun(opts) {
  await flushSave();
  try {
    const st = await papi("/run", { method: "POST", body: opts });
    S.job = st;
    S.jobLog = [];
    connectJob(0);
    if (S.tab !== "review") location.hash = `#/p/${enc(S.project.id)}`;
    render();
  } catch (e) { fail(e); }
}

const STAGES = [["prepare", "Preparing"], ["images", "Page images"], ["ocr", "Reading pages"], ["structure", "Hadiths & chapters"], ["done", "Files"]];

function renderProgress(body) {
  body.innerHTML = `<div class="center-state" style="max-width:780px"><div class="card progress-card" id="progress">
      <div class="ring"><svg viewBox="0 0 132 132"><circle class="track" cx="66" cy="66" r="56"/><circle class="val" id="ringVal" cx="66" cy="66" r="56" stroke-dasharray="351.86" stroke-dashoffset="351.86"/></svg><div class="pct" id="pct">0%</div></div>
      <div class="prog-info">
        <h2 id="progTitle">Starting…</h2>
        <div class="sub" id="progSub"></div>
        <ol class="stepper" id="stepper"></ol>
        <button class="btn danger sm" id="cancelBtn">${icon("x")}Cancel</button>
      </div>
      <details class="log" id="logBox"><summary>Show log</summary><pre id="logPre"></pre></details>
    </div>
    <p class="muted small" style="text-align:center;margin-top:14px">You can close this tab: the conversion keeps running, and pages already read are kept if it stops.</p></div>`;
  $("#cancelBtn").onclick = async () => {
    try { await api("/job/cancel", { method: "POST" }); toast("Cancelling…"); } catch (e) { fail(e); }
  };
  updateProgress();
}

function updateProgress() {
  const j = S.job;
  if (!j || !$("#progress")) return;
  const pct = Math.min(100, j.percent);
  $("#pct").textContent = `${Math.floor(pct)}%`;
  $("#ringVal").style.strokeDashoffset = String(351.86 * (1 - pct / 100));
  const titles = { prepare: "Preparing the PDF…", images: "Taking out the page images…", ocr: "Reading pages with OCR…", structure: "Finding hadiths, chapters and sections…", done: "Writing files…" };
  $("#progTitle").textContent = titles[j.stage] || "Working…";
  let sub = `${duration(j.elapsed)} elapsed`;
  if (j.stage === "ocr" && j.ocr_total) sub = `Page ${fmt(j.ocr_done)} of ${fmt(j.ocr_total)}` + (j.eta != null && j.ocr_done ? ` · about ${duration(j.eta)} left` : "") + ` · ${duration(j.elapsed)} elapsed`;
  $("#progSub").textContent = sub;
  const idx = STAGES.findIndex(([k]) => k === j.stage);
  $("#stepper").innerHTML = STAGES.map(([k, label], i) =>
    `<li class="${i < idx ? "done" : i === idx ? "active" : ""}"><i>${i < idx ? icon("check") : i + 1}</i>${label}</li>`).join("");
  const pre = $("#logPre");
  const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 8;
  pre.textContent = S.jobLog.join("\n");
  if (atBottom) pre.scrollTop = pre.scrollHeight;
}

// ------------------------------------------------------------------ review: list
function isTodo(h, seq) { return !h.reviewed && (h.flags.length > 0 || !!seq[h.uid]); }
function todoCount() {
  const seq = sequenceIssues(S.book.hadiths);
  return S.book.hadiths.filter((h) => isTodo(h, seq)).length;
}

const FILTERS = [["todo", "To review"], ["spelling", "Spelling"], ["all", "All"], ["edited", "Edited"], ["done", "Done"]];

function filtered() {
  const hs = S.book.hadiths, seq = sequenceIssues(hs);
  let list = hs;
  if (S.filter === "todo") list = hs.filter((h) => isTodo(h, seq));
  else if (S.filter === "spelling") list = hs.filter((h) => S.spell[h.uid]);
  else if (S.filter === "edited") list = hs.filter((h) => h.text !== h.ocr_text);
  else if (S.filter === "done") list = hs.filter((h) => h.reviewed);
  const q = S.query.trim();
  if (q) {
    const num = fromBn(q).replace(/^#/, "");
    list = /^\d+$/.test(num) ? list.filter((h) => String(h.no ?? "").startsWith(num)) : list.filter((h) => h.text.includes(q));
  }
  return { list, seq };
}

function renderReview(body) {
  body.innerHTML = `<div class="review">
    <aside class="rv-list">
      <div class="rv-filters">
        <div class="seg" id="seg"></div>
        <div class="search">${icon("search")}<input class="input" id="q" placeholder="Number or words…  ( / )" value="${esc(S.query)}"></div>
      </div>
      <div class="rv-items" id="items"></div>
    </aside>
    <section class="rv-editor" id="editor"></section>
    <section class="rv-page" id="pageView"></section>
  </div>`;
  $("#q").oninput = debounce((e) => { S.query = e.target.value; renderList(); }, 150);
  $("#seg").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    S.filter = b.dataset.f;
    renderList();
    const { list } = filtered();
    if (list.length && !list.some((h) => h.uid === S.current)) select(list[0].uid);
  };
  $("#items").onclick = (e) => { const b = e.target.closest(".item"); if (b) select(b.dataset.uid); };
  const { list } = filtered();
  if (!S.current || !S.book.hadiths.some((h) => h.uid === S.current)) {
    S.current = (list[0] || S.book.hadiths[0] || {}).uid;
    if (!list.length && S.filter === "todo") S.filter = "all";
  }
  renderList();
  renderEditor();
}

function renderList() {
  if (!$("#items")) return;
  const hs = S.book.hadiths, seq = sequenceIssues(hs);
  const counts = {
    todo: hs.filter((h) => isTodo(h, seq)).length, spelling: hs.filter((h) => S.spell[h.uid]).length, all: hs.length,
    edited: hs.filter((h) => h.text !== h.ocr_text).length, done: hs.filter((h) => h.reviewed).length,
  };
  $("#seg").innerHTML = FILTERS.map(([k, l]) => `<button data-f="${k}" class="${S.filter === k ? "on" : ""}">${l} <b>${fmt(counts[k])}</b></button>`).join("");
  const tc = $("#todoCount");
  if (tc) { tc.textContent = counts.todo || "✓"; tc.classList.toggle("zero", !counts.todo); }
  const { list } = filtered();
  const box = $("#items");
  if (!list.length) {
    box.innerHTML = S.filter === "todo" && !S.query
      ? `<div class="list-empty">${icon("check")}All flagged hadiths are reviewed.<br>Export the files when you are ready.</div>`
      : `<div class="list-empty">Nothing matches.</div>`;
    return;
  }
  box.innerHTML = list.map((h) => {
    const err = seq[h.uid], sp = S.spell[h.uid];
    const dots = h.reviewed ? `<span class="done-mark">${icon("check")}</span>`
      : (err ? `<span class="dot err" title="${esc(err)}"></span>` : "") + (h.flags.length ? `<span class="dot" title="${esc(h.flags.join(", "))}"></span>` : "")
        + (sp ? `<span class="dot spell" title="${plural(sp.length, "possible misspelling")}"></span>` : "");
    return `<button class="item ${h.uid === S.current ? "active" : ""} ${h.reviewed ? "reviewed" : ""}" data-uid="${h.uid}">
      <span class="no">${h.no ?? "—"}</span><span class="snip">${esc(h.text.slice(0, 70))}</span><span class="mk">${dots}</span></button>`;
  }).join("");
  const act = $(".item.active", box);
  if (act) act.scrollIntoView({ block: "nearest" });
}

function cur() { return S.book && S.book.hadiths.find((h) => h.uid === S.current); }

async function select(uid, { keepFilter = false } = {}) {
  if (uid === S.current) return;
  await flushSave();
  S.current = uid;
  S.page = null;
  S.focusWord = null;
  if (!keepFilter && !filtered().list.some((h) => h.uid === uid)) { S.filter = "all"; S.query = ""; if ($("#q")) $("#q").value = ""; renderList(); }
  $$(".item").forEach((b) => b.classList.toggle("active", b.dataset.uid === uid));
  const act = $(".item.active");
  if (act) act.scrollIntoView({ block: "nearest" });
  renderEditor();
}

function step(dir) {
  const { list } = filtered();
  const i = list.findIndex((h) => h.uid === S.current);
  const next = list[i < 0 ? 0 : i + dir];
  if (next) select(next.uid);
}

// ------------------------------------------------------------------ review: editor
function sectionOptions(h) {
  const b = S.book, groups = {};
  b.sections.forEach((name, si) => { const ci = b.section_chapter[si] ?? -1; (groups[ci] = groups[ci] || []).push([si, name]); });
  const opt = ([si, name]) => `<option value="${si}" ${si === h.section ? "selected" : ""}>${si + 1}. ${esc(name)}</option>`;
  return `<option value="-1" ${h.section < 0 ? "selected" : ""}>— No section —</option>` +
    Object.keys(groups).sort((a, z) => a - z).map((ci) => ci >= 0 && b.chapters[ci]
      ? `<optgroup label="${esc(`${+ci + 1}. ${b.chapters[ci]}`)}">${groups[ci].map(opt).join("")}</optgroup>`
      : groups[ci].map(opt).join("")).join("");
}

/** Doubtful (low OCR confidence) words of the hadith: one entry per distinct word, worst confidence first. */
function doubtWords(h) {
  const by = {};
  for (const d of h.doubt || []) (by[d.w] = by[d.w] || []).push(d);
  return Object.entries(by).map(([w, ds]) => ({ w, ds, conf: Math.min(...ds.map((d) => d.conf)) }))
    .sort((a, z) => a.conf - z.conf);
}

function wholeWordAt(text, i, len) {
  return !BN_CHAR.test(text[i - 1] || "") && !BN_CHAR.test(text[i + len] || "");
}

/** Ranges of the text to highlight: possible misspellings (all occurrences) and doubtful words (as many
 *  occurrences, in reading order, as the OCR reported). -> [{s, e, cls, key}] sorted, without overlaps. */
function highlightRanges(text, h) {
  const out = [];
  for (const sp of S.spell[h.uid] || []) {
    for (let i = text.indexOf(sp.w); i >= 0; i = text.indexOf(sp.w, i + 1)) {
      if (wholeWordAt(text, i, sp.w.length)) out.push({ s: i, e: i + sp.w.length, cls: "m-spell", key: "s:" + sp.w });
    }
  }
  for (const d of doubtWords(h)) {
    let left = d.ds.length;
    for (let i = text.indexOf(d.w); i >= 0 && left > 0; i = text.indexOf(d.w, i + 1)) {
      out.push({ s: i, e: i + d.w.length, cls: d.conf < 40 ? "m-doubt strong" : "m-doubt", key: "d:" + d.w });
      left--;
    }
  }
  out.sort((a, b) => a.s - b.s || (a.cls === "m-spell" ? -1 : 1));
  const clean = [];
  for (const r of out) if (!clean.length || r.s >= clean[clean.length - 1].e) clean.push(r);
  return clean;
}

function paintMirror() {
  const ta = $("#textIn"), mir = $("#mirror"), h = cur();
  if (!ta || !mir || !h) return;
  const text = ta.value;
  let html = "", at = 0;
  for (const r of highlightRanges(text, h)) {
    html += esc(text.slice(at, r.s)) + `<mark class="${r.cls}" data-key="${esc(r.key)}">${esc(text.slice(r.s, r.e))}</mark>`;
    at = r.e;
  }
  mir.innerHTML = html + esc(text.slice(at)) + (text.endsWith("\n") ? " " : "");
}

function grow() {
  const ta = $("#textIn");
  if (!ta) return;
  ta.style.height = "auto";
  ta.style.height = ta.scrollHeight + 2 + "px";
}

function wordsPanel(h) {
  const sp = S.spell[h.uid] || [], dw = doubtWords(h);
  const hint = S.book.word_conf ? "" : `<p class="hint words-hint">${icon("info")}No “OCR unsure” highlights: this book was read by an older version. Convert again → Advanced → <b>Read all pages again</b> adds them.</p>`;
  if (!sp.length && !dw.length) return hint;
  return hint + `<div class="words">
    ${sp.length ? `<div class="words-row"><span class="words-lbl" title="A rare spelling one letter away from a common word of this book">Possible misspellings</span>
      ${sp.map((x, i) => `<span class="chip spell"><button class="chip-w bn" data-sw="${i}" title="Show it in the text">${esc(x.w)}</button><span class="arrow">→</span><b class="bn">${esc(x.s)}</b><small>${fmt(x.n)}×</small>
        <button class="chip-a" data-fix="${i}" title="Replace it in this hadith">Fix</button><button class="chip-a" data-all="${i}" title="Replace it in the whole book">All…</button><button class="chip-a" data-ign="${i}" title="It is a real word: stop suggesting this">Ignore</button></span>`).join("")}</div>` : ""}
    ${dw.length ? `<div class="words-row"><span class="words-lbl" title="Words Tesseract read with low confidence: compare them with the scan">OCR unsure</span>
      ${dw.map((x, i) => `<button class="chip doubt ${x.conf < 40 ? "strong" : ""}" data-dw="${i}" title="Show it in the text and on the page"><span class="bn">${esc(x.w)}</span><small>${x.conf}%</small></button>`).join("")}</div>` : ""}
  </div>`;
}

function refreshWords() {
  const h = cur(), box = $("#wordsBox");
  if (!h || !box) return;
  box.innerHTML = wordsPanel(h);
  paintMirror();
}

function renderEditor() {
  const ed = $("#editor");
  if (!ed) return;
  const h = cur();
  if (!h) { ed.innerHTML = `<div class="list-empty">No hadith selected.</div>`; $("#pageView").innerHTML = ""; return; }
  const { list, seq } = filtered();
  const pos = list.findIndex((x) => x.uid === h.uid);
  const issues = [];
  if (seq[h.uid]) issues.push(`<div class="issue err">${icon("alert")}<div><b>Number check: ${esc(seq[h.uid])}</b><span>Hadith numbers should run 1, 2, 3… Fix the number, or split / merge hadiths.</span></div></div>`);
  for (const f of h.flags) {
    const [title, text] = describeFlag(h, f);
    const stale = /^(no_repaired|seq_gap|no_marker|manual_split)/.test(f) && !seq[h.uid] && h.no != null;
    issues.push(`<div class="issue ${stale ? "stale" : ""}">${icon("alert")}<div><b>${esc(title)}${stale ? " · number now fits" : ""}</b><span>${esc(text)}</span></div></div>`);
  }
  if (h.reviewed) issues.unshift(`<div class="issue ok">${icon("check")}<div><b>Reviewed</b><span>Left out of review.csv on export.</span></div></div>`);
  const edited = h.text !== h.ocr_text;
  ed.innerHTML = `
    <div class="ed-head">
      <label class="ed-title">Hadith <input class="input no-input" id="noIn" inputmode="numeric" value="${h.no ?? ""}" placeholder="?"></label>
      <div class="ed-pos">
        ${pos >= 0 ? `${fmt(pos + 1)} of ${fmt(list.length)}` : ""}
        <button class="icon-btn" id="prevBtn" title="Previous (Alt+↑)" ${pos <= 0 ? "disabled" : ""}>${icon("up")}</button>
        <button class="icon-btn" id="nextBtn" title="Next (Alt+↓)" ${pos < 0 || pos >= list.length - 1 ? "disabled" : ""}>${icon("down")}</button>
      </div>
    </div>
    ${issues.length ? `<div class="issues">${issues.join("")}</div>` : ""}
    <div class="field"><span>Section</span>
      <div class="sec-row"><select class="input bn" id="secIn">${sectionOptions(h)}</select>
        <button class="btn sm" id="newSecBtn" title="A new section begins at this hadith">${icon("plus")}<span class="hide-sm">New section here</span></button></div></div>
    <div class="ta-wrap"><div class="ta-mirror text-area" id="mirror" aria-hidden="true"></div><textarea class="input text-area" id="textIn" lang="bn" spellcheck="false">${esc(h.text)}</textarea></div>
    <div class="ed-meta">
      <span>Page ${h.page_start}${h.page_end !== h.page_start ? `–${h.page_end}` : ""}</span>
      <span>${fmt(h.text.length)} characters</span>
      ${h.conf ? `<span>OCR ${Math.round(h.conf)}%</span>` : ""}
      <span class="save-state" id="saveState">${edited ? "Edited" : ""}</span>
      ${edited ? `<button class="linkish" id="revertBtn">${icon("undo")}Revert to OCR text</button>` : ""}
    </div>
    <div id="wordsBox">${wordsPanel(h)}</div>
    ${h.foot ? `<details class="extra"><summary>Footnote (OCR)</summary><div class="body bn">${esc(h.foot)}</div></details>` : ""}
    ${h.arabic ? `<details class="extra"><summary>Arabic (OCR, not exported)</summary><div class="body ar" lang="ar">${esc(h.arabic)}</div></details>` : ""}
    <div class="ed-actions">
      <button class="btn sm" id="splitBtn" title="Split into two hadiths at the cursor">${icon("scissors")}<span>Split<span class="lbl-long"> at cursor</span></span></button>
      <button class="btn sm" id="mergeBtn" title="Append this text to the hadith before it" ${S.book.hadiths[0].uid === h.uid ? "disabled" : ""}>${icon("merge")}<span>Merge<span class="lbl-long"> with previous</span></span></button>
      <button class="btn sm danger" id="delBtn" title="Delete this hadith">${icon("trash")}</button>
      <span class="spacer"></span>
      ${h.reviewed ? `<button class="btn ok" id="revBtn">${icon("check")}Reviewed</button>`
        : `<button class="btn primary" id="revBtn">${icon("check")}Mark reviewed<kbd>${MAC ? "⌘" : "Ctrl"} ↵</kbd></button>`}
    </div>`;
  const ta = $("#textIn");
  savedText = h.text;
  ta.oninput = () => { h.text = ta.value; setSave("dirty"); paintMirror(); grow(); saveText(); };
  ta.onblur = () => flushSave();
  $("#noIn").onchange = (e) => {
    const raw = fromBn(e.target.value).trim();
    if (raw && !/^\d+$/.test(raw)) { toast("The number must be digits only", "err"); e.target.value = h.no ?? ""; return; }
    patch(h, { no: raw ? +raw : null }).then(() => { renderList(); renderEditor(); }).catch(fail);
  };
  $("#secIn").onchange = (e) => patch(h, { section: +e.target.value }).catch(fail);
  $("#newSecBtn").onclick = () => openNewSection(h);
  $("#prevBtn").onclick = () => step(-1);
  $("#nextBtn").onclick = () => step(1);
  $("#revBtn").onclick = () => toggleReviewed(h);
  $("#splitBtn").onclick = () => openSplit(h, ta.selectionStart);
  $("#mergeBtn").onclick = () => openMerge(h);
  $("#delBtn").onclick = () => openDelete(h);
  $("#wordsBox").onclick = (e) => onWordsClick(e, h);
  const rv = $("#revertBtn");
  if (rv) rv.onclick = async () => { try { await flushSave(); await patch(h, { text: h.ocr_text }); renderList(); renderEditor(); } catch (e) { fail(e); } };
  paintMirror();
  requestAnimationFrame(grow);
  renderPage();
}

/** Select the n-th highlighted occurrence of a word in the text (and scroll it into view). */
function showInText(key) {
  const ta = $("#textIn"), h = cur();
  const r = highlightRanges(ta.value, h).find((x) => x.key === key);
  if (!r) return;
  ta.focus({ preventScroll: true });
  ta.setSelectionRange(r.s, r.e);
  const mark = $(`#mirror mark[data-key="${CSS.escape(key)}"]`);
  if (mark) mark.scrollIntoView({ block: "center", behavior: "smooth" });
}

async function onWordsClick(e, h) {
  const b = e.target.closest("button");
  if (!b) return;
  const sp = S.spell[h.uid] || [];
  if (b.dataset.dw != null) {
    const d = doubtWords(h)[+b.dataset.dw];
    showInText("d:" + d.w);
    const rec = d.ds[0];
    S.focusWord = rec;
    S.page = rec.page;
    renderPage();
  } else if (b.dataset.sw != null) showInText("s:" + sp[+b.dataset.sw].w);
  else if (b.dataset.fix != null) {
    const x = sp[+b.dataset.fix], ta = $("#textIn");
    let text = ta.value, out = "", at = 0;
    for (let i = text.indexOf(x.w); i >= 0; i = text.indexOf(x.w, i + 1)) {
      if (wholeWordAt(text, i, x.w.length)) { out += text.slice(at, i) + x.s; at = i + x.w.length; }
    }
    ta.value = out + text.slice(at);
    ta.dispatchEvent(new Event("input"));
    await flushSave();
    reloadSpelling();
  } else if (b.dataset.all != null) {
    const x = sp[+b.dataset.all];
    openFind({ find: x.w, replace: x.s, word: true });
  } else if (b.dataset.ign != null) {
    const x = sp[+b.dataset.ign];
    try {
      applyEdit(await papi("/spelling/ignore", { method: "POST", body: { word: x.w } }), { msg: `“${x.w}” will not be suggested again` });
      renderList();
      refreshWords();
    } catch (err) { fail(err); }
  }
}

// --- saving: the textarea is the truth; savedText is what the server has for the hadith on screen
let savedText = null;
function setSave(s) {
  const el = $("#saveState");
  if (!el) return;
  el.className = `save-state ${s}`;
  el.innerHTML = s === "saving" ? `<span class="spinner" style="width:12px;height:12px"></span>Saving…`
    : s === "saved" ? `${icon("check")}Saved` : s === "err" ? `${icon("alert")}Not saved` : s === "dirty" ? "Editing…" : "";
}
const saveText = debounce(() => flushSave(), 700);
async function flushSave() {
  saveText.cancel();
  const ta = $("#textIn"), h = cur();
  if (!ta || !h || ta.value === savedText) return;
  const text = ta.value;
  savedText = text;                                   // set first, so a second flush does not send it again
  setSave("saving");
  try {
    await papi(`/hadiths/${h.uid}`, { method: "PATCH", body: { text } });
    S.project.unexported = true;
    S.project.has_edits = true;
    if (ta.isConnected && ta.value === text) setSave("saved");
    const item = $(`.item[data-uid="${h.uid}"] .snip`);
    if (item) item.textContent = text.slice(0, 70);
    reloadSpelling();
  } catch (e) { savedText = null; setSave("err"); fail(e); }
}

async function patch(h, change) {
  if (!("text" in change)) await flushSave();         // typed text first, so a re-render cannot lose it
  const r = await papi(`/hadiths/${h.uid}`, { method: "PATCH", body: change });
  Object.assign(h, r);
  S.project.unexported = true;
  S.project.has_edits = true;
  return r;
}

async function toggleReviewed(h) {
  await flushSave();
  const { list } = filtered();
  const i = list.findIndex((x) => x.uid === h.uid);
  const marking = !h.reviewed;
  try {
    await patch(h, { reviewed: marking });
    if (marking) {
      const nxt = S.filter === "todo" ? (list[i + 1] || list[i - 1]) : list[i + 1];
      if (nxt && nxt.uid !== h.uid) { S.current = nxt.uid; S.page = null; S.focusWord = null; }
    }
    renderList();
    renderEditor();
  } catch (e) { fail(e); }
}

// --- split / merge / delete / new section (all Undo-able)
function openSplit(h, at) {
  const text = $("#textIn").value;
  if (at == null || at <= 0 || at >= text.length) return toast("Click in the text where the second hadith begins, then press Split.", "err");
  const head = text.slice(0, at).trim(), tail = text.slice(at).trim();
  const mk = /^[\[\{]?\s*([০-৯0-9]{1,4})\s*[\]\}।]\s*/.exec(tail);   // a "[123]" marker right at the cut
  const tailText = mk ? tail.slice(mk[0].length) : tail;
  const guess = mk ? +fromBn(mk[1]) : (h.no != null ? h.no + 1 : "");
  const m = modal(`
    <header><h2>Split hadith ${h.no ?? ""}</h2><p>The text after the cursor becomes a new hadith right after this one.</p></header>
    <div class="split-preview">
      <div><span class="lbl">Stays as ${h.no ?? "this hadith"}</span><p class="bn">${esc(head)}</p></div>
      <div><span class="lbl">New hadith</span><p class="bn">${esc(tailText)}</p>
        <label class="field"><span>Number</span><input class="input" id="splitNo" inputmode="numeric" value="${guess}"></label></div>
    </div>
    ${mk ? `<div class="note">${icon("info")}<span>The “${esc(mk[0].trim())}” marker at the cut is removed; its number is used.</span></div>` : ""}
    <div class="modal-actions"><button class="btn ghost" data-close>Cancel</button><button class="btn primary" id="doSplit">${icon("scissors")}Split</button></div>`, "wide");
  $("#doSplit", m).onclick = async () => {
    const no = fromBn($("#splitNo", m).value).trim();
    try {
      await flushSave();
      const offset = mk ? text.length - tail.length + mk[0].length : at;
      const r = await papi(`/hadiths/${h.uid}/split`, { method: "POST", body: { offset, no: no ? +no : null } });
      closeModal();
      applyEdit(r, { current: r.result, msg: `Split hadith ${h.no ?? ""}` });
      renderList();
      renderEditor();
    } catch (e) { fail(e); }
  };
}

function openMerge(h) {
  const i = S.book.hadiths.findIndex((x) => x.uid === h.uid), prev = S.book.hadiths[i - 1];
  const m = modal(`
    <header><h2>Merge into hadith ${prev.no ?? ""}?</h2><p>The text of hadith ${h.no ?? ""} is appended to hadith ${prev.no ?? ""}, and ${h.no ?? "this one"} is removed.</p></header>
    <div class="split-preview">
      <div><span class="lbl">Hadith ${prev.no ?? ""} ends with</span><p class="bn">…${esc(prev.text.slice(-220))}</p></div>
      <div><span class="lbl">Appended</span><p class="bn">${esc(h.text.slice(0, 220))}${h.text.length > 220 ? "…" : ""}</p></div>
    </div>
    <div class="modal-actions"><button class="btn ghost" data-close>Cancel</button><button class="btn primary" id="doMerge">${icon("merge")}Merge</button></div>`, "wide");
  $("#doMerge", m).onclick = async () => {
    try {
      await flushSave();
      const r = await papi(`/hadiths/${h.uid}/merge`, { method: "POST" });
      closeModal();
      applyEdit(r, { current: r.result, msg: `Merged into hadith ${prev.no ?? ""}` });
      renderList();
      renderEditor();
    } catch (e) { fail(e); }
  };
}

function openDelete(h) {
  const m = modal(`
    <header><h2>Delete hadith ${h.no ?? ""}?</h2><p>Use this for junk the OCR took for a hadith. It is removed from the exported files. Undo brings it back.</p></header>
    <p class="bn" style="color:var(--text-2);max-height:140px;overflow:auto">${esc(h.text.slice(0, 400))}</p>
    <div class="modal-actions"><button class="btn ghost" data-close>Cancel</button><button class="btn danger solid" id="doDel">${icon("trash")}Delete</button></div>`);
  $("#doDel", m).onclick = async () => {
    try {
      await flushSave();
      const { list } = filtered();
      const i = list.findIndex((x) => x.uid === h.uid);
      const nxt = list[i + 1] || list[i - 1];
      const r = await papi(`/hadiths/${h.uid}`, { method: "DELETE" });
      closeModal();
      applyEdit(r, { current: nxt && nxt.uid, msg: `Deleted hadith ${h.no ?? ""}` });
      renderList();
      renderEditor();
    } catch (e) { fail(e); }
  };
}

function openNewSection(h) {
  const b = S.book, ci = h.section >= 0 ? b.section_chapter[h.section] : h.chapter;
  const m = modal(`
    <header><h2>New section at hadith ${h.no ?? ""}</h2>
      <p>The section begins here; this hadith and the ones after it in “${esc(b.sections[h.section] || "no section")}” move into it.${ci >= 0 && b.chapters[ci] ? ` It is part of chapter ${ci + 1}, ${esc(b.chapters[ci])}.` : ""}</p></header>
    <form id="nsForm"><label class="field"><span>Section name</span><input class="input bn" name="name" required></label>
    <div class="modal-actions"><button type="button" class="btn ghost" data-close>Cancel</button><button class="btn primary" type="submit">${icon("plus")}Add section</button></div></form>`);
  $("#nsForm", m).onsubmit = async (e) => {
    e.preventDefault();
    try {
      await flushSave();
      const r = await papi(`/hadiths/${h.uid}/new-section`, { method: "POST", body: { name: e.target.name.value } });
      closeModal();
      applyEdit(r, { msg: "Section added" });
      renderEditor();
    } catch (err) { fail(err); }
  };
}

// ------------------------------------------------------------------ review: page viewer
/** The hadith that starts last before (page, y): what a click on the scan opens. */
function hadithAt(page, y) {
  let best = null;
  for (const h of S.book.hadiths) {
    const a = h.anchor;
    if (!a) continue;
    if (a.page < page || (a.page === page && a.y0 <= y + 0.004)) best = h;
    else if (a.page > page) break;
  }
  return best || S.book.hadiths.find((h) => h.page_start <= page && page <= h.page_end) || null;
}

function renderPage() {
  const h = cur(), box = $("#pageView");
  if (!h || !box) return;
  const anchorPage = h.anchor ? h.anchor.page : h.page_start;
  const page = S.page ?? anchorPage;
  const pages = [];
  for (let p = h.page_start; p <= h.page_end; p++) pages.push(p);
  if (!pages.includes(anchorPage)) pages.unshift(anchorPage);
  const pct = (v) => `${(v * 100).toFixed(3)}%`;
  const mark = h.anchor && h.anchor.page === page
    ? `<div class="pv-mark" style="top:${pct(h.anchor.y0)};height:${pct(h.anchor.y1 - h.anchor.y0)}"></div>` : "";
  const words = (h.doubt || []).filter((d) => d.page === page).map((d) => {
    const [l, t, r, b] = d.box, focus = S.focusWord && S.focusWord.page === d.page && S.focusWord.box.join() === d.box.join();
    return `<div class="pv-word ${focus ? "focus" : ""} ${d.conf < 40 ? "strong" : ""}" style="left:${pct(l)};top:${pct(t)};width:${pct(r - l)};height:${pct(b - t)}" title="${esc(d.w)} · ${d.conf}%"></div>`;
  }).join("");
  box.innerHTML = `
    <div class="pv-bar">
      <button class="icon-btn" id="pgPrev" title="Previous page">${icon("left")}</button>
      <div class="pv-pages">${pages.map((p) => `<button class="pg-chip ${p === page ? "on" : ""}" data-p="${p}">${p}</button>`).join("")}
        ${pages.includes(page) ? "" : `<button class="pg-chip on">${page}</button>`}</div>
      <button class="icon-btn" id="pgNext" title="Next page">${icon("right")}</button>
      <div class="pv-zoom">
        <button class="icon-btn" id="zOut" title="Zoom out">${icon("minus")}</button><span>${Math.round(S.zoom * 100)}%</span>
        <button class="icon-btn" id="zIn" title="Zoom in">${icon("plus")}</button>
        <a class="icon-btn" href="/api/projects/${enc(S.pid)}/pages/${page}" target="_blank" title="Open the page image">${icon("external")}</a>
      </div>
    </div>
    <div class="pv-scroll" id="pvScroll">
      <div class="pv-canvas" id="pvCanvas" style="width:${S.zoom * 100}%" title="Click a line to open the hadith printed there"><img id="pvImg" alt="Page ${page}" src="/api/projects/${enc(S.pid)}/pages/${page}">${mark}${words}</div>
    </div>`;
  const img = $("#pvImg"), sc = $("#pvScroll");
  const target = S.focusWord && S.focusWord.page === page ? S.focusWord.box[1] : (h.anchor && h.anchor.page === page ? h.anchor.y0 : null);
  const toTarget = () => { if (target != null) sc.scrollTop = Math.max(0, img.clientHeight * target - sc.clientHeight * 0.3); };
  img.onload = toTarget;
  img.onerror = () => { sc.innerHTML = `<div class="pv-missing">No image for page ${page}.</div>`; };
  if (img.complete) toTarget();
  $("#pvCanvas").onclick = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const hit = hadithAt(page, (e.clientY - r.top) / r.height);
    if (hit && hit.uid !== S.current) { select(hit.uid).then(() => { S.page = page; renderPage(); }); }
  };
  box.querySelector(".pv-pages").onclick = (e) => { const b = e.target.closest("[data-p]"); if (b) { S.page = +b.dataset.p; renderPage(); } };
  $("#pgPrev").onclick = () => { if (page > 1) { S.page = page - 1; renderPage(); } };
  $("#pgNext").onclick = () => { S.page = page + 1; renderPage(); };
  const zoom = (f) => { S.zoom = Math.min(3, Math.max(0.5, +(S.zoom * f).toFixed(2))); const y = sc.scrollTop / (sc.scrollHeight || 1); renderPage(); requestAnimationFrame(() => { $("#pvScroll").scrollTop = y * $("#pvScroll").scrollHeight; }); };
  $("#zIn").onclick = () => zoom(1.25);
  $("#zOut").onclick = () => zoom(0.8);
}

// ------------------------------------------------------------------ find & replace
async function openFind(pre = {}) {
  await flushSave();
  const m = modal(`
    <header><h2>Find &amp; replace</h2><p>In the text of every hadith. Check the matches below before replacing; Undo takes it back.</p></header>
    <form id="frForm">
      <div class="row2">
        <label class="field"><span>Find</span><input class="input bn" name="find" value="${esc(pre.find || "")}" autocomplete="off"></label>
        <label class="field"><span>Replace with</span><input class="input bn" name="replace" value="${esc(pre.replace || "")}" autocomplete="off"></label>
      </div>
      <div class="fr-opts">
        <label class="check"><input type="checkbox" name="word" ${pre.word ? "checked" : ""}><span><b>Whole words</b></span></label>
        <label class="check"><input type="checkbox" name="regex"><span><b>Regular expression</b></span></label>
        <label class="check"><input type="checkbox" name="remember"><span><b>Also fix it in future conversions</b><small>Saved in ${esc(S.project.settings_file)}</small></span></label>
      </div>
      <div class="fr-results" id="frResults"><div class="list-empty">Type what to find.</div></div>
      <div class="modal-actions"><span class="muted small" id="frCount"></span><span class="spacer"></span>
        <button type="button" class="btn ghost" data-close>Close</button>
        <button type="submit" class="btn primary" id="frGo" disabled>Replace</button></div>
    </form>`, "wide");
  const f = $("#frForm", m);
  let matches = [];
  const opts = () => ({ find: f.find.value, replace: f.replace.value, word: f.word.checked, regex: f.regex.checked });
  const run = debounce(async () => {
    const o = opts();
    if (!o.find) { matches = []; $("#frResults").innerHTML = `<div class="list-empty">Type what to find.</div>`; $("#frCount").textContent = ""; $("#frGo").disabled = true; return; }
    try {
      const r = await papi("/search", { method: "POST", body: o });
      matches = r.matches;
      $("#frCount").textContent = r.total ? `${plural(r.total, "match", "matches")} in ${plural(r.hadiths, "hadith")}` : "No matches";
      $("#frResults").innerHTML = matches.length ? matches.map((x, i) => `
        <label class="fr-row"><input type="checkbox" data-i="${i}" checked>
          <span class="fr-no">${x.no ?? "—"}</span>
          <span class="fr-snips bn">${x.snippets.map(([a, b, c]) => `…${esc(a)}<mark>${esc(b)}</mark>${esc(c)}…`).join("<br>")}${x.count > 3 ? ` <span class="muted">(+${x.count - 3})</span>` : ""}</span>
          <button type="button" class="linkish" data-open="${x.uid}">Open</button></label>`).join("")
        : `<div class="list-empty">No matches.</div>`;
      updateGo();
    } catch (e) { $("#frResults").innerHTML = `<div class="list-empty">${esc(e.message)}</div>`; $("#frGo").disabled = true; $("#frCount").textContent = ""; }
  }, 250);
  const chosen = () => $$("#frResults input[data-i]").filter((c) => c.checked).map((c) => matches[+c.dataset.i].uid);
  const updateGo = () => { const n = chosen().length; $("#frGo").disabled = !n; $("#frGo").textContent = n ? `Replace in ${plural(n, "hadith")}` : "Replace"; };
  f.oninput = (e) => { if (e.target.dataset.i != null) updateGo(); else if (e.target.name !== "replace" && e.target.name !== "remember") run(); };
  $("#frResults").onclick = (e) => {
    const b = e.target.closest("[data-open]");
    if (!b) return;
    e.preventDefault();
    closeModal();
    if (S.tab !== "review") location.hash = `#/p/${enc(S.pid)}`;
    setTimeout(() => select(b.dataset.open), 50);
  };
  f.onsubmit = async (e) => {
    e.preventDefault();
    const o = opts(), uids = chosen();
    const all = uids.length === matches.length && matches.length < 300;
    try {
      const r = await papi("/replace", { method: "POST", body: { ...o, uids: all ? null : uids, remember: f.remember.checked } });
      closeModal();
      applyEdit(r, { msg: `Replaced ${plural(r.result[0], "time")} in ${plural(r.result[1], "hadith")}${f.remember.checked ? " · remembered" : ""}` });
      renderProject();
    } catch (err) { fail(err); }
  };
  if (pre.find) run();
}

// ------------------------------------------------------------------ structure
function renderStructure(body) {
  const b = S.book, counts = {};
  b.hadiths.forEach((h) => { counts[h.section] = (counts[h.section] || 0) + 1; });
  const bySec = {};
  b.sections.forEach((_, si) => { const ci = b.section_chapter[si] ?? -1; (bySec[ci] = bySec[ci] || []).push(si); });
  const chapterOpts = (ci) => `<option value="-1" ${ci < 0 ? "selected" : ""}>No chapter</option>` +
    b.chapters.map((n, i) => `<option value="${i}" ${i === ci ? "selected" : ""}>${i + 1}. ${esc(n)}</option>`).join("");
  const secLi = (si) => `<li>
      <span class="sec-no">${si + 1}</span><input class="input bn" data-s="${si}" value="${esc(b.sections[si])}">
      <span class="sec-count">${plural(counts[si] || 0, "hadith")}</span>
      <select class="input sec-move bn" data-move="${si}" title="Move to another chapter">${chapterOpts(b.section_chapter[si] ?? -1)}</select>
      <button class="icon-btn" data-delsec="${si}" title="Delete this section (its hadiths join the section before)">${icon("trash")}</button></li>`;
  const chapters = b.chapters.map((name, ci) => `
    <div class="card chapter-card">
      <div class="ch-head"><span class="ch-no">${ci + 1}</span><input class="input bn" data-c="${ci}" value="${esc(name)}">
        <button class="icon-btn" data-addch="${ci + 1}" title="Add a chapter after this one">${icon("plus")}</button>
        <button class="icon-btn" data-delch="${ci}" title="Delete this chapter (its sections join the chapter before)">${icon("trash")}</button></div>
      ${(bySec[ci] || []).length ? `<ol class="sec-list">${bySec[ci].map(secLi).join("")}</ol>` : `<div class="list-empty" style="padding:14px">No sections. Move sections here with the chapter menu of a section.</div>`}
    </div>`).join("");
  const loose = bySec[-1] || [];
  body.innerHTML = `<div class="tab-scroll" style="overflow:auto;flex:1"><div class="structure">
    <p class="intro">Fix the names the OCR got wrong, move sections between chapters, add or delete chapters. Names are used for the chapters and sections sheets of the workbook and the headings of the Word file.
      To start a new section, open its first hadith in Review and press <b>New section here</b>.</p>
    <div class="card title-card"><label class="field"><span>Book title <em>(first page of the Word file)</em></span><input class="input bn" id="bookTitle" value="${esc(b.title)}"></label></div>
    ${!b.chapters.length ? `<div><button class="btn" data-addch="0">${icon("plus")}Add a chapter</button></div>` : ""}
    ${chapters}
    ${loose.length ? `<div class="card chapter-card"><div class="ch-head"><span class="ch-no">–</span><h3>Sections without a chapter</h3></div><ol class="sec-list">${loose.map(secLi).join("")}</ol></div>` : ""}
    ${!b.chapters.length && !b.sections.length ? `<div class="card list-empty">No chapters or sections were found in this book.</div>` : ""}
  </div></div>
  <div class="savebar" id="saveBar" hidden><span>Unsaved names</span><button class="btn ghost sm" id="undoStruct">Reset</button><button class="btn primary sm" id="saveStruct">Save</button></div>`;
  const bar = $("#saveBar");
  body.oninput = (e) => { if (e.target.matches("[data-c], [data-s], #bookTitle")) bar.hidden = false; };
  $("#undoStruct").onclick = () => renderStructure(body);
  const saveNames = async (quiet) => {
    if (bar.hidden) return;
    const chaptersNew = b.chapters.map((n, ci) => $(`[data-c="${ci}"]`).value);
    const sectionsNew = b.sections.map((n, si) => $(`[data-s="${si}"]`).value);
    const r = await papi("/structure", { method: "PUT", body: { title: $("#bookTitle").value, chapters: chaptersNew, sections: sectionsNew } });
    applyEdit(r, { msg: quiet ? null : "Names saved" });
    bar.hidden = true;
  };
  $("#saveStruct").onclick = () => saveNames(false).catch(fail);
  const act = async (action, payload, msg) => {
    try {
      await saveNames(true);
      applyEdit(await papi(`/structure/${action}`, { method: "POST", body: payload }), { msg });
      renderProject();
    } catch (e) { fail(e); }
  };
  body.onchange = (e) => {
    const sel = e.target.closest("[data-move]");
    if (sel) act("move-section", { section: +sel.dataset.move, chapter: +sel.value }, "Section moved");
  };
  body.onclick = (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.dataset.delsec != null) {
      const si = +t.dataset.delsec;
      act("delete-section", { section: si }, `Deleted section ${si + 1}; its ${plural(counts[si] || 0, "hadith")} joined the ${si ? "section before" : "next section"}`);
    } else if (t.dataset.delch != null) {
      const ci = +t.dataset.delch;
      act("delete-chapter", { chapter: ci }, `Deleted chapter ${ci + 1}`);
    } else if (t.dataset.addch != null) {
      const at = +t.dataset.addch;
      const m = modal(`<header><h2>New chapter</h2><p>It becomes chapter ${at + 1}. Then move sections into it with each section's chapter menu.</p></header>
        <form id="chForm"><label class="field"><span>Chapter name</span><input class="input bn" name="name" required></label>
        <div class="modal-actions"><button type="button" class="btn ghost" data-close>Cancel</button><button class="btn primary" type="submit">${icon("plus")}Add chapter</button></div></form>`);
      $("#chForm", m).onsubmit = (ev) => { ev.preventDefault(); const name = ev.target.name.value; closeModal(); act("add-chapter", { at, name }, "Chapter added"); };
    }
  };
}

// ------------------------------------------------------------------ files
const FILE_INFO = {
  "book.xlsx": ["x", "sheet", "Workbook: chapters, sections and hadiths sheets"],
  "book.docx": ["w", "doc", "Word file: chapter and section headings with numbered hadiths"],
  "review.csv": ["c", "list", "The hadiths still flagged and not marked reviewed"],
};

function renderFiles(body) {
  const p = S.project;
  const rows = p.files.map((f) => {
    const [cls, ic, desc] = FILE_INFO[f.name];
    return `<div class="file-row"><div class="file-ic ${cls}">${icon(ic)}</div>
      <div class="grow"><b>${f.name}</b><p>${desc} · ${size(f.size)} · ${ago(f.mtime)}</p></div>
      <a class="btn sm" href="/api/projects/${enc(p.id)}/files/${f.name}" download>${icon("download")}Download</a></div>`;
  }).join("");
  body.innerHTML = `<div style="overflow:auto;flex:1"><div class="files">
    <div class="card export-card">
      <div><h2>Export</h2>
        <p>Writes book.xlsx, book.docx and review.csv again from the reviewed text, chapters and sections. Hadiths marked reviewed are left out of review.csv.</p>
        ${p.unexported ? `<div class="note warn" style="margin-top:6px">${icon("alert")}<span>You have changes that are not in the files yet.</span></div>` : ""}
      </div>
      <button class="btn primary lg" id="expBtn">${icon("download")}Export files</button>
    </div>
    <div class="card">${rows || `<div class="list-empty">No files yet.</div>`}</div>
    <div><button class="btn ghost" id="revealBtn">${icon("folder")}Show the output folder</button></div>
    ${p.has_edits ? `<div class="card danger-card"><div><h3>Discard review edits</h3><p>Go back to the OCR result. Text edits, splits, merges, deletions, renamed chapters and “reviewed” marks are all dropped. Undo brings them back while the app keeps running.</p></div>
      <button class="btn danger" id="discardBtn">${icon("trash")}Discard edits</button></div>` : ""}
  </div></div>`;
  $("#expBtn").onclick = exportFiles;
  $("#revealBtn").onclick = () => papi("/reveal", { method: "POST" }).catch(fail);
  const d = $("#discardBtn");
  if (d) d.onclick = () => {
    const m = modal(`<header><h2>Discard all review edits?</h2><p>Exported files stay as they are until you export again.</p></header>
      <div class="modal-actions"><button class="btn ghost" data-close>Cancel</button><button class="btn danger solid" id="doDiscard">Discard edits</button></div>`);
    $("#doDiscard", m).onclick = async () => {
      try {
        S.project = await papi("/discard", { method: "POST" });
        const r = await papi("/book");
        S.book = r.book;
        S.undo = r.undo;
        S.current = null;
        closeModal();
        toast("Edits discarded", "", { label: "Undo", fn: undo });
        render();
        loadSpelling();
      } catch (e) { fail(e); }
    };
  };
}

async function exportFiles() {
  await flushSave();
  const btns = $$('[data-act="export"], #expBtn');
  btns.forEach((b) => { b.disabled = true; b.dataset.label = b.innerHTML; b.innerHTML = `<span class="spinner" style="width:15px;height:15px"></span>Exporting…`; });
  try {
    const r = await papi("/export", { method: "POST", body: {} });
    S.project = r.project;
    toast(`Exported ${r.files.join(", ")}`);
    if (S.tab === "files") renderProject();
    else {
      btns.forEach((b) => { b.disabled = false; b.innerHTML = b.dataset.label; });
      const tab = $('.tabs a[href$="/files"]');
      if (tab) tab.innerHTML = "Files";
    }
  } catch (e) {
    fail(e);
    btns.forEach((b) => { b.disabled = false; b.innerHTML = b.dataset.label; });
  }
}

// ------------------------------------------------------------------ modal
function modal(html, cls = "") {
  closeModal();
  const m = document.createElement("div");
  m.className = "modal";
  m.innerHTML = `<div class="modal-card ${cls}" role="dialog" aria-modal="true">${html}</div>`;
  m.addEventListener("mousedown", (e) => { if (e.target === m) closeModal(); });
  m.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) closeModal(); });
  $("#modalRoot").append(m);
  const first = $("input:not([type=checkbox]), textarea, select, button.primary", m);
  if (first) setTimeout(() => first.focus(), 30);
  return m;
}
function closeModal() { $("#modalRoot").innerHTML = ""; }

// ------------------------------------------------------------------ keyboard
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && $(".modal")) return closeModal();
  if ($(".modal") || !S.book) return;
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
  const mod = e.metaKey || e.ctrlKey;
  if (mod && e.shiftKey && e.key.toLowerCase() === "f") { e.preventDefault(); openFind(); return; }
  if (mod && !e.shiftKey && e.key.toLowerCase() === "z" && !typing && S.undo) { e.preventDefault(); undo(); return; }
  if (S.tab !== "review" || !$("#editor")) return;
  if (mod && e.key === "Enter") { e.preventDefault(); const h = cur(); if (h) toggleReviewed(h); return; }
  if (e.altKey && e.key === "ArrowDown") { e.preventDefault(); step(1); return; }
  if (e.altKey && e.key === "ArrowUp") { e.preventDefault(); step(-1); return; }
  if (typing) return;
  if (e.key === "j") step(1);
  else if (e.key === "k") step(-1);
  else if (e.key === "/") { e.preventDefault(); $("#q").focus(); }
});
window.addEventListener("beforeunload", (e) => {
  const ta = $("#textIn");
  if (ta && ta.value !== savedText) { flushSave(); e.preventDefault(); }
});
window.addEventListener("resize", debounce(grow, 100));

// ------------------------------------------------------------------ start
window.addEventListener("hashchange", route);
(async () => {
  try {
    const j = await api("/job");
    if (j) { S.job = j; if (j.status === "running") connectJob(0); else S.jobLog = j.log; }
  } catch (e) { /* server just started */ }
  await route();
  onJobUpdate();
})();
