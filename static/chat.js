(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const messagesEl = $("#messages");
  const input = $("#chat-input");
  const form = $("#composer");
  const sendBtn = $("#send-btn");
  const sidebar = $("#sidebar");

  const state = {
    history: [],          // [{role, content}]
    faculty: [],          // [{name, slug, publication_count, research_interests}]
    facultyRegex: null,
    topics: {},           // key -> label
    busy: false,
  };

  // ---------------------------------------------------------------- utilities
  const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const escapeRegex = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const stripAccents = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "");

  async function api(path, opts) {
    const res = await fetch(path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
    return data;
  }

  // ----------------------------------------------------------------- markdown
  function inlineMd(text, refs) {
    let s = escapeHtml(text);
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      (_, t, u) => `<a href="${u}" target="_blank" rel="noopener">${t}</a>`);
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>");
    s = s.replace(/(^|[^\w])_([^_\n]+)_(?!\w)/g, "$1<em>$2</em>");
    // citations: [3] or [3, 5] or [3-5]
    s = s.replace(/\[(\d+(?:\s*[,;–-]\s*\d+)*)\]/g, (m, inner) => {
      const parts = inner.split(/\s*([,;–-])\s*/);
      let nums = [];
      for (let i = 0; i < parts.length; i += 2) {
        const n = parseInt(parts[i], 10);
        if (parts[i - 1] && /[–-]/.test(parts[i - 1]) && nums.length) {
          for (let k = nums[nums.length - 1] + 1; k <= n && k - nums[nums.length - 1] < 20; k++) nums.push(k);
        } else nums.push(n);
      }
      const links = nums.map((n) => refs && refs[n]
        ? `<a class="cite" href="${refs[n]}" target="_blank" rel="noopener" title="Open on PubMed">${n}</a>`
        : null);
      return links.every(Boolean) ? links.join("") : m;
    });
    return s;
  }

  function renderMarkdown(md, refs) {
    const lines = md.replace(/\r/g, "").split("\n");
    let html = "";
    let list = null; // "ul" | "ol"
    let para = [];
    const flushPara = () => { if (para.length) { html += `<p>${inlineMd(para.join(" "), refs)}</p>`; para = []; } };
    const closeList = () => { if (list) { html += `</${list}>`; list = null; } };

    for (const raw of lines) {
      const line = raw.trimEnd();
      let m;
      if (!line.trim()) { flushPara(); closeList(); continue; }
      if ((m = line.match(/^(#{1,4})\s+(.*)/))) {
        flushPara(); closeList();
        html += `<h3>${inlineMd(m[2], refs)}</h3>`;
      } else if ((m = line.match(/^\s*[-*•]\s+(.*)/))) {
        flushPara();
        if (list !== "ul") { closeList(); html += "<ul>"; list = "ul"; }
        html += `<li>${inlineMd(m[1], refs)}</li>`;
      } else if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) {
        flushPara();
        if (list !== "ol") { closeList(); html += "<ol>"; list = "ol"; }
        html += `<li>${inlineMd(m[1], refs)}</li>`;
      } else if (/^\s*(---|\*\*\*)\s*$/.test(line)) {
        flushPara(); closeList(); html += "<hr>";
      } else {
        if (list && /^\s{2,}/.test(raw)) { // continuation of a list item
          html = html.replace(/<\/li>$/, " " + inlineMd(line.trim(), refs) + "</li>");
        } else { closeList(); para.push(line.trim()); }
      }
    }
    flushPara(); closeList();
    return html;
  }

  // Wrap faculty names inside rendered HTML with clickable buttons (text nodes only).
  function linkFaculty(root) {
    if (!state.facultyRegex) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => n.parentElement.closest("a, button, code") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      const text = node.nodeValue;
      state.facultyRegex.lastIndex = 0;
      if (!state.facultyRegex.test(text)) continue;
      state.facultyRegex.lastIndex = 0;
      const frag = document.createDocumentFragment();
      let last = 0, m;
      while ((m = state.facultyRegex.exec(text))) {
        const fac = resolveFaculty(m[0]);
        if (!fac) continue;
        frag.append(text.slice(last, m.index));
        const btn = document.createElement("button");
        btn.className = "fac-link";
        btn.textContent = m[0];
        btn.title = `View ${fac.name}'s profile`;
        btn.addEventListener("click", () => showProfile(fac.slug));
        frag.append(btn);
        last = m.index + m[0].length;
      }
      frag.append(text.slice(last));
      node.replaceWith(frag);
    }
  }

  function resolveFaculty(matchText) {
    const t = stripAccents(matchText).toLowerCase().replace(/^dr\.?\s+/, "");
    return state.faculty.find((f) => {
      const full = stripAccents(f.name).toLowerCase();
      const last = full.replace(/\./g, "").split(/\s+/).pop();
      return t === full || t === last || full.endsWith(" " + t);
    });
  }

  function buildFacultyRegex() {
    const alts = [];
    for (const f of state.faculty) {
      const parts = f.name.replace(/\./g, "").split(/\s+/);
      const last = parts[parts.length - 1];
      alts.push(escapeRegex(f.name), escapeRegex(stripAccents(f.name)));
      alts.push(`${escapeRegex(parts[0])}\\s+${escapeRegex(last)}`);
      alts.push(`Dr\\.?\\s+${escapeRegex(last)}`, `Dr\\.?\\s+${escapeRegex(stripAccents(last))}`);
    }
    alts.sort((a, b) => b.length - a.length);
    state.facultyRegex = new RegExp(`(?<![\\w-])(?:${alts.join("|")})(?![\\w-])`, "g");
  }

  // ------------------------------------------------------------------ chat UI
  function scrollToBottom() { messagesEl.scrollTop = messagesEl.scrollHeight; }

  function addUserMessage(text) {
    $("#welcome")?.remove();
    const el = document.createElement("div");
    el.className = "msg user";
    el.textContent = text;
    messagesEl.append(el);
    scrollToBottom();
  }

  function addLoading() {
    const el = document.createElement("div");
    el.className = "msg bot";
    el.innerHTML = `<span class="typing" aria-label="Thinking"><span></span><span></span><span></span></span>
                    <span class="status-text">Searching publications…</span>`;
    messagesEl.append(el);
    scrollToBottom();
    const t = setTimeout(() => { const s = el.querySelector(".status-text"); if (s) s.textContent = "Writing an answer…"; }, 1800);
    return { el, done: () => clearTimeout(t) };
  }

  function renderBotMessage(el, data) {
    const refs = data.all_refs || {};
    let html = `<div class="body">${renderMarkdown(data.answer || "", refs)}</div>`;
    if (data.sources?.length) {
      html += `<div class="sources"><h4>Sources</h4>` + data.sources.map((s) => `
        <a class="source-card" href="${escapeHtml(s.pubmed_url)}" target="_blank" rel="noopener">
          <span class="ref">[${s.ref}]</span>
          <span><span class="title">${escapeHtml(s.title)}</span><br>
            <span class="details">${escapeHtml(s.authors)} · ${escapeHtml(s.journal)} (${s.year ?? "n.d."})</span></span>
          <span class="ext" aria-hidden="true">PubMed ↗</span>
        </a>`).join("") + `</div>`;
    }
    el.innerHTML = html;
    linkFaculty(el.querySelector(".body"));
  }

  async function send(text) {
    text = text.trim();
    if (!text || state.busy) return;
    state.busy = true;
    sendBtn.disabled = true;
    input.value = "";
    autosize();
    addUserMessage(text);
    const loading = addLoading();
    try {
      const data = await api("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history: state.history, topics: selectedTopics() }),
      });
      loading.done();
      renderBotMessage(loading.el, data);
      state.history.push({ role: "user", content: text }, { role: "assistant", content: data.answer });
      state.history = state.history.slice(-12);
    } catch (err) {
      loading.done();
      loading.el.innerHTML = `<p class="notice">Sorry, something went wrong: ${escapeHtml(err.message)}</p>`;
    } finally {
      state.busy = false;
      sendBtn.disabled = false;
      input.focus();
      scrollToBottom();
    }
  }

  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 160) + "px";
  }

  // ------------------------------------------------------------------ sidebar
  function renderFacultyList(filter = "") {
    const q = stripAccents(filter).toLowerCase().trim();
    const list = $("#faculty-list");
    const items = state.faculty.filter((f) => !q ||
      stripAccents(f.name).toLowerCase().includes(q) || (f.research_interests || "").toLowerCase().includes(q));
    const total = state.faculty.length;
    list.innerHTML = (q ? "" : `<li class="muted" style="padding:4px 8px;font-size:13px">● All (${total})</li>`) +
      (items.length ? items.map((f) => `
        <li><button data-slug="${f.slug}" title="${escapeHtml(f.research_interests)}">
          <span>${escapeHtml(f.name)}<span class="sub">${escapeHtml((f.research_interests || "").split(";").slice(0, 3).join(", "))}</span></span>
          <span class="count">${f.publication_count}</span>
        </button></li>`).join("") : `<li class="muted">No matches</li>`);
  }

  async function showProfile(slug, opts = {}) {
    if (!slug) return;
    try {
      const p = await api(`/api/faculty/${encodeURIComponent(slug)}`);
      const years = Object.keys(p.pubs_by_year).map(Number);
      let spark = "";
      if (years.length) {
        const y0 = Math.min(...years), y1 = Math.max(...years);
        const max = Math.max(...Object.values(p.pubs_by_year));
        const bars = [];
        for (let y = y0; y <= y1; y++) {
          const c = p.pubs_by_year[y] || 0;
          bars.push(`<div style="height:${(c / max) * 100}%" title="${y}: ${c} publication${c === 1 ? "" : "s"}"></div>`);
        }
        spark = `<h4>Publications per year</h4><div class="sparkline">${bars.join("")}</div>
                 <div class="sparkline-labels"><span>${y0}</span><span>${y1}</span></div>`;
      }
      $("#faculty-profile").innerHTML = `
        <div class="profile">
          <h3>${escapeHtml(p.name)}</h3>
          <div class="meta">${p.title ? escapeHtml(p.title) + "<br>" : ""}
            ${p.publication_count} publications${p.first_year ? ` · ${p.first_year}–${p.last_year}` : ""}</div>
          ${p.research_areas.length ? `<h4>Research areas</h4><div class="tags">${p.research_areas.map((a) =>
            `<span class="tag gold">${escapeHtml(a.label)} (${a.count})</span>`).join("")}</div>` : ""}
          <h4>Top topics</h4>
          <div class="tags">${p.top_topics.map(([t, c]) => `<span class="tag" title="${c} papers">${escapeHtml(t)}</span>`).join("") || '<span class="muted">n/a</span>'}</div>
          ${spark}
          ${p.top_coauthors.length ? `<h4>Frequent PM&amp;R collaborators</h4><div class="tags">${p.top_coauthors.slice(0, 6).map(([n, c]) =>
            `<span class="tag">${escapeHtml(n)} (${c})</span>`).join("")}</div>` : ""}
          <h4>Recent publications</h4>
          <ul class="paper-list">${p.recent_publications.map((r) => `
            <li><a href="${escapeHtml(r.pubmed_url)}" target="_blank" rel="noopener">${escapeHtml(r.title)}</a>
              <span class="yr">(${r.year ?? "n.d."})</span></li>`).join("")}</ul>
          <button class="ask-btn" id="ask-about">Ask about ${escapeHtml(p.name.split(" ")[0])}'s research</button>
        </div>`;
      $("#ask-about").addEventListener("click", () => {
        send(`Give me an overview of ${p.name}'s research and their most recent work.`);
        sidebar.classList.remove("open");
      });
      $("#faculty-list-view").hidden = true;
      $("#faculty-profile-view").hidden = false;
      if (!opts.quiet && window.matchMedia("(max-width: 860px)").matches) sidebar.classList.add("open");
      sidebar.scrollTop = 0;
    } catch (err) {
      console.error(err);
    }
  }

  function selectedTopics() {
    return [...document.querySelectorAll("#topic-list input:checked")].map((i) => i.value);
  }

  function renderActiveFilters() {
    const sel = selectedTopics();
    const el = $("#active-filters");
    $("#clear-topics").hidden = !sel.length;
    el.hidden = !sel.length;
    el.innerHTML = sel.length ? "Searching only: " + sel.map((k) =>
      `<span class="tag gold">${escapeHtml(state.topics[k] || k)}</span>`).join("") : "";
  }

  // --------------------------------------------------------------------- init
  async function init() {
    form.addEventListener("submit", (e) => { e.preventDefault(); send(input.value); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(input.value); }
    });
    input.addEventListener("input", autosize);
    $("#starter-chips").addEventListener("click", (e) => { if (e.target.matches(".chip")) send(e.target.textContent); });
    $("#faculty-search").addEventListener("input", (e) => renderFacultyList(e.target.value));
    $("#faculty-list").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-slug]");
      if (b) showProfile(b.dataset.slug);
    });
    $("#profile-back").addEventListener("click", () => {
      $("#faculty-profile-view").hidden = true;
      $("#faculty-list-view").hidden = false;
    });
    $("#topic-list").addEventListener("change", renderActiveFilters);
    $("#clear-topics").addEventListener("click", () => {
      document.querySelectorAll("#topic-list input").forEach((i) => (i.checked = false));
      renderActiveFilters();
    });
    $("#sidebar-toggle").addEventListener("click", () => {
      const open = sidebar.classList.toggle("open");
      $("#sidebar-toggle").setAttribute("aria-expanded", open);
    });
    $("#new-chat").addEventListener("click", () => location.reload());

    try {
      const [faculty, topics, stats] = await Promise.all([api("/api/faculty"), api("/api/topics"), api("/api/stats")]);
      state.faculty = faculty;
      buildFacultyRegex();
      renderFacultyList();
      for (const t of topics) {
        state.topics[t.key] = t.label;
        const c = document.querySelector(`[data-topic-count="${t.key}"]`);
        if (c) c.textContent = t.count;
      }
      const updated = stats.last_updated ? new Date(stats.last_updated).toLocaleDateString(undefined,
        { year: "numeric", month: "short", day: "numeric" }) : "unknown";
      $("#footer-stats").textContent = `${stats.total_publications.toLocaleString()} publications · ` +
        `${stats.faculty_count} faculty · Last updated: ${updated}` +
        (stats.llm_enabled ? "" : " · AI answers off (no API key)");
    } catch (err) {
      $("#faculty-list").innerHTML = `<li class="muted">${escapeHtml(err.message)}</li>`;
      $("#footer-stats").textContent = err.message;
    }
    input.focus();
  }

  init();
})();
