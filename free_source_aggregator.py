#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os,re,csv,json,base64,hashlib,asyncio,socket,ipaddress
from pathlib import Path
from urllib.parse import urlparse,parse_qs,unquote
from datetime import datetime,timezone,timedelta

import aiohttp,yaml

# =========================================================
# 配置
# =========================================================
DRIVE_CSV="https://drive.google.com/uc?export=download&id=1aGnaL6MH5tg9DEbRUsrYmpHWThRyVzcq"

OUT=Path("output")
CACHE=OUT/"cache"
OUT.mkdir(exist_ok=True)
CACHE.mkdir(exist_ok=True)

STATE=OUT/"source_state.json"
GEO_CACHE=OUT/"geo_cache.json"

CONCURRENCY=50
GEO_CONCURRENCY=20
TIMEOUT=15
MAX_SIZE=5*1024*1024

BJ=timezone(timedelta(hours=8))

HEADERS={
    "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                 "AppleWebKit/537.36 (KHTML, like Gecko) "
                 "Chrome/153.0.0.0 Safari/537.36",
    "Accept":"*/*",
    "Accept-Language":"zh-CN,zh;q=0.9,en;q=0.8",
    "Connection":"keep-alive",
}

NODE_SCHEMES=(
    "ss://","ssr://","vmess://","vless://","trojan://",
    "hysteria://","hysteria2://","hy2://","tuic://",
    "socks://","socks5://","http://","https://","wireguard://"
)

COUNTRY_WORDS=[
    "中国","大陆","香港","澳门","台湾","日本","韩国","新加坡",
    "美国","加拿大","英国","德国","法国","荷兰","俄罗斯","印度",
    "澳大利亚","新西兰","越南","泰国","菲律宾","马来西亚",
    "印尼","土耳其","瑞士","瑞典","挪威","芬兰","丹麦","波兰",
    "乌克兰","西班牙","意大利","巴西","墨西哥","阿根廷",
    "中国大陆","Hong Kong","Taiwan","Japan","Korea","Singapore",
    "United States","USA","US","Canada","UK","Germany","France",
    "Netherlands","Russia","India","Australia","New Zealand",
    "Vietnam","Thailand","Philippines","Malaysia","Indonesia",
    "Turkey","Switzerland","Sweden","Norway","Finland","Poland",
    "Ukraine","Spain","Italy","Brazil","Mexico","Argentina"
]

FLAGS=re.compile(
    r"[\U0001F1E6-\U0001F1FF]{2}|"
    r"🇭🇰|🇲🇴|🇹🇼|🇨🇳|🇯🇵|🇰🇷|🇸🇬|🇺🇸|🇨🇦|🇬🇧|🇩🇪|🇫🇷|🇳🇱"
)

# =========================================================
# 基础工具
# =========================================================
def now_bj():
    return datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S")

def sha256(s):
    return hashlib.sha256(s if isinstance(s,bytes) else s.encode()).hexdigest()

def b64d(s):
    try:
        s=re.sub(r"\s+","",s)
        s+="="*((4-len(s)%4)%4)
        return base64.urlsafe_b64decode(s).decode("utf-8","ignore")
    except:
        return ""

def clean_name(n):
    n=str(n or "").strip() or "Unnamed"
    return re.sub(r'[\x00-\x1f<>:"/\\|?*]','_',n)[:120]

def unique_name(name,used):
    base=clean_name(name)
    n=base
    i=2
    while n in used:
        n=f"{base}-{i}"
        i+=1
    used.add(n)
    return n

