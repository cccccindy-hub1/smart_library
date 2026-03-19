import argparse
import csv
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests

from belfer_stpp_crawler import (
    ALLOWED_CONTENT_PATH_PREFIXES,
    HEADERS,
    QueryParseError,
    build_api_search_url,
    canonicalize_url,
    compile_query,
    eval_rpn,
    fetch_api_search_meta,
    fetch_api_search_results,
    fetch_html,
    is_allowed_content_path,
    is_internal_belfer_url,
    load_matrix_items,
    match_matrix_items,
    parse_api_results_fragment,
    parse_detail_page,
    sanitize_text,
    sha1_text,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def read_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def write_json(path: str, data: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def count_existing_csv_rows(path: str) -> int:
    """
    Count existing data rows (excluding header). Returns 0 if file doesn't exist.
    """
    if not path or not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            r = csv.reader(f)
            # header
            next(r, None)
            n = 0
            for _ in r:
                n += 1
            return n
    except Exception:
        return 0


def open_csv_append(path: str, fieldnames: list[str]):
    """
    Open CSV for append; create & write header if needed.
    Returns (file_handle, DictWriter).
    """
    file_exists = os.path.exists(path)
    f = open(path, "a", encoding="utf-8-sig", newline="")
    w = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL, extrasaction="ignore", lineterminator="\n")
    if not file_exists or os.path.getsize(path) == 0:
        w.writeheader()
        f.flush()
    return f, w


def load_env_file(path: str, override: bool = False) -> None:
    """
    Load KEY=VALUE pairs from a .env-style file into os.environ.
    - Ignores blank lines and comments.
    - Supports optional "export KEY=VALUE".
    - Does not overwrite existing env vars by default.
    """
    if not path or not os.path.exists(path):
        return

    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export ") :].strip()
                if "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if not key:
                    continue

                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]

                if override or key not in os.environ:
                    os.environ[key] = value
    except Exception:
        # Fail-open: missing/invalid .env should not break the whole job.
        return


def extract_first_json_object(text: str) -> str | None:
    """
    Try to extract a JSON object from a model response.
    We look for the first {...} block that parses.
    """
    if not text:
        return None
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    # find candidate blocks
    for m in re.finditer(r"\{[\s\S]*\}", text):
        cand = m.group(0).strip()
        try:
            json.loads(cand)
            return cand
        except Exception:
            continue
    return None


def build_prompt(title: str, published: str, authors: list[str], topics: list[str], tags: list[str], body: str) -> str:
    """
    Ask for strict JSON only (machine-readable).
    """
    title = sanitize_text(title)
    published = sanitize_text(published)
    body = sanitize_text(body)
    a = "; ".join([sanitize_text(x) for x in authors if sanitize_text(x)])
    t = "; ".join([sanitize_text(x) for x in topics if sanitize_text(x)])
    g = "; ".join([sanitize_text(x) for x in tags if sanitize_text(x)])

    return (
        "Read the following think-tank article in full and extract structured information.\n"
        "Output STRICT JSON ONLY (no explanations, no prefixes/suffixes, no markdown).\n"
        "JSON schema:\n"
        "{\n"
        '  "main_content_en": "Main content in English (about 150-250 words). Must reflect the body; do not copy the title.",\n'
        '  "main_content_zh": "中文主要内容提取（约200-400字）。必须基于正文，不要照抄标题。",\n'
        '  "keywords_en": ["keyword1", "keyword2", "... (10-20 items)"],\n'
        '  "keywords_zh": ["关键词1", "关键词2", "... (10-20个)"],\n'
        '  "topic_words_en": ["topic1", "topic2", "... (3-8 items)"],\n'
        '  "topic_words_zh": ["主题1", "主题2", "... (3-8个)"],\n'
        '  "relevance_score": 0.0\n'
        "}\n"
        "Requirements:\n"
        "- Return BOTH English and Chinese fields as specified above.\n"
        "- main_content_en and main_content_zh should each be a cohesive paragraph.\n"
        "- keywords_en: 10-20 items, prefer technical/policy terms.\n"
        "- keywords_zh: 10-20 items, prefer technical/policy terms.\n"
        "- topic_words_en: 3-8 short topic labels.\n"
        "- topic_words_zh: 3-8 short topic labels.\n"
        "- relevance_score: 0~1 (higher = more related to emerging tech / science & tech policy).\n\n"
        "Metadata:\n"
        f"- title: {title}\n"
        f"- published: {published}\n"
        f"- authors: {a}\n"
        f"- topics: {t}\n"
        f"- tags: {g}\n\n"
        "Body:\n"
        f"{body}\n"
    )


