"use strict";

const $ = (s) => document.querySelector(s);
const state = { jobId: null, timer: null, started: 0, markdown: "", raw: false };
const STAGES = ["queued", "collecting", "analyzing", "synthesizing", "done"];

// ------------------------------------------------------------------ helpers

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function platformOf(url) {
  const u = (url || "").toLowerCase();
  if (/(^|\/\/|\.)youtube\.com|youtu\.be/.test(u)) return "youtube";
  if (/(^|\/\/|\.)instagram\.com/.test(u)) return "instagram";
  if (/(^|\/\/|\.)(facebook\.com|fb\.com|fb\.watch)/.test(u)) return "facebook";
  if (/(^|\/\/|\.)linkedin\.com/.test(u)) return "linkedin";
  if (/(^|\/\/|\.)github\.com/.test(u)) return "github";
  if (/(^|\/\/|\.)medium\.com/.test(u)) return "medium";
  if (/(^|\/\/|\.)substack\.com/.test(u)) return "substack";
  if (/(^|\/\/|\.)reddit\.com/.test(u)) return "reddit";
  if (/(^|\/\/|\.)(twitter\.com|x\.com)/.test(u)) return "twitter";
  if (/(^|\/\/|\.)news\.google\.com/.test(u)) return "news";
  if (/(^|\/\/|\.)(linktr\.ee|beacons\.ai|stan\.store)/.test(u)) return "linktree";
  if (/(^|\/\/|\.)patreon\.com/.test(u)) return "patreon";
  if (/(^|\/\/|\.)(ko-fi\.com|buymeacoffee\.com)/.test(u)) return "kofi";
  if (/(^|\/\/|\.)twitch\.tv/.test(u)) return "twitch";
  return u.trim() ? "website" : "—";
}

function showError(e) {
  const el = $("#error");
  el.textContent = e && e.reason ? `${e.reason}: ${e.message}` : (e?.message || String(e));
  el.classList.remove("hidden");
}
const clearError = () => $("#error").classList.add("hidden");

async function api(path, opts) {
  const res = await fetch(path, opts);
  let body;
  try { body = await res.json(); } catch { body = {}; }
  if (!res.ok) throw body.error || { reason: "http" + res.status, message: res.statusText };
  return body;
}

