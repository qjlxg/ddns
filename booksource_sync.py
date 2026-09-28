import asyncio
import json
import httpx
import re
import hashlib
from urllib.parse import urlparse, urljoin, quote
from bs4 import BeautifulSoup

# =========================
# 配置项
# =========================
OUTPUT_FILENAME = "exportBookSource.json"
WORKERS = 30
TIMEOUT = 12
MAX_DOWNLOAD = 3 * 1024 * 1024

# 预设的基础种子源（你可以保留或添加自己收集的稳定订阅源）
SEED_SOURCE_URLS = [
    # "https://raw.githubusercontent.com/example/legado/master/bookSource.json",
]

# 搜索关键词（用于自动从全网和 GitHub 挖掘书源文件）
SEARCH_QUERIES = [
    'Legado 书源 json',
    '阅读 书源 json',
    'Legado booksource json',
    '阅读书源 bookSourceUrl',
    'site:github.com Legado 书源 json',
    'site:github.com 阅读 书源 json',
    'site:raw.githubusercontent.com bookSourceUrl',
]

FILE_RE = re.compile(r'\.(json|txt|js|yaml|yml)$', re.I)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# =========================
# 第一阶段：自动发现阶段 (同步/阻塞部分)
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

def ddg_search(client, query):
    url = "https://html.duckduckgo.com/html/"
    try:
        r = client.get(url, params={"q": query}, timeout=TIMEOUT)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "lxml")
        out = []
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            if "uddg=" in href:
                m = re.search(r"uddg=([^&]+)", href)
                if m:
                    from urllib.parse import unquote
                    href = unquote(m.group(1))
            if is_http_url(href):
                out.append(href)
        return list(dict.fromkeys(out))
    except Exception:
        return []

def github_api_repositories(client, query):
    url = "https://api.github.com/search/repositories"
    try:
        r = client.get(url, params={"q": query, "per_page": 20, "sort": "updated"}, timeout=TIMEOUT)
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
        if not is_http_url(full):
            continue
        if FILE_RE.search(full.split("?")[0]) or "raw.githubusercontent.com" in full:
            result.add(full)
        if "github.com" in full and "/blob/" in full:
            raw = full.replace("https://github.com/", "https://raw.githubusercontent.com/", 1).replace("/blob/", "/", 1)
            result.add(raw)
    return result

def discover_candidate_urls(client):
    print("[*] 开始全网自动发现书源线索...")
    candidate_urls = set(SEED_SOURCE_URLS)
    
    # 1. 搜索引擎检索
    for q in SEARCH_QUERIES:
        for u in ddg_search(client, q):
            candidate_urls.add(clean_url(u))
            
    # 2. GitHub 仓库检索与文件提取
    github_repos = set()
    for q in ["Legado booksource", "阅读书源"]:
        for u in github_api_repositories(client, q):
            github_repos.add(u)
            
    for repo_url in github_repos:
        try:
            r = client.get(repo_url, timeout=TIMEOUT)
            if r.status_code == 200:
                for link in extract_file_links(repo_url, r.text):
                    candidate_urls.add(clean_url(link))
        except Exception:
            pass
            
    print(f"[+] 发现候选链接总数: {len(candidate_urls)}")
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
    if not isinstance(url, str) or not url.strip():
        return False
    if not isinstance(name, str) or not name.strip():
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
    for k in ["lastUpdateTime", "customOrder", "weight", "enabled", "enabledExplore"]:
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
            response = await client.get(book_url, timeout=5.0, follow_redirects=True)
            if response.status_code < 400:
                return True
        except Exception:
            pass
        return False

# =========================
# 主流程控制
# =========================
async def main():
    print("[*] 启动异步网络客户端...")
    # 使用标准同步客户端先抓取候选链接
    with httpx.Client(headers=HEADERS) as sync_client:
        candidates = discover_candidate_urls(sync_client)

    print(f"[+] 准备异步下载和解析 {len(candidates)} 个候选地址...")
    
    async with httpx.AsyncClient(headers=HEADERS) as client:
        semaphore = asyncio.Semaphore(WORKERS)
        
        # 步骤 1：并发拉取与解析所有候选文件中的书源
        tasks = [fetch_and_parse(client, url, semaphore) for url in candidates]
        results = await asyncio.gather(*tasks)
        
        raw_sources = []
        for res in results:
            if res:
                raw_sources.extend(res)
                
        print(f"[+] 原始提取书源总数: {len(raw_sources)}")
        
        # 步骤 2：结构清洗与去重
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
                # 覆盖规则更丰富的源
                existing = unique_sources[domain_key]
                if not existing.get("searchUrl") and norm.get("searchUrl"):
                    unique_sources[domain_key] = norm

        cleaned_sources = list(unique_sources.values())
        print(f"[+] 去重及基础清洗后数量: {len(cleaned_sources)}")
        
        # 步骤 3：可用性连通性过滤
        print("[*] 开始进行后端存活可用性过滤...")
        test_tasks = [test_source_validity(client, src, semaphore) for src in cleaned_sources]
        test_results = await asyncio.gather(*test_tasks)
        
        valid_sources = [
            src for src, is_valid in zip(cleaned_sources, test_results) 
            if is_valid
        ]
        
        # 如果连通性过滤由于目标网站防爬导致误杀过多，可兜底退回到 cleaned_sources
        if len(valid_sources) < 10 and len(cleaned_sources) > 0:
            print("[!] 提示：存活验证通过较少，采用清洗后全量书源保障可用性。")
            valid_sources = cleaned_sources
        else:
            print(f"[+] 有效存活书源留存数量: {len(valid_sources)}")
            
        # 步骤 4：保存标准格式 JSON 供客户端导入
        with open(OUTPUT_FILENAME, "w", encoding="utf-8") as f:
            json.dump(valid_sources, f, ensure_ascii=False, indent=2)
            
        print(f"[+] 已成功输出至客户端导入文件: {OUTPUT_FILENAME}")

if __name__ == "__main__":
    asyncio.run(main())
