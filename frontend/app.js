const state = {
  // 当前源对应的所有行
  rows: [],
  // 过滤后的行
  filteredRows: [],
  currentPage: 1,
  pageSize: 20,
  selected: null,
  // 当前选中的 CSV 相对路径（以 ../result/ 开头）
  csvPath: "",
  // 所有可用的 CSV 文件名（不含路径）
  csvList: [],
  // 源名字友好显示
  currentSourceLabel: "",
};

const refs = {
  // 左侧源列表
  sourceList: document.getElementById("sourceList"),
  // 顶部统计区
  currentSourceName: document.getElementById("currentSourceName"),
  currentSourceMeta: document.getElementById("currentSourceMeta"),
  statTotal: document.getElementById("statTotal"),
  statFiltered: document.getElementById("statFiltered"),
  statRange: document.getElementById("statRange"),
  // 筛选与列表
  searchInput: document.getElementById("searchInput"),
  typeFilter: document.getElementById("typeFilter"),
  topicFilter: document.getElementById("topicFilter"),
  sourceFilter: document.getElementById("sourceFilter"),
  pageSize: document.getElementById("pageSize"),
  resetBtn: document.getElementById("resetBtn"),
  tableContainer: document.getElementById("tableContainer"),
  stats: document.getElementById("stats"),
  prevBtn: document.getElementById("prevBtn"),
  nextBtn: document.getElementById("nextBtn"),
  pageInfo: document.getElementById("pageInfo"),
  // 详情抽屉
  detailDrawer: document.getElementById("detailDrawer"),
  detailTitle: document.getElementById("detailTitle"),
  detailBody: document.getElementById("detailBody"),
  closeDrawer: document.getElementById("closeDrawer"),
};

// 启动入口
async function bootstrap() {
  try {
    await initSourceList();
    bindEvents(); // 绑定一次全局事件监听
  } catch (e) {
    console.error(e);
  }
}

// 读取 result/ 目录，构建左侧源列表
async function initSourceList() {
  refs.stats.textContent = "正在读取 result/ 下的 CSV 列表...";
  try {
    const resp = await fetch("../result/");
    if (!resp.ok) {
      throw new Error(`无法读取 result 目录 (${resp.status})`);
    }
    const html = await resp.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, "text/html");
    const links = Array.from(doc.querySelectorAll("a"))
      .map((a) => a.getAttribute("href") || "")
      .filter((href) => href.toLowerCase().endsWith(".csv"));

    const unique = Array.from(new Set(links)).sort();
    state.csvList = unique;

    refs.sourceList.innerHTML = "";
    if (!unique.length) {
      refs.sourceList.innerHTML = '<div class="source-meta">result/ 目录下未找到 CSV 文件。</div>';
      refs.stats.textContent = "暂无可用数据源。";
      return;
    }

    unique.forEach((name) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "source-item";
      item.dataset.name = name;

      const base = name.replace(/\.csv$/i, "");
      item.innerHTML = `
        <div class="source-name">${escapeHtml(base)}</div>
        <div class="source-meta">
          <span class="badge badge-pill">CSV</span>
          <span class="badge" data-badge-count="${escapeHtml(name)}">- 条</span>
        </div>
      `;

      item.addEventListener("click", async () => {
        // 切换激活态
        refs.sourceList.querySelectorAll(".source-item").forEach((el) => el.classList.remove("active"));
        item.classList.add("active");

        state.csvPath = "../result/" + name;
        state.currentSourceLabel = base;
        // 重置筛选 UI 和分页
        resetFilters(true);
        await loadCsvAndRender();
      });

      refs.sourceList.appendChild(item);
    });

    // 默认选中最新一个源（列表最后一个）
    const last = refs.sourceList.querySelector(".source-item:last-child");
    if (last) {
      last.click();
    }
  } catch (error) {
    refs.stats.textContent = `加载 CSV 列表失败: ${error.message}`;
  }
}