def load_json(path,default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except:
        return default

def save_json(path,obj):
    path.write_text(
        json.dumps(obj,ensure_ascii=False,indent=2),
        encoding="utf-8"
    )

# =========================================================
# URL / 节点识别
# =========================================================
def extract_urls(text):
    pat=(
        r'(?i)(?:https?://|ss://|ssr://|vmess://|vless://|'
        r'trojan://|hysteria2?://|hy2://|tuic://|'
        r'socks5?://|wireguard://)[^\s<>"\'`]+'
    )
    return re.findall(pat,text)

def looks_m3u(s):
    h=s[:3000].upper()
    return "#EXTM3U" in h or "#EXTINF" in h

def looks_tvbox(s):
    try:
        x=json.loads(s)
        return isinstance(x,dict) and any(
            k in x for k in ("sites","lives","spider","parses","flags")
        )
    except:
        return False

# =========================================================
# 节点 URI
# =========================================================
def parse_vmess(u):
    try:
        x=json.loads(b64d(u[8:]))
        return {
            "type":"vmess",
            "server":x.get("add",""),
            "port":int(x.get("port",443)),
            "uuid":x.get("id",""),
            "alterId":int(x.get("aid",0) or 0),
            "cipher":x.get("scy","auto"),
            "tls":str(x.get("tls","")).lower() in ("tls","1","true"),
            "network":x.get("net","tcp"),
            "servername":x.get("sni",""),
            "name":x.get("ps","vmess")
        }
    except:
        return None

def parse_ss(u):
    try:
        p=urlparse(u)
        raw=unquote(p.username or "")
        if ":" in raw:
            method,password=raw.split(":",1)
        else:
            z=b64d(raw)
            if ":" not in z:
                return None
            method,password=z.split(":",1)

        return {
            "type":"ss",
            "server":p.hostname or "",
            "port":p.port or 443,
            "cipher":method,
            "password":password,
            "name":unquote(parse_qs(p.query).get("name",[""])[0])
                   or f"ss-{p.hostname}"
        }
    except:
        return None

def parse_uri(u):
    try:
        p=urlparse(u)
        scheme=p.scheme.lower()
        q=parse_qs(p.query)

        if scheme=="vmess":
            return parse_vmess(u)

        if scheme=="ss":
            return parse_ss(u)

        if scheme in ("vless","trojan","hysteria","hysteria2","hy2","tuic"):
            typ="hysteria2" if scheme in ("hy2","hysteria2") else scheme

            d={
                "type":typ,
                "server":p.hostname or "",
                "port":p.port or 443
            }

            if scheme=="vless":
                d["uuid"]=unquote(p.username or "")
            elif scheme=="trojan":
                d["password"]=unquote(p.username or "")
            elif scheme in ("hysteria","hysteria2","hy2"):
                d["password"]=unquote(p.username or "")
            elif scheme=="tuic":
                d["uuid"]=unquote(p.username or "")
                d["password"]=unquote(p.password or "")

            for k in (
                "security","type","flow","sni","servername","alpn",
                "fp","pbk","sid","spx","path","host","headerType",
                "allowInsecure","insecure","obfs","obfs-password"
            ):
                if k in q:
                    d[k]=q[k][0]

            name=(
                q.get("remarks",[""])[0] or
                q.get("name",[""])[0] or
                f"{typ}-{p.hostname}"
            )

            d["name"]=unquote(name)
            return d

        if scheme in ("socks","socks5","http","https"):
            d={
                "type":"socks5" if scheme.startswith("socks") else "http",
                "server":p.hostname or "",
                "port":p.port or 443,
                "name":unquote(q.get("remarks",[""])[0])
                       or unquote(q.get("name",[""])[0])
                       or f"{scheme}-{p.hostname}"
            }

            if p.username:
                d["username"]=unquote(p.username)
            if p.password:
                d["password"]=unquote(p.password)

            return d

    except:
        pass

    return None

# =========================================================
# YAML / JSON / Base64 节点
# =========================================================
def parse_yaml_nodes(text):
    try:
        x=yaml.safe_load(text)
    except:
        return []

    if not isinstance(x,dict):
        return []

    p=x.get("proxies")

    if not isinstance(p,list):
        return []

    return [
        dict(n) for n in p
        if isinstance(n,dict) and n.get("server") and n.get("type")
    ]

def parse_json_nodes(text):
    try:
        x=json.loads(text)
    except:
        return []

    if not isinstance(x,dict):
        return []

    p=x.get("proxies")

    if not isinstance(p,list):
        return []

    return [
        dict(n) for n in p
        if isinstance(n,dict) and n.get("server") and n.get("type")
    ]

def parse_nodes(text):
    # YAML
    x=parse_yaml_nodes(text)
    if x:
        return x

    # JSON
    x=parse_json_nodes(text)
    if x:
        return x

    # URI
    out=[]
    for u in extract_urls(text):
        n=parse_uri(u)
        if n:
            out.append(n)

    if out:
        return out

    # 整段 Base64
    d=b64d(text.strip())
    if d and d!=text:
        return parse_nodes(d)

    return []

# =========================================================
# M3U
# =========================================================
def parse_m3u(text):
    out=[]
    info=None

    for line in text.splitlines():
        line=line.strip()

        if line.upper().startswith("#EXTINF"):
            info=line

        elif line and not line.startswith("#") and info:
            m=re.search(r'#EXTINF:[^,]*,(.*)',info)
            name=m.group(1).strip() if m else "Unnamed"

            out.append({
                "name":name,
                "url":line,
                "extinf":info
            })

            info=None

    return out

# =========================================================
# TVBox
# =========================================================
def parse_tvbox(text):
    try:
        x=json.loads(text)
    except:
        return None

    if not isinstance(x,dict):
        return None

    if not any(k in x for k in ("sites","lives","spider","parses")):
        return None

    return x

# =========================================================
# 地理位置
# =========================================================
def name_has_geo(name):
    if not name:
        return False

    if FLAGS.search(name):
        return True

    low=name.lower()

    for x in COUNTRY_WORDS:
        if x.lower() in low:
            return True

    return False

def extract_server(node):
    s=str(node.get("server","")).strip()

    if not s:
        return ""

    try:
        ipaddress.ip_address(s)
        return s
    except:
        return s

async def resolve_host(host,cache):
    if not host:
        return ""

    try:
        ipaddress.ip_address(host)
        return host
    except:
        pass

    if host in cache:
        return cache[host]

    try:
        infos=await asyncio.to_thread(
            socket.getaddrinfo,
            host,None,socket.AF_INET
        )

        ip=infos[0][4][0] if infos else ""

        if ip:
            cache[host]=ip

        return ip
    except:
        return ""

async def geo_lookup(session,ip,sem):
    async with sem:
        try:
            url=f"http://ip-api.com/json/{ip}?lang=zh-CN&fields=status,country,countryCode,regionName,city,query"

            async with session.get(
                url,
                headers=HEADERS,
                timeout=10
            ) as r:

                if r.status!=200:
                    return None

                x=await r.json(content_type=None)

                if x.get("status")!="success":
                    return None

                return {
                    "ip":ip,
                    "country":x.get("country",""),
                    "countryCode":x.get("countryCode",""),
                    "region":x.get("regionName",""),
                    "city":x.get("city","")
                }

        except:
            return None

def geo_label(g):
    if not g:
        return ""

    country=g.get("country","")
    city=g.get("city","")

    if country and city:
        return f"{country}-{city}"

    return country or city

async def enrich_geo(nodes,geo_cache):
    host_cache=load_json(OUT/"dns_cache.json",{})

    # 先解析所有域名
    unique_hosts=list({
        extract_server(n)
        for n in nodes
        if extract_server(n)
    })

    host_to_ip={}

    for host in unique_hosts:
        host_to_ip[host]=await resolve_host(host,host_cache)

    save_json(OUT/"dns_cache.json",host_cache)

    ips=list({
        ip for ip in host_to_ip.values()
        if ip
    })

    need=[ip for ip in ips if ip not in geo_cache]

    if need:
        sem=asyncio.Semaphore(GEO_CONCURRENCY)

        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=GEO_CONCURRENCY)
        ) as s:

            tasks=[
                geo_lookup(s,ip,sem)
                for ip in need
            ]

            results=await asyncio.gather(*tasks)

        for ip,g in zip(need,results):
            if g:
                geo_cache[ip]=g

        save_json(GEO_CACHE,geo_cache)

    for n in nodes:
        name=n.get("name","")
        if name_has_geo(name):
            continue

        host=extract_server(n)
        ip=host_to_ip.get(host,"")

        if not ip:
            continue

        g=geo_cache.get(ip)
        label=geo_label(g)

        if label:
            n["name"]=f"{clean_name(name)} [{label}]"

