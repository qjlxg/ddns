import asyncio
import json
import httpx
import re
import hashlib
import os
import csv
from urllib.parse import urlparse, urljoin, unquote
from bs4 import BeautifulSoup

# =========================
# 配置项
# =========================
OUTPUT_FILENAME = "exportBookSource.json"
STATS_FILENAME = "search_stats.csv"
WORKERS = 40          # 异步并发数
TIMEOUT = 15          # 超时时间
MAX_DOWNLOAD = 5 * 1024 * 1024

# 预设的高质量种子源 / 聚合仓库直链（作为冷启动补充）
SEED_SOURCE_URLS = [
    # 1. XIU2 / Yuedu
    "https://jsdelivr.onmicrosoft.cn/gh/XIU2/Yuedu@master/shuyuan",
    "https://raw.githubusercontent.com/XIU2/Yuedu/master/shuyuan",
    "https://jsd.onmicrosoft.cn/gh/XIU2/Yuedu/shuyuan",
    "https://raw.githubusercontent.com/aoaostar/legado/refs/heads/release/sources/2a1f129b.json",
    
    # 2. AOAOSTAR / legado 聚合
    "https://legado.aoaostar.com/sources/b778fe6b.json",
    "https://legado.aoaostar.com/sources/71e56d4f.json",
    "https://legado.aoaostar.com/sources/4dc410d1.json",
    "https://legado.aoaostar.com/sources/e3e5d620.json",
    
    # 3. Gitee / 国内代码托管
    "https://gitee.com/YiJieSS/Yuedu/raw/master/bookSource.json",
    "https://gitee.com/zoeybai/read/raw/Xiaobai/bangdan.json",
    "https://www.gitlink.org.cn/api/yi-c/yd/raw?filepath=sy.json",
    "https://gitee.com/fjhy2021/yuedu/raw/master/shuyuan.json",
    
    # 4. Tickmao / Novel
    "https://cdn.jsdelivr.net/gh/tickmao/Novel@master/sources/legado/full.json",
    "https://raw.githubusercontent.com/tickmao/Novel/master/sources/legado/full.json",
    
    # 5. 轻小说 / 日轻专项
    "https://raw.githubusercontent.com/jiwangyihao/source-j-legado/main/bilinovel.json",
    "https://raw.githubusercontent.com/jiwangyihao/source-j-legado/main/wenku.json",
    "https://raw.githubusercontent.com/jiwangyihao/source-j-legado/main/fishhawk.json",
    
    # 6. 其他 GitHub 综合书源
    "https://raw.githubusercontent.com/MoGu123456/yuedu/main/shuyuan.json",
    "https://raw.githubusercontent.com/ywdblog/legado/master/shuyuan.json",
    "https://raw.githubusercontent.com/DesperadoJ/LegadoConfig/master/source.json",
    "https://raw.githubusercontent.com/shidahuilang/shuyuan/shuyuan/good.json",
    
    # 7. DowneyRem / PixivSource
    "https://raw.githubusercontent.com/DowneyRem/PixivSource/main/pixiv.json",
    "https://raw.githubusercontent.com/DowneyRem/PixivSource/main/normal.json",
    "https://raw.githubusercontent.com/DowneyRem/PixivSource/main/books.json",

    # 8. 外部书源管理站
    "https://shuyuan.yiove.com/sub.json",
    "https://yuedu.miaogongzi.net/gx.html",
    "https://www.yckceo.com/yuedu/shuyuan/index.html",
]

# GitHub 代码检索关键词（精准匹配文件/仓库）
GITHUB_QUERIES = [
    "filename:bookSource.json",
    "filename:shuyuan.json",
    "Legado bookSource",
    "阅读 书源 json",
    "Legado 书源"
]

BLACKLIST_DOMANS = ['baidu.com', 'qq.com', 'bilibili.com', 'zhihu.com', 'so.com']
BLACKLIST_KEYWORDS = ['点此广告', '加群', '淘宝', '返利', 'APP下载']

FILE_RE = re.compile(r'\.(json|txt|js|yaml|yml)$', re.I)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
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

# =========================
# 第一阶段：GitHub Search API 驱动的自动发现
# =========================
def search_github_api(client, query):
    """通过 GitHub Search API 搜索代码或仓库"""
    github_token = os.environ.get("bot", "").strip()
    api_headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    if github_token:
        api_headers["Authorization"] = f"Bearer {github_token}"
    
    discovered_urls = set()
    
    # 1. 搜索代码文件 (Code Search)
    code_url = "https://api.github.com/search/code"
    try:
        r = client.get(code_url, params={"q": query, "per_page": 20}, headers=api_headers, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            for item in data.get("items", []):
                # 将 GitHub 网页/api 链接转换为 raw 直链
                html_url = item.get("html_url", "")
                if html_url:
                    raw_url = html_url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
                    discovered_urls.add(clean_url(raw_url))
        elif r.status_code == 403:
            print(f"[!] GitHub API 触发限频或 Token 权限不足 (403): {query}")
        else:
            print(f"[!] GitHub Code API 返回状态码 {r.status_code}，关键词: {query}")
    except Exception as e:
        print(f"[!] GitHub Code 搜索异常: {e}")

    # 2. 搜索仓库 (Repository Search)
    repo_url = "https://api.github.com/search/repositories"
    try:
        r = client.get(repo_url, params={"q": query, "per_page": 10, "sort": "updated"}, headers=api_headers, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            for item in data.get("items", []):
                default_branch = item.get("default_branch", "master")
                clone_url = item.get("html_url")
                if clone_url:
                    # 常见的标准路径推测
                    owner_repo = clone_url.replace("https://github.com/", "")
                    discovered_urls.add(f"https://raw.githubusercontent.com/{owner_repo}/{default_branch}/bookSource.json")
                    discovered_urls.add(f"https://raw.githubusercontent.com/{owner_repo}/{default_branch}/shuyuan.json")
    except Exception as e:
        print(f"[!] GitHub Repo 搜索异常: {e}")

    return list(discovered_urls)

def discover_candidate_urls(client):
    print("[*] 开始通过 GitHub Search API 深度发掘书源线索...")
    candidate_urls = set(SEED_SOURCE_URLS)
    
    for q in GITHUB_QUERIES:
        print(f"[-] 正在通过 GitHub API 检索: {q}", end="", flush=True)
        urls = search_github_api(client, q)
        print(f" -> 发现线索: {len(urls)} 条")
        for u in urls:
            candidate_urls.add(u)
            
    print(f"[+] 候选资源链接总数 (含种子源): {len(candidate_urls)}")
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
    print("[*] 启动 GitHub API 异步书源同步引擎...")
    
    # 建立客户端并传入 Token（若环境变量存在）
    github_token = os.environ.get("bot", "").strip()
    client_headers = dict(HEADERS)
    if github_token:
        client_headers["Authorization"] = f"Bearer {github_token}"

    with httpx.Client(headers=client_headers) as sync_client:
        candidates = discover_candidate_urls(sync_client)

    print(f"[+] 准备异步并发下载与解析 {len(candidates)} 个候选地址...")
    
    async with httpx.AsyncClient(headers=client_headers) as client:
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
