
# 你先跑一次全量（282 页）生成一个“母表”CSV，比如：
cd /Volumes/TPU301
source .venv/bin/activate

python belfer_stpp_crawler.py \
  --keywords "" \
  --content-max-words 150 \
  --max-pages 500 \
  --api-limit 8 \
  --sleep 0.3 \
  --output belfer_stpp_all_150w.csv \
  --raw-dir belfer_raw_all_150w


# 1) 不加关键词（会接近 282 页）
cd /Volumes/TPU301
source .venv/bin/activate

python belfer_stpp_crawler.py \
  --content-max-words 150 \
  --max-pages 500 \
  --sleep 0.3 \
  --api-limit 8 \
  --output belfer_stpp_all_150w.csv \
  --raw-dir belfer_raw_all_150w

# 然后在本地对这个母表做二次筛选（不再访问 Belfer 网站、调参很快）。我已经给你加了脚本：/Volumes/TPU301/belfer_post_filter.py
# 二次筛选脚本怎么用


python belfer_post_filter.py \
  --input belfer_stpp_all_150w.csv \
  --output belfer_stpp_ai_150w.csv \
  --query '(AI OR "artificial intelligence") AND NOT podcast' \
  --matrix "/Volumes/TPU301/相关资料/分类矩阵.csv" \
  --emerging-tech-only \
  --overwrite-topic-with-matrix \
  --raw-dir belfer_raw_all_150w  


# 2) 只做一个简单包含词筛选（更像“快速调参”）
python belfer_post_filter.py \
  --input belfer_stpp_all_150w.csv \
  --output belfer_contains_ai.csv \
  --contains AI \
  --raw-dir belfer_raw_all_150w

# 改检索式：只改 --query，几秒钟重新产出一个新 CSV
# 改新兴科技范围：只改 分类矩阵.csv（或换一个矩阵文件/子集文件），再跑一次二次筛选
# 输出 topic 归档：用 --overwrite-topic-with-matrix 把 topic 直接改成矩阵主题（同时保留 matrix_topics）
# 如果你告诉我你们想要“二次筛选后导出字段顺序固定成你们那套列”（序号/英文名/国别/编号/时间/机构/主要内容/关键词…），我也可以把 belfer_post_filter.py 的输出列顺序锁死成模板。