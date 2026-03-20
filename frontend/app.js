const state = {
  rows: [],
  filteredRows: [],
  currentPage: 1,
  pageSize: 20,

  csvPath: "",
  csvList: [],
  csvDirBase: "../result/",
  currentSourceLabel: "",

  navMode: "all", // all | starred | recent
};

const LS_FAV = "smartlib_favorites_v1"; // Set<rowId>
const LS_READ = "smartlib_read_v1"; // { [rowId]: timestamp }

const stateStore = {
  favorites: new Set(),
  readMap: {},
};

const refs = {
  // sidebar
  sourceList: document.getElementById("sourceList"),
  navAllBtn: document.getElementById("navAllBtn"),
  navStarredBtn: document.getElementById("navStarredBtn"),
  navRecentBtn: document.getElementById("navRecentBtn"),
  navAllCount: document.getElementById("navAllCount"),
  navStarredCount: document.getElementById("navStarredCount"),
  navRecentCount: document.getElementById("navRecentCount"),
  topicFilter: document.getElementById("topicFilter"),
  sourceFilter: document.getElementById("sourceFilter"),

  // header
  searchInput: document.getElementById("searchInput"),
  markAllReadBtn: document.getElementById("markAllReadBtn"),

  // content
  currentSourceName: document.getElementById("currentSourceName"),
  currentSourceMeta: document.getElementById("currentSourceMeta"),
  statTotal: document.getElementById("statTotal"),
  statFiltered: document.getElementById("statFiltered"),
  statRange: document.getElementById("statRange"),
  pageSize: document.getElementById("pageSize"),
  stats: document.getElementById("stats"),
  articleList: document.getElementById("articleList"),
  prevBtn: document.getElementById("prevBtn"),
  nextBtn: document.getElementById("nextBtn"),
  pageInfo: document.getElementById("pageInfo"),

  // drawer
  detailDrawer: document.getElementById("detailDrawer"),
  detailTitle: document.getElementById("detailTitle"),
  detailBody: document.getElementById("detailBody"),
  closeDrawer: document.getElementById("closeDrawer"),
  favoriteBtn: document.getElementById("favoriteBtn"),
};

async function bootstrap() {
  loadLocalState();
  try {
    bindEvents();
    await initSourceList();
    // initSourceList() will auto-select latest and trigger first load
  } catch (e) {
    console.error(e);
    if (refs.stats) refs.stats.textContent = `初始化失败: ${e.message}`;
  }
}

function loadLocalState() {
  try {
    const favRaw = localStorage.getItem(LS_FAV);
    if (favRaw) stateStore.favorites = new Set(JSON.parse(favRaw));
  } catch {
    stateStore.favorites = new Set();
  }

  try {
    const readRaw = localStorage.getItem(LS_READ);
    if (readRaw) stateStore.readMap = JSON.parse(readRaw) || {};
  } catch {
    stateStore.readMap = {};
  }
}

function saveFavorites() {
  localStorage.setItem(LS_FAV, JSON.stringify(Array.from(stateStore.favorites)));
}

function saveReadMap() {
  localStorage.setItem(LS_READ, JSON.stringify(stateStore.readMap));
}

function rowId(row) {
  return row.url || `${row.title}|${row.dateRaw}`;
}

function isStarred(row) {
  return stateStore.favorites.has(rowId(row));
}

function isRead(row) {
  const id = rowId(row);
  return stateStore.readMap[id] != null;
}

function markRead(row) {
  const id = rowId(row);
  if (!id) return;
  stateStore.readMap[id] = Date.now();
  saveReadMap();
}

function toggleFavorite(row) {
  const id = rowId(row);
  if (!id) return;
  if (stateStore.favorites.has(id)) stateStore.favorites.delete(id);
  else stateStore.favorites.add(id);
  saveFavorites();
}

function setNavMode(mode) {
  state.navMode = mode;
  if (refs.navAllBtn) refs.navAllBtn.classList.toggle("active", mode === "all");
  if (refs.navStarredBtn) refs.navStarredBtn.classList.toggle("active", mode === "starred");
  if (refs.navRecentBtn) refs.navRecentBtn.classList.toggle("active", mode === "recent");
}