def call_chat_completions(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    http_referer: str,
    x_title: str,
    timeout_sec: int,
    max_tokens: int,
    temperature: float,
    top_p: float,
    retries: int,
    sleep_base_sec: float,
) -> dict[str, Any]:
    """
    OpenAI-compatible /v1/chat/completions.
    """
    # Shengsuanyun router commonly exposes OpenAI-compatible endpoint under /api/v1/chat/completions.
    # Accept base_url in any of these forms:
    # - https://router.shengsuanyun.com
    # - https://router.shengsuanyun.com/api
    # - https://router.shengsuanyun.com/api/v1
    bu = base_url.rstrip("/")
    if bu.endswith("/api/v1"):
        url = bu + "/chat/completions"
    elif bu.endswith("/api"):
        url = bu + "/v1/chat/completions"
    else:
        url = bu + "/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if http_referer:
        headers["HTTP-Referer"] = http_referer
    if x_title:
        headers["X-Title"] = x_title
    payload = {
        "model": model,
        "stream": False,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
    }

    last_err: Exception | None = None
    for i in range(max(1, retries)):
        try:
            resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=timeout_sec)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f"transient status {resp.status_code}: {resp.text[:200]}")
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, dict) else {"_raw": data}
        except Exception as e:
            last_err = e
            if i == retries - 1:
                break
            time.sleep(sleep_base_sec * (2**i))
    raise RuntimeError(f"LLM call failed after retries: {last_err}")


def parse_llm_json(resp: dict[str, Any]) -> dict[str, Any]:
    """
    Extract assistant content and parse JSON.
    """
    choices = resp.get("choices")
    if not isinstance(choices, list) or not choices:
        return {"_error": "no_choices", "_resp": resp}
    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, str) or not content.strip():
        return {"_error": "empty_content", "_resp": resp}

    json_text = extract_first_json_object(content)
    if not json_text:
        return {"_error": "no_json_found", "_content": content[:2000], "_resp": resp}
    try:
        parsed = json.loads(json_text)
        return parsed if isinstance(parsed, dict) else {"_error": "json_not_object", "_parsed": parsed}
    except Exception as e:
        return {"_error": f"json_parse_failed: {e}", "_json_text": json_text[:2000], "_resp": resp}


def list_json_files(dir_path: str) -> list[str]:
    files = []
    for name in os.listdir(dir_path):
        if name.endswith(".json") and not name.startswith("._"):
            files.append(os.path.join(dir_path, name))
    files.sort()
    return files


