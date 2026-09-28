import asyncio
import json
import httpx
from urllib.parse import urlparse

# 1. 预设的原始书源订阅/公开链接列表（可自行扩展）
SOURCE_URLS = [
    "https://raw.githubusercontent.com/example/legado/master/bookSource.json",
    # 更多订阅源...
]

# 异步获取单个源文件的内容
async def fetch_source_file(client, url):
    try:
        response = await client.get(url, timeout=15.0)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"[-] 获取源失败 {url}: {e}")
    return []

# 2. 核心去重与清洗逻辑
def clean_and_deduplicate(raw_sources):
    unique_sources = {}
    
    for item in raw_sources:
        # 基础字段检查
        url = item.get("bookSourceUrl")
        name = item.get("bookSourceName")
        if not url or not name:
            continue
            
        # 规范化 URL（去除末尾斜杠，统一小写等）
        parsed_url = urlparse(url.strip())
        domain_key = parsed_url.netloc + parsed_url.path.rstrip('/')
        
        if not domain_key:
            continue
            
        # 去重：如果已存在，按需覆盖（例如保留带搜索规则更完整的）
        if domain_key not in unique_sources:
            unique_sources[domain_key] = item
        else:
            # 简单的替换策略：如果新源有具体的 searchUrl 而旧的没有，则替换
            existing = unique_sources[domain_key]
            if not existing.get("searchUrl") and item.get("searchUrl"):
                unique_sources[domain_key] = item

    return list(unique_sources.values())

# 3. 简单的可用性验证（可选）
async def test_source_validity(client, source):
    book_url = source.get("bookSourceUrl")
    try:
        # 发送 HEAD 或 GET 请求测试目标网站是否存活
        response = await client.get(book_url, timeout=5.0, follow_redirects=True)
        if response.status_code < 400:
            return True
    except Exception:
        pass
    return False

async def main():
    all_raw_sources = []
    
    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:
        # 步骤 1：批量拉取
        tasks = [fetch_source_file(client, url) for url in SOURCE_URLS]
        results = await asyncio.gather(*tasks)
        for res in results:
            if isinstance(res, list):
                all_raw_sources.extend(res)
                
        print(f"[+] 原始拉取总数: {len(all_raw_sources)}")
        
        # 步骤 2：去重与结构清洗
        cleaned_sources = clean_and_deduplicate(all_raw_sources)
        print(f"[+] 去重及基础清洗后数量: {len(cleaned_sources)}")
        
        # 步骤 3：验证有效性（并发测试，限制并发数避免被封）
        print("[*] 开始进行有效性过滤...")
        valid_sources = []
        semaphore = asyncio.Semaphore(20) # 限制并发
        
        async def bounded_test(source):
            async with semaphore:
                is_valid = await test_source_validity(client, source)
                return source if is_valid else None

        test_tasks = [bounded_test(src) for src in cleaned_sources]
        test_results = await asyncio.gather(*test_tasks)
        
        valid_sources = [s for s in test_results if s is not None]
        print(f"[+] 有效书源留存数量: {len(valid_sources)}")
        
        # 步骤 4：保存供客户端导入的标准 JSON 文件
        output_filename = "exportBookSource.json"
        with open(output_filename, "w", encoding="utf-8") as f:
            json.dump(valid_sources, f, ensure_ascii=False, indent=2)
            
        print(f"[+] 已成功输出至文件: {output_filename}")

if __name__ == "__main__":
    asyncio.run(main())