function updateNavCounts() {
  const total = state.rows.length || 0;
  const starred = state.rows.filter((r) => isStarred(r)).length;
  const recent = state.rows.filter((r) => isRead(r)).length;
  if (refs.navAllCount) refs.navAllCount.textContent = String(total);
  if (refs.navStarredCount) refs.navStarredCount.textContent = String(starred);
  if (refs.navRecentCount) refs.navRecentCount.textContent = String(recent);
}

function bindEvents() {
  // Top search
  if (refs.searchInput) {
    refs.searchInput.addEventListener("input", debounce(() => {
      state.currentPage = 1;
      applyFilters();
    }, 180));
  }

  // Nav modes
  refs.navAllBtn.addEventListener("click", () => {
    setNavMode("all");
    state.currentPage = 1;
    applyFilters();
  });
  refs.navStarredBtn.addEventListener("click", () => {
    setNavMode("starred");
    state.currentPage = 1;
    applyFilters();
  });
  refs.navRecentBtn.addEventListener("click", () => {
    setNavMode("recent");
    state.currentPage = 1;
    applyFilters();
  });

  // Filters (Topic / Source) in sidebar
  refs.topicFilter.addEventListener("change", () => {
    state.currentPage = 1;
    applyFilters();
  });
  refs.sourceFilter.addEventListener("change", () => {
    state.currentPage = 1;
    applyFilters();
  });

  // Page size + paging
  refs.pageSize.addEventListener("change", () => {
    state.pageSize = Number(refs.pageSize.value);
    state.currentPage = 1;
    render();
  });
  refs.prevBtn.addEventListener("click", () => {
    if (state.currentPage > 1) {
      state.currentPage -= 1;
      render();
    }
  });
  refs.nextBtn.addEventListener("click", () => {
    const maxPage = Math.max(1, Math.ceil(state.filteredRows.length / state.pageSize));
    if (state.currentPage < maxPage) {
      state.currentPage += 1;
      render();
    }
  });

  // Mark all read
  refs.markAllReadBtn.addEventListener("click", () => {
    if (!state.rows.length) return;
    const now = Date.now();
    state.rows.forEach((r) => {
      const id = rowId(r);
      if (id) stateStore.readMap[id] = now;
    });
    saveReadMap();
    updateNavCounts();
    if (state.navMode === "recent") {
      state.currentPage = 1;
      applyFilters();
    } else {
      refs.stats.textContent = "已标记当前数据源为已读";
      setTimeout(() => {
        if (refs.stats) refs.stats.textContent = "";
      }, 1200);
    }
  });

  // Drawer
  refs.closeDrawer.addEventListener("click", closeDrawer);
  refs.detailDrawer.addEventListener("click", (e) => {
    if (e.target === refs.detailDrawer) closeDrawer();
  });

  refs.favoriteBtn.addEventListener("click", () => {
    if (!refs.detailDrawer || refs.detailDrawer.classList.contains("hidden")) return;
    const id = refs.detailDrawer.dataset.currentId;
    if (!id) return;
    // We don't have the full row object here; toggle from stored sets.
    if (stateStore.favorites.has(id)) stateStore.favorites.delete(id);
    else stateStore.favorites.add(id);
    saveFavorites();
    updateNavCounts();
    refs.favoriteBtn.textContent = stateStore.favorites.has(id) ? "★" : "☆";
    // Also re-render list if needed (Starred view).
    if (state.navMode === "starred") {
      state.currentPage = 1;
      applyFilters();
    } else {
      render();
    }
  });

  // List click delegation
  refs.articleList.addEventListener("click", (e) => {
    const starBtn = e.target.closest(".star-btn");
    if (starBtn) {
      e.stopPropagation();
      const id = starBtn.dataset.starId;
      if (!id) return;
      // Toggle directly by id without needing row reconstruction
      if (stateStore.favorites.has(id)) stateStore.favorites.delete(id);
      else stateStore.favorites.add(id);
      saveFavorites();
      updateNavCounts();
      // Update star icon state
      starBtn.classList.toggle("on", stateStore.favorites.has(id));
      // If we are in "Starred" view, re-filter
      if (state.navMode === "starred") {
        state.currentPage = 1;
        applyFilters();
      } else {
        render();
      }
      return;
    }

    const rowEl = e.target.closest(".article-row");
    if (!rowEl) return;
    const idx = Number(rowEl.dataset.detailIndex);
    const row = state.filteredRows[idx];
    openDrawer(row);
  });
}