def iter_belfer_raw_docs(
    *,
    keywords: str,
    api_program_id: str,
    api_type: str,
    api_content_type: str,
    api_limit: int,
    max_pages: int,
    sleep_sec: float,
    allowed_prefixes: tuple[str, ...],
    source_exact: str,
    source_contains: str,
    query: str,
    require_type_article: bool,
) -> list[dict[str, Any]]:
    """
    Crawl Belfer STPP-like list via /api/search/search, then fetch detail pages and parse into raw doc dicts.
    Returns a list of raw dicts containing at least: node_id/canonical_url/title/published/authors/topics/tags/summary/body.
    """
    # Build a minimal config-like object (duck typing): build_api_search_url expects attributes.
    class _Cfg:
        api_search_endpoint = "https://www.belfercenter.org/api/search/search"

        def __init__(self):
            self.api_program_id = api_program_id
            self.api_type = api_type
            self.api_content_type = api_content_type
            self.api_limit = api_limit
            self.api_keywords = keywords

    cfg = _Cfg()

    rpn = compile_query(query) if query.strip() else None

    # Determine total pages from meta (filtered by keywords/type/program)
    meta0 = fetch_api_search_meta(cfg, page=1)
    total_pages = meta0.get("totalPages") if isinstance(meta0, dict) else None
    if not isinstance(total_pages, int) or total_pages <= 0:
        total_pages = max_pages
    total_pages = min(int(total_pages), int(max_pages))

    seen_url: set[str] = set()

    for page in range(1, total_pages + 1):
        api_url = build_api_search_url(cfg, page=page)
        print(f"[INFO] crawl page {page}/{total_pages}: {api_url}")
        results_html = fetch_api_search_results(cfg, page=page)
        records = parse_api_results_fragment(results_html, topic_name="All")
        if not records:
            break

        for r in records:
            url = canonicalize_url(r.get("url", ""))
            if not url:
                continue
            if url in seen_url:
                continue

            if not is_internal_belfer_url(url):
                continue
            if not is_allowed_content_path(url, allowed_prefixes):
                continue

            src = sanitize_text(r.get("source", ""))
            if source_exact and src != source_exact:
                continue
            if source_contains and (source_contains.lower() not in src.lower()):
                continue

            # Only keep academic-paper-like "Article" items if requested
            if require_type_article:
                typ = sanitize_text(r.get("type", ""))
                if typ.lower() != "article":
                    continue

            # boolean query prefilter (cheap): over title/source/type
            if rpn is not None:
                pre = " ".join([r.get("title", ""), r.get("type", ""), r.get("source", ""), r.get("topic", "")])
                try:
                    if not eval_rpn(rpn, pre):
                        continue
                except QueryParseError:
                    pass

            # fetch detail
            try:
                html = fetch_html(url)
                detail = parse_detail_page(html, url)
                # keep list-page fields too
                detail["_list"] = {
                    "type": r.get("type", ""),
                    "source": r.get("source", ""),
                    "date": r.get("date", ""),
                    "topic": r.get("topic", ""),
                }
                seen_url.add(detail.get("canonical_url") or url)
                yield detail
            except Exception as e:
                print(f"[WARN] detail fetch/parse failed: {e} -> {url}")
                continue

        time.sleep(float(sleep_sec))

    # generator ends


