import csv
import os
import re
import time
import json
import hashlib
import argparse
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup, Tag

# 站点主域名，用来补全相对链接
BASE_DOMAIN = "https://www.belfercenter.org"
ALLOWED_NETLOCS = {"www.belfercenter.org", "belfercenter.org"}

# 仅保留 Belfer 站内“文章/研究/数据”类内容页（可按需要扩展）
# 说明：列表页里常见的 “In The News” 会直接指向站外媒体，因此默认会被过滤掉
ALLOWED_CONTENT_PATH_PREFIXES = (
    "/publication/",
    "/analysis/",
    "/research-analysis/",
    "/report/",
    "/paper/",
    "/policy-brief/",
    "/research/",
    "/data/",
)

# 研究列表基础 URL（默认入口）
# 注意：该页面“列表结果”由前端调用 /api/search/search 渲染，HTML 里不一定直接包含结果链接。
BASE_LIST_URL = (
    "https://www.belfercenter.org/programs/science-technology-and-public-policy/"
    "research-science-technology-and-public-policy?_page=1&keywords=&_limit=8"
    "&type=research_and_analysis&program=5931"
)

# ===== 关键配置：在浏览器中选择 Topic 过滤后，把 URL 填到这里 =====
# 操作方法举例：
# 1. 打开：https://www.belfercenter.org/programs/science-technology-and-public-policy/research-science-technology-and-public-policy
# 2. 在页面左侧 "Topic" 中勾选例如 "Artificial Intelligence"
# 3. 等页面刷新后，复制浏览器地址栏完整 URL
# 4. 粘贴到下面的字典里
TOPIC_URLS = {
    # 示例（运行前请用真实地址替换下面的占位符）：
    # "Artificial Intelligence": "https://www.belfercenter.org/...你复制来的完整URL...",
    # "Energy": "https://www.belfercenter.org/...你复制来的完整URL...",
}

# 请求头（简单模拟浏览器）
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

@dataclass(frozen=True)
class CrawlerConfig:
    scope_list_url: str = BASE_LIST_URL
    include_external_links: bool = False
    allowed_content_path_prefixes: tuple[str, ...] = ALLOWED_CONTENT_PATH_PREFIXES

    # Boolean 检索式（AND/OR/NOT + () + 引号短语）
    # 为空表示不过滤（抓取全部站内内容页）
    query: str = ""

    # 详情页抓取开关
    fetch_detail: bool = True
    fetch_author_affiliation: bool = False

    # 分类矩阵（item_en/item_zh）路径，空表示不打标签
    matrix_csv_path: str = ""

    # 仅保留命中“新兴科技矩阵”的文档（用于定向爬取新兴科技）
    emerging_tech_only: bool = False

    # 用分类矩阵命中的 item_en 作为“topic/主题”来源（用于归档与聚类）
    # - False: 保留列表页的 topic（通常是 All 或手工 Topic）
    # - True: 生成 matrix_topics，并可选择覆盖 topic 字段
    topics_from_matrix: bool = True
    overwrite_topic_with_matrix: bool = False
    matrix_topic_max: int = 10

    # 翻页控制
    max_pages: int = 500
    sleep_sec: float = 1.5
    stop_after_filtered_empty_pages: int = 5

    # 列表抓取方式：
    # - 传统 HTML: 解析 scope page 的 HTML
    # - API Search: 调用 /api/search/search 获取结果（推荐；该 STPP Research 页面结果是 JS 渲染，HTML 里没有）
    use_api_search: bool = True
    api_search_endpoint: str = "https://www.belfercenter.org/api/search/search"
    api_program_id: str = "5931"  # STPP program id（从页面过滤 URL 观测到）
    api_type: str = "research_and_analysis"  # Type facet 的值（Research & Analysis）
    api_content_type: str = ""  # Content Type facet（例如 Article=1；空表示不限制）
    api_limit: int = 8
    api_keywords: str = ""  # 传给站内搜索（不是布尔检索式）
    source_exact: str = ""  # 列表字段 source 必须精确匹配（可为空表示不限制）
    source_contains: str = ""  # 列表字段 source 包含子串（不区分大小写；可为空表示不限制）

    # 输出内容控制（CSV）
    content_max_words: int = 150  # “主要内容”在 CSV 中最多保留多少英文单词；完整正文仍保存到 raw json

    # 输出
    output_csv: str = "belfer_stpp_records.csv"
    output_raw_dir: str = "belfer_raw"
    save_raw_json: bool = True
    save_raw_html: bool = False


def _safe_mkdir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def canonicalize_url(url: str) -> str:
    """
    URL 规范化：去掉常见跟踪参数、统一 netloc、去掉末尾斜杠。
    """
    parts = urlparse(url.strip())
    netloc = parts.netloc.lower()
    if netloc == "belfercenter.org":
        netloc = "www.belfercenter.org"

    query = parse_qs(parts.query, keep_blank_values=True)
    for k in list(query.keys()):
        if k.lower() in {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}:
            query.pop(k, None)

    new_parts = list(parts)
    new_parts[1] = netloc
    new_parts[4] = urlencode(query, doseq=True)
    normalized = urlunparse(new_parts)
    if normalized.endswith("/") and len(normalized) > len("https://x/"):
        normalized = normalized.rstrip("/")
    return normalized


def is_internal_belfer_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in {"http", "https"} and p.netloc.lower() in ALLOWED_NETLOCS
    except Exception:
        return False


