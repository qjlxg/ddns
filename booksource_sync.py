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
]

# yckceo 书源合集 JSON 模板（由爬虫动态填充 ID）
YCKCEO_JSON_TMPL = "https://www.yckceo.com/yuedu/shuyuans/json/id/{id}.json"
YCKCEO_COLLECTIONS_URL = "https://www.yckceo.com/yuedu/shuyuans/index.html"
# 最多抓取多少个合集（按页面出现顺序，一般越新越靠前）
YCKCEO_MAX_COLLECTIONS = 50

GITHUB_QUERIES = [
    "filename:bookSource.json",
    "filename:booksource.json",
    "filename:shuyuan.json",
    "filename:source.json",
    "filename:sources.json",
    "filename:bookSource",
    "filename:shuyuan",
    "filename:full.json path:legado",
    "filename:*.json \"bookSourceUrl\"",
    "bookSourceUrl ruleSearch",
    "bookSourceName ruleContent",
    "bookSourceUrl ruleToc",
    "searchUrl ruleSearch ruleBookInfo",
    "ruleSearch ruleBookInfo ruleToc ruleContent",
    "Legado bookSource",
    "Legado booksource",
    "Legado source",
    "Legado sources",
    "Legado 书源",
    "LegadoConfig",
    "阅读 书源",
    "阅读 书源 json",
    "阅读APP 书源",
    "阅读 app 书源",
    "开源 阅读 书源",
    "yuedu shuyuan",
    "yuedu booksource",
    "yuedu 书源",
    "legado 书源 json",
]

BLACKLIST_DOMAINS = ["baidu.com", "qq.com", "bilibili.com", "zhihu.com", "so.com"]
BLACKLIST_KEYWORDS = ["点此广告", "加群", "淘宝", "返利", "APP下载"]

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36 Edg/154.0.0.0",
     "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",}          
  

   


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
# yckceo 源仓库合集抓取（核心改进）
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
# 解析 / 清洗 / 测活
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
            if len(response.content) > 20 * 1024 * 1024:
                print(f"[!] 跳过过大文件: {url} ({len(response.content)} bytes)")
                return []
            obj = json_loads_loose(response.text)
            found = extract_booksources(obj)
            if not found and isinstance(obj, str):
                found = extract_booksources(json_loads_loose(obj))
            return found
        except Exception:
            return []

async def test_source_validity(client, source, semaphore):
    """
    轻量测活：优先探测根域名可达性。
    注意：根路径 403/404 但搜索可用的源会被误杀，故主流程有兜底。
    """
    async with semaphore:
        book_url = source.get("bookSourceUrl") or ""
        try:
            parsed = urlparse(book_url)
            if not parsed.scheme or not parsed.netloc:
                return False
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            response = await client.get(
                base_url,
                headers={
                    "User-Agent": HEADERS["User-Agent"],
                    "Accept-Language": HEADERS["Accept-Language"],
                },
                timeout=6.0,
                follow_redirects=True,
            )
            return response.status_code < 400
        except Exception:
            return False

# =========================
# 主流程
# =========================
async def main():
    print("[*] 启动书源同步引擎 (GitHub + yckceo 合集)...")
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

        # 去重
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

        # 测活
        print("[*] 开始后端站点存活验证...")
        test_tasks = [test_source_validity(client, src, semaphore) for src in cleaned_sources]
        test_results = await asyncio.gather(*test_tasks)
        valid_sources = [src for src, ok in zip(cleaned_sources, test_results) if ok]

        if len(valid_sources) < 20 and cleaned_sources:
            print("[!] 存活验证过滤较多，自动回退到清洗后全量书源。")
            valid_sources = cleaned_sources
        else:
            print(f"[+] 有效存活书源数量: {len(valid_sources)}")

        with open(OUTPUT_FILENAME, "w", encoding="utf-8") as f:
            json.dump(valid_sources, f, ensure_ascii=False, indent=2)
        print(f"[+] 完成！输出文件: {OUTPUT_FILENAME}")

if __name__ == "__main__":
    asyncio.run(main())
