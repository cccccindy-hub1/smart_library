const refs = {
  jobStatus: document.getElementById("jobStatus"),
  csvList: document.getElementById("csvList"),
  refreshJobBtn: document.getElementById("refreshJobBtn"),
  refreshCsvBtn: document.getElementById("refreshCsvBtn"),
};

async function init() {
  refs.refreshJobBtn.addEventListener("click", loadLastJobStatus);
  refs.refreshCsvBtn.addEventListener("click", loadCsvList);
  await Promise.all([loadLastJobStatus(), loadCsvList()]);
}

async function loadLastJobStatus() {
  const raw = localStorage.getItem("gsm:lastCrawlJob");
  if (!raw) {
    refs.jobStatus.innerHTML = `<div class="muted">暂无历史任务。请先在“数据爬取”页面启动任务。</div>`;
    return;
  }

  let saved;
  try {
    saved = JSON.parse(raw);
  } catch {
    refs.jobStatus.innerHTML = `<div class="muted">历史任务记录损坏，建议重新启动任务。</div>`;
    return;
  }

  const apiBase = String(saved.apiBase || "http://localhost:9000").replace(/\/+$/, "");
  const jobId = String(saved.jobId || "");
  if (!jobId) {
    refs.jobStatus.innerHTML = `<div class="muted">暂无有效 Job ID。</div>`;
    return;
  }

  refs.jobStatus.innerHTML = `<div class="muted">正在读取任务 ${escapeHtml(jobId)} ...</div>`;
  try {
    const resp = await fetch(`${apiBase}/api/crawl/jobs/${encodeURIComponent(jobId)}`);
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data?.message || data?.detail || `HTTP ${resp.status}`);
    }

    refs.jobStatus.innerHTML = `
      <div><strong>Job ID:</strong> ${escapeHtml(jobId)}</div>
      <div><strong>状态:</strong> ${escapeHtml(String(data.status || "-"))}</div>
      <div><strong>开始时间:</strong> ${escapeHtml(String(data.started_at || "-"))}</div>
      <div><strong>结束时间:</strong> ${escapeHtml(String(data.ended_at || "-"))}</div>
      <div><strong>已处理:</strong> ${escapeHtml(String(data.processed ?? "-"))}</div>
      <div><strong>成功/失败:</strong> ${escapeHtml(String(data.success_count ?? "-"))} / ${escapeHtml(
      String(data.failed_count ?? "-")
    )}</div>
      <div class="muted">后端地址：${escapeHtml(apiBase)}</div>
    `;
  } catch (e) {
    refs.jobStatus.innerHTML = `
      <div><strong>Job ID:</strong> ${escapeHtml(jobId)}</div>
      <div class="muted">无法拉取实时状态：${escapeHtml(e.message)}</div>
      <div class="muted">后端地址：${escapeHtml(apiBase)}</div>
    `;
  }
}

async function loadCsvList() {
  refs.csvList.innerHTML = `<li class="muted">读取中...</li>`;
  let files = await listCsvFrom("../result/");
  let baseDir = "../result/";
  if (!files.length) {
    files = await listCsvFrom("../");
    baseDir = "../";
  }

  if (!files.length) {
    refs.csvList.innerHTML = `<li class="muted">未找到 CSV 文件（result/ 与根目录均为空）。</li>`;
    return;
  }

  refs.csvList.innerHTML = files
    .slice()
    .reverse()
    .map((f) => {
      const name = f.replace(/\.csv$/i, "");
      return `<li><span>${escapeHtml(name)}</span><a href="${baseDir}${encodeURI(f)}" target="_blank" rel="noopener noreferrer">打开</a></li>`;
    })
    .join("");
}

async function listCsvFrom(dirHref) {
  try {
    const resp = await fetch(dirHref);
    if (!resp.ok) return [];
    const html = await resp.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, "text/html");
    return Array.from(doc.querySelectorAll("a"))
      .map((a) => a.getAttribute("href") || "")
      .filter((href) => href.toLowerCase().endsWith(".csv") && !href.startsWith("._"))
      .sort();
  } catch {
    return [];
  }
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

init();
