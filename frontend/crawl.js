const state = {
  apiBase: "http://localhost:9000",
  jobId: "",
  pollTimer: null,
  logCursor: 0,
};

const refs = {
  crawlForm: document.getElementById("crawlForm"),
  apiBase: document.getElementById("apiBase"),
  model: document.getElementById("model"),
  outputRawDir: document.getElementById("outputRawDir"),
  outputCsv: document.getElementById("outputCsv"),
  crawlKeywords: document.getElementById("crawlKeywords"),
  apiProgramId: document.getElementById("apiProgramId"),
  apiSearchEndpoint: document.getElementById("apiSearchEndpoint"),
  apiType: document.getElementById("apiType"),
  apiContentType: document.getElementById("apiContentType"),
  apiLimit: document.getElementById("apiLimit"),
  maxPages: document.getElementById("maxPages"),
  sleep: document.getElementById("sleep"),
  sourceExact: document.getElementById("sourceExact"),
  sourceContains: document.getElementById("sourceContains"),
  query: document.getElementById("query"),
  limit: document.getElementById("limit"),
  requireArticle: document.getElementById("requireArticle"),
  resume: document.getElementById("resume"),
  startBtn: document.getElementById("startBtn"),
  refreshBtn: document.getElementById("refreshBtn"),
  stopBtn: document.getElementById("stopBtn"),
  jobMeta: document.getElementById("jobMeta"),
  jobLogs: document.getElementById("jobLogs"),
  runStartedAt: document.getElementById("runStartedAt"),
  runDuration: document.getElementById("runDuration"),
};

function init() {
  refs.crawlForm.addEventListener("submit", onSubmit);
  refs.refreshBtn.addEventListener("click", refreshOnce);
  refs.stopBtn.addEventListener("click", stopJob);
  refs.apiBase.addEventListener("change", () => {
    state.apiBase = refs.apiBase.value.trim().replace(/\/+$/, "");
  });

  state.apiBase = refs.apiBase.value.trim().replace(/\/+$/, "");
  loadLastJob();
}