# =========================================================
# HTTP 获取
# =========================================================
async def fetch_one(session,url,old,sem):
    async with sem:

        headers=dict(HEADERS)

        # 有服务器版本标识时直接做条件请求
        if old.get("etag"):
            headers["If-None-Match"]=old["etag"]

        if old.get("last_modified"):
            headers["If-Modified-Since"]=old["last_modified"]

        try:
            async with session.get(
                url,
                headers=headers,
                timeout=TIMEOUT,
                allow_redirects=True
            ) as r:

                etag=r.headers.get("ETag","")
                lm=r.headers.get("Last-Modified","")

                # 服务器确认没变
                if r.status==304:
                    return {
                        "status":"not_modified",
                        "http_status":304,
                        "etag":etag or old.get("etag",""),
                        "last_modified":lm or old.get("last_modified","")
                    }

                if r.status!=200:
                    return {
                        "status":"failed",
                        "http_status":r.status,
                        "reason":f"HTTP {r.status}",
                        "etag":etag,
                        "last_modified":lm
                    }

                data=await r.content.read(MAX_SIZE+1)

                if len(data)>MAX_SIZE:
                    return {
                        "status":"failed",
                        "http_status":r.status,
                        "reason":f"超过最大文件限制 {MAX_SIZE//1024//1024}MB",
                        "etag":etag,
                        "last_modified":lm
                    }

                text=data.decode("utf-8","ignore")
                digest=sha256(data)

                # 没有 304，但正文没有变化
                if old.get("sha256")==digest:
                    return {
                        "status":"unchanged",
                        "http_status":r.status,
                        "etag":etag,
                        "last_modified":lm,
                        "sha256":digest
                    }

                return {
                    "status":"changed",
                    "http_status":r.status,
                    "etag":etag,
                    "last_modified":lm,
                    "sha256":digest,
                    "text":text
                }

        except asyncio.TimeoutError:
            return {
                "status":"failed",
                "http_status":0,
                "reason":"timeout"
            }

        except aiohttp.ClientError as e:
            return {
                "status":"failed",
                "http_status":0,
                "reason":f"http:{type(e).__name__}"
            }

        except Exception as e:
            return {
                "status":"failed",
                "http_status":0,
                "reason":f"{type(e).__name__}:{e}"
            }

