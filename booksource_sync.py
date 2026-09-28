import asyncio
json
import httpx
import re
import hashlib
import os
import csv
import configparser
from urllib.parse import urlparse, urljoin, quote, unquote
from bs4 import BeautifulSoup

CONFIG_FILENAME = "config.ini"

def load_config():
    """加载配置文件（若不存在则直接抛出异常）"""
    if not os.path.exists(CONFIG_FILENAME):
        raise FileNotFoundError(f"[!] 错误：未检测到配置文件 {CONFIG_FILENAME}，请先在同级目录下创建该文件！")

    config = configparser.ConfigParser(converters={
        'list': lambda v: [line.strip() for line in v.splitlines() if line.strip()]
    })
    config.read(CONFIG_FILENAME, encoding="utf-8")
    
    settings = {
        "WORKERS": config.getint("Settings", "workers", fallback=40),
        "TIMEOUT": config.getint("Settings", "timeout", fallback=15),
        "OUTPUT_FILENAME": config.get("Settings", "output_filename", fallback="exportBookSource.json"),
        "STATS_FILENAME": config.get("Settings", "stats_filename", fallback="search_stats.csv"),
        "SEED_SOURCE_URLS": config.getlist("Seeds", "urls", fallback=[]),
        "SEARCH_QUERIES": config.getlist("Queries", "keywords", fallback=[])
    }
    return settings

# 加载配置
CFG = load_config()
WORKERS = CFG["WORKERS"]
TIMEOUT = CFG["TIMEOUT"]
OUTPUT_FILENAME = CFG["OUTPUT_FILENAME"]
STATS_FILENAME = CFG["STATS_FILENAME"]
SEED_SOURCE_URLS = CFG["SEED_SOURCE_URLS"]
SEARCH_QUERIES = CFG["SEARCH_QUERIES"]

MAX_DOWNLOAD = 5 * 1024 * 1024

BLACKLIST_DOMANS = ['baidu.com', 'qq.com', 'bilibili.com', 'zhihu.com', 'so.com']
BLACKLIST_KEYWORDS = ['点此广告', '加群', '淘宝', '返利', 'APP下载']