def main() -> None:
    # Load .env from project root / script directory when available.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    load_env_file(os.path.join(script_dir, ".env"), override=False)
    load_env_file(os.path.join(os.getcwd(), ".env"), override=False)

    parser = argparse.ArgumentParser(description="Enrich Belfer raw json with LLM extraction (summary/points/keywords).")
    # Two modes:
    # 1) enrich-only: provide --input-raw-dir
    # 2) crawl+enrich: omit --input-raw-dir and provide crawl params (keywords, source filters, etc.)
    parser.add_argument("--input-raw-dir", default="", help="Directory containing per-doc raw json (from crawler).")
    parser.add_argument("--output-raw-dir", required=True, help="Directory to write per-doc enriched json.")
    parser.add_argument("--output-csv", required=True, help="Output CSV summary of LLM fields.")
    parser.add_argument("--base-url", default="https://router.shengsuanyun.com/api/v1", help="API base URL (host).")
    parser.add_argument("--model", default="ali/qwen3-max-2026-01-23", help="Model name.")
    parser.add_argument("--api-key-env", default="SHENGSUANYUN_API_KEY", help="Env var name holding API key.")
    parser.add_argument("--http-referer", default="", help="Optional HTTP-Referer header for the LLM router.")
    parser.add_argument("--x-title", default="", help="Optional X-Title header for the LLM router.")
    parser.add_argument("--max-tokens", type=int, default=1200, help="Max tokens for model output.")
    parser.add_argument("--temperature", type=float, default=0.6, help="Sampling temperature.")
    parser.add_argument("--top-p", type=float, default=0.7, help="Nucleus sampling top_p.")
    parser.add_argument("--timeout-sec", type=int, default=120, help="HTTP timeout seconds.")
    parser.add_argument("--retries", type=int, default=3, help="Retries for transient failures.")
    parser.add_argument("--sleep-base-sec", type=float, default=1.5, help="Base backoff seconds.")
    parser.add_argument("--resume", action="store_true", help="Skip docs already enriched in output-raw-dir.")
    parser.add_argument("--limit", type=int, default=0, help="Optional limit of docs to process (0 = all).")
    # Crawl params (used when --input-raw-dir is empty)
    parser.add_argument("--crawl-keywords", default="", help="Belfer search API keywords (param: keywords=...).")
    parser.add_argument("--api-program-id", default="5931", help="Program id for /api/search/search (default: 5931 for STPP).")
    parser.add_argument("--api-type", default="research_and_analysis", help="Type facet for /api/search/search (default: research_and_analysis).")
    parser.add_argument("--api-content-type", default="", help='Content Type facet for /api/search/search (e.g., Article is \"1\").')
    parser.add_argument("--api-limit", type=int, default=8, help="Per-page limit for /api/search/search (default: 8).")
    parser.add_argument("--max-pages", type=int, default=500, help="Max pages to crawl (cap by meta.totalPages).")
    parser.add_argument("--sleep", type=float, default=0.3, help="Sleep seconds between pages during crawl.")
    parser.add_argument("--source-exact", default="", help="Keep only list items where source equals this value exactly.")
    parser.add_argument("--source-contains", default="", help="Keep only list items where source contains this substring (case-insensitive).")
    parser.add_argument("--query", default="", help="Boolean query (AND/OR/NOT) to prefilter before fetching details.")
    parser.add_argument(
        "--require-article",
        action="store_true",
        help='Only keep items whose list "type" equals "Article" (discard Policy Briefs, Reports & Papers, etc.).',
    )
    parser.add_argument("--matrix", default="", help="Optional 分类矩阵.csv to fill item_en/item_zh/matrix_topics.")
    parser.add_argument("--matrix-topic-max", type=int, default=10, help="Max matrix topics to keep per doc.")
    args = parser.parse_args()

    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        raise SystemExit(f"Missing API key. Please set env var {args.api_key_env}.")

    ensure_dir(args.output_raw_dir)

    matrix_items = load_matrix_items(args.matrix) if args.matrix else []

    raw_docs: list[dict[str, Any]] = []
    files: list[str] = []
    if args.input_raw_dir.strip():
        files = list_json_files(args.input_raw_dir)
        if args.limit and args.limit > 0:
            files = files[: int(args.limit)]
    else:
        # crawl generator is consumed in the main loop to allow streaming output
        pass

    # Output exactly the same “mother table” template as belfer_stpp_crawler.py
    fieldnames = [
        "序号",
        "英文名",
        "国别",
        "编号",
        "时间",
        "机构",
        "主要内容",
        "主要内容_zh",
        "关键词",
        "关键词_zh",
        "主题词",
        "主题词_zh",
        "item_en",
        "item_zh",
        "title",
        "url",
        "type",
        "source",
        "topic",
        "matrix_topics",
        "authors",
        "author_affiliations",
        "topics",
        "tags",
    ]

    # Always write to a fresh CSV per run under a \"result\" subdirectory,
    # with creation time suffix to avoid overwriting previous outputs.
    base_csv = args.output_csv
    base_dir = os.path.dirname(base_csv) or os.getcwd()
    base_name = os.path.splitext(os.path.basename(base_csv))[0] or "belfer_llm_enrich"
    result_dir = os.path.join(base_dir, "result")
    ensure_dir(result_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(result_dir, f"{base_name}_{ts}.csv")

    start_seq = count_existing_csv_rows(csv_path)
    fcsv, writer = open_csv_append(csv_path, fieldnames=fieldnames)
    try:
        # Build iterable: either existing raw json files, or a crawl generator.
        if files:
            def _iter():
                for p in files:
                    yield (p, read_json(p))
        else:
            def _iter():
                gen = iter_belfer_raw_docs(
                    keywords=args.crawl_keywords,
                    api_program_id=str(args.api_program_id).strip(),
                    api_type=str(args.api_type).strip(),
                    api_content_type=str(args.api_content_type).strip(),
                    api_limit=int(args.api_limit),
                    max_pages=int(args.max_pages),
                    sleep_sec=float(args.sleep),
                    allowed_prefixes=ALLOWED_CONTENT_PATH_PREFIXES,
                    source_exact=str(args.source_exact).strip(),
                    source_contains=str(args.source_contains).strip(),
                    query=str(args.query).strip(),
                    require_type_article=bool(args.require_article),
                )
                for i, d in enumerate(gen, start=1):
                    if args.limit and args.limit > 0 and i > int(args.limit):
                        break
                    yield (f"_crawl_{i}", d)

        processed = 0
        for src_id, raw in _iter():
            doc_id = sanitize_text(str(raw.get("node_id") or "")).strip()
            url = sanitize_text(str(raw.get("canonical_url") or raw.get("url") or "")).strip()
            if not doc_id:
                doc_id = sha1_text(url)[:12] if url else os.path.splitext(os.path.basename(src_id))[0]

            out_path = os.path.join(args.output_raw_dir, f"{doc_id}.json")
            if args.resume and os.path.exists(out_path):
                continue

            title = str(raw.get("title") or "")
            published = str(raw.get("published") or "")
            authors = raw.get("authors") or []
            topics = raw.get("topics") or []
            tags = raw.get("tags") or []
            body = str(raw.get("body") or "")

            prompt = build_prompt(
                title=title,
                published=published,
                authors=authors if isinstance(authors, list) else [],
                topics=topics if isinstance(topics, list) else [],
                tags=tags if isinstance(tags, list) else [],
                body=body,
            )

            resp = call_chat_completions(
                base_url=args.base_url,
                api_key=api_key,
                model=args.model,
                prompt=prompt,
                http_referer=str(args.http_referer).strip(),
                x_title=str(args.x_title).strip(),
                timeout_sec=int(args.timeout_sec),
                max_tokens=int(args.max_tokens),
                temperature=float(args.temperature),
                top_p=float(args.top_p),
                retries=int(args.retries),
                sleep_base_sec=float(args.sleep_base_sec),
            )
            parsed = parse_llm_json(resp)

            enriched = dict(raw)
            enriched["llm"] = {
                "model": args.model,
                "time": utc_now_iso(),
                "prompt_hash": sha1_text(prompt),
                "result": parsed,
            }
            write_json(out_path, enriched)

            # Fill matrix fields if matrix is provided (match over LLM main content + title + tags/topics + body)
            item_en: list[str] = []
            item_zh: list[str] = []
            matrix_topics = ""
            if matrix_items:
                llm_main_en = sanitize_text(str(parsed.get("main_content_en") or "")) if isinstance(parsed, dict) else ""
                llm_main_zh = sanitize_text(str(parsed.get("main_content_zh") or "")) if isinstance(parsed, dict) else ""
                hay = " ".join(
                    [
                        sanitize_text(title),
                        llm_main_en,
                        llm_main_zh,
                        sanitize_text("; ".join(tags) if isinstance(tags, list) else str(tags)),
                        sanitize_text("; ".join(topics) if isinstance(topics, list) else str(topics)),
                        sanitize_text(body),
                    ]
                ).strip()
                item_en, item_zh = match_matrix_items(hay, matrix_items)
                matrix_topics = "; ".join(item_en[: max(0, int(args.matrix_topic_max))])

            # LLM fields (prefer explicit en/zh; keep backward-compatible fallbacks)
            llm_main_en = sanitize_text(str(parsed.get("main_content_en") or "")) if isinstance(parsed, dict) else ""
            llm_main_zh = sanitize_text(str(parsed.get("main_content_zh") or "")) if isinstance(parsed, dict) else ""
            if not llm_main_en:
                llm_main_en = sanitize_text(str(parsed.get("main_content_zh") or "")) if isinstance(parsed, dict) else ""
            if not llm_main_zh:
                llm_main_zh = sanitize_text(str(parsed.get("main_content_zh") or "")) if isinstance(parsed, dict) else ""

            llm_topic_words_en = (parsed.get("topic_words_en") if isinstance(parsed, dict) else None) or None
            llm_topic_words_zh = (parsed.get("topic_words_zh") if isinstance(parsed, dict) else None) or (
                parsed.get("topics_zh") if isinstance(parsed, dict) else None
            )
            llm_keywords_en = (parsed.get("keywords_en") if isinstance(parsed, dict) else None) or None
            llm_keywords_zh = (parsed.get("keywords_zh") if isinstance(parsed, dict) else None) or None

            llm_topic_words_en_str = (
                "; ".join([sanitize_text(x) for x in llm_topic_words_en]) if isinstance(llm_topic_words_en, list) else ""
            )
            llm_topic_words_zh_str = (
                "; ".join([sanitize_text(x) for x in llm_topic_words_zh]) if isinstance(llm_topic_words_zh, list) else ""
            )
            llm_keywords_en_str = (
                "; ".join([sanitize_text(x) for x in llm_keywords_en]) if isinstance(llm_keywords_en, list) else ""
            )
            llm_keywords_zh_str = (
                "; ".join([sanitize_text(x) for x in llm_keywords_zh]) if isinstance(llm_keywords_zh, list) else ""
            )

            list_type = sanitize_text(str((raw.get("_list") or {}).get("type") or ""))
            list_source = sanitize_text(str((raw.get("_list") or {}).get("source") or ""))
            list_topic = sanitize_text(str((raw.get("_list") or {}).get("topic") or raw.get("topic") or ""))

            row = {
                "序号": str(start_seq + processed + 1),
                "英文名": "Belfer Center for Science and International Affairs",
                "国别": "USA",
                "编号": doc_id,
                "时间": sanitize_text(published),
                "机构": "Belfer Center",
                # LLM 覆盖/填充
                "主要内容": llm_main_en,
                "主要内容_zh": llm_main_zh,
                "关键词": llm_keywords_en_str,
                "关键词_zh": llm_keywords_zh_str,
                "主题词": llm_topic_words_en_str or llm_keywords_en_str,
                "主题词_zh": llm_topic_words_zh_str or llm_keywords_zh_str,
                "item_en": "; ".join(item_en),
                "item_zh": "; ".join(item_zh),
                "title": sanitize_text(title),
                "url": url,
                "type": list_type,
                "source": list_source,
                "topic": list_topic,
                "matrix_topics": matrix_topics,
                "authors": "; ".join([sanitize_text(x) for x in authors]) if isinstance(authors, list) else "",
                "author_affiliations": "; ".join([sanitize_text(x) for x in (raw.get("author_affiliations") or [])]) if isinstance(raw.get("author_affiliations"), list) else "",
                "topics": "; ".join([sanitize_text(x) for x in topics]) if isinstance(topics, list) else "",
                "tags": "; ".join([sanitize_text(x) for x in tags]) if isinstance(tags, list) else "",
            }
            writer.writerow(row)
            fcsv.flush()
            processed += 1

            if processed % 10 == 0:
                print(f"[INFO] processed {processed}")
    finally:
        fcsv.close()


if __name__ == "__main__":
    main()

