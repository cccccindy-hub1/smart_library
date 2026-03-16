#!/usr/bin/env bash
set -euo pipefail

cd "/Volumes/TPU301"

if [[ -f ".venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

if [[ ! -f ".env" ]]; then
  echo "Missing .env. Copy .env.example -> .env and fill SHENGSUANYUN_API_KEY." >&2
  exit 1
fi

# .env should be valid shell syntax: KEY="value"
# shellcheck disable=SC1091
set -a
source ".env"
set +a

if [[ -z "${SHENGSUANYUN_API_KEY:-}" ]]; then
  echo "SHENGSUANYUN_API_KEY is empty. Please set it in .env" >&2
  exit 1
fi

BASE_URL="${SHENGSUANYUN_BASE_URL:-https://router.shengsuanyun.com}"
MODEL="${SHENGSUANYUN_MODEL:-deepseek/deepseek-r1}"

INPUT_RAW_DIR="${1:-belfer_raw_all_150w}"
OUTPUT_RAW_DIR="${2:-belfer_raw_llm}"
OUTPUT_CSV="${3:-belfer_llm_feedback.csv}"

python "belfer_llm_enrich.py" \
  --input-raw-dir "$INPUT_RAW_DIR" \
  --output-raw-dir "$OUTPUT_RAW_DIR" \
  --output-csv "$OUTPUT_CSV" \
  --base-url "$BASE_URL" \
  --model "$MODEL" \
  --api-key-env "SHENGSUANYUN_API_KEY" \
  --resume

