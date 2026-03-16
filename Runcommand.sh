cd /Volumes/TPU301
source .venv/bin/activate
source .env

python belfer_llm_enrich.py \
  --output-raw-dir belfer_raw_llm_article_belfer \
  --output-csv belfer_llm_article_belfer_mother_table.csv \
  --crawl-keywords "" \
  --api-program-id "" \
  --api-type "" \
  --api-limit 8 \
  --max-pages 500 \
  --sleep 0.3 \
  --require-article \
  --source-contains "Belfer" \
  --model deepseek/deepseek-r1 \
  --resume

# 包含llm_enrich的爬虫运行方法

(.venv) (base) guo@guodeMacBook-Air TPU301 % cd /Volumes/TPU301
source .venv/bin/activate
set -a; source .env; set +a

python belfer_llm_enrich.py \
  --output-raw-dir belfer_raw_llm_article_belfer \
  --output-csv belfer_llm_article_belfer_mother_table.csv \
  --crawl-keywords "" \
  --api-program-id "" \
  --api-type "research_and_analysis" \
  --api-content-type "1" \
  --api-limit 8 \
  --max-pages 500 \
  --sleep 0.3 \
  --source-contains "Belfer" \
  --base-url "https://router.shengsuanyun.com" \
  --http-referer "https://www.postman.com" \
  --x-title "Postman" \
  --model deepseek/deepseek-v3.2 \
  --resume