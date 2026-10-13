// ClipForge UI. Plain JavaScript, no build step.

const main = document.getElementById("main");

// ------------------------------------------------------------------ helpers
function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "list" || k === "for" || k === "download" || k.startsWith("aria-") || k.startsWith("data-"))
      el.setAttribute(k, v === true ? "" : v);
    else if (k in el) { try { el[k] = v; } catch { el.setAttribute(k, v); } }
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

async function api(path, opts = {}) {
  const o = { method: opts.method || "GET", headers: {} };
  if (opts.json !== undefined) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(opts.json); }
  if (opts.body) o.body = opts.body;
  const r = await fetch("/api" + path, o);
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) {
    const d = data && data.detail;
    throw new Error(typeof d === "string" ? d : d ? JSON.stringify(d) : (typeof data === "string" && data) || r.statusText);
  }
  return data;
}

function toast(msg, isErr = false) {
  const t = h("div", { class: "toast" + (isErr ? " err" : ""), role: isErr ? "alert" : "status" }, msg);
  document.getElementById("toasts").append(t);
  setTimeout(() => t.remove(), isErr ? 8000 : 3800);
}
const guard = (fn) => async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message || String(e), true); } };

const fmtTime = (s) => {
  s = Math.max(0, Number(s) || 0);
  const m = Math.floor(s / 60);
  return `${m}:${(s - m * 60).toFixed(1).padStart(4, "0")}`;
};
function parseTime(str) {
  str = String(str).trim();
  if (!str) return NaN;
  if (!str.includes(":")) return parseFloat(str);
  const parts = str.split(":").map(parseFloat);
  if (parts.some(isNaN)) return NaN;
  return parts.reduce((acc, p) => acc * 60 + p, 0);
}
const fmtDur = (s) => {
  s = Math.round(s || 0);
  const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
  return hh ? `${hh}h ${String(mm).padStart(2, "0")}m` : mm ? `${mm}m ${String(ss).padStart(2, "0")}s` : `${ss}s`;
};
const fmtBytes = (n) => {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return (n / 1024 ** i).toFixed(i > 1 ? 1 : 0) + " " + u[i];
};
const chip = (text, kind = "") => h("span", { class: "chip " + kind }, text);
const bar = (pct, indeterminate = false) =>
  h("div", { class: "bar" + (indeterminate ? " indeterminate" : ""), role: "progressbar", "aria-valuenow": Math.round(pct) },
    h("i", { style: { width: Math.max(0, Math.min(100, pct)) + "%" } }));
function setBar(el, pct) { el.firstChild.style.width = Math.max(0, Math.min(100, pct)) + "%"; el.setAttribute("aria-valuenow", Math.round(pct)); }

function modal(content) {
  const overlay = h("div", { class: "overlay", onclick: (e) => { if (e.target === overlay) close(); } },
    h("div", { class: "modal", role: "dialog", "aria-modal": "true" }, content));
  const onKey = (e) => { if (e.key === "Escape") close(); };
  function close() { overlay.remove(); document.removeEventListener("keydown", onKey); }
  document.addEventListener("keydown", onKey);
  document.body.append(overlay);
  return close;
}

const ACTIVE = ["queued", "downloading", "audio", "transcribing", "analyzing"];

