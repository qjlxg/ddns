#!/usr/bin/env python3
"""
httpx_probe.py
批量探测域名/URL 存活状态、状态码、网页标题、Server、基础技术栈。
输入: targets.txt（每行一个 host 或 URL）
输出: results/httpx_probe_YYYYMMDD_HHMMSS.csv（北京时间）
"""

import csv
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

# ==================== 配置 ====================
INPUT_FILE = Path("targets.txt")
OUTPUT_DIR = Path("results")
MAX_WORKERS = 30
TIMEOUT = 12
FOLLOW_REDIRECTS = True
USER_AGENT = "Mozilla/5.0 (compatible; httpx_probe/1.0; +https://github.com)"

# 北京时间
BJ_TZ = timezone(timedelta(hours=8))

# ==================== 工具函数 ====================
def now_bj() -> str:
    return datetime.now(BJ_TZ).strftime("%Y%m%d_%H%M%S")

def normalize_url(line: str) -> str | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if not line.startswith(("http://", "https://")):
        # 默认尝试 https，失败再试 http 由探测逻辑处理
        return f"https://{line}"
    return line

def extract_title(html: str) -> str:
    try:
        soup = BeautifulSoup(html, "html.parser")
        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            return title_tag.string.strip()[:200]
        # 备选：meta title
        meta = soup.find("meta", property="og:title") or soup.find("meta", attrs={"name": "title"})
        if meta and meta.get("content"):
            return meta["content"].strip()[:200]
    except Exception:
        pass
    return ""

def detect_tech(headers: httpx.Headers, html: str, server: str) -> str:
    """简单技术栈识别（基于 header + 页面特征）"""
    techs = set()
    server_lower = (server or "").lower()
    html_lower = html[:5000].lower() if html else ""

    # Server header
    if "nginx" in server_lower:
        techs.add("Nginx")
    if "apache" in server_lower:
        techs.add("Apache")
    if "microsoft-iis" in server_lower or "iis" in server_lower:
        techs.add("IIS")
    if "cloudflare" in server_lower:
        techs.add("Cloudflare")
    if "openresty" in server_lower:
        techs.add("OpenResty")

    # Headers
    if "x-powered-by" in headers:
        powered = headers["x-powered-by"].lower()
        if "php" in powered:
            techs.add("PHP")
        if "asp.net" in powered:
            techs.add("ASP.NET")
        if "express" in powered:
            techs.add("Express")
    if "x-aspnet-version" in headers:
        techs.add("ASP.NET")
    if "cf-ray" in headers:
        techs.add("Cloudflare")

    # HTML 特征
    if "wp-content" in html_lower or "wordpress" in html_lower:
        techs.add("WordPress")
    if "drupal" in html_lower:
        techs.add("Drupal")
    if "joomla" in html_lower:
        techs.add("Joomla")
    if "vue" in html_lower or "vue.js" in html_lower:
        techs.add("Vue.js")
    if "react" in html_lower or "react-dom" in html_lower:
        techs.add("React")
    if "jquery" in html_lower:
        techs.add("jQuery")
    if "bootstrap" in html_lower:
        techs.add("Bootstrap")
    if "laravel" in html_lower:
        techs.add("Laravel")
    if "thinkphp" in html_lower:
        techs.add("ThinkPHP")

    return ",".join(sorted(techs)) if techs else ""

def probe_one(url: str) -> dict:
    """探测单个 URL"""
    result = {
        "url": url,
        "final_url": "",
        "status_code": 0,
        "title": "",
        "server": "",
        "content_type": "",
        "content_length": 0,
        "tech": "",
        "ip": "",
        "error": "",
        "response_time_ms": 0,
    }

    start = time.time()
    try:
        with httpx.Client(
            follow_redirects=FOLLOW_REDIRECTS,
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT},
            verify=False,  # 忽略证书错误，测绘场景常用
        ) as client:
            resp = client.get(url)
            elapsed = int((time.time() - start) * 1000)

            result["final_url"] = str(resp.url)
            result["status_code"] = resp.status_code
            result["server"] = resp.headers.get("server", "")
            result["content_type"] = resp.headers.get("content-type", "").split(";")[0]
            result["content_length"] = len(resp.content)
            result["response_time_ms"] = elapsed

            # 尝试获取 IP（简单方式）
            try:
                result["ip"] = str(resp.extensions.get("network_stream").get_extra_info("peername")[0]) if resp.extensions.get("network_stream") else ""
            except Exception:
                pass

            html = ""
            if "text/html" in result["content_type"] or resp.status_code < 400:
                try:
                    html = resp.text
                    result["title"] = extract_title(html)
                except Exception:
                    pass

            result["tech"] = detect_tech(resp.headers, html, result["server"])

    except httpx.TimeoutException:
        result["error"] = "timeout"
    except httpx.ConnectError as e:
        result["error"] = f"connect_error: {str(e)[:80]}"
    except Exception as e:
        result["error"] = f"error: {str(e)[:100]}"

    return result

def main():
    if not INPUT_FILE.exists():
        print(f"[!] 输入文件不存在: {INPUT_FILE}")
        print("    请创建 targets.txt，每行一个域名或 URL")
        sys.exit(1)

    targets = []
    with open(INPUT_FILE, encoding="utf-8", errors="ignore") as f:
        for line in f:
            u = normalize_url(line)
            if u:
                targets.append(u)

    if not targets:
        print("[!] targets.txt 为空")
        sys.exit(1)

    print(f"[*] 共加载 {len(targets)} 个目标")
    print(f"[*] 并发: {MAX_WORKERS} | 超时: {TIMEOUT}s")
    print(f"[*] 开始探测... (北京时间 {datetime.now(BJ_TZ).strftime('%Y-%m-%d %H:%M:%S')})")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = now_bj()
    output_file = OUTPUT_DIR / f"httpx_probe_{timestamp}.csv"
    latest_file = OUTPUT_DIR / "httpx_probe_latest.csv"

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_url = {executor.submit(probe_one, url): url for url in targets}
        done = 0
        for future in as_completed(future_to_url):
            done += 1
            res = future.result()
            results.append(res)
            status = res["status_code"] or res["error"]
            title_short = (res["title"][:40] + "...") if len(res["title"]) > 40 else res["title"]
            print(f"[{done}/{len(targets)}] {res['url']} -> {status} | {title_short}")

    fieldnames = [
        "url", "final_url", "status_code", "title", "server",
        "content_type", "content_length", "tech", "ip",
        "response_time_ms", "error"
    ]

    with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    # 同时写一份 latest
    with open(latest_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    alive = sum(1 for r in results if 200 <= r["status_code"] < 400)
    print(f"\n[+] 完成！存活: {alive}/{len(results)}")
    print(f"[+] 结果已保存: {output_file}")
    print(f"[+] 最新结果: {latest_file}")

if __name__ == "__main__":
    # 忽略 SSL 警告
    import warnings
    warnings.filterwarnings("ignore")
    main()