def is_allowed_content_path(url: str, allowed_prefixes: tuple[str, ...]) -> bool:
    p = urlparse(url)
    path = p.path or ""
    return any(path.startswith(pref) for pref in allowed_prefixes)


def sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()

def sanitize_text(s: str) -> str:
    """
    清洗文本以减少 CSV/Excel 解析错列与乱码风险：
    - 去掉 NUL 等控制字符
    - 把换行/制表统一压成空格
    - 合并多余空白
    """
    if not s:
        return ""
    s = s.replace("\x00", " ")
    s = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", " ", s)
    s = re.sub(r"[\r\n\t]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def truncate_chars(s: str, max_chars: int) -> str:
    if not s or max_chars <= 0:
        return ""
    s = sanitize_text(s)
    return s[:max_chars]

def truncate_words(s: str, max_words: int) -> str:
    """
    以“英文单词数”为单位截断（words），避免按字符截断导致内容过短/过长。
    规则：用空白切分；保留前 max_words 个 token。
    """
    if not s or max_words <= 0:
        return ""
    s = sanitize_text(s)
    parts = s.split()
    if len(parts) <= max_words:
        return s
    return " ".join(parts[:max_words])


class QueryParseError(Exception):
    pass


def tokenize_query(q: str) -> list[str]:
    """
    支持：
    - 运算符：AND / OR / NOT（大小写不敏感）
    - 括号： ( )
    - 引号短语："machine learning"
    - 普通词：ai
    """
    q = q.strip()
    if not q:
        return []
    tokens: list[str] = []
    i = 0
    while i < len(q):
        ch = q[i]
        if ch.isspace():
            i += 1
            continue
        if ch in "()":
            tokens.append(ch)
            i += 1
            continue
        if ch == '"':
            j = i + 1
            while j < len(q) and q[j] != '"':
                j += 1
            if j >= len(q):
                raise QueryParseError("Unclosed quote in query")
            phrase = q[i + 1 : j].strip()
            if phrase:
                tokens.append(f'"{phrase}"')
            i = j + 1
            continue
        # word/operator
        j = i
        while j < len(q) and (not q[j].isspace()) and q[j] not in "()":
            j += 1
        word = q[i:j].strip()
        if word:
            tokens.append(word)
        i = j
    return tokens


def to_rpn(tokens: list[str]) -> list[str]:
    """
    Shunting-yard：把布尔表达式转为 RPN。
    优先级：NOT > AND > OR
    """
    prec = {"NOT": 3, "AND": 2, "OR": 1}
    out: list[str] = []
    ops: list[str] = []
    for t in tokens:
        u = t.upper()
        if u in {"AND", "OR", "NOT"}:
            while ops:
                top = ops[-1]
                if top == "(":
                    break
                if prec.get(top, 0) >= prec[u]:
                    out.append(ops.pop())
                else:
                    break
            ops.append(u)
        elif t == "(":
            ops.append("(")
        elif t == ")":
            while ops and ops[-1] != "(":
                out.append(ops.pop())
            if not ops or ops[-1] != "(":
                raise QueryParseError("Mismatched parentheses")
            ops.pop()
        else:
            out.append(t)
    while ops:
        op = ops.pop()
        if op in {"(", ")"}:
            raise QueryParseError("Mismatched parentheses")
        out.append(op)
    return out


def eval_rpn(rpn: list[str], text: str) -> bool:
    text_l = text.lower()
    stack: list[bool] = []
    for t in rpn:
        u = t.upper()
        if u == "NOT":
            if not stack:
                raise QueryParseError("NOT missing operand")
            stack.append(not stack.pop())
        elif u in {"AND", "OR"}:
            if len(stack) < 2:
                raise QueryParseError(f"{u} missing operand")
            b = stack.pop()
            a = stack.pop()
            stack.append((a and b) if u == "AND" else (a or b))
        else:
            # term
            term = t
            if term.startswith('"') and term.endswith('"'):
                term = term[1:-1]
            term = term.strip().lower()
            if not term:
                stack.append(False)
            else:
                stack.append(term in text_l)
    if len(stack) != 1:
        raise QueryParseError("Invalid query expression")
    return stack[0]


def compile_query(query: str):
    tokens = tokenize_query(query)
    if not tokens:
        return None
    rpn = to_rpn(tokens)
    return rpn


def load_matrix_items(matrix_csv_path: str) -> list[dict]:
    """
    读取分类矩阵，返回 [{item_en, item_zh}, ...]
    """
    if not matrix_csv_path or (not os.path.exists(matrix_csv_path)):
        return []
    items: list[dict] = []
    with open(matrix_csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            en = (row.get("item_en") or "").strip()
            zh = (row.get("item_zh") or "").strip()
            if en and zh:
                items.append({"item_en": en, "item_zh": zh})
    return items


def match_matrix_items(text: str, matrix_items: list[dict]) -> tuple[list[str], list[str]]:
    """
    简单匹配：如果 item_en 作为子串出现在文本中，就认为命中。
    """
    if not matrix_items:
        return ([], [])
    text_l = text.lower()
    hit_en: list[str] = []
    hit_zh: list[str] = []
    for it in matrix_items:
        en = it["item_en"]
        if en.lower() in text_l:
            hit_en.append(en)
            hit_zh.append(it["item_zh"])
    return (hit_en, hit_zh)


def build_page_url(base_url: str, page: int) -> str:
    """
    在已有 URL 的基础上设置/修改 page 参数。
    适用于原来没有 ?page= 的 URL，也适用于已经有别的 query 参数的 URL。
    """
    parts = list(urlparse(base_url))
    query = parse_qs(parts[4])
    # 有的列表页用 _page（如 program research）；有的用 page
    if "_page" in query or "_page=" in base_url:
        query["_page"] = [str(page)]
    else:
        query["page"] = [str(page)]
    parts[4] = urlencode(query, doseq=True)
    return urlunparse(parts)

def build_api_search_url(config: CrawlerConfig, page: int) -> str:
    """
    Belfer 的 program research 页面结果通过 /api/search/search 返回（JSON 包一段 HTML）。
    分页参数为 _page；每页条数 _limit；其他参数：type, program, keywords。
    """
    parts = list(urlparse(config.api_search_endpoint))
    query = parse_qs(parts[4])
    query["_page"] = [str(page)]
    query["_limit"] = [str(int(config.api_limit))]
    if config.api_type:
        query["type"] = [config.api_type]
    if config.api_program_id:
        query["program"] = [str(config.api_program_id)]
    # research-analysis 页的 “Content Type” facet 名为 content-type（例如 Article => 1）
    api_ct = getattr(config, "api_content_type", "")
    if api_ct:
        query["content-type"] = [str(api_ct)]
    # Belfer 的关键词参数名就是 keywords
    query["keywords"] = [config.api_keywords or ""]
    parts[4] = urlencode(query, doseq=True)
    return urlunparse(parts)

def fetch_api_search_results(config: CrawlerConfig, page: int) -> str:
    """
    返回 API 的 results HTML 片段；无结果返回空字符串。
    """
    url = build_api_search_url(config, page)
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    results_html = data.get("results") or ""
    if not isinstance(results_html, str):
        return ""
    return results_html

def fetch_api_search_meta(config: CrawlerConfig, page: int) -> dict:
    """
    获取 API meta（用于自动翻页 totalPages/totalResults）。
    """
    url = build_api_search_url(config, page)
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    meta = data.get("meta") or {}
    return meta if isinstance(meta, dict) else {}

def parse_api_results_fragment(results_html: str, topic_name: str) -> list[dict]:
    """
    解析 /api/search/search 返回的 HTML 片段。
    """
    soup = BeautifulSoup(results_html, "html.parser")
    records: list[dict] = []
    for art in soup.select("article.teaser"):
        link = art.select_one("h2 a[href]") or art.select_one("a[href]")
        if not isinstance(link, Tag):
            continue
        title = re.sub(r"\s+", " ", link.get_text(" ", strip=True)).strip()
        href = link.get("href", "").strip()
        if not href:
            continue
        if href.startswith("/"):
            url = BASE_DOMAIN + href
        else:
            url = href

        # date
        date_str = ""
        t = art.select_one("time")
        if isinstance(t, Tag):
            date_str = re.sub(r"\s+", " ", (t.get("datetime") or t.get_text(strip=True) or "")).strip()

        # content type badge
        content_type = ""
        typ = art.select_one(".type")
        if isinstance(typ, Tag):
            content_type = re.sub(r"\s+", " ", typ.get_text(" ", strip=True)).strip()

        # source (if any)
        source = ""
        src = art.select_one(".source strong")
        if isinstance(src, Tag):
            source = re.sub(r"\s+", " ", src.get_text(" ", strip=True)).strip()

        record = {
            "topic": topic_name,
            "title": title,
            "url": canonicalize_url(url),
            "date": date_str,
            "type": content_type,
            "source": source,
        }
        records.append(record)
    return records


def fetch_html(url: str, retries: int = 3, delay: float = 2.0) -> str:
    """
    发送 GET 请求，带简单重试。
    """
    for i in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            print(f"[WARN] 请求失败({i+1}/{retries}): {e} -> {url}")
            if i == retries - 1:
                raise
            time.sleep(delay)
    return ""


def extract_visible_text(soup: BeautifulSoup) -> str:
    """
    抽取页面正文文本（尽量排除脚本/导航）。
    """
    for bad in soup.select("script, style, nav, header, footer, noscript, aside"):
        bad.decompose()
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_detail_page(html: str, url: str) -> dict:
    """
    解析详情页：正文、时间、作者、关键词/标签等。
    由于 Belfer 可能是 Drupal 渲染，选择器采用“多策略兜底”。
    """
    soup = BeautifulSoup(html, "html.parser")

    def meta(name: str = "", prop: str = "") -> str:
        if name:
            tag = soup.find("meta", attrs={"name": name})
            if isinstance(tag, Tag) and tag.has_attr("content"):
                return str(tag["content"]).strip()
        if prop:
            tag = soup.find("meta", attrs={"property": prop})
            if isinstance(tag, Tag) and tag.has_attr("content"):
                return str(tag["content"]).strip()
        return ""

    title = meta(prop="og:title") or (soup.find("h1").get_text(strip=True) if soup.find("h1") else "")
    canonical = meta(prop="og:url") or meta(name="canonical") or url

    # 摘要/导语：优先取 meta description / og:description（通常就是页面导语）
    summary = meta(prop="og:description") or meta(name="description") or ""
    summary = sanitize_text(summary)

    published = meta(prop="article:published_time") or meta(name="date") or ""
    if not published:
        # 优先取正文区的时间，避免抓到“相关推荐”的时间
        t = soup.select_one("main time[datetime]") or soup.select_one("article time[datetime]") or soup.find("time")
        if isinstance(t, Tag):
            published = (t.get("datetime") or t.get_text(strip=True) or "").strip()

    # 作者（多来源兜底）
    authors: list[str] = []
    author_profile_urls: list[str] = []
    author_meta = meta(name="author")
    if author_meta:
        authors = [a.strip() for a in re.split(r"[;,/]| and ", author_meta) if a.strip()]
    if not authors:
        for a in soup.select('a[href^="/expert/"], a[href^="/person/"]'):
            txt = a.get_text(" ", strip=True)
            if txt and txt not in authors:
                authors.append(txt)
            href = a.get("href", "").strip() if a.has_attr("href") else ""
            if href.startswith("/"):
                href = BASE_DOMAIN + href
            if href and href not in author_profile_urls and is_internal_belfer_url(href):
                author_profile_urls.append(canonicalize_url(href))

    # 兜底：从页面 byline 文案中解析作者（常见：".card-meta" 里包含 "by X and Y"）
    if not authors:
        byline = ""
        by_el = soup.select_one("main .card-meta") or soup.select_one("article .card-meta") or soup.select_one(".card-meta")
        if isinstance(by_el, Tag):
            byline = re.sub(r"\s+", " ", by_el.get_text(" ", strip=True)).strip()
        if byline:
            m = re.search(r"\bby\b\s+(.*)$", byline, flags=re.IGNORECASE)
            if m:
                names = m.group(1).strip().strip("-–—:").strip()
                # 常见分隔：and / & / ;
                parts = re.split(r"\s+(?:and|&)\s+|[;|]+", names, flags=re.IGNORECASE)
                parsed = []
                for p in parts:
                    p = p.strip()
                    if not p:
                        continue
                    # 去掉多余的重复空格
                    p = re.sub(r"\s+", " ", p)
                    # 防止把长句误判为作者
                    if len(p) <= 80:
                        parsed.append(p)
                # 去重保持顺序
                for n in parsed:
                    if n not in authors:
                        authors.append(n)

    # 关键词/标签（meta 优先，其次页面正文内的 tags/topics）
    keywords_raw = meta(name="news_keywords") or meta(name="keywords") or ""
    keywords: list[str] = []
    if keywords_raw:
        keywords = [k.strip() for k in re.split(r"[;,]", keywords_raw) if k.strip()]
    # 仅从正文区域提取（避免把页眉导航“Explore the Latest”当成 tags）
    content_root = soup.select_one("main") or soup
    tags_container = content_root.select_one(".tags")  # 该容器通常就是页面的主题标签

    topics: list[str] = []
    tags: list[str] = []

    if isinstance(tags_container, Tag):
        for a in tags_container.select("a[href]"):
            ttxt = a.get_text(" ", strip=True)
            if ttxt and ttxt not in tags:
                tags.append(ttxt)
            href = a.get("href", "").strip() if a.has_attr("href") else ""
            if href and (href.startswith("/topic/") or "/topics/" in href):
                if ttxt and ttxt not in topics:
                    topics.append(ttxt)
    else:
        # 兜底：从正文内找 /topic(s)/ 链接，但排除 header/nav/footer
        def _in_bad_container(t: Tag) -> bool:
            for p in t.parents:
                if not isinstance(p, Tag):
                    continue
                if p.name in {"header", "nav", "footer"}:
                    return True
                cls = p.get("class", []) or []
                if isinstance(cls, str):
                    cls = [cls]
                cls_l = " ".join(cls).lower()
                if "search-tags" in cls_l or "menu" in cls_l:
                    return True
            return False

        for a in content_root.select('a[href^="/topic/"], a[href*="/topics/"]'):
            if not isinstance(a, Tag):
                continue
            if _in_bad_container(a):
                continue
            ttxt = a.get_text(" ", strip=True)
            if ttxt and ttxt not in topics:
                topics.append(ttxt)

    # 如果 keywords 为空，则用 tags/topics 推导一份（满足“关键词/标签很重要”的口径）
    if not keywords:
        keywords = list(dict.fromkeys((tags or []) + (topics or [])))

    # 正文：优先 node/article 容器，否则回退到整页可见文本
    body_text = ""
    main = soup.select_one("article") or soup.select_one("div.node__content") or soup.select_one("main")
    if isinstance(main, Tag):
        body_text = extract_visible_text(BeautifulSoup(str(main), "html.parser"))
    else:
        body_text = extract_visible_text(soup)

    # 如果 meta 没有摘要，则从正文区找第一段“像导语”的段落作为摘要兜底
    if not summary:
        content_root = soup.select_one("main") or soup
        paras: list[str] = []
        for p in content_root.select("p"):
            if not isinstance(p, Tag):
                continue
            t = sanitize_text(p.get_text(" ", strip=True))
            if not t:
                continue
            tl = t.lower()
            if tl.startswith("author "):
                continue
            if "photo credit" in tl:
                continue
            if len(t) < 60:
                continue
            paras.append(t)
        if paras:
            summary = paras[0]

    # 尝试提取 Drupal node id
    node_id = ""
    if isinstance(main, Tag) and main.has_attr("data-history-node-id"):
        node_id = str(main["data-history-node-id"]).strip()
    if not node_id:
        m = re.search(r"/(\d{4,})$", urlparse(canonical).path.rstrip("/"))
        if m:
            node_id = m.group(1)

    return {
        "title": title,
        "canonical_url": canonicalize_url(canonical),
        "summary": summary,
        "published": published,
        "authors": authors,
        "author_profile_urls": author_profile_urls,
        "keywords": keywords,
        "topics": topics,
        "tags": tags,
        "body": body_text,
        "node_id": node_id,
    }

def parse_author_profile_affiliation(html: str) -> str:
    """
    从作者页解析单位/头衔信息（页面结构可能变化，尽量宽松匹配）。
    """
    soup = BeautifulSoup(html, "html.parser")
    # 常见字段：title / organization / affiliation
    parts: list[str] = []
    for sel in (
        ".field--name-field-title",
        ".field--name-field-position",
        ".field--name-field-organization",
        ".field--name-field-affiliation",
        ".field--name-field-employer",
    ):
        tag = soup.select_one(sel)
        if isinstance(tag, Tag):
            t = tag.get_text(" ", strip=True)
            if t:
                parts.append(t)
    if not parts:
        # 兜底：抓取作者页 main 的可见文本前 200 字
        main = soup.select_one("main") or soup.select_one("article")
        if isinstance(main, Tag):
            t = extract_visible_text(BeautifulSoup(str(main), "html.parser"))
            if t:
                parts.append(t[:200])
    parts = [p for p in parts if p]
    return " | ".join(dict.fromkeys(parts))


def parse_list_page(html: str, topic_name: str):
    """
    解析列表页，返回该页的文章记录列表。
    如果这一页解析不到任何文章，就返回空 list。
    """
    soup = BeautifulSoup(html, "html.parser")

    records = []

    # 只在“列表结果区”找标题链接，避免抓到页眉/侧边栏/外部新闻模块的 h2
    # 兜底策略：优先 view-content，其次 main/article，最后才是全页 h2
    title_links = []
    for sel in ("div.view-content h2 a", "main h2 a", "article h2 a"):
        found = soup.select(sel)
        if found:
            title_links = found
            break
    if not title_links:
        title_links = [h2.find("a") for h2 in soup.find_all("h2")]

    for title_tag in title_links:
        if not isinstance(title_tag, Tag):
            continue

        title = title_tag.get_text(" ", strip=True)
        url = title_tag.get("href", "").strip() if title_tag.has_attr("href") else ""
        if not url:
            continue
        if url.startswith("/"):
            url = BASE_DOMAIN + url

        date_str = ""
        content_type = ""
        source = ""

        # 在标题附近查找元信息（日期 / 类型 / 来源）
        meta_container = None

        # 先看标题父节点之后的兄弟节点
        parent = title_tag.parent
        if isinstance(parent, Tag):
            for sib in parent.next_siblings:
                if isinstance(sib, Tag):
                    meta_container = sib
                    break

        # 找不到就退回到 h2 之后的兄弟节点里继续找
        if meta_container is None:
            for sib in title_tag.next_siblings:
                if isinstance(sib, Tag):
                    meta_container = sib
                    break

        if isinstance(meta_container, Tag):
            text = meta_container.get_text(" ", strip=True)

            # 日期：优先找 <time> 标签，否则用正则从文本里提取
            time_tag = meta_container.find("time")
            if isinstance(time_tag, Tag):
                date_str = time_tag.get_text(strip=True)
            else:
                m_date = re.search(
                    r"(Jan\.|Feb\.|Mar\.|Apr\.|May|Jun\.|Jul\.|Aug\.|Sep\.|Oct\.|Nov\.|Dec\.)\s+\d{1,2},\s+\d{4}",
                    text,
                )
                if m_date:
                    date_str = m_date.group(0)

            # 类型：根据常见文案匹配
            if "In The News" in text:
                content_type = "In The News"
            elif "Research & Analysis" in text:
                content_type = "Research & Analysis"

            # 来源：尝试从 "from XXX" 中截出来源名称
            m_source = re.search(r"\bfrom\s+(.+)", text)
            if m_source:
                source = m_source.group(1).strip()

        record = {
            "topic": topic_name,
            "title": title,
            "url": canonicalize_url(url),
            "date": date_str,
            "type": content_type,
            "source": source,
        }
        records.append(record)

    return records


def crawl_topic(
    topic_name: str,
    base_url: str,
    config: CrawlerConfig,
):
    """
    循环翻页抓取某一个 Topic 下的所有文章。
    """
    print(f"\n=== 开始抓取 Topic: {topic_name} ===")
    all_records = []
    page = 0
    filtered_empty_streak = 0

    # 自动翻页：如果是 API 模式且 max_pages 很大/默认值，则以 meta.totalPages 为准
    api_total_pages = None
    if config.use_api_search:
        try:
            meta0 = fetch_api_search_meta(config, page=1)
            tp = meta0.get("totalPages")
            if isinstance(tp, int) and tp > 0:
                api_total_pages = tp
        except Exception:
            api_total_pages = None

    hard_max = int(config.max_pages)
    if api_total_pages is not None:
        hard_max = min(hard_max, int(api_total_pages))

    while page < hard_max:
        if config.use_api_search:
            api_url = build_api_search_url(config, page=page + 1)  # API 的 _page 从 1 开始更符合站内习惯
            print(f"[INFO] API 抓取 {topic_name} 第 {page+1} 页: {api_url}")
            try:
                results_html = fetch_api_search_results(config, page=page + 1)
            except Exception as e:
                print(f"[ERROR] API 抓取 {topic_name} 第 {page+1} 页失败，将跳过该页。错误: {e}")
                page += 1
                time.sleep(float(config.sleep_sec))
                continue
            records = parse_api_results_fragment(results_html, topic_name)
        else:
            page_url = build_page_url(base_url, page)
            print(f"[INFO] 抓取 {topic_name} 第 {page} 页: {page_url}")
            try:
                html = fetch_html(page_url)
            except Exception as e:
                # 某一页如果因为网络 / SSL 错误抓取失败，则跳过这一页，继续后面的页码
                print(f"[ERROR] 抓取 {topic_name} 第 {page} 页失败，将跳过该页。错误: {e}")
                page += 1
                time.sleep(float(config.sleep_sec))
                continue
            records = parse_list_page(html, topic_name)

        if not records:
            print(f"[INFO] 第 {page} 页没有解析到文章，认为该 Topic 结束。")
            break

        # 过滤：域名 + 内容路径前缀
        filtered = []
        for r in records:
            u = r["url"]
            internal = is_internal_belfer_url(u)
            allowed_path = is_allowed_content_path(u, config.allowed_content_path_prefixes) if internal else False
            if internal and allowed_path:
                # 过滤：source（在抓详情页前先减少集合）
                src = sanitize_text(r.get("source", ""))
                if config.source_exact and src != config.source_exact:
                    continue
                if config.source_contains and (config.source_contains.lower() not in src.lower()):
                    continue
                filtered.append(r)
            else:
                # 默认不包含外链（如 The Conversation / NYT 等）
                if config.include_external_links:
                    r2 = dict(r)
                    r2["external"] = "1" if not internal else "0"
                    filtered.append(r2)

        if not filtered:
            print(f"[INFO] 第 {page} 页记录均被过滤（站外或非内容页），继续翻页。")
            filtered_empty_streak += 1
            if filtered_empty_streak >= int(config.stop_after_filtered_empty_pages):
                print(
                    f"[INFO] 连续 {filtered_empty_streak} 页过滤后为空，停止该 Topic（可能该列表主要为站外新闻链接）。"
                )
                break
            page += 1
            time.sleep(float(config.sleep_sec))
            continue
        else:
            filtered_empty_streak = 0

        print(f"[INFO] 第 {page} 页解析到 {len(records)} 条记录。")
        all_records.extend(filtered)

        # 每页抓完就追加写入一个临时 CSV，防止进程中断导致全部丢失
        safe_topic = topic_name.replace(" ", "_")
        temp_filename = f"belfer_stpp_articles_{safe_topic}_temp.csv"
        append_to_csv(filtered, temp_filename)

        page += 1
        time.sleep(float(config.sleep_sec))  # 礼貌性限速，避免给网站压力

    print(f"=== Topic: {topic_name} 抓取完成，共 {len(all_records)} 条 ===")
    return all_records


def save_to_csv(records, filename: str):
    """
    把抓到的结果保存到 CSV 文件。
    """
    if not records:
        print("[WARN] 没有数据，不写入 CSV。")
        return

    preferred = [
        "序号",
        "英文名",
        "国别",
        "编号",
        "时间",
        "机构",
        "主要内容",
        "关键词",
        "主题词",
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
    # 只有 enriched 结果才使用固定列顺序。
    # 注意：列表索引记录也包含 source/type/url/date 等字段，不能用“是否含 source”来判断 enriched。
    keys0 = list(records[0].keys())
    is_enriched = ("英文名" in records[0]) or ("序号" in records[0]) or ("编号" in records[0]) or ("主要内容" in records[0])
    if is_enriched:
        extra = [k for k in keys0 if k not in preferred]
        fieldnames = preferred + sorted(extra)
    else:
        fieldnames = keys0
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            quoting=csv.QUOTE_ALL,  # 强制加引号，减少 Excel/解析器错列风险
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for r in records:
            writer.writerow(r)

    print(f"[INFO] 已写入 {len(records)} 条到 {filename}")


def append_to_csv(records, filename: str):
    """
    追加写入到 CSV，用于中途保存抓取进度。
    如果文件不存在，会自动写入表头。
    """
    if not records:
        return

    preferred = [
        "序号",
        "英文名",
        "国别",
        "编号",
        "时间",
        "机构",
        "主要内容",
        "关键词",
        "主题词",
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
    keys0 = list(records[0].keys())
    is_enriched = ("英文名" in records[0]) or ("序号" in records[0]) or ("编号" in records[0]) or ("主要内容" in records[0])
    if is_enriched:
        extra = [k for k in keys0 if k not in preferred]
        fieldnames = preferred + sorted(extra)
    else:
        fieldnames = keys0
    file_exists = os.path.exists(filename)
    mode = "a" if file_exists else "w"

    with open(filename, mode, newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            quoting=csv.QUOTE_ALL,
            extrasaction="ignore",
            lineterminator="\n",
        )
        if not file_exists:
            writer.writeheader()
        for r in records:
            writer.writerow(r)


def enrich_records_with_detail(
    records: list[dict],
    config: CrawlerConfig,
    matrix_items: list[dict],
) -> list[dict]:
    """
    详情页补全 + 去重 + 分类归档。
    """
    _safe_mkdir(config.output_raw_dir)
    rpn = compile_query(config.query) if config.query else None

    out: list[dict] = []
    seen_url: dict[str, int] = {}  # canonical_url -> idx in out
    seen_sig: dict[str, int] = {}  # title+date+body hash -> idx

    for rec in records:
        url = rec.get("url", "")
        url = canonicalize_url(url)

        # 预过滤（检索式）：尽量用 title/type/source/topic 减少详情页抓取量
        pre_text = " ".join(
            [
                rec.get("title", ""),
                rec.get("type", ""),
                rec.get("source", ""),
                rec.get("topic", ""),
            ]
        ).strip()
        if rpn is not None:
            try:
                if not eval_rpn(rpn, pre_text):
                    continue
            except QueryParseError:
                # 表达式异常时不做过滤，避免误删
                pass

        detail = {}
        if config.fetch_detail and is_internal_belfer_url(url):
            try:
                html = fetch_html(url)
                detail = parse_detail_page(html, url)

                # 可选：抓取作者单位/头衔（每个作者会额外发请求，成本较高）
                author_affiliations: list[str] = []
                if config.fetch_author_affiliation:
                    for ap in detail.get("author_profile_urls", [])[:10]:
                        try:
                            ah = fetch_html(ap)
                            aff = parse_author_profile_affiliation(ah)
                            if aff:
                                author_affiliations.append(aff)
                        except Exception:
                            continue
                if author_affiliations:
                    detail["author_affiliations"] = author_affiliations

                # 归档解析结果（默认仅保存 json；如需可选保存 html）
                doc_key = detail.get("node_id") or sha1_text(url)[:12]
                json_path = os.path.join(config.output_raw_dir, f"{doc_key}.json")
                if config.save_raw_html:
                    raw_path = os.path.join(config.output_raw_dir, f"{doc_key}.html")
                    with open(raw_path, "w", encoding="utf-8") as f:
                        f.write(html)
                if config.save_raw_json:
                    with open(json_path, "w", encoding="utf-8") as f:
                        json.dump(detail, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"[WARN] 详情页抓取/解析失败，将保留列表信息: {e} -> {url}")

        canonical = detail.get("canonical_url") or url
        if canonical in seen_url:
            continue

        body = sanitize_text(detail.get("body", ""))
        published = detail.get("published") or rec.get("date", "")

        sig = sha1_text(
            (detail.get("title") or rec.get("title", "")).strip()
            + "|"
            + str(published).strip()
            + "|"
            + body[:2000]
        )
        if sig in seen_sig:
            continue

        text_for_classify = " ".join(
            [
                detail.get("title") or rec.get("title", ""),
                " ".join(detail.get("keywords", []) or []),
                " ".join(detail.get("topics", []) or []),
                " ".join(detail.get("tags", []) or []),
                body,
            ]
        )
        item_en, item_zh = match_matrix_items(text_for_classify, matrix_items)
        if config.emerging_tech_only and (not item_en):
            continue

        matrix_topics = item_en[: max(0, int(config.matrix_topic_max))]
        topic_value = rec.get("topic", "")
        if config.topics_from_matrix:
            if config.overwrite_topic_with_matrix:
                topic_value = "; ".join(matrix_topics)

        doc_id = detail.get("node_id") or sha1_text(canonical)[:12]

        out_rec = {
            # 需求字段（最小集）
            "序号": "",  # 生成后再填
            "英文名": "Belfer Center for Science and International Affairs",
            "国别": "USA",
            "编号": doc_id,
            "时间": published,
            "机构": "Belfer Center (STPP)",
            # CSV 中“主要内容”限制 150 字；完整正文见 raw json
            "主要内容": truncate_words((detail.get("summary") or ""), int(config.content_max_words))
            or truncate_words(body, int(config.content_max_words)),
            "关键词": "; ".join([sanitize_text(x) for x in (detail.get("keywords", []) or []) if sanitize_text(x)]),
            "主题词": "; ".join(
                [x for x in (detail.get("keywords", []) or []) + (detail.get("topics", []) or []) + (detail.get("tags", []) or [])][:50]
            ),
            "item_en": "; ".join(item_en),
            "item_zh": "; ".join(item_zh),
            # 追溯字段（便于审计与专家复核）
            "title": detail.get("title") or rec.get("title", ""),
            "url": canonical,
            "type": rec.get("type", ""),
            "source": sanitize_text(rec.get("source", "")),
            "topic": topic_value,
            "matrix_topics": "; ".join(matrix_topics),
            "authors": "; ".join([sanitize_text(x) for x in (detail.get("authors", []) or []) if sanitize_text(x)]),
            "author_affiliations": "; ".join([sanitize_text(x) for x in (detail.get("author_affiliations", []) or []) if sanitize_text(x)]),
            "topics": "; ".join([sanitize_text(x) for x in (detail.get("topics", []) or []) if sanitize_text(x)]),
            "tags": "; ".join([sanitize_text(x) for x in (detail.get("tags", []) or []) if sanitize_text(x)]),
        }

        seen_url[canonical] = len(out)
        seen_sig[sig] = len(out)
        out.append(out_rec)

    # 填序号
    for i, r in enumerate(out, start=1):
        r["序号"] = str(i)
    return out


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    default_matrix = os.path.join(here, "相关资料", "分类矩阵.csv")

    parser = argparse.ArgumentParser(description="Belfer STPP crawler (internal-only + boolean query + metadata).")
    parser.add_argument("--scope-url", default=BASE_LIST_URL, help="Crawl scope list URL (STPP research list).")
    parser.add_argument("--query", default="", help='Boolean query, e.g. \'"artificial intelligence" OR AI\' AND NOT podcast')
    parser.add_argument("--include-external", action="store_true", help="Include external news links (default: false).")
    parser.add_argument("--no-detail", action="store_true", help="Do not fetch detail pages (faster, less fields).")
    parser.add_argument("--author-affiliation", action="store_true", help="Fetch author profile pages to parse affiliations.")
    parser.add_argument("--matrix", default=default_matrix, help="Path to 分类矩阵.csv.")
    parser.add_argument("--emerging-tech-only", action="store_true", help="Keep only docs that match at least one matrix item.")
    parser.add_argument(
        "--topics-from-matrix",
        action="store_true",
        help="Generate matrix_topics from 分类矩阵命中项（默认开启）；不传则保持默认（开启）。",
    )
    parser.add_argument(
        "--no-topics-from-matrix",
        action="store_true",
        help="Disable matrix_topics generation; keep list-page topic only.",
    )
    parser.add_argument(
        "--overwrite-topic-with-matrix",
        action="store_true",
        help="Overwrite 'topic' field with '; '.join(matrix_topics) (default: keep original topic and add matrix_topics).",
    )
    parser.add_argument("--matrix-topic-max", type=int, default=10, help="Max matrix topics to keep per doc (default: 10).")
    parser.add_argument("--max-pages", type=int, default=500, help="Max pages to crawl per topic (default: 500).")
    parser.add_argument("--sleep", type=float, default=1.5, help="Sleep seconds between pages (default: 1.5).")
    parser.add_argument(
        "--stop-after-filtered-empty-pages",
        type=int,
        default=5,
        help="Stop if N consecutive pages have 0 records after internal/path filtering (default: 5).",
    )
    parser.add_argument("--output", default="belfer_stpp_records.csv", help="Output CSV for enriched records.")
    parser.add_argument("--raw-dir", default="belfer_raw", help="Directory to archive raw html/json per doc.")
    parser.add_argument("--save-html", action="store_true", help="Also save raw .html files into raw-dir (default: json only).")

    # API search 模式参数
    parser.add_argument("--no-api-search", action="store_true", help="Disable /api/search/search mode; fall back to parsing HTML list page.")
    parser.add_argument("--api-program-id", default="5931", help="Program id for /api/search/search (default: 5931 for STPP).")
    parser.add_argument("--api-type", default="research_and_analysis", help="Type facet for /api/search/search (default: research_and_analysis).")
    parser.add_argument("--api-limit", type=int, default=8, help="Per-page limit for /api/search/search (default: 8).")
    parser.add_argument("--keywords", default="", help="Keywords for Belfer site search API (param: keywords=...).")
    parser.add_argument("--source-exact", default="", help='Keep only list items where source equals this value exactly (e.g. "Belfer Center for Science and International Affairs").')
    parser.add_argument("--source-contains", default="", help='Keep only list items where source contains this substring (case-insensitive), e.g. "Belfer Center".')
    parser.add_argument("--content-max-words", type=int, default=150, help="Max words for 主要内容 in CSV (default: 150).")
    args = parser.parse_args()

    config = CrawlerConfig(
        scope_list_url=args.scope_url,
        include_external_links=bool(args.include_external),
        query=args.query.strip(),
        fetch_detail=not bool(args.no_detail),
        fetch_author_affiliation=bool(args.author_affiliation),
        matrix_csv_path=args.matrix,
        emerging_tech_only=bool(args.emerging_tech_only),
        topics_from_matrix=(not bool(args.no_topics_from_matrix)),
        overwrite_topic_with_matrix=bool(args.overwrite_topic_with_matrix),
        matrix_topic_max=int(args.matrix_topic_max),
        max_pages=int(args.max_pages),
        sleep_sec=float(args.sleep),
        stop_after_filtered_empty_pages=int(args.stop_after_filtered_empty_pages),
        use_api_search=(not bool(args.no_api_search)),
        api_program_id=str(args.api_program_id).strip(),
        api_type=str(args.api_type).strip(),
        api_limit=int(args.api_limit),
        api_keywords=str(args.keywords).strip(),
        source_exact=str(args.source_exact).strip(),
        source_contains=str(args.source_contains).strip(),
        content_max_words=int(args.content_max_words),
        output_csv=args.output,
        output_raw_dir=args.raw_dir,
        save_raw_html=bool(args.save_html),
        save_raw_json=True,
    )

    matrix_items = load_matrix_items(config.matrix_csv_path)
    all_records: list[dict] = []

    if TOPIC_URLS:
        # 按 Topic 过滤抓取
        for topic_name, url in TOPIC_URLS.items():
            records = crawl_topic(topic_name, url, config=config)
            all_records.extend(records)
    else:
        # 未配置 Topic，则直接从基础 URL 抓取“全部分类”
        print("未配置 TOPIC_URLS，将从基础 URL 抓取全部分类（All Topics）。")
        records = crawl_topic("All", config.scope_list_url, config=config)
        all_records.extend(records)

    # 旧输出：仅列表页（用于回溯）
    save_to_csv(all_records, "belfer_stpp_articles.csv")

    # 新输出：详情补全 + 去重 + 分类
    enriched = enrich_records_with_detail(all_records, config=config, matrix_items=matrix_items)
    if enriched:
        save_to_csv(enriched, config.output_csv)
        print(f"[INFO] enriched 输出完成：{config.output_csv}")
    else:
        print("[WARN] enriched 输出为空（可能是过滤过严或列表解析未命中站内内容链接）。")


if __name__ == "__main__":
    main()

