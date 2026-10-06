import asyncio
import json
import httpx
import re
import hashlib
import os
import time
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup

# =========================
# 配置项
# =========================
OUTPUT_FILENAME = "exportBookSource.json"
WORKERS = 40
TIMEOUT = 15

# 搜索测活配置（任意一个关键词成功即可保留）
TEST_KEYWORDS = [
    "我的",
    "斗破苍穹",
    "完美世界",
]
SEARCH_TIMEOUT = 10.0
MIN_VALID_RESULTS = 1

# 预设的高质量种子源 / 聚合仓库直链（冷启动补充）
SEED_SOURCE_URLS = [
    # 1. XIU2 精品书源（多 CDN）
    "https://jsd.onmicrosoft.cn/gh/XIU2/Yuedu/shuyuan",
    "https://cdn.jsdmirror.com/gh/XIU2/Yuedu/shuyuan",
    "https://bitbucket.org/xiu2/yuedu/raw/master/shuyuan",
    "https://ghfast.top/https://raw.githubusercontent.com/XIU2/Yuedu/master/shuyuan",
    "https://raw.githubusercontent.com/XIU2/Yuedu/master/shuyuan",
    # 2. AOAOSTAR 聚合
    "https://legado.aoaostar.com/sources/b778fe6b.json",
    "https://legado.aoaostar.com/sources/71e56d4f.json",
    "https://legado.aoaostar.com/sources/4dc410d1.json",
    "https://legado.aoaostar.com/sources/e3e5d620.json",
    # 3. Tickmao / Novel 全量
    "https://cdn.jsdelivr.net/gh/tickmao/Novel@master/sources/legado/full.json",
    "https://raw.githubusercontent.com/tickmao/Novel/master/sources/legado/full.json",
    # 4. 轻小说 / 日轻专项
    "https://raw.githubusercontent.com/jiwangyihao/source-j-legado/main/bilinovel.json",
    "https://raw.githubusercontent.com/jiwangyihao/source-j-legado/main/wenku.json",
    "https://fastly.jsdelivr.net/gh/jiwangyihao/source-j-legado@main/bilinovel.json",
    # 5. Pixiv / 特殊源
    "https://raw.githubusercontent.com/DowneyRem/PixivSource/main/pixiv.json",
    "https://cdn.jsdelivr.net/gh/DowneyRem/PixivSource@main/pixiv.json",
    "https://raw.githubusercontent.com/DowneyRem/PixivSource/main/normal.json",
    "https://raw.githubusercontent.com/DowneyRem/PixivSource/main/books.json",
    # 6. 其他综合源
    "https://raw.githubusercontent.com/shidahuilang/shuyuan/shuyuan/good.json",
    "https://shuyuan.yiove.com/sub.json",
    "https://www.yckceo.com/yuedu/shuyuans/json/id/1298.json",  # 阿豪书源（精品筛选）
    "https://www.yckceo.com/yuedu/shuyuans/json/id/1296.json",  # 夏鈴·再校验合并
    "https://www.yckceo.com/yuedu/shuyuans/json/id/1290.json",  # 校验大合集
    "https://www.yckceo.com/yuedu/shuyuans/json/id/1279.json",  # 精选 senhora
    "https://www.yckceo.com/yuedu/shuyuans/json/id/1271.json",  # 合并优选书源
    "https://www.yckceo.com/yuedu/shuyuans/json/id/1244.json",  # 筛选去木马·专注读书
    "https://www.yckceo.com/yuedu/shuyuans/json/id/1297.json",  # 自用小合集
    "https://www.yckceo.com/yuedu/shuyuans/json/id/1285.json",  # 精品书源
]

# yckceo 书源合集 JSON 模板（由爬虫动态填充 ID）
YCKCEO_JSON_TMPL = "https://www.yckceo.com/yuedu/shuyuans/json/id/{id}.json"
YCKCEO_COLLECTIONS_URL = "https://www.yckceo.com/yuedu/shuyuans/index.html"
# 最多抓取多少个合集（按页面出现顺序，一般越新越靠前）
YCKCEO_MAX_COLLECTIONS = 50

GITHUB_QUERIES = ["Legado 书源", "legado 书源 json"]

BLACKLIST_DOMAINS = ["baidu.com", "qq.com", "bilibili.com", "zhihu.com", "so.com"]
BLACKLIST_KEYWORDS = ["点此广告", "加群", "淘宝", "返利", "APP下载"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36 Edg/154.0.0.0",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
}