async function onSubmit(e) {
  e.preventDefault();
  clearError();
  setStatus("正在提交任务...");

  try {
    const payload = buildPayload();
    const resp = await apiFetch("/api/crawl/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    state.jobId = String(resp.job_id || "").trim();
    if (!state.jobId) {
      throw new Error("后端未返回 job_id");
    }

    state.logCursor = 0;
    saveLastJob();
    setStatus(`任务已启动，Job ID: ${escapeHtml(state.jobId)}`);
    refs.runStartedAt.textContent = formatLocalTime(new Date());
    refs.jobLogs.textContent = "";
    startPolling();
    await refreshOnce();
  } catch (err) {
    setError(`启动失败: ${err.message}`);
  }
}

function buildPayload() {
  state.apiBase = refs.apiBase.value.trim().replace(/\/+$/, "");
  return {
    command: "belfer_llm_enrich",
    args: {
      model: refs.model.value.trim(),
      output_raw_dir: refs.outputRawDir.value.trim(),
      output_csv: refs.outputCsv.value.trim(),
      crawl_keywords: refs.crawlKeywords.value.trim(),
      api_program_id: refs.apiProgramId.value.trim(),
      api_search_endpoint: refs.apiSearchEndpoint.value.trim(),
      api_type: refs.apiType.value.trim(),
      api_content_type: refs.apiContentType.value.trim(),
      api_limit: Number(refs.apiLimit.value),
      max_pages: Number(refs.maxPages.value),
      sleep: Number(refs.sleep.value),
      source_exact: refs.sourceExact.value.trim(),
      source_contains: refs.sourceContains.value.trim(),
      query: refs.query.value.trim(),
      limit: Number(refs.limit.value),
      require_article: refs.requireArticle.checked,
      resume: refs.resume.checked,
    },
  };
}

function startPolling() {
  stopPolling();
  state.pollTimer = setInterval(() => {
    refreshOnce().catch(() => {
      // keep polling; surface errors in UI
    });
  }, 2500);
}

function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

async function refreshOnce() {
  clearError();
  if (!state.jobId) {
    setError("暂无 Job ID，请先启动任务。");
    return;
  }

  try {
    const status = await apiFetch(`/api/crawl/jobs/${encodeURIComponent(state.jobId)}`);
    renderStatus(status);

    const logs = await apiFetch(
      `/api/crawl/jobs/${encodeURIComponent(state.jobId)}/logs?from=${encodeURIComponent(String(state.logCursor))}`
    );
    appendLogs(logs);

    if (isFinishedStatus(status.status)) {
      stopPolling();
    }
  } catch (err) {
    setError(`刷新失败: ${err.message}`);
  }
}

async function stopJob() {
  clearError();
  if (!state.jobId) {
    setError("暂无运行中的任务。");
    return;
  }
  try {
    await apiFetch(`/api/crawl/jobs/${encodeURIComponent(state.jobId)}/stop`, { method: "POST" });
    setStatus(`已发送停止请求: ${escapeHtml(state.jobId)}`);
    await refreshOnce();
  } catch (err) {
    setError(`停止失败: ${err.message}`);
  }
}

function renderStatus(status) {
  const startedAt = status.started_at || "-";
  const endedAt = status.ended_at || "-";
  const processed = status.processed ?? "-";
  const ok = status.success_count ?? "-";
  const failed = status.failed_count ?? "-";
  const currentStatus = status.status || "unknown";

  refs.jobMeta.innerHTML = `
    <div><strong>Job ID:</strong> ${escapeHtml(state.jobId)}</div>
    <div><strong>状态:</strong> ${escapeHtml(currentStatus)}</div>
    <div><strong>开始时间:</strong> ${escapeHtml(startedAt)}</div>
    <div><strong>结束时间:</strong> ${escapeHtml(endedAt)}</div>
    <div><strong>已处理:</strong> ${escapeHtml(String(processed))}</div>
    <div><strong>成功:</strong> ${escapeHtml(String(ok))} / <strong>失败:</strong> ${escapeHtml(String(failed))}</div>
  `;
  refs.runStartedAt.textContent = startedAt && startedAt !== "-" ? String(startedAt) : refs.runStartedAt.textContent;
  refs.runDuration.textContent = calcDurationText(startedAt, endedAt);
}

function appendLogs(logResp) {
  const lines = Array.isArray(logResp.lines) ? logResp.lines : [];
  if (!lines.length) return;
  refs.jobLogs.textContent += lines.join("\n") + "\n";
  refs.jobLogs.scrollTop = refs.jobLogs.scrollHeight;
  state.logCursor = Number(logResp.next_cursor || state.logCursor + lines.length);
}

function isFinishedStatus(s) {
  return ["succeeded", "failed", "cancelled", "stopped", "finished"].includes(String(s).toLowerCase());
}

function setStatus(text) {
  refs.jobMeta.innerHTML = `<div>${escapeHtml(text)}</div>`;
}

function setError(text) {
  refs.jobMeta.innerHTML = `<div class="error-text">${escapeHtml(text)}</div>`;
}

function clearError() {
  // keep as no-op for now; status panel is fully rendered by next action
}

async function apiFetch(path, options = {}) {
  const base = state.apiBase || refs.apiBase.value.trim().replace(/\/+$/, "");
  if (!base) {
    throw new Error("请填写后端地址");
  }
  const resp = await fetch(`${base}${path}`, options);
  const isJson = (resp.headers.get("content-type") || "").includes("application/json");
  const data = isJson ? await resp.json() : await resp.text();
  if (!resp.ok) {
    const msg = typeof data === "string" ? data : data.message || JSON.stringify(data);
    throw new Error(msg || `HTTP ${resp.status}`);
  }
  return data;
}

function saveLastJob() {
  try {
    localStorage.setItem(
      "gsm:lastCrawlJob",
      JSON.stringify({
        apiBase: state.apiBase,
        jobId: state.jobId,
        logCursor: state.logCursor,
      })
    );
  } catch (_err) {
    // ignore
  }
}

function loadLastJob() {
  try {
    const raw = localStorage.getItem("gsm:lastCrawlJob");
    if (!raw) return;
    const saved = JSON.parse(raw);
    if (saved && typeof saved === "object") {
      if (saved.apiBase) {
        refs.apiBase.value = String(saved.apiBase);
        state.apiBase = String(saved.apiBase);
      }
      if (saved.jobId) {
        state.jobId = String(saved.jobId);
        state.logCursor = Number(saved.logCursor || 0);
        setStatus(`已恢复上次任务: ${escapeHtml(state.jobId)}`);
        startPolling();
        refreshOnce().catch(() => {});
      }
    }
  } catch (_err) {
    // ignore
  }
}

function formatLocalTime(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(
    d.getMinutes()
  )}:${pad(d.getSeconds())}`;
}

function calcDurationText(startedAt, endedAt) {
  if (!startedAt || startedAt === "-") return "-";
  const startMs = Date.parse(String(startedAt));
  if (Number.isNaN(startMs)) return "-";

  const endMs = endedAt && endedAt !== "-" ? Date.parse(String(endedAt)) : Date.now();
  const finalMs = Number.isNaN(endMs) ? Date.now() : endMs;
  const diff = Math.max(0, finalMs - startMs);

  const totalSec = Math.floor(diff / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;

  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
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
