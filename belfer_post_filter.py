import argparse
import csv
import os
import json

from belfer_stpp_crawler import (
    QueryParseError,
    compile_query,
    eval_rpn,
    load_matrix_items,
    match_matrix_items,
    sanitize_text,
    sha1_text,
)


def read_rows(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def write_rows(path: str, rows: list[dict], fieldnames: list[str]) -> None:
    if not rows:
        print("[WARN] No rows to write.")
        return
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            quoting=csv.QUOTE_ALL,
            extrasaction="ignore",
            lineterminator="\n",
        )
        w.writeheader()
        for r in rows:
            w.writerow(r)


def load_raw_doc(raw_dir: str, doc_id: str, url: str) -> dict | None:
    """
    Try to load raw json by doc_id first; fallback to sha1(url)[:12].
    """
    if not raw_dir:
        return None
    candidates = []
    if doc_id:
        candidates.append(os.path.join(raw_dir, f"{doc_id}.json"))
    if url:
        candidates.append(os.path.join(raw_dir, f"{sha1_text(url)[:12]}.json"))
    for p in candidates:
        if p and os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
    return None


def main():
    parser = argparse.ArgumentParser(description="Second-pass filter for Belfer STPP exports (local, fast iteration).")
    parser.add_argument("--input", required=True, help="Input CSV (e.g., belfer_stpp_all_150w.csv).")
    parser.add_argument("--output", required=True, help="Output CSV after filtering.")
    parser.add_argument("--query", default="", help="Boolean query (AND/OR/NOT, parentheses, quoted phrases).")
    parser.add_argument("--contains", default="", help="Simple substring filter (case-insensitive) over title/summary/body.")
    parser.add_argument("--source-exact", default="", help='Keep only rows where column "source" equals this value (exact match).')
    parser.add_argument("--source-contains", default="", help='Keep only rows where column "source" contains this substring (case-insensitive).')
    parser.add_argument("--matrix", default="", help="Path to 分类矩阵.csv for item_en/item_zh tagging.")
    parser.add_argument("--emerging-tech-only", action="store_true", help="Keep only docs that match at least one matrix item.")
    parser.add_argument("--overwrite-topic-with-matrix", action="store_true", help="Overwrite topic with matrix_topics.")
    parser.add_argument("--matrix-topic-max", type=int, default=10, help="Max matrix topics to keep per doc.")
    parser.add_argument("--raw-dir", default="", help="Optional raw json dir to use full summary/body for filtering.")
    parser.add_argument(
        "--backfill-from-raw",
        action="store_true",
        help="If raw-dir provided, backfill authors/topics/tags/关键词/时间/主要内容 from raw json when present.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Optional limit rows in output (0 = no limit).")
    args = parser.parse_args()

    rows = read_rows(args.input)
    print(f"[INFO] loaded rows: {len(rows)}")

    rpn = compile_query(args.query) if args.query.strip() else None
    contains = args.contains.strip().lower()
    source_exact = args.source_exact.strip()
    source_contains = args.source_contains.strip().lower()

    matrix_items = load_matrix_items(args.matrix) if args.matrix else []

    out: list[dict] = []
    seen_url: set[str] = set()

    for row in rows:
        url = (row.get("url") or "").strip()
        if url and url in seen_url:
            continue

        doc_id = (row.get("编号") or "").strip()
        raw = load_raw_doc(args.raw_dir, doc_id=doc_id, url=url)

        # source filter (exact/contains)
        src = sanitize_text(row.get("source", ""))
        if source_exact and (src != source_exact):
            continue
        if source_contains and (source_contains not in src.lower()):
            continue

        title = sanitize_text(row.get("title", ""))
        summary = sanitize_text((raw or {}).get("summary") or row.get("主要内容", ""))
        body = sanitize_text((raw or {}).get("body") or "")
        tags = sanitize_text(row.get("tags", ""))
        topics = sanitize_text(row.get("topics", ""))
        keywords = sanitize_text(row.get("关键词", ""))

        hay = " ".join([title, summary, keywords, topics, tags, body]).strip()

        if contains and (contains not in hay.lower()):
            continue

        if rpn is not None:
            try:
                if not eval_rpn(rpn, hay):
                    continue
            except QueryParseError:
                # 如果表达式写错，宁可不过滤也不要误删
                pass

        # Backfill from raw json (optional): ensures the output has authors/topics/tags/keywords even if mother CSV was generated w/ --no-detail
        if args.backfill_from_raw and isinstance(raw, dict):
            # fixed fields
            if not (row.get("英文名") or "").strip():
                row["英文名"] = "Belfer Center for Science and International Affairs"
            if not (row.get("国别") or "").strip():
                row["国别"] = "USA"
            if not (row.get("机构") or "").strip():
                row["机构"] = "Belfer Center (STPP)"
            if not (row.get("编号") or "").strip():
                rid = sanitize_text(str(raw.get("node_id") or "")).strip()
                row["编号"] = rid or (sha1_text(url)[:12] if url else "")

            if raw.get("published") and not (row.get("时间") or "").strip():
                row["时间"] = sanitize_text(str(raw.get("published")))
            if raw.get("summary"):
                # keep CSV's 主要内容 if it is not empty; otherwise fill
                if not (row.get("主要内容") or "").strip():
                    row["主要内容"] = sanitize_text(str(raw.get("summary")))
            if raw.get("authors"):
                row["authors"] = "; ".join([sanitize_text(x) for x in (raw.get("authors") or []) if sanitize_text(x)])
            if raw.get("topics"):
                row["topics"] = "; ".join([sanitize_text(x) for x in (raw.get("topics") or []) if sanitize_text(x)])
            if raw.get("tags"):
                row["tags"] = "; ".join([sanitize_text(x) for x in (raw.get("tags") or []) if sanitize_text(x)])
            if raw.get("keywords"):
                row["关键词"] = "; ".join([sanitize_text(x) for x in (raw.get("keywords") or []) if sanitize_text(x)])

        # matrix tagging
        item_en, item_zh = match_matrix_items(hay, matrix_items) if matrix_items else ([], [])
        if args.emerging_tech_only and (not item_en):
            continue

        matrix_topics = item_en[: max(0, int(args.matrix_topic_max))]
        if matrix_items:
            row["item_en"] = "; ".join(item_en)
            row["item_zh"] = "; ".join(item_zh)
            row["matrix_topics"] = "; ".join(matrix_topics)
            if args.overwrite_topic_with_matrix:
                row["topic"] = "; ".join(matrix_topics)

        out.append(row)
        if url:
            seen_url.add(url)

        if args.limit and len(out) >= int(args.limit):
            break

    # re-number 序号
    for i, r in enumerate(out, start=1):
        r["序号"] = str(i)

    # keep original column order if possible
    fieldnames = list(rows[0].keys()) if rows else []
    if out and fieldnames:
        # include newly backfilled keys
        for k in out[0].keys():
            if k not in fieldnames:
                fieldnames.append(k)
        write_rows(args.output, out, fieldnames=fieldnames)
    elif out:
        write_rows(args.output, out, fieldnames=list(out[0].keys()))

    print(f"[INFO] wrote rows: {len(out)} -> {args.output}")


if __name__ == "__main__":
    main()


