# Frontend 使用说明

这是一个零依赖前端页面，包含两个子页面：

- 数据浏览页：浏览 `belfer_llm_article_belfer_mother_table.csv`
- 爬取任务页：对接 `belfer_llm_enrich.py` 对应后端任务接口

## 功能

- 关键词搜索（标题、正文、关键词、作者、标签）
- 按 `type` / `topic` / `source` 筛选
- 分页浏览
- 文章详情抽屉（中英文摘要、关键词、主题词、标签）
- 新增“爬取任务”页面，支持配置常用爬取参数并轮询状态/日志

## 启动方式

建议在项目根目录启动一个本地静态服务器（避免浏览器直接打开 `file://` 时的跨域限制）：

```bash
python -m http.server 8000
```

然后在浏览器打开：

- [http://localhost:8000/frontend/](http://localhost:8000/frontend/)
- [http://localhost:8000/frontend/crawl.html](http://localhost:8000/frontend/crawl.html)

## 启动后端 API（任选其一）

先安装依赖：

```bash
pip install -r requirements.txt
```

### FastAPI

```bash
uvicorn crawl_api_fastapi:app --host 0.0.0.0 --port 9000 --reload
```

### Flask

```bash
python crawl_api_flask.py
```

> 默认 Flask 示例端口是 `8000`。如果你前端静态服务也跑在 `8000`，请把 Flask 改到其他端口（比如 `9000`）或把前端后端分开端口。

## 数据源

默认读取项目根目录下：

- `belfer_llm_article_belfer_mother_table.csv`

如需切换数据文件，修改 `frontend/app.js` 的 `CSV_PATH`。

## 爬取任务页接口约定

前端将 `belfer_llm_enrich.py` 视作后端任务执行器，默认请求这些接口（可在页面上改后端地址）：

- `POST /api/crawl/start`：启动任务  
  请求体示例：
  ```json
  {
    "command": "belfer_llm_enrich",
    "args": {
      "model": "ali/qwen3-max-2026-01-23",
      "output_raw_dir": "belfer_raw_llm_article_belfer",
      "output_csv": "belfer_llm_article_belfer_mother_table.csv",
      "crawl_keywords": "",
      "api_program_id": "5931",
      "api_type": "research_and_analysis",
      "api_content_type": "",
      "api_limit": 8,
      "max_pages": 500,
      "sleep": 0.3,
      "source_exact": "",
      "source_contains": "Belfer",
      "query": "",
      "limit": 0,
      "require_article": true,
      "resume": true
    }
  }
  ```

- `GET /api/crawl/jobs/{jobId}`：读取任务状态
- `GET /api/crawl/jobs/{jobId}/logs?from=<cursor>`：增量读取日志
- `POST /api/crawl/jobs/{jobId}/stop`：停止任务

建议后端返回 JSON，至少包含：

- 启动接口：`{ "job_id": "..." }`
- 状态接口：`status`, `started_at`, `ended_at`, `processed`, `success_count`, `failed_count`
- 日志接口：`lines`（字符串数组）, `next_cursor`（数字）