async function initSourceList() {
  refs.stats.textContent = "正在读取 result/ 下的 CSV 列表...";
  const listCsv = async (dirHref, baseHref) => {
    const resp = await fetch(dirHref);
    if (!resp.ok) return [];
    const html = await resp.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, "text/html");
    const links = Array.from(doc.querySelectorAll("a"))
      .map((a) => a.getAttribute("href") || "")
      .filter((href) => href.toLowerCase().endsWith(".csv") && !href.startsWith("._"));
    const unique = Array.from(new Set(links)).sort();
    state.csvDirBase = baseHref;
    return unique;
  };

  let unique = await listCsv("../result/", "../result/");
  if (!unique.length) {
    // Fallback for initial usage: allow root-level CSVs if result/ is empty.
    unique = await listCsv("../", "../");
  }

  state.csvList = unique;
  refs.sourceList.innerHTML = "";
  if (!unique.length) {
    refs.sourceList.textContent = "未找到可用的 CSV 文件（检查 result/ 或项目根目录）。";
    refs.stats.textContent = "暂无可用数据源。";
    return;
  }

  unique.forEach((name) => {
    const base = name.replace(/\.csv$/i, "");
    const item = document.createElement("button");
    item.type = "button";
    item.className = "source-item";
    item.dataset.name = name;
    item.innerHTML = `
      <div class="source-left">
        <span class="source-icon">CSV</span>
        <span class="source-name">${escapeHtml(base)}</span>
      </div>
      <span class="source-count">0</span>
    `;
    item.addEventListener("click", async () => {
      refs.sourceList.querySelectorAll(".source-item").forEach((el) => el.classList.remove("active"));
      item.classList.add("active");

      state.csvPath = state.csvDirBase + name;
      state.currentSourceLabel = base;
      state.currentPage = 1;

      // Reset view-dependent UI but keep local favorites/read state
      await loadCsvAndRender();
    });
    refs.sourceList.appendChild(item);
  });

  // Default select latest
  const last = refs.sourceList.querySelector(".source-item:last-child");
  if (last) last.click();
}

async function loadCsvAndRender() {
  refs.stats.textContent = "正在加载 CSV 数据...";
  try {
    if (!state.csvPath) return;
    const text = await fetchCsv(state.csvPath);
    const rows = parseCsv(text);
    state.rows = rows.map(normalizeRow);
    state.currentPage = 1;

    updateTopStatsBase();
    updateNavCounts();
    const activeSource = refs.sourceList.querySelector(".source-item.active .source-count");
    if (activeSource) activeSource.textContent = String(state.rows.length);

    clearSelect(refs.topicFilter);
    clearSelect(refs.sourceFilter);
    fillSelect(refs.topicFilter, uniqueValues(state.rows, "topic"));
    fillSelect(refs.sourceFilter, uniqueValues(state.rows, "source"));

    applyFilters();
    refs.stats.textContent = "";
  } catch (e) {
    console.error(e);
    refs.stats.textContent = `加载失败: ${e.message}`;
  }
}

async function fetchCsv(path) {
  const resp = await fetch(path);
  if (!resp.ok) throw new Error(`无法读取 CSV (${resp.status})`);
  return resp.text();
}

function parseCsv(text) {
  const rows = [];
  let cur = "";
  let row = [];
  let inQuotes = false;

  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    const next = text[i + 1];

    if (char === '"') {
      if (inQuotes && next === '"') {
        cur += '"';
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
    } else if (char === "," && !inQuotes) {
      row.push(cur);
      cur = "";
    } else if ((char === "\n" || char === "\r") && !inQuotes) {
      if (char === "\r" && next === "\n") i += 1;
      row.push(cur);
      if (row.some((cell) => cell !== "")) rows.push(row);
      row = [];
      cur = "";
    } else {
      cur += char;
    }
  }

  if (cur !== "" || row.length > 0) {
    row.push(cur);
    rows.push(row);
  }

  if (!rows.length) return [];
  const header = rows[0].map((x) => x.trim());
  const data = rows.slice(1).map((cells) => {
    const item = {};
    header.forEach((key, idx) => {
      item[key] = (cells[idx] || "").trim();
    });
    return item;
  });
  return data;
}