# =========================
# 辅助函数
# =========================
def clean_url(u):
    if not isinstance(u, str):
        return ""
    u = u.strip()
    if u.startswith("//"):
        u = "https:" + u
    return u.replace("&amp;", "&")


def is_http_url(u):
    return isinstance(u, str) and (u.startswith("http://") or u.startswith("https://"))


def is_blacklisted(url, name=""):
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    for bd in BLACKLIST_DOMAINS:
        if bd in netloc:
            return True
    for kw in BLACKLIST_KEYWORDS:
        if kw in (name or ""):
            return True
    return False


# =========================
# 第一阶段：GitHub Search API
# =========================
def search_github_api(client, query):
    github_token = os.environ.get("bot", "").strip() or os.environ.get("GITHUB_TOKEN", "").strip()
    api_headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": HEADERS["User-Agent"],
    }
    if github_token:
        if github_token.startswith("Bearer ") or github_token.startswith("token "):
            api_headers["Authorization"] = github_token
        else:
            api_headers["Authorization"] = f"Bearer {github_token}"

    discovered_urls = set()
    # Code Search
    try:
        r = client.get(
            "https://api.github.com/search/code",
            params={"q": query, "per_page": 20},
            headers=api_headers,
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            for item in r.json().get("items", []):
                html_url = item.get("html_url", "")
                if html_url:
                    raw_url = html_url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
                    discovered_urls.add(clean_url(raw_url))
        elif r.status_code == 403:
            print(f"[!] GitHub API 限频/权限不足 (403): {query}")
        else:
            print(f"[!] GitHub Code API {r.status_code}: {query}")
    except Exception as e:
        print(f"[!] GitHub Code 搜索异常: {e}")

    # Repository Search
    try:
        r = client.get(
            "https://api.github.com/search/repositories",
            params={"q": query, "per_page": 10, "sort": "updated"},
            headers=api_headers,
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            for item in r.json().get("items", []):
                default_branch = item.get("default_branch", "master")
                clone_url = item.get("html_url") or ""
                if clone_url:
                    owner_repo = clone_url.replace("https://github.com/", "").rstrip("/")
                    discovered_urls.add(
                        f"https://raw.githubusercontent.com/{owner_repo}/{default_branch}/bookSource.json"
                    )
                    discovered_urls.add(
                        f"https://raw.githubusercontent.com/{owner_repo}/{default_branch}/shuyuan.json"
                    )
    except Exception as e:
        print(f"[!] GitHub Repo 搜索异常: {e}")

    time.sleep(1.5)
    return list(discovered_urls)


def discover_candidate_urls(client):
    print("[*] 开始通过 GitHub Search API 深度发掘书源线索...")
    candidate_urls = set(SEED_SOURCE_URLS)
    for q in GITHUB_QUERIES:
        print(f"[-] 正在通过 GitHub API 检索: {q}", end="", flush=True)
        urls = search_github_api(client, q)
        print(f" -> 发现线索: {len(urls)} 条")
        candidate_urls.update(urls)
    print(f"[+] 候选资源链接总数 (含种子源): {len(candidate_urls)}")
    return list(candidate_urls)


# =========================
# yckceo 源仓库合集抓取
# =========================
async def fetch_yckceo_collection_ids(client, max_collections=YCKCEO_MAX_COLLECTIONS):
    """
    打开 yckceo 书源合集列表页，提取合集 ID。
    合集详情页: /yuedu/shuyuans/content/id/{id}.html
    对应 JSON:  /yuedu/shuyuans/json/id/{id}.json
    """
    print("[*] 开始从 yckceo.com 抓取书源合集 ID...")
    ids = []
    seen = set()
    # 合集列表可能分页，先抓前几页
    for page in range(1, 6):
        url = YCKCEO_COLLECTIONS_URL if page == 1 else f"{YCKCEO_COLLECTIONS_URL}?page={page}"
        try:
            r = await client.get(url, timeout=TIMEOUT, follow_redirects=True)
            if r.status_code != 200:
                print(f"[!] yckceo 合集页 HTTP {r.status_code}: {url}")
                break
            soup = BeautifulSoup(r.text, "html.parser")
            page_new = 0
            # 匹配 /yuedu/shuyuans/content/id/数字
            for a in soup.find_all("a", href=True):
                href = a["href"]
                m = re.search(r"/yuedu/shuyuans/content/id/(\d+)", href)
                if not m:
                    continue
                cid = m.group(1)
                if cid in seen:
                    continue
                seen.add(cid)
                ids.append(cid)
                page_new += 1
                if len(ids) >= max_collections:
                    break
            # 正则兜底（页面 JS/文本里也可能出现）
            if len(ids) < max_collections:
                for m in re.finditer(r"/yuedu/shuyuans/content/id/(\d+)", r.text):
                    cid = m.group(1)
                    if cid not in seen:
                        seen.add(cid)
                        ids.append(cid)
                        page_new += 1
                        if len(ids) >= max_collections:
                            break
            print(f"[-] yckceo 合集第 {page} 页: 新增 {page_new} 个 ID，累计 {len(ids)}")
            if page_new == 0 or len(ids) >= max_collections:
                break
        except Exception as e:
            print(f"[!] 抓取 yckceo 合集页异常 ({url}): {e}")
            break
    print(f"[+] 从 yckceo 共提取合集 ID: {len(ids)} 个")
    return ids[:max_collections]


async def fetch_yckceo_sources(client, max_collections=YCKCEO_MAX_COLLECTIONS):
    """
    返回可直接下载的 yckceo 合集 JSON 直链列表。
    """
    ids = await fetch_yckceo_collection_ids(client, max_collections=max_collections)
    links = [YCKCEO_JSON_TMPL.format(id=cid) for cid in ids]
    print(f"[+] yckceo JSON 直链: {len(links)} 条")
    return links


# =========================
# 解析 / 清洗
# =========================
def json_loads_loose(text):
    text = (text or "").strip().lstrip("\ufeff")
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    for pos in [text.find("["), text.find("{")]:
        if pos >= 0:
            try:
                return json.loads(text[pos:].strip())
            except Exception:
                pass
    return None


def looks_like_booksource(x):
    if not isinstance(x, dict):
        return False
    url = x.get("bookSourceUrl")
    name = x.get("bookSourceName")
    if not isinstance(url, str) or not url.strip() or not is_http_url(url.strip()):
        return False
    if not isinstance(name, str) or not name.strip():
        return False
    if is_blacklisted(url, name):
        return False
    rules = ["searchUrl", "ruleSearch", "ruleBookInfo", "ruleToc", "ruleContent"]
    score = sum(1 for k in rules if k in x)
    return score >= 1


def extract_booksources(obj):
    result = []
    if looks_like_booksource(obj):
        result.append(obj)
    elif isinstance(obj, list):
        for x in obj:
            if looks_like_booksource(x):
                result.append(x)
    elif isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, list):
                for x in v:
                    if looks_like_booksource(x):
                        result.append(x)
            elif isinstance(v, dict) and looks_like_booksource(v):
                result.append(v)
    return result


def normalize_source(src):
    if not isinstance(src, dict):
        return None
    s = dict(src)
    s["bookSourceUrl"] = clean_url(s.get("bookSourceUrl", ""))
    s["bookSourceName"] = str(s.get("bookSourceName", "")).strip()
    if not s["bookSourceUrl"] or not s["bookSourceName"]:
        return None
    if "bookSourceType" not in s:
        s["bookSourceType"] = 0
    defaults = {
        "bookSourceGroup": "",
        "bookUrlPattern": "",
        "loginUrl": "",
        "header": "",
        "searchUrl": "",
        "exploreUrl": "",
        "enabled": True,
        "enabledExplore": False,
        "customOrder": 0,
        "weight": 0,
        "lastUpdateTime": 0,
        "ruleSearch": "{}",
        "ruleExplore": "{}",
        "ruleBookInfo": "{}",
        "ruleToc": "{}",
        "ruleContent": "{}",
    }
    for k, v in defaults.items():
        if k not in s:
            s[k] = v
    for k in ["ruleSearch", "ruleExplore", "ruleBookInfo", "ruleToc", "ruleContent"]:
        if isinstance(s.get(k), dict):
            s[k] = json.dumps(s[k], ensure_ascii=False, separators=(",", ":"))
    return s


def source_hash(src):
    x = dict(src)
    for k in ["lastUpdateTime", "customOrder", "weight", "enabled", "enabledExplore", "bookSourceGroup"]:
        x.pop(k, None)
    raw = json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


async def fetch_and_parse(client, url, semaphore):
    async with semaphore:
        try:
            response = await client.get(url, timeout=TIMEOUT, follow_redirects=True)
            if response.status_code != 200:
                return []
            # yckceo 部分合集体积较大，限制过大响应
            if len(response.content) > 30 * 1024 * 1024:
                print(f"[!] 跳过过大文件: {url} ({len(response.content)} bytes)")
                return []
            obj = json_loads_loose(response.text)
            found = extract_booksources(obj)
            if not found and isinstance(obj, str):
                found = extract_booksources(json_loads_loose(obj))
            return found
        except Exception:
            return []


# =========================
# 真实搜索测活（中间方案，不完整模拟 Legado）
# =========================
def _safe_json(obj):
    if isinstance(obj, str):
        try:
            return json.loads(obj)
        except Exception:
            return {}
    return obj if isinstance(obj, dict) else {}


def build_search_request(source, keyword):
    """
    尽量根据 searchUrl 构造一个可发送的请求。
    支持：
    - {{key}} / {key}
    - 相对路径补全
    - 简单 POST（如果 searchUrl 里带有 method 或 body 提示）
    返回: (method, url, data, headers) 或 None
    """
    search_url = (source.get("searchUrl") or "").strip()
    if not search_url:
        return None

    # 处理常见占位符
    url = search_url
    for ph in ["{{key}}", "{key}", "{{keyword}}", "{keyword}", "{{searchKey}}"]:
        if ph in url:
            url = url.replace(ph, keyword)
            break
    else:
        # 没有占位符时尝试追加
        if "?" in url:
            url += f"&key={keyword}" if not url.endswith(("&", "?")) else f"key={keyword}"
        else:
            url += f"?key={keyword}"

    # 相对路径补全
    base = (source.get("bookSourceUrl") or "").rstrip("/")
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = urljoin(base + "/", url.lstrip("/"))
    elif not url.startswith("http"):
        url = urljoin(base + "/", url)

    url = clean_url(url)

    # 处理 header
    headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept-Language": HEADERS["Accept-Language"],
        "Referer": source.get("bookSourceUrl", ""),
    }
    extra = source.get("header")
    if isinstance(extra, str) and extra.strip():
        try:
            extra_h = json.loads(extra)
            if isinstance(extra_h, dict):
                headers.update({str(k): str(v) for k, v in extra_h.items()})
        except Exception:
            pass

    # 简单判断是否 POST
    method = "GET"
    data = None
    if ",{" in search_url or "method" in search_url.lower() or "@post" in search_url.lower():
        method = "POST"
        data = {"key": keyword, "searchkey": keyword, "keyword": keyword}

    return method, url, data, headers