// ------------------------------------------------------------------ router
let current = null;
async function route() {
  if (current && current.destroy) current.destroy();
  const hash = location.hash.replace(/^#/, "") || "/";
  const [path, qs] = hash.split("?");
  const params = new URLSearchParams(qs || "");
  document.querySelectorAll(".nav").forEach((a) => {
    const r = a.dataset.route;
    a.classList.toggle("active", r === path || (r === "/" && path.startsWith("/video")));
  });
  let page;
  if (path.startsWith("/video/")) page = VideoPage(parseInt(path.split("/")[2], 10));
  else if (path === "/library") page = LibraryPage();
  else if (path === "/models") page = ModelsPage();
  else if (path === "/settings") page = SettingsPage(params);
  else page = HomePage();
  current = page;
  main.replaceChildren(page.el);
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);

// ------------------------------------------------------------------ Home
function HomePage() {
  const ta = h("textarea", { placeholder: "Paste YouTube links, one per line", "aria-label": "YouTube links" });
  const file = h("input", { type: "file", accept: "video/*,audio/*" });
  const go = h("button", { class: "btn primary", onclick: guard(start) }, "Find clips");
  const banner = h("div");
  const list = h("div", { class: "vlist" });
  const el = h("div", {},
    h("h1", {}, "Turn long videos into short clips"),
    h("p", { class: "sub" }, "Paste links or upload a file. ClipForge transcribes each video, finds the strongest moments, and lets you choose which ones to render."),
    banner,
    h("div", { class: "panel intake" }, ta,
      h("div", { class: "row between", style: { marginTop: "12px" } },
        h("label", { class: "row small muted" }, "Or upload a file", file), go)),
    list);

  async function start() {
    const urls = ta.value.split(/\s+/).map((s) => s.trim()).filter((s) => /^https?:\/\//i.test(s));
    if (!urls.length && !file.files[0]) { toast("Paste at least one link that starts with https://, or choose a file.", true); return; }
    go.disabled = true;
    try {
      if (urls.length) { await api("/videos", { method: "POST", json: { urls } }); ta.value = ""; }
      if (file.files[0]) {
        const fd = new FormData(); fd.append("file", file.files[0]);
        await api("/videos/upload", { method: "POST", body: fd });
        file.value = "";
      }
      toast("Added to the queue.");
      await refresh();
    } finally { go.disabled = false; }
  }

  function card(v) {
    const active = ACTIVE.includes(v.status);
    const kind = v.status === "ready" ? "ok" : v.status === "failed" ? "err" : active ? "info" : "";
    const label = v.status === "ready" ? v.stage_label : v.status === "failed" ? "Failed" : v.status === "cancelled" ? "Cancelled" : v.stage_label;
    const act = h("button", {
      class: "btn sm ghost", onclick: guard(async (e) => {
        e.preventDefault(); e.stopPropagation();
        if (active) { await api(`/videos/${v.id}/cancel`, { method: "POST" }); }
        else if (confirm("Remove this video and its suggestions? Clips you already rendered stay in the library.")) {
          await api(`/videos/${v.id}`, { method: "DELETE" });
        }
        refresh();
      })
    }, active ? "Cancel" : "Remove");
    return h("a", { class: "vcard", href: `#/video/${v.id}` },
      v.has_thumb ? h("img", { class: "thumb", src: `/api/videos/${v.id}/thumb`, alt: "", loading: "lazy" }) : h("div", { class: "thumb" }),
      h("div", {},
        h("div", { class: "title" }, v.title || v.url),
        h("div", { class: "meta" }, chip(label, kind), v.duration ? h("span", { class: "small muted" }, fmtDur(v.duration)) : null,
          active ? bar(v.progress * 100, v.status === "queued") : null),
        v.status === "failed" && v.error ? h("div", { class: "small", style: { color: "var(--err)", marginTop: "6px" } }, v.error) : null),
      act);
  }

  async function refresh() {
    try {
      const { videos } = await api("/videos");
      list.replaceChildren(...(videos.length ? videos.map(card)
        : [h("div", { class: "empty" }, "No videos yet. Paste a link above to get started.")]));
    } catch (e) { /* server restarting */ }
  }

  async function checkSetup() {
    try {
      const hl = await api("/health");
      const issues = [];
      if (!hl.ffmpeg) issues.push("ffmpeg is not installed on this server.");
      if (!hl.faster_whisper) issues.push("faster-whisper is not installed. Run: pip install faster-whisper");
      else if (!hl.whisper_ready) issues.push(`The speech model "${hl.whisper_model}" is not downloaded yet.`);
      if (!hl.llm) issues.push(hl.llm_message);
      else if (hl.llm_provider === "ollama") {
        if (!hl.filter_model && !hl.ranker_model) issues.push("No language model is selected yet.");
        else for (const m of [hl.filter_model, hl.ranker_model]) {
          if (m && !hl.llm_models.includes(m) && !hl.llm_models.includes(m + ":latest")) issues.push(`The model "${m}" is not installed in Ollama.`);
        }
      } else if (!hl.filter_model && !hl.ranker_model) issues.push("No language model name is set in Settings.");
      const uniq = [...new Set(issues)];
      banner.replaceChildren(uniq.length ? h("div", { class: "banner" },
        h("strong", {}, "Finish setting up before your first run"),
        h("ul", {}, uniq.map((i) => h("li", {}, i))),
        h("div", { style: { marginTop: "8px" } }, h("a", { class: "btn sm", href: "#/models" }, "Open Models"))) : "");
    } catch (e) { /* ignore */ }
  }

  refresh(); checkSetup();
  const timer = setInterval(refresh, 2000);
  return { el, destroy() { clearInterval(timer); } };
}

// ------------------------------------------------------------------ Video review
function VideoPage(id) {
  const root = h("div");
  let data = null, signature = "", timer = null, stopped = false;
  let refs = null;       // for the "processing" view
  let cardRefs = new Map(); // clip id -> {update(clip)}
  let player = null, rafId = null, defaults = null, updateRenderBar = () => {};

  const STEPS = [["downloading", "Download the video"], ["audio", "Extract the audio"], ["transcribing", "Transcribe the speech"], ["analyzing", "Find the best moments"]];

  async function load() {
    if (stopped) return;
    try { data = await api(`/videos/${id}`); }
    catch (e) { root.replaceChildren(h("div", { class: "empty" }, "This video was not found."), h("p", {}, h("a", { href: "#/" }, "Back to all videos"))); clearInterval(timer); return; }
    const v = data.video;
    const mode = v.status === "ready" ? "ready" : ["failed", "cancelled"].includes(v.status) ? "failed" : "processing";
    const sig = mode + ":" + (mode === "ready" ? data.clips.map((c) => c.id).join(",") : "");
    if (sig !== signature) { signature = sig; build(mode); } else update(mode);
  }

  // ---- processing
  function buildProcessing() {
    const v = data.video;
    const label = h("div", { class: "muted" });
    const b = bar(0);
    const steps = STEPS.map(([k, t]) => h("div", { class: "step" }, h("span", { class: "dot" }), t));
    refs = { label, b, steps };
    root.replaceChildren(
      h("h1", {}, v.title || v.url),
      h("p", { class: "sub" }, "ClipForge is working on this video. You can leave this page; it keeps running."),
      h("div", { class: "panel" }, label, h("div", { style: { margin: "10px 0 4px" } }, b), h("div", { class: "steps" }, steps),
        h("div", { style: { marginTop: "16px" } }, h("button", { class: "btn", onclick: guard(async () => { await api(`/videos/${id}/cancel`, { method: "POST" }); load(); }) }, "Cancel"))));
    updateProcessing();
  }
  function updateProcessing() {
    const v = data.video;
    refs.label.textContent = v.status === "queued" ? "Waiting for the previous job to finish" : v.stage_label;
    setBar(refs.b, v.progress * 100);
    const idx = STEPS.findIndex(([k]) => k === v.status);
    refs.steps.forEach((s, i) => { s.className = "step" + (i < idx ? " done" : i === idx ? " now" : ""); });
  }

  // ---- failed
  function buildFailed() {
    const v = data.video;
    root.replaceChildren(
      h("h1", {}, v.title || v.url),
      h("div", { class: "banner err" }, h("strong", {}, v.status === "cancelled" ? "Cancelled" : "This video could not be processed"),
        v.error ? h("p", { style: { margin: "6px 0 0" } }, v.error) : null),
      h("div", { class: "row" },
        h("button", { class: "btn primary", onclick: guard(async () => { await api(`/videos/${id}/retry`, { method: "POST" }); load(); }) }, "Retry"),
        h("a", { class: "btn", href: "#/models" }, "Check models"), h("a", { class: "btn", href: "#/settings" }, "Open settings")));
  }

  // ---- ready
  async function buildReady() {
    const v = data.video;
    if (!defaults) { try { defaults = (await api("/settings")).settings; } catch { defaults = {}; } }
    cardRefs = new Map();
    player = h("video", { class: "player", controls: true, preload: "metadata", src: `/api/videos/${id}/source` });
    const head = h("div", { class: "head", style: { left: "0%" } });
    const tl = h("div", { class: "timeline", title: "Click to jump. Bars show where suggested clips are; taller means a higher score.", onclick: (e) => {
      if (e.target !== tl && e.target !== head) return;
      const r = tl.getBoundingClientRect(); player.currentTime = ((e.clientX - r.left) / r.width) * dur();
    } }, head);
    const dur = () => v.duration || player.duration || 1;
    player.addEventListener("timeupdate", () => { head.style.left = (player.currentTime / dur()) * 100 + "%"; });

    const segs = new Map();
    function drawSeg(c) {
      let s = segs.get(c.id);
      if (!s) {
        s = h("div", { class: "seg", title: c.title, onclick: (ev) => { ev.stopPropagation(); const f = cardRefs.get(c.id); if (f) { f.el.scrollIntoView({ behavior: "smooth", block: "center" }); preview(c); } } });
        segs.set(c.id, s); tl.append(s);
      }
      s.style.left = (c.start / dur()) * 100 + "%";
      s.style.width = ((c.end - c.start) / dur()) * 100 + "%";
      s.style.height = (c.score != null ? 25 + (Math.min(c.score, 10) / 10) * 75 : 40) + "%";
      s.classList.toggle("sel", !!c.selected);
    }
    player.addEventListener("loadedmetadata", () => data.clips.forEach(drawSeg));

    function preview(c) {
      cancelAnimationFrame(rafId);
      player.currentTime = c.start; player.play().catch(() => {});
      const tick = () => { if (player.paused) return; if (player.currentTime >= c.end) { player.pause(); return; } rafId = requestAnimationFrame(tick); };
      rafId = requestAnimationFrame(tick);
    }

    function makeCard(c) {
      const sel = h("input", { type: "checkbox", checked: !!c.selected, "aria-label": "Select clip", onchange: guard(async () => {
        c.selected = sel.checked; el.classList.toggle("selected", c.selected); drawSeg(c); updateRenderBar();
        await api(`/clips/${c.id}`, { method: "PATCH", json: { selected: c.selected } });
      }) });
      const title = h("input", { type: "text", class: "title", value: c.title, "aria-label": "Clip title", onchange: guard(async () => {
        await api(`/clips/${c.id}`, { method: "PATCH", json: { title: title.value } });
      }) });
      const durEl = h("span", { class: "small muted mono" });
      const mkTime = (key) => {
        const inp = h("input", { type: "text", value: fmtTime(c[key]), "aria-label": key === "start" ? "Start time" : "End time", onchange: guard(async () => {
          const t = parseTime(inp.value);
          if (isNaN(t)) { inp.value = fmtTime(c[key]); toast("Use a time like 1:23.5 or 83.5", true); return; }
          try {
            const r = await api(`/clips/${c.id}`, { method: "PATCH", json: { [key]: t } });
            c.start = r.start; c.end = r.end;
          } catch (e) { toast(e.message, true); }
          inp.value = fmtTime(c[key]); refreshTimes(); drawSeg(c);
        }) });
        return inp;
      };
      const sInp = mkTime("start"), eInp = mkTime("end");
      function refreshTimes() { sInp.value = fmtTime(c.start); eInp.value = fmtTime(c.end); durEl.textContent = `${Math.round(c.end - c.start)}s`; }
      const useNow = (inp) => h("button", { class: "btn sm ghost", title: "Use the current player position", onclick: () => { inp.value = fmtTime(player.currentTime); inp.dispatchEvent(new Event("change")); } }, "Use playhead");
      const status = h("div", { class: "row" });
      const statusBar = bar(0);
      const err = h("div", { class: "small", style: { color: "var(--err)" } });

      function update(nc) {
        Object.assign(c, nc);
        const st = c.status;
        status.replaceChildren();
        if (st === "queued") status.append(chip("Queued", "info"));
        else if (st === "rendering") status.append(chip("Rendering " + Math.round(c.progress * 100) + "%", "info"));
        else if (st === "ready") status.append(chip("Rendered", "ok"), h("a", { class: "small", href: "#/library" }, "View in library"));
        else if (st === "failed") status.append(chip("Failed", "err"));
        statusBar.style.display = st === "rendering" || st === "queued" ? "" : "none";
        setBar(statusBar, c.progress * 100);
        err.textContent = st === "failed" ? (c.error || "") : "";
      }

      const el = h("div", { class: "cand" + (c.selected ? " selected" : "") },
        h("div", { class: "col" }, sel, h("div", { class: "score", title: "Combined score out of 10" }, c.score != null ? c.score.toFixed(1) : "–"),
          c.rank ? h("div", { class: "small muted" }, "#" + c.rank) : null),
        h("div", { class: "stack", style: { gap: "8px", minWidth: 0 } },
          title,
          c.hook ? h("div", { class: "hook" }, "“" + c.hook + "”") : null,
          c.reason ? h("div", { class: "small muted" }, c.reason) : null,
          h("div", { class: "times" }, h("span", { class: "small muted" }, "Start"), sInp, useNow(sInp),
            h("span", { class: "small muted" }, "End"), eInp, useNow(eInp), durEl,
            h("button", { class: "btn sm", onclick: () => preview(c) }, "Preview")),
          status, statusBar, err));
      refreshTimes(); update(c);
      cardRefs.set(c.id, { el, update });
      drawSeg(c);
      return el;
    }

    const list = h("div", { class: "cands" }, data.clips.length ? data.clips.map(makeCard)
      : h("div", { class: "empty" }, "No suggestions yet. Add a clip at the playhead, or press Find again."));

    const sels = () => data.clips.filter((c) => c.selected);
    const aspect = h("select", { "aria-label": "Shape" }, [["9:16", "Vertical 9:16"], ["1:1", "Square 1:1"], ["4:5", "Portrait 4:5"], ["16:9", "Wide 16:9"]].map(([v2, t]) => h("option", { value: v2, selected: v2 === (defaults.aspect || "9:16") }, t)));
    const reframe = h("select", { "aria-label": "Framing" }, [["face", "Follow the speaker"], ["center", "Center crop"], ["blur", "Fit with blurred background"]].map(([v2, t]) => h("option", { value: v2, selected: v2 === (defaults.reframe || "face") }, t)));
    const caps = h("input", { type: "checkbox", checked: defaults.captions !== false, id: "caps" });
    const renderBtn = h("button", { class: "btn primary", onclick: guard(async () => {
      const ids = sels().map((c) => c.id);
      if (!ids.length) return;
      const r = await api(`/videos/${id}/render`, { method: "POST", json: { clip_ids: ids, aspect: aspect.value, reframe: reframe.value, captions: caps.checked } });
      toast(r.queued ? `${r.queued} clip${r.queued > 1 ? "s" : ""} queued for rendering.` : "Those clips are already rendering.");
      load();
    }) }, "Render");
    updateRenderBar = () => { const n = sels().length; renderBtn.textContent = n ? `Render ${n} clip${n > 1 ? "s" : ""}` : "Select clips to render"; renderBtn.disabled = !n; };
    updateRenderBar();

    const setAll = guard(async (pick) => {
      for (const c of data.clips) {
        const want = pick(c);
        if (want !== !!c.selected) {
          await api(`/clips/${c.id}`, { method: "PATCH", json: { selected: want } });
          c.selected = want;
          const f = cardRefs.get(c.id); f.el.classList.toggle("selected", want); f.el.querySelector("input[type=checkbox]").checked = want; drawSeg(c);
        }
      }
      updateRenderBar();
    });

    const addAtPlayhead = guard(async () => {
      const t = player.currentTime || 0;
      await api("/clips", { method: "POST", json: { video_id: id, start: t, end: Math.min(t + 45, dur()), title: "Custom clip" } });
      load();
    });

    root.replaceChildren(
      h("div", { class: "row between" },
        h("div", {}, h("h1", {}, v.title || v.url), h("p", { class: "sub", style: { marginBottom: "16px" } }, `${fmtDur(v.duration)} long. Pick the clips you want, adjust their start and end if needed, then render.`)),
        h("a", { class: "btn", href: "#/" }, "All videos")),
      h("div", { class: "review" },
        h("div", { class: "player-wrap" }, player, tl,
          h("div", { class: "row", style: { marginTop: "12px" } },
            h("button", { class: "btn sm", onclick: addAtPlayhead }, "Add clip at playhead"),
            h("button", { class: "btn sm ghost", onclick: guard(async () => { if (confirm("Search again with your current settings? Suggestions you have not rendered will be replaced.")) { await api(`/videos/${id}/retry`, { method: "POST" }); load(); } }) }, "Find again"))),
        h("div", {},
          h("div", { class: "row between", style: { marginBottom: "12px" } },
            h("h2", {}, `${data.clips.length} suggested clip${data.clips.length === 1 ? "" : "s"}`),
            h("div", { class: "row" },
              h("button", { class: "btn sm", onclick: () => setAll((c) => c.rank != null && c.rank <= 5) }, "Select top 5"),
              h("button", { class: "btn sm ghost", onclick: () => setAll(() => false) }, "Clear"))),
          list,
          h("div", { class: "renderbar row between" },
            h("div", { class: "row" }, aspect, reframe, h("label", { class: "row small", for: "caps" }, caps, "Captions")),
            renderBtn))));
  }

  function updateReady() { for (const c of data.clips) { const f = cardRefs.get(c.id); if (f) f.update(c); } }

  function build(mode) {
    cancelAnimationFrame(rafId);
    if (mode === "processing") buildProcessing();
    else if (mode === "failed") buildFailed();
    else buildReady();
  }
  function update(mode) { if (mode === "processing") updateProcessing(); else if (mode === "ready") updateReady(); }

  load();
  timer = setInterval(load, 1500);
  return { el: root, destroy() { stopped = true; clearInterval(timer); cancelAnimationFrame(rafId); if (player) player.pause(); } };
}

// ------------------------------------------------------------------ Library
function LibraryPage() {
  const grid = h("div", { class: "grid-clips" });
  const folder = h("code", {});
  const el = h("div", {},
    h("h1", {}, "Library"),
    h("p", { class: "sub" }, "Your rendered clips. Download them or send them straight to YouTube."),
    h("div", { class: "panel row between", style: { marginBottom: "18px" } },
      h("div", {}, h("span", { class: "muted" }, "Saved on the server in "), folder),
      h("a", { class: "small", href: "#/settings" }, "Change folder")),
    grid);
  let sig = "", refs = new Map(), timer;

  async function openUpload(c) {
    let yt = { connected: false };
    let st = {};
    try { yt = await api("/youtube/status"); st = (await api("/settings")).settings; } catch (e) { toast(e.message, true); return; }
    if (!yt.connected) {
      const close = modal(h("div", { class: "stack" }, h("h2", {}, "Connect YouTube first"),
        h("p", { class: "muted" }, "Add your Google OAuth keys and sign in once in Settings. After that you can upload with one click."),
        h("div", { class: "row end" }, h("button", { class: "btn", onclick: () => close() }, "Close"), h("a", { class: "btn primary", href: "#/settings", onclick: () => close() }, "Open settings"))));
      return;
    }
    const title = h("input", { type: "text", value: c.title, maxLength: 100 });
    const desc = h("textarea", {}, c.hook ? c.hook : "");
    const tags = h("input", { type: "text", placeholder: "podcast, interview, highlights" });
    const priv = h("select", {}, ["private", "unlisted", "public"].map((p) => h("option", { value: p, selected: p === (st.yt_privacy || "private") }, p[0].toUpperCase() + p.slice(1))));
    const shorts = h("input", { type: "checkbox", checked: st.yt_add_shorts_tag !== false });
    const go = h("button", { class: "btn primary", onclick: guard(async () => {
      go.disabled = true;
      try {
        await api(`/clips/${c.id}/youtube`, { method: "POST", json: { title: title.value, description: desc.value, tags: tags.value.split(",").map((t) => t.trim()).filter(Boolean), privacy: priv.value, add_shorts_tag: shorts.checked } });
        close(); toast("Upload started."); load();
      } finally { go.disabled = false; }
    }) }, "Upload");
    const close = modal(h("div", { class: "stack" }, h("h2", {}, "Upload to YouTube"),
      h("label", { class: "field" }, h("span", { class: "lbl" }, "Title"), title),
      h("label", { class: "field" }, h("span", { class: "lbl" }, "Description"), desc),
      h("label", { class: "field" }, h("span", { class: "lbl" }, "Tags (comma separated)"), tags),
      h("label", { class: "field" }, h("span", { class: "lbl" }, "Visibility"), priv,
        h("span", { class: "hint" }, "YouTube forces uploads from unverified Google projects to Private until the project passes Google's audit. See Settings.")),
      h("label", { class: "check" }, shorts, h("span", {}, "Add #Shorts to vertical clips")),
      h("div", { class: "row end" }, h("button", { class: "btn", onclick: () => close() }, "Cancel"), go)));
  }

  function card(c) {
    const wide = c.width > c.height;
    const ytBox = h("div", { class: "small" });
    const ytBar = bar(0);
    const upBtn = h("button", { class: "btn sm", onclick: () => openUpload(c) }, "Upload to YouTube");
    function upd(nc) {
      Object.assign(c, nc);
      ytBox.replaceChildren();
      if (c.yt_status === "uploading") { ytBox.append(chip("Uploading " + Math.round(c.yt_progress * 100) + "%", "info")); }
      else if (c.yt_status === "done") ytBox.append(chip("Uploaded", "ok"), " ", h("a", { href: `https://youtu.be/${c.yt_id}`, target: "_blank", rel: "noopener" }, "Open on YouTube"));
      else if (c.yt_status === "failed") ytBox.append(chip("Upload failed", "err"), h("div", { style: { color: "var(--err)", marginTop: "4px" } }, c.yt_error || ""));
      ytBar.style.display = c.yt_status === "uploading" ? "" : "none";
      setBar(ytBar, (c.yt_progress || 0) * 100);
      upBtn.disabled = c.yt_status === "uploading";
      upBtn.textContent = c.yt_status === "done" ? "Upload again" : "Upload to YouTube";
    }
    const del = h("button", { class: "btn sm danger ghost", onclick: guard(async () => {
      if (!confirm("Delete this clip file from the server?")) return;
      await api(`/clips/${c.id}`, { method: "DELETE" }); load();
    }) }, "Delete");
    const el = h("div", { class: "clip" },
      h("video", { class: wide ? "wide" : "", controls: true, preload: "metadata", poster: c.has_thumb ? `/api/clips/${c.id}/thumb` : null, src: `/api/clips/${c.id}/file` }),
      h("div", { class: "body" },
        h("div", { class: "ttl" }, c.title),
        h("div", { class: "small muted" }, `${Math.round(c.end - c.start)}s · ${c.width}×${c.height} · ${fmtBytes(c.size)}`),
        h("div", { class: "small muted", style: { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }, title: c.video_title }, "From: " + (c.video_title || "")),
        ytBox, ytBar,
        h("div", { class: "actions" },
          h("a", { class: "btn sm", href: `/api/clips/${c.id}/file?download=1`, download: "" }, "Download"), upBtn, del)));
    upd(c);
    refs.set(c.id, upd);
    return el;
  }

  async function load() {
    let r;
    try { r = await api("/clips?status=ready"); } catch { return; }
    folder.textContent = r.folder;
    const s = r.clips.map((c) => c.id + ":" + (c.yt_status === "uploading" ? "up" : c.yt_status || "")).join("|");
    if (s !== sig) {
      sig = s; refs = new Map();
      grid.replaceChildren(...(r.clips.length ? r.clips.map(card) : [h("div", { class: "empty", style: { gridColumn: "1 / -1" } }, "Nothing here yet. Render clips from a video and they will appear here.")]));
    } else r.clips.forEach((c) => refs.get(c.id) && refs.get(c.id)(c));
  }
  load();
  timer = setInterval(load, 2500);
  return { el, destroy() { clearInterval(timer); } };
}

// ------------------------------------------------------------------ Models
const RECOMMENDED = [
  { name: "gpt-oss:20b", fit: "16 GB GPU", note: "Fast mixture-of-experts model. A good default ranker on 16 GB cards." },
  { name: "qwen3:14b", fit: "12-16 GB GPU", note: "Reliable JSON output. Strong all-rounder." },
  { name: "qwen3:8b", fit: "8-12 GB GPU", note: "Light and quick. Good for the first pass." },
  { name: "gemma3:12b", fit: "12-16 GB GPU", note: "Good writing quality for titles and hooks." },
  { name: "qwen3:30b", fit: "24 GB GPU", note: "Mixture-of-experts. High quality at good speed." },
  { name: "qwen3:32b", fit: "24 GB GPU", note: "Best quality for a single 24 GB card." },
  { name: "gemma3:27b", fit: "24 GB GPU", note: "Strong at judging tone and humor." },
];

function ModelsPage() {
  let settings = {}, ollama = { ok: false, models: [] }, whisper = { models: [] }, status = { ollama: {}, whisper: {} }, health = null;
  const sysBox = h("div", { class: "panel" });
  const ollamaBox = h("div", { class: "panel" });
  const whisperBox = h("div", { class: "panel" });
  const pullInput = h("input", { type: "text", placeholder: "Model name, e.g. qwen3:14b", "aria-label": "Model to download", list: "installed-models" });
  const filterIn = h("input", { type: "text", list: "installed-models", placeholder: "Model name", "aria-label": "First pass model" });
  const rankerIn = h("input", { type: "text", list: "installed-models", placeholder: "Model name", "aria-label": "Ranking model" });
  const dl = h("datalist", { id: "installed-models" });
  const listBox = h("div"), pullsBox = h("div"), recBox = h("div");
  let timer, busyPoll = false;

  const el = h("div", {},
    h("h1", {}, "Models"),
    h("p", { class: "sub" }, "Download and choose the models ClipForge uses. Downloads run on the server, so you can close this page."),
    sysBox, ollamaBox, whisperBox, dl);

  ollamaBox.append(
    h("header", {}, h("h2", {}, "Language model"), h("p", { class: "hint", style: { margin: 0 } }, "Finds and ranks the best moments. Runs through Ollama on this server.")),
    h("div", { id: "ollama-status" }), pullsBox, listBox,
    h("h3", { style: { margin: "18px 0 6px" } }, "Download a model"),
    h("div", { class: "row" }, h("div", { style: { flex: 1, minWidth: "220px" } }, pullInput),
      h("button", { class: "btn primary", onclick: guard(() => pull(pullInput.value)) }, "Download")),
    h("p", { class: "hint" }, "Any tag from ollama.com/library works. Large models can take a while on a slow connection."),
    recBox,
    h("h3", { style: { margin: "18px 0 6px" } }, "Which model does what"),
    h("div", { class: "fields" },
      h("label", { class: "field" }, h("span", { class: "lbl" }, "First pass (reads the whole transcript)"), filterIn,
        h("span", { class: "hint" }, "A smaller, faster model is fine here.")),
      h("label", { class: "field" }, h("span", { class: "lbl" }, "Ranking (picks the final order)"), rankerIn,
        h("span", { class: "hint" }, "Use your strongest model. Use the same one for both if you only have one."))),
    h("div", { class: "row", style: { marginTop: "12px" } }, h("button", { class: "btn primary", onclick: guard(saveRoles) }, "Save choices")));

  async function saveRoles() {
    await api("/settings", { method: "POST", json: { filter_model: filterIn.value.trim(), ranker_model: rankerIn.value.trim() } });
    settings.filter_model = filterIn.value.trim(); settings.ranker_model = rankerIn.value.trim();
    toast("Model choices saved."); renderOllama();
  }
  async function pull(name) {
    name = (name || "").trim();
    if (!name) { toast("Type a model name first.", true); return; }
    await api("/models/ollama/pull", { method: "POST", json: { name } });
    pullInput.value = ""; await refreshStatus(); poll();
  }
  const installed = (name) => ollama.models.some((m) => m.name === name || m.name === name + ":latest");

  function pullRow(name, st, onRetry) {
    const failed = !!st.error;
    return h("div", { class: "model" },
      h("div", {}, h("strong", {}, name), " ", h("span", { class: "small muted" }, failed ? "" : (st.status || "")),
        st.total ? h("span", { class: "small muted" }, ` ${fmtBytes(st.completed)} of ${fmtBytes(st.total)}`) : null),
      failed ? chip("Failed", "err") : chip(Math.round(st.percent || 0) + "%", "info"),
      failed ? h("div", { class: "small", style: { color: "var(--err)", gridColumn: "1 / -1" } }, st.error) : bar(st.percent || 0, !st.percent));
  }

  function renderOllama() {
    const stEl = ollamaBox.querySelector("#ollama-status");
    if (settings.llm_provider === "openai") {
      stEl.replaceChildren(h("div", { class: "banner" }, "You are using an OpenAI-compatible API. Models are hosted by the provider, so there is nothing to download. Set the model names below and the API details in ",
        h("a", { href: "#/settings" }, "Settings"), "."));
    } else if (!ollama.ok) {
      stEl.replaceChildren(h("div", { class: "banner err" }, h("strong", {}, "Ollama is not reachable"), h("p", { style: { margin: "6px 0" } }, ollama.message || ""),
        h("p", { class: "small muted", style: { margin: 0 } }, "Install it on the server, then start it:"),
        h("pre", { class: "code" }, "curl -fsSL https://ollama.com/install.sh | sh\nollama serve")));
    } else stEl.replaceChildren(h("p", { class: "small muted" }, `Connected to Ollama ${ollama.version || ""} at ${settings.ollama_host}`));

    dl.replaceChildren(...ollama.models.map((m) => h("option", { value: m.name })));
    const act = Object.entries(status.ollama).filter(([, s]) => !s.done);
    pullsBox.replaceChildren(...act.map(([n, s]) => pullRow(n, s)));

    listBox.replaceChildren(...(ollama.models.length ? [
      h("h3", { style: { margin: "14px 0 0" } }, "Installed"),
      ...ollama.models.map((m) => {
        const roles = [];
        if (m.name === settings.filter_model) roles.push(chip("First pass", "ok"));
        if (m.name === settings.ranker_model) roles.push(chip("Ranking", "ok"));
        return h("div", { class: "model" },
          h("div", {}, h("strong", {}, m.name), " ", h("span", { class: "small muted" }, [m.params, m.quant, fmtBytes(m.size)].filter(Boolean).join(" · ")), " ", roles),
          h("div", { class: "row" },
            h("button", { class: "btn sm", onclick: guard(() => setRole("filter_model", m.name, filterIn)) }, "Use for first pass"),
            h("button", { class: "btn sm", onclick: guard(() => setRole("ranker_model", m.name, rankerIn)) }, "Use for ranking"),
            h("button", { class: "btn sm danger ghost", onclick: guard(async () => { if (confirm(`Remove ${m.name} from this server?`)) { await api("/models/ollama?name=" + encodeURIComponent(m.name), { method: "DELETE" }); await loadAll(); } }) }, "Remove")));
      })] : (ollama.ok ? [h("p", { class: "muted" }, "No models installed yet. Pick one below.")] : [])));

    recBox.replaceChildren(h("h3", { style: { margin: "18px 0 0" } }, "Recommended"),
      ...RECOMMENDED.map((r) => {
        const have = installed(r.name), busy = status.ollama[r.name] && !status.ollama[r.name].done && !status.ollama[r.name].error;
        return h("div", { class: "model" },
          h("div", {}, h("strong", {}, r.name), " ", chip(r.fit), h("div", { class: "small muted" }, r.note)),
          have ? chip("Installed", "ok") : h("button", { class: "btn sm", disabled: busy || !ollama.ok, onclick: guard(() => pull(r.name)) }, busy ? "Downloading" : "Download"));
      }));
  }
  async function setRole(key, name, input) {
    await api("/settings", { method: "POST", json: { [key]: name } });
    settings[key] = name; input.value = name; renderOllama();
    toast(key === "filter_model" ? `${name} will do the first pass.` : `${name} will do the ranking.`);
  }

  function renderWhisper() {
    whisperBox.replaceChildren(
      h("header", {}, h("h2", {}, "Speech model (Whisper)"), h("p", { class: "hint", style: { margin: 0 } }, "Turns the audio into a transcript with word timings.")),
      !whisper.installed ? h("div", { class: "banner err" }, "faster-whisper is not installed on the server. Run ", h("code", {}, "pip install faster-whisper"), " and restart ClipForge.") : null,
      ...whisper.models.map((m) => {
        const dlst = status.whisper[m.name] || m.download;
        const busy = dlst && !dlst.done && !dlst.error;
        const active = settings.whisper_model === m.name;
        return h("div", { class: "model" },
          h("div", {}, h("strong", {}, m.name), " ", h("span", { class: "small muted" }, `${m.size_mb >= 1000 ? (m.size_mb / 1000).toFixed(1) + " GB" : m.size_mb + " MB"}`), " ",
            active ? chip("In use", "ok") : null, !m.multilingual ? chip("English only") : null, h("div", { class: "small muted" }, m.note)),
          h("div", { class: "row" },
            m.downloaded ? [
              !active ? h("button", { class: "btn sm", onclick: guard(async () => { await api("/settings", { method: "POST", json: { whisper_model: m.name } }); settings.whisper_model = m.name; renderWhisper(); toast(`${m.name} will be used for transcription.`); }) }, "Use this") : null,
              h("button", { class: "btn sm danger ghost", onclick: guard(async () => { if (confirm(`Delete ${m.name}?`)) { await api("/models/whisper/" + m.name, { method: "DELETE" }); await loadAll(); } }) }, "Delete")]
              : h("button", { class: "btn sm", disabled: busy || !whisper.installed, onclick: guard(async () => { await api("/models/whisper/download", { method: "POST", json: { name: m.name } }); await refreshStatus(); poll(); }) }, busy ? "Downloading" : "Download")),
          busy ? bar(dlst.percent || 0, !dlst.percent) : null,
          dlst && dlst.error ? h("div", { class: "small", style: { color: "var(--err)", gridColumn: "1 / -1" } }, dlst.error) : null);
      }));
  }

  function renderSys() {
    if (!health) { sysBox.replaceChildren(h("p", { class: "muted" }, "Checking this server...")); return; }
    const gpu = health.gpus.length ? health.gpus.map((g) => `${g.name} (${(g.vram_total_mb / 1024).toFixed(0)} GB)`).join(", ") : "None detected. Speech recognition will run on the CPU, which is slow.";
    const yes = (ok, t) => chip(t, ok ? "ok" : "err");
    sysBox.replaceChildren(h("header", {}, h("h2", {}, "This server")),
      h("dl", { class: "kv" },
        h("dt", {}, "GPU"), h("dd", {}, gpu),
        h("dt", {}, "ffmpeg"), h("dd", {}, yes(health.ffmpeg, health.ffmpeg ? "Installed" : "Missing")),
        h("dt", {}, "Video encoder"), h("dd", {}, health.encoder === "h264_nvenc" ? "GPU (NVENC)" : health.encoder === "libx264" ? "CPU (x264)" : "Unknown"),
        h("dt", {}, "Downloader"), h("dd", {}, yes(!!health.yt_dlp, health.yt_dlp ? "yt-dlp " + health.yt_dlp : "Missing")),
        h("dt", {}, "Speech engine"), h("dd", {}, yes(!!health.faster_whisper, health.faster_whisper ? "faster-whisper " + health.faster_whisper : "Missing")),
        h("dt", {}, "Language model"), h("dd", {}, yes(health.llm, health.llm ? "Connected" : "Not connected"), " ", h("span", { class: "small muted" }, health.llm ? "" : health.llm_message))));
  }

  async function refreshStatus() {
    const prev = status;
    status = await api("/models/status");
    let changed = false;
    for (const kind of ["ollama", "whisper"]) for (const [k, v] of Object.entries(status[kind])) {
      const p = prev[kind][k];
      if (v.done && (!p || !p.done)) changed = true;
    }
    if (changed) await loadAll(); else { renderOllama(); renderWhisper(); }
  }
  async function loadAll() {
    const [s, o, w, hl] = await Promise.all([api("/settings"), api("/models/ollama"), api("/models/whisper"), api("/health").catch(() => null)]);
    settings = s.settings; ollama = o; whisper = w; health = hl;
    if (document.activeElement !== filterIn) filterIn.value = settings.filter_model;
    if (document.activeElement !== rankerIn) rankerIn.value = settings.ranker_model;
    try { status = await api("/models/status"); } catch { /* ignore */ }
    renderSys(); renderOllama(); renderWhisper();
  }
  function poll() {
    clearInterval(timer);
    timer = setInterval(async () => {
      if (busyPoll) return; busyPoll = true;
      try {
        const active = Object.values(status.ollama).some((s) => !s.done && !s.error) || Object.values(status.whisper).some((s) => !s.done && !s.error);
        if (active) await refreshStatus();
      } catch { /* ignore */ } finally { busyPoll = false; }
    }, 1000);
  }
  loadAll().then(poll);
  return { el, destroy() { clearInterval(timer); } };
}

// ------------------------------------------------------------------ Settings
const SCHEMA = [
  { title: "Language model", desc: "Chooses and ranks the moments. Ollama runs on this server for free. You can also use any OpenAI-compatible API.", fields: [
    { key: "llm_provider", label: "Provider", type: "select", options: [["ollama", "Ollama (on this server)"], ["openai", "OpenAI-compatible API"]] },
    { key: "ollama_host", label: "Ollama address", type: "text", show: (s) => s.llm_provider === "ollama" },
    { key: "openai_base_url", label: "API base URL", type: "text", show: (s) => s.llm_provider === "openai", hint: "For example https://api.groq.com/openai/v1, https://openrouter.ai/api/v1, or https://generativelanguage.googleapis.com/v1beta/openai" },
    { key: "llm_api_key", label: "API key", type: "secret", show: (s) => s.llm_provider === "openai" },
    { key: "filter_model", label: "First-pass model", type: "text", hint: "Reads the whole transcript in sections. Smaller is fine." },
    { key: "ranker_model", label: "Ranking model", type: "text", hint: "Picks the final order. Use your best model." },
    { key: "llm_temperature", label: "Temperature", type: "number", step: 0.05, min: 0, max: 1.5, hint: "Lower is steadier. 0.2 works well." },
    { key: "llm_num_ctx", label: "Context size (tokens)", type: "number", step: 1024, min: 2048, show: (s) => s.llm_provider === "ollama", hint: "Raise this if long sections get cut off. Uses more VRAM." },
    { key: "llm_disable_thinking", label: "Turn off thinking mode", type: "bool", show: (s) => s.llm_provider === "ollama", hint: "Much faster for models that think before answering." },
    { key: "unload_models", label: "Free GPU memory after each video", type: "bool", show: (s) => s.llm_provider === "ollama" },
  ] },
  { title: "Speech to text", desc: "Download models on the Models page.", fields: [
    { key: "whisper_model", label: "Whisper model", type: "text", hint: "tiny, base, small, medium, large-v3, large-v3-turbo or distil-large-v3" },
    { key: "whisper_device", label: "Run on", type: "select", options: [["auto", "Automatic"], ["cuda", "GPU"], ["cpu", "CPU"]] },
    { key: "whisper_compute", label: "Precision", type: "select", options: [["auto", "Automatic"], ["float16", "float16 (GPU)"], ["int8_float16", "int8 + float16 (GPU, saves VRAM)"], ["int8", "int8 (CPU)"]] },
    { key: "language", label: "Spoken language", type: "text", hint: "Leave empty to detect it. Use a code such as en, fr or yo." },
  ] },
  { title: "Choosing clips", desc: "", fields: [
    { key: "min_clip_sec", label: "Shortest clip (seconds)", type: "number", min: 5 },
    { key: "max_clip_sec", label: "Longest clip (seconds)", type: "number", min: 10 },
    { key: "num_candidates", label: "Suggestions per video", type: "number", min: 1, max: 40 },
    { key: "chunk_minutes", label: "Section length for the first pass (minutes)", type: "number", min: 2, max: 30, hint: "Longer sections give the model more context but need more memory." },
    { key: "energy_weight", label: "Loudness influence (0 to 0.5)", type: "number", step: 0.05, min: 0, max: 0.5, hint: "Gives a small boost to loud, energetic moments." },
  ] },
  { title: "Rendering", desc: "", fields: [
    { key: "aspect", label: "Default shape", type: "select", options: [["9:16", "Vertical 9:16"], ["1:1", "Square 1:1"], ["4:5", "Portrait 4:5"], ["16:9", "Wide 16:9"]] },
    { key: "reframe", label: "Default framing", type: "select", options: [["face", "Follow the speaker"], ["center", "Center crop"], ["blur", "Fit with blurred background"]] },
    { key: "captions", label: "Burn in captions by default", type: "bool" },
    { key: "encoder", label: "Video encoder", type: "select", options: [["auto", "Automatic"], ["libx264", "CPU (x264)"], ["h264_nvenc", "NVIDIA GPU (NVENC)"]] },
    { key: "quality", label: "Quality (lower is sharper)", type: "number", min: 14, max: 30, hint: "18 to 23 is a good range." },
    { key: "output_width", label: "Output width (pixels)", type: "number", min: 480, max: 2160, step: 60, hint: "1080 gives 1080×1920 for vertical clips." },
  ] },
  { title: "Captions", desc: "", preview: true, fields: [
    { key: "caption_font", label: "Font", type: "text", hint: "Must be installed on the server, or put a font file in data/fonts." },
    { key: "caption_size", label: "Size", type: "number", min: 30, max: 200 },
    { key: "caption_color", label: "Text colour", type: "color" },
    { key: "caption_highlight", label: "Spoken-word colour", type: "color" },
    { key: "caption_outline", label: "Outline colour", type: "color" },
    { key: "caption_words", label: "Words per line", type: "number", min: 1, max: 8 },
    { key: "caption_position", label: "Height from bottom (%)", type: "number", min: 3, max: 80 },
    { key: "caption_upper", label: "Capital letters", type: "bool" },
  ] },
  { title: "Downloading", desc: "", fields: [
    { key: "max_height", label: "Largest video size", type: "select", options: [["480", "480p"], ["720", "720p"], ["1080", "1080p"], ["1440", "1440p"], ["2160", "4K"]], numeric: true },
    { key: "cookies", label: "YouTube cookies (cookies.txt)", type: "secret-area", hint: "Only needed if YouTube says the server must prove it is not a bot. Export cookies.txt from a logged-in browser and paste it here." },
  ] },
  { title: "Storage", desc: "", fields: [
    { key: "clips_dir", label: "Folder for rendered clips", type: "text", hint: "A path on the server. Leave empty to use the data/clips folder inside ClipForge." },
  ] },
];

function SettingsPage(params) {
  let settings = {}, yt = {};
  const inputs = {};
  const root = h("div");
  const preview = h("div", { class: "cap-preview" });

  function readValue(f) {
    const i = inputs[f.key];
    if (f.type === "bool") return i.checked;
    if (f.type === "number" || f.numeric) return Number(i.value);
    return i.value;
  }
  const current = () => { const s = { ...settings }; for (const f of allFields()) if (inputs[f.key] && !f.type.startsWith("secret")) s[f.key] = readValue(f); return s; };
  const allFields = () => SCHEMA.flatMap((s) => s.fields);

  async function save(silent = false) {
    const body = {};
    for (const f of allFields()) {
      const i = inputs[f.key]; if (!i) continue;
      if (f.type.startsWith("secret")) { if (i.value) body[f.key] = i.value; } else body[f.key] = readValue(f);
    }
    for (const k of ["yt_client_id", "yt_privacy"]) if (inputs[k]) body[k] = inputs[k].value;
    if (inputs.yt_client_secret && inputs.yt_client_secret.value) body.yt_client_secret = inputs.yt_client_secret.value;
    if (inputs.yt_add_shorts_tag) body.yt_add_shorts_tag = inputs.yt_add_shorts_tag.checked;
    const r = await api("/settings", { method: "POST", json: body });
    settings = r.settings;
    if (!silent) toast("Settings saved.");
    return r;
  }

  function field(f) {
    let input;
    if (f.type === "select") {
      input = h("select", {}, f.options.map(([v, t]) => h("option", { value: v, selected: String(settings[f.key]) === String(v) }, t)));
    } else if (f.type === "bool") {
      input = h("input", { type: "checkbox", checked: !!settings[f.key] });
    } else if (f.type === "number") {
      input = h("input", { type: "number", value: settings[f.key], step: f.step || 1, min: f.min, max: f.max });
    } else if (f.type === "color") {
      input = h("input", { type: "color", value: settings[f.key] });
    } else if (f.type === "secret") {
      input = h("input", { type: "password", autocomplete: "off", placeholder: settings[f.key + "_set"] ? "Saved. Type to replace." : "Not set" });
    } else if (f.type === "secret-area") {
      input = h("textarea", { autocomplete: "off", placeholder: settings[f.key + "_set"] ? "Saved. Paste new contents to replace." : "Not set" });
    } else {
      input = h("input", { type: "text", value: settings[f.key] ?? "" });
    }
    inputs[f.key] = input;
    input.addEventListener("input", onChange); input.addEventListener("change", onChange);
    const hint = f.hint ? h("span", { class: "hint" }, f.hint) : null;
    const clear = f.type.startsWith("secret") && settings[f.key + "_set"]
      ? h("button", { class: "btn sm ghost danger", style: { alignSelf: "flex-start" }, onclick: guard(async () => { if (confirm("Remove the saved value?")) { await api("/settings", { method: "POST", json: { [f.key]: "" } }); toast("Removed."); load(); } }) }, "Remove saved value") : null;
    if (f.type === "bool") return h("label", { class: "check", "data-key": f.key }, input, h("span", {}, h("span", { class: "lbl", style: { fontWeight: 650 } }, f.label), hint ? h("div", {}, hint) : null));
    return h("label", { class: "field", "data-key": f.key }, h("span", { class: "lbl" }, f.label), input, hint, clear);
  }

  function applyVisibility() {
    const s = current();
    for (const f of allFields()) {
      if (!f.show) continue;
      const el = root.querySelector(`[data-key="${f.key}"]`);
      if (el) el.style.display = f.show(s) ? "" : "none";
    }
  }
  function onChange() { applyVisibility(); drawPreview(); }

  function drawPreview() {
    if (!inputs.caption_size) return;
    const s = current();
    const words = ["THIS", "IS", "HOW", "IT", "LOOKS"].slice(0, Math.max(1, Math.min(5, s.caption_words)));
    preview.style.setProperty("--ph", "320px");
    preview.replaceChildren(h("div", { class: "cap-line", style: {
      bottom: s.caption_position + "%", fontFamily: `"${s.caption_font}", Arial, sans-serif`, fontSize: (s.caption_size * 180 / 1080) + "px", color: s.caption_color,
      webkitTextStroke: Math.max(1, s.caption_size * 0.075 * 180 / 1080) + "px " + s.caption_outline, paintOrder: "stroke fill", textTransform: s.caption_upper ? "uppercase" : "none",
    } }, words.map((w, i) => h("span", { style: i === 1 ? { color: s.caption_highlight } : {} }, w + " "))));
  }

  function ytPanel() {
    const dis = h("button", { class: "btn", onclick: guard(async () => { await api("/youtube/disconnect", { method: "POST" }); toast("Disconnected from YouTube."); load(); }) }, "Disconnect");
    const connect = h("button", { class: "btn primary", onclick: guard(async () => {
      await save(true);
      const r = await api("/youtube/connect"); location.href = r.url;
    }) }, yt.connected ? "Reconnect" : "Connect YouTube");
    const idF = { key: "yt_client_id", label: "Google OAuth client ID", type: "text" };
    const secF = { key: "yt_client_secret", label: "Google OAuth client secret", type: "secret" };
    return h("section", { class: "panel" },
      h("header", {}, h("h2", {}, "YouTube upload"), h("p", { class: "hint", style: { margin: 0 } }, "Lets you upload rendered clips from the Library. You need your own free Google OAuth keys.")),
      h("div", { class: "row", style: { marginBottom: "12px" } }, yt.connected ? chip("Connected", "ok") : chip("Not connected", "warn"), yt.connected ? dis : null),
      h("div", { class: "fields" }, field(idF), field(secF),
        h("label", { class: "field" }, h("span", { class: "lbl" }, "Default visibility"), (inputs.yt_privacy = h("select", {}, ["private", "unlisted", "public"].map((p) => h("option", { value: p, selected: settings.yt_privacy === p }, p[0].toUpperCase() + p.slice(1)))))),
        h("label", { class: "check" }, (inputs.yt_add_shorts_tag = h("input", { type: "checkbox", checked: !!settings.yt_add_shorts_tag })), h("span", { style: { fontWeight: 650 } }, "Add #Shorts to vertical clips"))),
      h("div", { style: { margin: "16px 0 12px" } }, connect),
      h("details", {},
        h("summary", { style: { cursor: "pointer", fontWeight: 650 } }, "How to get the keys"),
        h("ol", { class: "small" },
          h("li", {}, "In Google Cloud Console, create a project and enable ", h("strong", {}, "YouTube Data API v3"), "."),
          h("li", {}, "Open the OAuth consent screen, choose External, and add your own Google account as a test user."),
          h("li", {}, "Create an OAuth client ID of type ", h("strong", {}, "Web application"), ". Under authorized redirect URIs add exactly:"),
          h("li", { style: { listStyle: "none" } }, h("div", { class: "row" }, h("code", {}, yt.redirect_uri || ""), h("button", { class: "btn sm", onclick: () => { navigator.clipboard && navigator.clipboard.writeText(yt.redirect_uri); toast("Copied."); } }, "Copy"))),
          h("li", {}, "Paste the client ID and secret above, press Connect YouTube, and approve access.")),
        h("p", { class: "small muted" }, "Google only accepts a plain http redirect for localhost. If ClipForge runs on a remote server, open it through an SSH tunnel (ssh -L 8000:localhost:8000 you@server) and use http://localhost:8000."),
        h("p", { class: "small muted" }, "While your Google app is in Testing mode, sign-in expires after 7 days and you will need to reconnect. YouTube also keeps uploads from unverified API projects private until the project passes Google's audit. Each upload uses 1,600 of the default 10,000 daily quota units, so about 6 uploads per day.")));
  }

  async function load() {
    const [s, y] = await Promise.all([api("/settings"), api("/youtube/status").catch(() => ({}))]);
    settings = s.settings; yt = y;
    for (const k of Object.keys(inputs)) delete inputs[k];
    const sections = SCHEMA.map((sec) => h("section", { class: "panel" },
      h("header", {}, h("h2", {}, sec.title), sec.desc ? h("p", { class: "hint", style: { margin: 0 } }, sec.desc) : null),
      sec.preview ? h("div", { class: "row", style: { alignItems: "flex-start", gap: "24px" } }, h("div", { class: "fields", style: { flex: 1 } }, sec.fields.map(field)), preview) : h("div", { class: "fields" }, sec.fields.map(field))));
    sections.splice(sections.length - 1, 0, ytPanel());
    root.replaceChildren(
      h("div", { class: "row between" }, h("div", {}, h("h1", {}, "Settings"), h("p", { class: "sub" }, "Keys and options are stored on the server in the data folder. Secrets are never shown again after saving.")),
        h("button", { class: "btn primary", onclick: guard(() => save()) }, "Save settings")),
      ...sections,
      h("div", { class: "row end", style: { marginTop: "18px" } },
        h("button", { class: "btn", onclick: guard(async () => { await save(true); const r = await api("/llm/test", { method: "POST" }); toast(r.message, !r.ok); }) }, "Test language model connection"),
        h("button", { class: "btn primary", onclick: guard(() => save()) }, "Save settings")));
    applyVisibility(); drawPreview();
  }

  const style = document.createElement("style");
  style.textContent = ".cap-preview{position:relative;flex:none;width:180px;height:320px;border-radius:10px;background:linear-gradient(160deg,#2b3a55,#0f1623);overflow:hidden}.cap-line{position:absolute;left:6%;right:6%;text-align:center;font-weight:800;line-height:1.15}";
  root.append(style);

  load().then(() => {
    if (params.get("yt") === "ok") toast("YouTube connected.");
    if (params.get("yt") === "error") toast("YouTube sign-in failed: " + (params.get("msg") || "unknown error"), true);
    if (params.get("yt")) history.replaceState(null, "", "#/settings");
  }).catch((e) => toast(e.message, true));
  return { el: root, destroy() {} };
}

route();