# =========================================================
# 单 URL 解析
# =========================================================
def parse_content(text):
    # M3U
    if looks_m3u(text):
        x=parse_m3u(text)

        if x:
            return {
                "type":"m3u",
                "items":x,
                "count":len(x),
                "reason":""
            }

        return {
            "type":"m3u",
            "items":[],
            "count":0,
            "reason":"识别到M3U格式，但没有有效节目"
        }

    # TVBox
    if looks_tvbox(text):
        x=parse_tvbox(text)

        if x:
            return {
                "type":"tvbox",
                "items":x,
                "count":1,
                "reason":""
            }

    # 节点
    x=parse_nodes(text)

    if x:
        return {
            "type":"nodes",
            "items":x,
            "count":len(x),
            "reason":""
        }

    if text.lstrip().startswith("{"):
        return {
            "type":"json_unknown",
            "items":[],
            "count":0,
            "reason":"JSON可读取，但不是识别的TVBox/节点格式"
        }

    if text.strip():
        return {
            "type":"unknown",
            "items":[],
            "count":0,
            "reason":"未识别为节点、M3U、TVBox或Base64"
        }

    return {
        "type":"empty",
        "items":[],
        "count":0,
        "reason":"正文为空"
    }

# =========================================================
# CSV
# =========================================================
def get_urls(text):
    out=[]

    for row in csv.reader(text.splitlines()):
        if not row:
            continue

        u=row[0].strip()

        if u.startswith(("http://","https://")):
            out.append(u)

    return list(dict.fromkeys(out))

# =========================================================
# 输出重建
# =========================================================
def rebuild_outputs(records,geo_cache):
    nodes=[]
    m3u=[]
    tv=[]

    for url,r in records.items():

        typ=r.get("type")

        if typ=="nodes":
            nodes.extend(r.get("items",[]))

        elif typ=="m3u":
            m3u.extend(r.get("items",[]))

        elif typ=="tvbox":
            tv.append(r.get("items",{}))

    # ---------------- 节点去重 ----------------
    nd=[]
    seen=set()

    for n in nodes:
        if not isinstance(n,dict):
            continue

        h=dict(n)
        h.pop("name",None)

        key=hashlib.sha256(
            json.dumps(h,sort_keys=True,ensure_ascii=False).encode()
        ).hexdigest()

        if key in seen:
            continue

        seen.add(key)
        nd.append(dict(n))

    # 地理位置
    asyncio.run(enrich_geo(nd,geo_cache))

    # 名称最终去重
    used=set()

    for n in nd:
        n["name"]=unique_name(
            n.get("name") or n.get("server") or "Unnamed",
            used
        )

    # ---------------- M3U 去重 ----------------
    mo=[]
    seen=set()

    for x in m3u:
        if not isinstance(x,dict):
            continue

        key=sha256(
            f"{x.get('url','')}|{x.get('name','')}"
        )

        if key in seen:
            continue

        seen.add(key)
        mo.append(dict(x))

    used=set()

    for x in mo:
        x["name"]=unique_name(x.get("name","Unnamed"),used)

    # ---------------- TVBox 合并 ----------------
    merged={}

    for obj in tv:
        if not isinstance(obj,dict):
            continue

        for k,v in obj.items():

            if isinstance(v,list):
                merged.setdefault(k,[])
                merged[k].extend(v)

            elif k not in merged:
                merged[k]=v

    # TVBox列表去重
    for k in ("sites","lives","parses"):
        if isinstance(merged.get(k),list):

            out=[]
            seen=set()

            for x in merged[k]:

                key=sha256(
                    json.dumps(
                        x,
                        sort_keys=True,
                        ensure_ascii=False
                    ).encode()
                )

                if key not in seen:
                    seen.add(key)
                    out.append(x)

            merged[k]=out

    # ---------------- 写 YAML ----------------
    with open(OUT/"nodes.yaml","w",encoding="utf-8") as f:
        yaml.safe_dump(
            {"proxies":nd},
            f,
            allow_unicode=True,
            sort_keys=False
        )

    # ---------------- 写 M3U ----------------
    with open(OUT/"playlist.m3u","w",encoding="utf-8") as f:
        f.write("#EXTM3U\n")

        for x in mo:
            f.write(
                x.get("extinf",
                      f'#EXTINF:-1,{x.get("name","Unnamed")}')+"\n"
            )
            f.write(x.get("url","")+"\n")

    # ---------------- 写 TVBox ----------------
    with open(OUT/"tvbox.json","w",encoding="utf-8") as f:
        json.dump(
            merged,
            f,
            ensure_ascii=False,
            indent=2
        )

    return len(nd),len(mo),merged