// Markdown → HTML. Input is escaped FIRST: the dossier embeds scraped
// third-party text, so nothing from it may ever reach the DOM as live markup.
function renderMarkdown(md) {
  let h = esc(md);
  h = h.replace(/^### (.*)$/gm, "<h3>$1</h3>")
       .replace(/^## (.*)$/gm, "<h2>$1</h2>")
       .replace(/^# (.*)$/gm, "<h1>$1</h1>")
       .replace(/^\s*---\s*$/gm, "<hr>")
       .replace(/`([^`]+)`/g, "<code>$1</code>")
       .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
       .replace(/\*(.+?)\*/g, "<em>$1</em>")
       .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
                '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
       .replace(/&lt;(https?:\/\/[^&\s]+)&gt;/g,
                '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
  h = h.replace(/(?:^[-*] .*(?:\n|$))+/gm, (m) =>
    "<ul>" + m.trim().split("\n")
      .map((l) => "<li>" + l.replace(/^[-*] /, "") + "</li>").join("") + "</ul>");
  return h.split(/\n{2,}/).map((blk) => {
    const t = blk.trim();
    if (!t) return "";
    return /^<(h[123]|ul|hr|pre)/.test(t) ? t : `<p>${t.replace(/\n/g, "<br>")}</p>`;
  }).join("\n");
}

// ------------------------------------------------------------------- intake

function addLinkRow(value = "") {
  const row = document.createElement("div");
  row.className = "linkrow";
  row.innerHTML = `<span class="badge">—</span>
    <input type="text" class="lurl" spellcheck="false"
           placeholder="https://example.com  ·  youtube.com/@handle  ·  instagram.com/name">
    <button class="icon rm" title="Remove">×</button>`;
  const input = row.querySelector(".lurl");
  const badge = row.querySelector(".badge");
  input.value = value;
  const sync = () => {
    const p = platformOf(input.value);
    badge.textContent = p;
    badge.className = "badge " + (p === "—" ? "" : p);
  };
  input.addEventListener("input", sync);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
  row.querySelector(".rm").onclick = () => {
    row.remove();
    if (!document.querySelectorAll(".linkrow").length) addLinkRow();
  };
  sync();
  $("#links").appendChild(row);
  return input;
}

function currentLinks() {
  return [...document.querySelectorAll(".lurl")].map((i) => i.value.trim()).filter(Boolean);
}

async function submit() {
  const links = currentLinks();
  if (!links.length) return showError({ reason: "noLinks", message: "Add at least one link." });
  clearError();
  $("#go").disabled = true;
  try {
    const body = { links, entity_name: $("#entity").value.trim() || null };
    const res = await api("/api/jobs", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    startWatching(res.job_id);
  } catch (e) {
    showError(e);
    $("#go").disabled = false;
  }
}

// ----------------------------------------------------------------- progress

function paintStages(status) {
  const idx = STAGES.indexOf(status);
  for (const el of document.querySelectorAll(".stage")) {
    const i = STAGES.indexOf(el.dataset.stage);
    el.className = "stage" + (status === "error" ? (i === 0 ? " err" : "")
      : i < idx ? " done" : i === idx ? (status === "done" ? " done" : " active") : "");
  }
  if (status === "error") {
    const cur = document.querySelector(".stage");
    if (cur) cur.className = "stage err";
  }
}

const NOTE = {
  queued: "Waiting for the worker (one job at a time — the GPU holds one model).",
  collecting: "Rendering pages, extracting text, capturing screenshots.",
  analyzing: "Local vision model reading each screenshot (~10s per image).",
  synthesizing: "gpt-oss map-reducing all sources into the dossier.",
};

function startWatching(jobId) {
  state.jobId = jobId;
  state.started = Date.now();
  $("#jobid").textContent = jobId;
  $("#progress").classList.remove("hidden");
  $("#result").classList.add("hidden");
  paintStages("queued");
  clearInterval(state.timer);
  state.timer = setInterval(poll, 2000);
  poll();
}

async function poll() {
  if (!state.jobId) return;
  try {
    const j = await api(`/api/jobs/${state.jobId}`);
    paintStages(j.status);
    $("#elapsed").textContent = `${Math.round((Date.now() - state.started) / 1000)}s elapsed`;
    $("#stagenote").textContent = NOTE[j.status] || "";
    if (j.status === "done" || j.status === "error") {
      clearInterval(state.timer);
      $("#go").disabled = false;
      if (j.status === "error") {
        $("#stagenote").textContent = "";
        showError({ reason: "jobFailed", message: j.error || "Job failed." });
      } else {
        await showResult(j);
      }
      loadRecent();
    }
  } catch (e) {
    clearInterval(state.timer);
    $("#go").disabled = false;
    showError(e);
  }
}

// ------------------------------------------------------------------- result

async function showResult(job) {
  const { markdown } = await api(`/api/jobs/${job.id}/dossier`);
  state.markdown = markdown;
  $("#dossier").innerHTML = renderMarkdown(markdown);
  $("#rawmd").textContent = markdown;
  renderSources(job);
  $("#result").classList.remove("hidden");
}

function fileUrl(jobId, relPath) {
  return `/api/jobs/${jobId}/file?path=${encodeURIComponent(relPath)}`;
}

function renderSources(job) {
  const box = $("#sources");
  box.innerHTML = "";
  for (const a of job.artifacts || []) {
    const el = document.createElement("div");
    el.className = "src";
    const methods = (a.facts?.methods_succeeded || []).join(", ");
    const facts = Object.entries(a.facts || {})
      .filter(([k]) => !["provenance", "methods_attempted", "methods_succeeded", "pages_visited"].includes(k))
      .map(([k, v]) => `<b>${esc(k)}:</b> ${esc(typeof v === "object" ? JSON.stringify(v) : v)}`)
      .join(" &nbsp;·&nbsp; ");

    el.innerHTML = `
      <div class="row between">
        <div class="row">
          <span class="badge ${esc(a.platform)}">${esc(a.platform)}</span>
          <span class="badge ${a.ok ? "ok" : "fail"}">${a.ok ? "collected" : "partial / failed"}</span>
        </div>
        <span class="hint">${esc(a.method || "")}${methods ? " · " + esc(methods) : ""}</span>
      </div>
      <div class="u">${esc(a.url)}</div>
      ${facts ? `<div class="facts">${facts}</div>` : ""}
      ${a.errors?.length ? `<div class="errs">${a.errors.map(esc).join("<br>")}</div>` : ""}`;

    if (a.text_blocks?.length) {
      const d = document.createElement("details");
      d.innerHTML = `<summary>Extracted text (${a.text_blocks.length} block${a.text_blocks.length > 1 ? "s" : ""})</summary>` +
        a.text_blocks.map((b) =>
          `<div class="block"><div class="bl">${esc(b.label)}${b.method ? " · " + esc(b.method) : ""}</div>${esc(b.text)}</div>`).join("");
      el.appendChild(d);
    }

    const notes = (a.vision_notes || []).filter((n) => n.description || n.skipped);
    if (notes.length) {
      const d = document.createElement("details");
      d.innerHTML = `<summary>Vision notes (${notes.length})</summary>`;
      for (const n of notes) {
        const isShot = n.kind === "screenshot";
        const src = isShot ? fileUrl(job.id, n.ref) : null;
        const w = document.createElement("div");
        w.className = "vnote";
        // No loading="lazy": these sit inside collapsed <details> (already
        // deferred) and are served from localhost, and lazy proved unreliable
        // at actually firing here — a shown-but-never-loaded image is worse
        // than fetching a few MB locally.
        w.innerHTML =
          (src ? `<img src="${esc(src)}" alt="screenshot"
                     onclick="window.open('${esc(src)}','_blank')">` : "") +
          `<div class="d">${n.description ? esc(n.description)
            : `<span class="hint">skipped — ${esc(n.skipped)}</span>`}</div>`;
        d.appendChild(w);
      }
      el.appendChild(d);
    }
    box.appendChild(el);
  }
}

function slug() {
  return ($("#entity").value.trim() || "dossier").replace(/[^a-z0-9]+/gi, "-").toLowerCase();
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

function download() {
  saveBlob(new Blob([state.markdown], { type: "text/markdown" }), `${slug()}.md`);
}

async function downloadPdf() {
  if (!state.jobId) return;
  const btn = $("#downloadpdf");
  const label = btn.textContent;
  btn.disabled = true; btn.textContent = "Generating…";
  try {
    const r = await fetch(`/api/jobs/${state.jobId}/pdf`);
    if (!r.ok) {
      let e; try { e = (await r.json()).error; } catch { e = null; }
      throw e || { reason: "pdfFailed", message: `PDF generation failed (${r.status}).` };
    }
    saveBlob(await r.blob(), `${slug()}.pdf`);
  } catch (e) {
    showError(e);
  } finally {
    btn.disabled = false; btn.textContent = label;
  }
}

// ------------------------------------------------------------- recent jobs

async function loadRecent() {
  try {
    const { jobs: list } = await api("/api/jobs");
    if (!list.length) return;
    $("#recent").innerHTML = list.slice(0, 8).map((j) => {
      const name = j.inputs?.entity_name || j.inputs?.links?.[0] || "(unnamed)";
      const when = new Date(j.created_at * 1000).toLocaleString();
      return `<div class="rec">
        <span>${j.dossier_available ? `<a data-id="${esc(j.id)}">${esc(name)}</a>` : esc(name)}
          <span class="hint">· ${esc(j.status)} · ${(j.inputs?.links || []).length} link(s)</span></span>
        <span class="hint">${esc(when)}</span></div>`;
    }).join("");
    for (const a of document.querySelectorAll("#recent a[data-id]")) {
      a.onclick = async () => {
        try {
          const j = await api(`/api/jobs/${a.dataset.id}`);
          state.jobId = j.id;
          $("#progress").classList.add("hidden");
          clearError();
          await showResult(j);
          window.scrollTo({ top: 0, behavior: "smooth" });
        } catch (e) { showError(e); }
      };
    }
    $("#recentwrap").classList.remove("hidden");
  } catch { /* recent list is a nicety; never block the app on it */ }
}

// ------------------------------------------------------------------ wiring

$("#addlink").onclick = () => addLinkRow().focus();
$("#go").onclick = submit;
$("#download").onclick = download;
$("#downloadpdf").onclick = downloadPdf;
$("#printpdf").onclick = () => window.print();
$("#toggleraw").onclick = () => {
  state.raw = !state.raw;
  $("#rawmd").classList.toggle("hidden", !state.raw);
  $("#dossier").classList.toggle("hidden", state.raw);
  $("#toggleraw").textContent = state.raw ? "View rendered" : "View raw markdown";
};
$("#entity").addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });

addLinkRow();
loadRecent();