// 加载当前 csvPath 对应的数据并渲染
async function loadCsvAndRender() {
  try {
    if (!state.csvPath) {
      refs.stats.textContent = "请先在左侧选择一个 CSV 数据源。";
      return;
    }

    refs.stats.textContent = "正在加载 CSV 数据...";
    const text = await fetchCsv(state.csvPath);
    const rows = parseCsv(text);
    state.rows = rows.map(normalizeRow);
    state.filteredRows = [...state.rows];

    // 更新顶部源信息与统计
    updateTopStats();

    // 重置筛选下拉选项
    clearSelect(refs.typeFilter);
    clearSelect(refs.topicFilter);
    clearSelect(refs.sourceFilter);
    fillSelect(refs.typeFilter, uniqueValues(state.rows, "type"));
    fillSelect(refs.topicFilter, uniqueValues(state.rows, "topic"));
    fillSelect(refs.sourceFilter, uniqueValues(state.rows, "source"));

    applyFilters();
  } catch (error) {
    refs.stats.textContent = `加载失败: ${error.message}`;
  }
}

async function fetchCsv(path) {
  const resp = await fetch(path);
  if (!resp.ok) {
    throw new Error(`无法读取 CSV (${resp.status})`);
  }
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
      if (char === "\r" && next === "\n") {
        i += 1;
      }
      row.push(cur);
      if (row.some((cell) => cell !== "")) {
        rows.push(row);
      }
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
  // 保留第一个“全部”选项，清空其余
  const first = select.querySelector("option");
  select.innerHTML = "";
  if (first) {
    first.value = "";
    first.textContent = "全部";
    select.appendChild(first);
  }
}

function fillSelect(select, values) {
  values.forEach((v) => {
    const opt = document.createElement("option");
    opt.value = v;
    opt.textContent = v;
    select.appendChild(opt);
  });
}

function bindEvents() {
  if (!refs.searchInput) return;

  refs.searchInput.addEventListener("input", debounce(applyFilters, 180));
  refs.typeFilter.addEventListener("change", applyFilters);
  refs.topicFilter.addEventListener("change", applyFilters);
  refs.sourceFilter.addEventListener("change", applyFilters);
  refs.pageSize.addEventListener("change", () => {
    state.pageSize = Number(refs.pageSize.value);
    state.currentPage = 1;
    render();
  });
  refs.resetBtn.addEventListener("click", () => resetFilters(false));

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

  refs.closeDrawer.addEventListener("click", closeDrawer);
  refs.detailDrawer.addEventListener("click", (e) => {
    if (e.target === refs.detailDrawer) closeDrawer();
  });
}

// 重置筛选；forSourceChange=true 时不重绘顶部提示（由 loadCsvAndRender 控制）
function resetFilters(forSourceChange) {
  if (!refs.searchInput) return;
  refs.searchInput.value = "";
  refs.typeFilter.value = "";
  refs.topicFilter.value = "";
  refs.sourceFilter.value = "";
  refs.pageSize.value = "20";
  state.pageSize = 20;
  state.currentPage = 1;
  if (!forSourceChange) {
    applyFilters();
  }
}

function applyFilters() {
  const q = refs.searchInput.value.trim().toLowerCase();
  const type = refs.typeFilter.value;
  const topic = refs.topicFilter.value;
  const source = refs.sourceFilter.value;

  state.filteredRows = state.rows.filter((r) => {
    if (type && r.type !== type) return false;
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
    ]
      .join(" ")
      .toLowerCase();
    return bucket.includes(q);
  });

  state.filteredRows.sort((a, b) => {
    const da = a.publishedAt ? a.publishedAt.valueOf() : 0;
    const db = b.publishedAt ? b.publishedAt.valueOf() : 0;
    return db - da;
  });

  state.currentPage = 1;
  render();
}