# =========================================================
# 主程序
# =========================================================
async def async_main():

    print("="*60)
    print(f"开始时间：{now_bj()} 北京时间")
    print("="*60)

    state=load_json(STATE,{})
    geo_cache=load_json(GEO_CACHE,{})

    # -----------------------------------------------------
    # 获取源 CSV
    # -----------------------------------------------------
    print("读取 Google Drive CSV...")

    sem=asyncio.Semaphore(2)

    async with aiohttp.ClientSession() as session:

        result=await fetch_one(
            session,
            DRIVE_CSV,
            {},
            sem
        )

    if not result.get("text"):
        # fetch_one 设计上这里不会保存 text 给 unchanged
        raise RuntimeError(
            "Google Drive CSV 下载失败："+result.get("reason","unknown")
        )

    csv_text=result["text"]
    urls=get_urls(csv_text)

    print(f"发现来源 URL：{len(urls)}")

    # -----------------------------------------------------
    # URL 抓取
    # -----------------------------------------------------
    old_urls=set(state.keys())

    sem=asyncio.Semaphore(CONCURRENCY)

    new_state={}
    changed_records={}

    stats=[]

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            limit=CONCURRENCY,
            ttl_dns_cache=300
        )
    ) as session:

        tasks=[
            fetch_one(
                session,
                url,
                state.get(url,{ }),
                sem
            )
            for url in urls
        ]

        results=await asyncio.gather(*tasks)

    # -----------------------------------------------------
    # 处理每个 URL
    # -----------------------------------------------------
    for url,result in zip(urls,results):

        old=state.get(url,{})
        status=result.get("status")

        cache_name=sha256(url)+".json"
        cache_path=CACHE/cache_name

        # -------- 未变化 --------
        if status in ("not_modified","unchanged"):

            rec=old.copy()

            rec["last_checked"]=now_bj()

            if result.get("etag"):
                rec["etag"]=result["etag"]

            if result.get("last_modified"):
                rec["last_modified"]=result["last_modified"]

            new_state[url]=rec

            stats.append({
                "url":url,
                "status":status,
                "http_status":result.get("http_status",200),
                "type":rec.get("type",""),
                "count":rec.get("count",0),
                "reason":"",
                "last_modified":rec.get("last_modified",""),
                "content_sha256":rec.get("sha256",""),
                "last_updated":rec.get("last_updated","")
            })

            continue

        # -------- 抓取失败 --------
        if status=="failed":

            rec=old.copy()

            rec["last_checked"]=now_bj()
            rec["last_error"]=result.get("reason","unknown")

            # 如果以前成功过，保留以前缓存
            if old:
                new_state[url]=rec
            else:
                rec={
                    "type":"failed",
                    "count":0,
                    "last_checked":now_bj(),
                    "last_error":result.get("reason","unknown")
                }
                new_state[url]=rec

            stats.append({
                "url":url,
                "status":"failed",
                "http_status":result.get("http_status",0),
                "type":rec.get("type","failed"),
                "count":rec.get("count",0),
                "reason":rec.get("last_error",""),
                "last_modified":rec.get("last_modified",""),
                "content_sha256":rec.get("sha256",""),
                "last_updated":rec.get("last_updated","")
            })

            continue

        # -------- 新内容 / URL --------
        text=result.get("text","")

        parsed=parse_content(text)

        rec={
            "type":parsed["type"],
            "count":parsed["count"],
            "reason":parsed["reason"],
            "etag":result.get("etag",""),
            "last_modified":result.get("last_modified",""),
            "sha256":result.get("sha256",""),
            "last_checked":now_bj(),
            "last_updated":now_bj(),
            "cache":cache_name
        }

        # 保存解析结果，不保存原始大文本
        save_json(
            cache_path,
            {
                "url":url,
                "type":parsed["type"],
                "count":parsed["count"],
                "items":parsed["items"],
                "reason":parsed["reason"]
            }
        )

        new_state[url]=rec

        stats.append({
            "url":url,
            "status":"new" if not old else "changed",
            "http_status":result.get("http_status",200),
            "type":parsed["type"],
            "count":parsed["count"],
            "reason":parsed["reason"],
            "last_modified":result.get("last_modified",""),
            "content_sha256":result.get("sha256",""),
            "last_updated":rec["last_updated"]
        })

        changed_records[url]=parsed

    # -----------------------------------------------------
    # 删除已经不在 CSV 中的 URL
    # -----------------------------------------------------
    removed=set(state)-set(urls)

    for url in removed:
        print(f"移除来源：{url}")

    # -----------------------------------------------------
    # 从缓存恢复全部记录
    # -----------------------------------------------------
    records={}

    for url,rec in new_state.items():

        cache_name=rec.get("cache")

        if not cache_name:
            continue

        p=CACHE/cache_name

        if not p.exists():
            continue

        try:
            x=load_json(p,{})

            records[url]={
                "type":x.get("type",""),
                "count":x.get("count",0),
                "items":x.get("items",[]),
                "reason":x.get("reason","")
            }

        except:
            pass

    # -----------------------------------------------------
    # 保存状态
    # -----------------------------------------------------
    save_json(STATE,new_state)

    # -----------------------------------------------------
    # 重建最终输出
    # -----------------------------------------------------
    print("重新整理全部缓存内容...")

    node_count,m3u_count,tv= rebuild_outputs(
        records,
        geo_cache
    )

    # -----------------------------------------------------
    # 统计 CSV
    # -----------------------------------------------------
    fields=[
        "url","status","http_status","type","count","reason",
        "last_modified","content_sha256","last_updated"
    ]

    with open(
        OUT/"statistics.csv",
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        w=csv.DictWriter(f,fieldnames=fields)
        w.writeheader()
        w.writerows(stats)

    # -----------------------------------------------------
    # 失败 / 0内容
    # -----------------------------------------------------
    with open(
        OUT/"failed.csv",
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        w=csv.DictWriter(f,fieldnames=fields)
        w.writeheader()

        for x in stats:
            if x["count"]==0:
                w.writerow(x)

    # -----------------------------------------------------
    # 汇总
    # -----------------------------------------------------
    counts={}

    for x in stats:
        counts[x["status"]]=counts.get(x["status"],0)+1

    types={}

    for x in stats:
        types[x["type"]]=types.get(x["type"],0)+1

    print()
    print("="*60)
    print("运行完成")
    print("="*60)
    print(f"北京时间：{now_bj()}")
    print(f"来源 URL ：{len(urls)}")
    print(f"新增     ：{counts.get('new',0)}")
    print(f"发生变化 ：{counts.get('changed',0)}")
    print(f"304跳过  ：{counts.get('not_modified',0)}")
    print(f"哈希未变 ：{counts.get('unchanged',0)}")
    print(f"失败     ：{counts.get('failed',0)}")
    print("-"*60)
    print(f"节点     ：{node_count}")
    print(f"M3U节目  ：{m3u_count}")
    print(f"TVBox源  ：{sum(len(tv.get(k,[])) for k in ('sites','lives','parses') if isinstance(tv.get(k),list))}")
    print("-"*60)
    print("类型统计：")

    for k,v in sorted(types.items()):
        print(f"  {k:<15} {v}")

    print("-"*60)
    print(f"输出目录：{OUT.resolve()}")
    print("="*60)

def main():
    asyncio.run(async_main())

if __name__=="__main__":
    main()