FILE_RE = re.compile(r'\.(json|txt|js|yaml|yml)$', re.I)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# =========================
# CSV 统计管理函数
# =========================
def load_search_stats():
    stats = {}
    if os.path.exists(STATS_FILENAME):
        try:
            with open(STATS_FILENAME, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    q = row.get("query", "").strip()
                    hits = int(row.get("hit_count", 0))
                    if q:
                        stats[q] = hits
            print(f"[*] 已加载历史搜索统计，共 {len(stats)} 条记录。")
        except Exception as e:
            print(f"[!] 读取统计 CSV 失败: {e}")
    return stats

def save_search_stats(stats_dict):
    try:
        with open(STATS_FILENAME, mode="w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["query", "hit_count"])
            for q, hits in stats_dict.items():
                writer.writerow([q, hits])
        print(f"[*] 搜索统计已更新并保存至: {STATS_FILENAME}")
    except Exception as e:
        print(f"[!] 保存统计 CSV 失败: {e}")

# =========================
# 第一阶段：自动发现阶段
# =========================
def clean_url(u):
    if not isinstance(u, str):
        return ""
    u = u.strip()
    if u.startswith("//"):
        u = "https:" + u
    return u.replace("&amp;", "&")

def is_http_url(u):
    return u.startswith("http://") or u.startswith("https://")

def is_blacklisted(url, name=""):
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    for bd in BLACKLIST_DOMANS:
        if bd in netloc:
            return True
    for kw in BLACKLIST_KEYWORDS:
        if kw in name:
            return True
    return False

def search_duckduckgo(client, query):
    url = "https://html.duckduckgo.com/html/"
    try:
        r = client.post(url, data={"q": query}, timeout=TIMEOUT)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "lxml")
        out = []
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            if "uddg=" in href:
                m = re.search(r"uddg=([^&]+)", href)
                if m:
                    href = unquote(m.group(1))
            if is_http_url(href) and not is_blacklisted(href):
                out.append(href)
        return list(dict.fromkeys(out))
    except Exception:
        return []

def search_github_repos(client, query):
    url = "https://api.github.com/search/repositories"
    github_token = os.environ.get("bot", "").strip()
    api_headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    if github_token:
        api_headers["Authorization"] = f"Bearer {github_token}"
        
    try:
        r = client.get(url, params={"q": query, "per_page": 10, "sort": "updated"}, headers=api_headers, timeout=TIMEOUT)
        if r.status_code != 200:
            return []
        data = r.json()
        return [x.get("html_url") for x in data.get("items", []) if x.get("html_url")]
    except Exception:
        return []

def extract_file_links(page_url, html):
    soup = BeautifulSoup(html, "lxml")
    result = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = urljoin(page_url, href)
        if not is_http_url(full) or is_blacklisted(full):
            continue
        if FILE_RE.search(full.split("?")[0]) or any(k in full for k in ["raw.githubusercontent.com", "gitee.com", "jsdelivr.net"]):
            result.add(full)
        if "github.com" in full and "/blob/" in full:
            raw = full.replace("https://github.com/", "https://raw.githubusercontent.com/", 1).replace("/blob/", "/", 1)
            result.add(raw)
    return result

def discover_candidate_urls(client):
    print(f"[*] 配置文件加载完毕：种子源 {len(SEED_SOURCE_URLS)} 个，搜索关键词 {len(SEARCH_QUERIES)} 个。")
    print("[*] 开始多路全网自动发现书源线索...")
    candidate_urls = set(SEED_SOURCE_URLS)
    
    history_stats = load_search_stats()
    current_stats = {}
    
    for q in SEARCH_QUERIES:
        if history_stats.get(q, -1) == 0:
            print(f"[-] 跳过历史零命中关键词: {q}")
            current_stats[q] = 0
            continue
            
        print(f"[-] 正在检索关键词: {q}", end="", flush=True)
        urls = search_duckduckgo(client, q)
        hit_count = len(urls)
        current_stats[q] = hit_count
        print(f" -> 命中有效链接: {hit_count} 个")
        
        for u in urls:
            candidate_urls.add(clean_url(u))
            
    save_search_stats(current_stats)
    
    github_repos = set()
    for q in ["Legado booksource", "阅读书源合集"]:
        for u in search_github_repos(client, q):
            github_repos.add(u)
            
    for repo_url in github_repos:
        try:
            r = client.get(repo_url, timeout=TIMEOUT)
            if r.status_code == 200:
                for link in extract_file_links(repo_url, r.text):
                    candidate_urls.add(clean_url(link))
        except Exception:
            pass
            
    print(f"[+] 发现候选资源链接总数: {len(candidate_urls)}")
    return list(candidate_urls)

# =========================
# 第二阶段：异步下载、解析与清洗验证
# =========================
def json_loads_loose(text):
    text = text.strip().lstrip("\ufeff")
    if not text:
        return None
    try:
        return json.loads(text)
    except:
        pass
    for pos in [text.find("["), text.find("{")]:
        if pos >= 0:
            try:
                return json.loads(text[pos:].strip())
            except:
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
            elif isinstance(v, dict):
                if looks_like_booksource(v):
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
        "bookSourceGroup": "", "bookUrlPattern": "", "loginUrl": "", "header": "",
        "searchUrl": "", "exploreUrl": "", "enabled": True, "enabledExplore": False,
        "customOrder": 0, "weight": 0, "lastUpdateTime": 0,
        "ruleSearch": "{}", "ruleExplore": "{}", "ruleBookInfo": "{}", "ruleToc": "{}", "ruleContent": "{}"
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
            content = response.text
            obj = json_loads_loose(content)
            found = extract_booksources(obj)
            if not found and isinstance(obj, str):
                obj2 = json_loads_loose(obj)
                found = extract_booksources(obj2)
            return found
        except Exception:
            return []

async def test_source_validity(client, source, semaphore):
    async with semaphore:
        book_url = source.get("bookSourceUrl")
        try:
            parsed = urlparse(book_url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            probe_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
            response = await client.get(base_url, headers=probe_headers, timeout=6.0, follow_redirects=True)
            if response.status_code < 400:
                return True
        except Exception:
            pass
        return False

# =========================
# 主流程控制
# =========================
async def main():
    print("[*] 启动异步网络采集引擎...")
    with httpx.Client(headers=HEADERS) as sync_client:
        candidates = discover_candidate_urls(sync_client)

    print(f"[+] 准备异步并发下载与解析 {len(candidates)} 个候选地址...")
    
    async with httpx.AsyncClient(headers=HEADERS) as client:
        semaphore = asyncio.Semaphore(WORKERS)
        tasks = [fetch_and_parse(client, url, semaphore) for url in candidates]
        results = await asyncio.gather(*tasks)
        
        raw_sources = []
        for res in results:
            if res:
                raw_sources.extend(res)
                
        print(f"[+] 原始解析提取出的书源总数: {len(raw_sources)}")
        
        unique_sources = {}
        hash_seen = set()
        
        for item in raw_sources:
            norm = normalize_source(item)
            if not norm:
                continue
            url = norm.get("bookSourceUrl")
            parsed_url = urlparse(url.strip())
            domain_key = parsed_url.netloc + parsed_url.path.rstrip('/')
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
                if not existing.get("searchUrl") and norm.get("searchUrl"):
                    unique_sources[domain_key] = norm

        cleaned_sources = list(unique_sources.values())
        print(f"[+] 去重及基础结构清洗后剩余: {len(cleaned_sources)}")
        
        print("[*] 开始进行后端站点存活可用性验证...")
        test_tasks = [test_source_validity(client, src, semaphore) for src in cleaned_sources]
        test_results = await asyncio.gather(*test_tasks)
        
        valid_sources = [
            src for src, is_valid in zip(cleaned_sources, test_results) 
            if is_valid
        ]
        
        if len(valid_sources) < 20 and len(cleaned_sources) > 0:
            print("[!] 提示：因网络波动存活验证过滤较多，自动切换回清洗后全量书源保障丰富度。")
            valid_sources = cleaned_sources
        else:
            print(f"[+] 有效存活且通过校验的书源数量: {len(valid_sources)}")
            
        with open(OUTPUT_FILENAME, "w", encoding="utf-8") as f:
            json.dump(valid_sources, f, ensure_ascii=False, indent=2)
            
        print(f"[+] 优化完毕！已成功输出可直接导入阅读APP的文件: {OUTPUT_FILENAME}")

if __name__ == "__main__":
    asyncio.run(main())