function render() {
  const total = state.filteredRows.length;
  const start = (state.currentPage - 1) * state.pageSize;
  const end = start + state.pageSize;
  const pageRows = state.filteredRows.slice(start, end);
  const maxPage = Math.max(1, Math.ceil(total / state.pageSize));

  refs.pageInfo.textContent = `第 ${state.currentPage} / ${maxPage} 页`;
  refs.stats.textContent = `共 ${state.rows.length} 条，筛选后 ${total} 条`;
  refs.statTotal.textContent = String(state.rows.length || 0);
  refs.statFiltered.textContent = String(total || 0);
  refs.prevBtn.disabled = state.currentPage <= 1;
  refs.nextBtn.disabled = state.currentPage >= maxPage;

  const html = `
    <table>
      <thead>
        <tr>
          <th style="width: 52px">序号</th>
          <th>标题</th>
          <th style="width: 120px">时间</th>
          <th style="width: 120px">类型</th>
          <th style="width: 220px">来源</th>
          <th style="width: 92px">操作</th>
        </tr>
      </thead>
      <tbody>
        ${
          pageRows.length
            ? pageRows
                .map(
                  (r, idx) => `
                    <tr>
                      <td>${escapeHtml(String(start + idx + 1))}</td>
                      <td>
                        <a class="title-link" href="${escapeHtml(r.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(r.title || "(无标题)")}</a>
                      </td>
                      <td>${escapeHtml(shortDate(r.dateRaw))}</td>
                      <td>${escapeHtml(r.type)}</td>
                      <td>${escapeHtml(r.source)}</td>
                      <td><button class="btn" data-detail="${start + idx}">查看</button></td>
                    </tr>
                  `
                )
                .join("")
            : `<tr><td colspan="6">没有匹配结果</td></tr>`
        }
      </tbody>
    </table>
  `;

  refs.tableContainer.innerHTML = html;
  refs.tableContainer.querySelectorAll("[data-detail]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const i = Number(btn.getAttribute("data-detail"));
      openDrawer(state.filteredRows[i]);
    });
  });
}

function openDrawer(row) {
  if (!row) return;
  refs.detailTitle.textContent = row.title || "详情";
  refs.detailBody.innerHTML = `
    <p>
      <strong>时间:</strong> ${escapeHtml(row.dateRaw || "-")}<br />
      <strong>类型:</strong> ${escapeHtml(row.type || "-")}<br />
      <strong>来源:</strong> ${escapeHtml(row.source || "-")}<br />
      <strong>URL:</strong> <a class="title-link" href="${escapeHtml(row.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(row.url)}</a>
    </p>

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
  refs.detailDrawer.classList.add("hidden");
}

function chips(title, arr) {
  if (!arr.length) return "";
  return `
    <h3>${escapeHtml(title)}</h3>
    <div class="chip-row">
      ${arr.map((x) => `<span class="chip">${escapeHtml(x)}</span>`).join("")}
    </div>
  `;
}

function splitSemi(s) {
  if (!s) return [];
  return s
    .split(";")
    .map((x) => x.trim())
    .filter(Boolean);
}

function shortDate(s) {
  if (!s) return "-";
  return s.slice(0, 10);
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

// 根据当前 rows 计算顶部统计信息（时间跨度等）
function updateTopStats() {
  const total = state.rows.length;
  refs.statTotal.textContent = String(total || 0);

  if (!total) {
    refs.currentSourceName.textContent = state.currentSourceLabel || "未选择数据源";
    refs.currentSourceMeta.textContent = "请在左侧选择一个 CSV 文件。";
    refs.statRange.textContent = "-";
    return;
  }

  // 计算时间范围
  const dates = state.rows
    .map((r) => r.publishedAt)
    .filter((d) => d instanceof Date && !Number.isNaN(d.valueOf()))
    .map((d) => d.valueOf());
  if (!dates.length) {
    refs.statRange.textContent = "-";
  } else {
    const min = new Date(Math.min(...dates));
    const max = new Date(Math.max(...dates));
    const fmt = (d) => d.toISOString().slice(0, 10);
    refs.statRange.textContent = `${fmt(min)} ~ ${fmt(max)}`;
  }

  refs.currentSourceName.textContent = state.currentSourceLabel || "未命名数据源";
  refs.currentSourceMeta.textContent = `${total} 条记录 · 来自 result/ 目录`;
}

bootstrap();