def is_bad_page(text: str) -> bool:
    """快速判断是否验证码 / Cloudflare / 广告 / 无关页"""
    if not text or len(text) < 80:
        return True

    bad_signals = [
        "验证码", "captcha", "滑动验证", "人机验证", "安全验证",
        "访问频率过快", "请稍后再试", "请求过于频繁",
        "cloudflare", "just a moment", "checking your browser",
        "请开启javascript", "enable javascript",
        "网站维护", "站点维护", "正在维护",
        "广告", "点击下载", "立即下载app", "加群",
        "淘宝", "返利", "优惠券",
    ]
    lower = text.lower()
    return any(s in text or s in lower for s in bad_signals)


def try_extract_books(text: str, rule_search: dict) -> list:
    """
    极简结果提取（不做完整 CSS/XPath/JSONPath 引擎）
    目标：只要能拿到至少一本「看起来像小说」的结果即可。
    返回 [{"name": ..., "url": ...}, ...]
    """
    results = []

    # 1. 规则提示
    book_list_sel = rule_search.get("bookList") or rule_search.get("list") or ""
    name_sel = rule_search.get("name") or rule_search.get("bookName") or ""

    # 2. 启发式：找包含中文书名 + href 的模式
    pattern = re.compile(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>\s*([^<]*[\u4e00-\u9fff]{2,20}[^<]*)\s*</a>',
        re.I
    )
    for m in pattern.finditer(text):
        href, title = m.group(1).strip(), m.group(2).strip()
        title = re.sub(r'\s+', ' ', title)
        if len(title) < 2 or len(title) > 40:
            continue
        # 过滤明显不是书名的
        if any(x in title for x in ["首页", "登录", "注册", "排行", "分类", "搜索", "下一页", "上一页"]):
            continue
        results.append({"name": title, "url": href})
        if len(results) >= 8:
            break

    # 3. 如果规则里明确有 name 选择器，且页面里出现了这些选择器字符串，也加分
    if book_list_sel and book_list_sel in text and name_sel:
        if not results:
            results.append({"name": "规则匹配到列表", "url": ""})

    return results


