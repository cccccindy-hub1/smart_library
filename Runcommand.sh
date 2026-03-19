# ============================================================
# Smart Library - Windows PowerShell 启动命令
# 使用 py310 conda 环境
# ============================================================

---------- 1. 启动前端（静态文件服务，端口 8000）----------
在项目根目录运行：
  conda run -n py310 python -m http.server 8000

浏览器访问：
  http://localhost:8000/frontend/
  http://localhost:8000/frontend/crawl.html

---------- 2. 启动后端 FastAPI（端口 9000）----------
在项目根目录运行：
  conda run -n py310 uvicorn crawl_api_fastapi:app --host 0.0.0.0 --port 9000 --reload

---------- 3. 一键启动（PowerShell 复制粘贴即可）----------

# 3a. 启动前端（后台运行）
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd D:\self\smart_library; conda activate py310; python -m http.server 8000"

# 3b. 启动后端（后台运行）
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd D:\self\smart_library; conda activate py310; uvicorn crawl_api_fastapi:app --host 0.0.0.0 --port 9000 --reload"

# ============================================================
# 或者在同一个终端顺序启动（前端放后台 &）：
# conda activate py310
# Start-Job { cd D:\self\smart_library; python -m http.server 8000 }
# uvicorn crawl_api_fastapi:app --host 0.0.0.0 --port 9000 --reload
# ============================================================

# ---------- 4. 爬取任务命令（LLM enrich）----------
# conda activate py310
# python belfer_llm_enrich.py `
#   --output-raw-dir belfer_raw_llm_article_belfer `
#   --output-csv belfer_llm_article_belfer_mother_table.csv `
#   --crawl-keywords "" `
#   --api-program-id "5931" `
#   --api-type "research_and_analysis" `
#   --api-limit 8 `
#   --max-pages 500 `
#   --sleep 0.3 `
#   --source-contains "Belfer" `
#   --require-article `
#   --base-url "https://router.shengsuanyun.com/api/v1" `
#   --model "ali/qwen3-max-2026-01-23" `
#   --resume