function normalizeRow(r) {
  return {
    ...r,
    title: r.title || "",
    url: r.url || "",
    type: r.type || "",
    source: r.source || "",
    topic: r.topic || "",
    dateRaw: r["时间"] || "",
    publishedAt: safeDate(r["时间"]),
    contentEn: r["主要内容"] || "",
    contentZh: r["主要内容_zh"] || "",
    keywordsEn: r["关键词"] || "",
    keywordsZh: r["关键词_zh"] || "",
    topicWordsEn: r["主题词"] || "",
    topicWordsZh: r["主题词_zh"] || "",
    authors: r["authors"] || "",
    tags: r["tags"] || "",
    topics: r["topics"] || "",
  };
}

function safeDate(v) {
  if (!v) return null;
  const d = new Date(v);
  return Number.isNaN(d.valueOf()) ? null : d;
}

function uniqueValues(arr, key) {
  return [...new Set(arr.map((x) => (x[key] || "").trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

function clearSelect(select) {
  if (!select) return;
  select.innerHTML = '<option value="">全部</option>';
}

function fillSelect(select, values) {
  values.forEach((v) => {
    const opt = document.createElement("option");
    opt.value = v;
    opt.textContent = v;
    select.appendChild(opt);
  });
}

function previewText(row) {
  const txt = (row.contentEn || row.contentZh || "").replace(/\s+/g, " ").trim();
  return txt ? txt.slice(0, 220) : "";
}

function formatDate(s) {
  if (!s) return "-";
  return String(s).slice(0, 10);
}

function applyFilters() {
  const q = refs.searchInput ? refs.searchInput.value.trim().toLowerCase() : "";
  const topic = refs.topicFilter ? refs.topicFilter.value : "";
  const source = refs.sourceFilter ? refs.sourceFilter.value : "";

  const base = state.rows || [];

  let filtered = base.filter((r) => {
    if (state.navMode === "starred" && !isStarred(r)) return false;
    if (state.navMode === "recent") {
      const id = rowId(r);
      if (!id || stateStore.readMap[id] == null) return false;
    }

    if (topic && r.topic !== topic) return false;
    if (source && r.source !== source) return false;

    if (!q) return true;
    const bucket = [
      r.title,
      r.contentEn,
      r.contentZh,
      r.keywordsEn,
      r.keywordsZh,
      r.authors,
      r.tags,
      r.topics,
      r.url,
      r.source,
      r.topic,
    ]
      .join(" ")
      .toLowerCase();
    return bucket.includes(q);
  });

  if (state.navMode === "recent") {
    filtered.sort((a, b) => {
      const da = stateStore.readMap[rowId(a)] || 0;
      const db = stateStore.readMap[rowId(b)] || 0;
      return db - da;
    });
  } else {
    filtered.sort((a, b) => {
      const da = a.publishedAt ? a.publishedAt.valueOf() : 0;
      const db = b.publishedAt ? b.publishedAt.valueOf() : 0;
      return db - da;
    });
  }

  state.filteredRows = filtered;
  render();
}

function render() {
  const total = state.filteredRows.length || 0;
  const start = (state.currentPage - 1) * state.pageSize;
  const end = start + state.pageSize;
  const pageRows = state.filteredRows.slice(start, end);
  const maxPage = Math.max(1, Math.ceil(total / state.pageSize));

  refs.pageInfo.textContent = `${state.currentPage} / ${maxPage}`;
  refs.prevBtn.disabled = state.currentPage <= 1;
  refs.nextBtn.disabled = state.currentPage >= maxPage;

  refs.statTotal.textContent = String(state.rows.length || 0);
  refs.statFiltered.textContent = String(total);

  if (!pageRows.length) {
    refs.articleList.innerHTML = `<div class="load-status" style="margin: 10px 0;">No results.</div>`;
    return;
  }

  refs.articleList.innerHTML = pageRows
    .map((row, idx) => {
      const globalIndex = start + idx;
      const id = rowId(row);
      const starred = stateStore.favorites.has(id);
      const avColor = avatarColor(row.source || "unknown");
      const avInitial = avatarInitial(row.source || row.title || "?");
      const read = isRead(row);
      return `
        <div class="article-row ${read ? "read" : ""}" data-detail-index="${globalIndex}">
          <button class="star-btn ${starred ? "on" : ""}" data-star-id="${escapeHtml(id)}" type="button" aria-label="star">
            ★
          </button>

          <div class="article-left">
            <div class="source-avatar" style="background:${avColor}">${escapeHtml(avInitial)}</div>
            <div class="article-main">
              <div class="article-source">${escapeHtml(row.source || "-")}</div>
              <div class="article-title">${escapeHtml(row.title || "(无标题)")}</div>
              <div class="article-preview">${escapeHtml(previewText(row))}</div>
            </div>
          </div>

          <div class="article-date">${escapeHtml(formatDate(row.dateRaw))}</div>
        </div>
      `;
    })
    .join("");
}

function avatarColor(s) {
  // Deterministic color hash -> pleasant-ish greys
  let h = 0;
  const str = String(s);
  for (let i = 0; i < str.length; i += 1) h = (h * 31 + str.charCodeAt(i)) % 360;
  // keep it mostly neutral, not neon
  return `hsl(${h}, 20%, 85%)`;
}

function avatarInitial(s) {
  const txt = String(s || "").trim();
  return txt ? txt[0].toUpperCase() : "?";
}

function openDrawer(row) {
  if (!row) return;

  // Mark as read on open (Inoreader-like behavior)
  markRead(row);
  updateNavCounts();
  render();

  const id = rowId(row);
  refs.detailDrawer.dataset.currentId = id;

  refs.favoriteBtn.textContent = stateStore.favorites.has(id) ? "★" : "☆";
  updateNavCounts();

  refs.detailTitle.textContent = row.title || "Details";
  refs.detailBody.innerHTML = `
    <div class="meta">
      <p><b>时间:</b> ${escapeHtml(row.dateRaw || "-")}</p>
      <p><b>类型:</b> ${escapeHtml(row.type || "-")}</p>
      <p><b>来源:</b> ${escapeHtml(row.source || "-")}</p>
      <p><b>URL:</b> ${
        row.url
          ? `<a href="${escapeHtml(row.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(row.url)}</a>`
          : "-"
      }</p>
    </div>

    ${chips("作者", splitSemi(row.authors))}
    ${chips("Topic", splitSemi(row.topics))}
    ${chips("Tags", splitSemi(row.tags))}
    ${chips("关键词 EN", splitSemi(row.keywordsEn))}
    ${chips("关键词 ZH", splitSemi(row.keywordsZh))}
    ${chips("主题词 EN", splitSemi(row.topicWordsEn))}
    ${chips("主题词 ZH", splitSemi(row.topicWordsZh))}

    <h3>主要内容 (EN)</h3>
    <p>${escapeHtml(row.contentEn || "-")}</p>

    <h3>主要内容 (ZH)</h3>
    <p>${escapeHtml(row.contentZh || "-")}</p>
  `;

  refs.detailDrawer.classList.remove("hidden");
}

function closeDrawer() {
  if (refs.detailDrawer) {
    refs.detailDrawer.classList.add("hidden");
    delete refs.detailDrawer.dataset.currentId;
  }
}

function chips(title, arr) {
  if (!arr || !arr.length) return "";
  return `
    <h3>${escapeHtml(title)}</h3>
    <div class="chip-row">
      ${arr.map((x) => `<span class="chip">${escapeHtml(x)}</span>`).join("")}
    </div>
  `;
}

function splitSemi(s) {
  if (!s) return [];
  return String(s)
    .split(";")
    .map((x) => x.trim())
    .filter(Boolean);
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function debounce(fn, delay) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

function updateTopStatsBase() {
  const total = state.rows.length || 0;
  refs.statTotal.textContent = String(total);

  refs.currentSourceName.textContent = state.currentSourceLabel || "未命名数据源";
  refs.currentSourceMeta.textContent = state.csvPath ? `来自 ${state.csvPath}` : "来自 result/ 目录";

  if (!total) {
    refs.statRange.textContent = "-";
    return;
  }

  const dates = state.rows
    .map((r) => r.publishedAt)
    .filter((d) => d instanceof Date && !Number.isNaN(d.valueOf()))
    .map((d) => d.valueOf());

  if (!dates.length) {
    refs.statRange.textContent = "-";
    return;
  }

  const min = new Date(Math.min(...dates));
  const max = new Date(Math.max(...dates));
  const fmt = (d) => d.toISOString().slice(0, 10);
  refs.statRange.textContent = `${fmt(min)} ~ ${fmt(max)}`;
}

bootstrap();