async def test_source_by_search(client, source, semaphore):
    """
    真实搜索测活：
    - 真实发起搜索
    - 判断是否验证码/无关页
    - 尝试提取到至少一本像样的书
    任意一个 TEST_KEYWORDS 成功即可返回 True
    """
    async with semaphore:
        if not (source.get("searchUrl") or "").strip():
            return False

        for keyword in TEST_KEYWORDS:
            req = build_search_request(source, keyword)
            if not req:
                continue

            method, url, data, headers = req
            try:
                if method == "POST":
                    r = await client.post(
                        url, data=data, headers=headers,
                        timeout=SEARCH_TIMEOUT, follow_redirects=True
                    )
                else:
                    r = await client.get(
                        url, headers=headers,
                        timeout=SEARCH_TIMEOUT, follow_redirects=True
                    )

                if r.status_code >= 400:
                    continue

                text = r.text
                if is_bad_page(text):
                    continue

                rule = _safe_json(source.get("ruleSearch", "{}"))
                books = try_extract_books(text, rule)

                if len(books) >= MIN_VALID_RESULTS:
                    return True

            except Exception:
                continue

        return False


# =========================
# 主流程
# =========================
async def main():
    print("[*] 启动书源同步引擎 (GitHub + yckceo 合集 + 真实搜索测活)...")
    github_token = os.environ.get("bot", "").strip() or os.environ.get("GITHUB_TOKEN", "").strip()
    client_headers = dict(HEADERS)
    if github_token:
        if github_token.startswith("Bearer ") or github_token.startswith("token "):
            client_headers["Authorization"] = github_token
        else:
            client_headers["Authorization"] = f"Bearer {github_token}"

    # 同步阶段：GitHub 发现
    with httpx.Client(headers=client_headers) as sync_client:
        candidates_github = discover_candidate_urls(sync_client)

    async with httpx.AsyncClient(headers=client_headers, follow_redirects=True) as client:
        # 1) yckceo 书源合集 JSON 直链
        yckceo_links = await fetch_yckceo_sources(client, max_collections=YCKCEO_MAX_COLLECTIONS)
        # 2) 合并候选
        candidates = list(set(candidates_github + yckceo_links))
        print(f"[+] 含 yckceo 在内，候选资源链接总数: {len(candidates)}")

        semaphore = asyncio.Semaphore(WORKERS)
        tasks = [fetch_and_parse(client, url, semaphore) for url in candidates]
        results = await asyncio.gather(*tasks)

        raw_sources = []
        for res in results:
            if res:
                raw_sources.extend(res)
        print(f"[+] 原始解析提取出的书源总数: {len(raw_sources)}")

        # 去重 + 结构清洗
        unique_sources = {}
        hash_seen = set()
        for item in raw_sources:
            norm = normalize_source(item)
            if not norm:
                continue
            url = norm.get("bookSourceUrl", "").strip()
            parsed_url = urlparse(url)
            domain_key = parsed_url.netloc + parsed_url.path.rstrip("/")
            if not domain_key:
                continue
            sh = source_hash(norm)
            if sh in hash_seen:
                continue
            hash_seen.add(sh)
            if domain_key not in unique_sources:
                unique_sources[domain_key] = norm
            else:
                existing = unique_sources[domain_key]
                # 优先保留带 searchUrl 的版本
                if not existing.get("searchUrl") and norm.get("searchUrl"):
                    unique_sources[domain_key] = norm

        cleaned_sources = list(unique_sources.values())
        print(f"[+] 去重及基础结构清洗后剩余: {len(cleaned_sources)}")

        # ========== 真实搜索测活 ==========
        print(f"[*] 开始真实搜索验证（关键词: {TEST_KEYWORDS}）...")
        test_tasks = [
            test_source_by_search(client, src, semaphore)
            for src in cleaned_sources
        ]
        test_results = await asyncio.gather(*test_tasks)
        valid_sources = [
            src for src, ok in zip(cleaned_sources, test_results) if ok
        ]

        print(f"[+] 搜索验证通过的书源数量: {len(valid_sources)}")

        # 可选：如果过滤太狠，回退到结构清洗后的全量（方便调试）
        if len(valid_sources) < 10 and cleaned_sources:
            print("[!] 搜索验证过严，自动回退到结构清洗后的全量书源（仅用于调试）")
            valid_sources = cleaned_sources

        with open(OUTPUT_FILENAME, "w", encoding="utf-8") as f:
            json.dump(valid_sources, f, ensure_ascii=False, indent=2)
        print(f"[+] 完成！输出文件: {OUTPUT_FILENAME}")


if __name__ == "__main__":
    asyncio.run(main())