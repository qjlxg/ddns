#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio,base64,csv,hashlib,ipaddress,json,re,socket
from datetime import datetime,timezone,timedelta
from pathlib import Path
from urllib.parse import parse_qs,unquote,urlparse

import aiohttp,yaml

# ================= 配置 =================
DRIVE_CSV="https://drive.google.com/uc?export=download&id=1aGnaL6MH5tg9DEbRUsrYmpHWThRyVzcq"
OUT=Path("output"); CACHE=OUT/"cache"
OUT.mkdir(exist_ok=True); CACHE.mkdir(exist_ok=True)

STATE=OUT/"source_state.json"
GEO_CACHE=OUT/"geo_cache.json"
DNS_CACHE=OUT/"dns_cache.json"

CONCURRENCY=50
GEO_CONCURRENCY=15
TIMEOUT=15
MAX_SIZE=5*1024*1024
BJ=timezone(timedelta(hours=8))

HEADERS={
    "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                 "AppleWebKit/537.36 (KHTML, like Gecko) "
                 "Chrome/153.0.0.0 Safari/537.36",
    "Accept":"*/*",
    "Accept-Language":"zh-CN,zh;q=0.9,en;q=0.8",
    "Connection":"keep-alive"
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
    "印度尼西亚","印尼","土耳其","瑞士","瑞典","挪威","芬兰",
    "丹麦","波兰","乌克兰","西班牙","意大利","巴西","墨西哥",
    "阿根廷","Hong Kong","Taiwan","Japan","Korea","Singapore",
    "United States","USA","Canada","UK","Germany","France",
    "Netherlands","Russia","India","Australia","Vietnam","Thailand",
    "Philippines","Malaysia","Indonesia","Turkey","Switzerland",
    "Sweden","Norway","Finland","Poland","Ukraine","Spain","Italy",
    "Brazil","Mexico","Argentina"
]

FLAGS=re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")

# ================= 基础 =================
def now():
    return datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S")

def sha(x):
    return hashlib.sha256(x if isinstance(x,bytes) else x.encode()).hexdigest()

def loadj(p,default):
    try:return json.loads(p.read_text(encoding="utf-8"))
    except:return default

def savej(p,x):
    p.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding="utf-8")

def b64d(s):
    try:
        s=re.sub(r"\s+","",s); s+="="*((4-len(s)%4)%4)
        return base64.urlsafe_b64decode(s).decode("utf-8","ignore")
    except:return ""

def clean_name(x):
    return re.sub(r'[\x00-\x1f<>:"/\\|?*]','_',str(x or "Unnamed").strip())[:120]

def unique_name(x,used):
    x=clean_name(x); n=x; i=2
    while n in used:n=f"{x}-{i}"; i+=1
    used.add(n); return n

# ================= URL识别 =================
def extract_urls(text):
    p=r'(?i)(?:https?://|ss://|ssr://|vmess://|vless://|trojan://|'
    p+=r'hysteria2?://|hy2://|tuic://|socks5?://|wireguard://)[^\s<>"\'`]+'
    return re.findall(p,text)

def is_m3u(text):
    return "#EXTM3U" in text[:3000].upper() or "#EXTINF" in text[:3000].upper()

def is_tvbox(text):
    try:
        x=json.loads(text)
        return isinstance(x,dict) and any(k in x for k in ("sites","lives","spider","parses"))
    except:return False

# ================= 节点协议 =================
def parse_vmess(u):
    try:
        x=json.loads(b64d(u[8:]))
        return {
            "type":"vmess","server":x.get("add",""),
            "port":int(x.get("port",443)),
            "uuid":x.get("id",""),
            "alterId":int(x.get("aid",0) or 0),
            "cipher":x.get("scy","auto"),
            "tls":str(x.get("tls","")).lower() in ("tls","1","true"),
            "network":x.get("net","tcp"),
            "servername":x.get("sni",""),
            "name":x.get("ps","vmess")
        }
    except:return None

def parse_ss(u):
    try:
        p=urlparse(u); raw=unquote(p.username or "")
        if ":" not in raw:
            raw=b64d(raw)
        if ":" not in raw:return None
        method,password=raw.split(":",1)
        q=parse_qs(p.query)
        return {
            "type":"ss","server":p.hostname or "",
            "port":p.port or 443,"cipher":method,
            "password":password,
            "name":unquote(q.get("remarks",[""])[0]) or f"ss-{p.hostname}"
        }
    except:return None

def parse_uri(u):
    try:
        p=urlparse(u); s=p.scheme.lower(); q=parse_qs(p.query)
        if s=="vmess":return parse_vmess(u)
        if s=="ss":return parse_ss(u)

        if s in ("vless","trojan","hysteria","hysteria2","hy2","tuic"):
            typ="hysteria2" if s in ("hy2","hysteria2") else s
            d={"type":typ,"server":p.hostname or "","port":p.port or 443}

            if s=="vless":d["uuid"]=unquote(p.username or "")
            elif s=="trojan":d["password"]=unquote(p.username or "")
            elif s in ("hysteria","hysteria2","hy2"):d["password"]=unquote(p.username or "")
            elif s=="tuic":
                d["uuid"]=unquote(p.username or "")
                d["password"]=unquote(p.password or "")

            for k in ("security","type","flow","sni","servername","alpn","fp",
                      "pbk","sid","spx","path","host","headerType",
                      "allowInsecure","insecure","obfs","obfs-password"):
                if k in q:d[k]=q[k][0]

            d["name"]=unquote(
                q.get("remarks",[""])[0] or
                q.get("name",[""])[0] or
                f"{typ}-{p.hostname}"
            )
            return d

        if s in ("socks","socks5","http","https"):
            d={"type":"socks5" if s.startswith("socks") else "http",
               "server":p.hostname or "","port":p.port or 443,
               "name":unquote(q.get("remarks",[""])[0]) or f"{s}-{p.hostname}"}
            if p.username:d["username"]=unquote(p.username)
            if p.password:d["password"]=unquote(p.password)
            return d
    except:pass
    return None

def parse_yaml_nodes(text):
    try:x=yaml.safe_load(text)
    except:return []
    if not isinstance(x,dict) or not isinstance(x.get("proxies"),list):return []
    return [dict(n) for n in x["proxies"] if isinstance(n,dict) and n.get("server") and n.get("type")]

def parse_json_nodes(text):
    try:x=json.loads(text)
    except:return []
    if not isinstance(x,dict) or not isinstance(x.get("proxies"),list):return []
    return [dict(n) for n in x["proxies"] if isinstance(n,dict) and n.get("server") and n.get("type")]

def parse_nodes(text):
    x=parse_yaml_nodes(text)
    if x:return x
    x=parse_json_nodes(text)
    if x:return x

    out=[]
    for u in extract_urls(text):
        n=parse_uri(u)
        if n:out.append(n)
    if out:return out

    d=b64d(text.strip())
    if d and d!=text:return parse_nodes(d)
    return []

# ================= M3U =================
def parse_m3u(text):
    out=[]; info=None
    for line in text.splitlines():
        line=line.strip()
        if line.upper().startswith("#EXTINF"):info=line
        elif line and not line.startswith("#") and info:
            m=re.search(r'#EXTINF:[^,]*,(.*)',info)
            out.append({
                "name":m.group(1).strip() if m else "Unnamed",
                "url":line,"extinf":info
            })
            info=None
    return out

# ================= TVBox =================
def parse_tvbox(text):
    try:x=json.loads(text)
    except:return None
    if not isinstance(x,dict):return None
    return x if any(k in x for k in ("sites","lives","spider","parses")) else None

# ================= 内容解析 =================
def parse_content(text):
    if is_m3u(text):
        x=parse_m3u(text)
        return "m3u",x,len(x),"" if x else "M3U格式存在但没有有效节目"

    if is_tvbox(text):
        x=parse_tvbox(text)
        if x:return "tvbox",x,1,""

    x=parse_nodes(text)
    if x:return "nodes",x,len(x),""

    if text.lstrip().startswith("{"):
        return "json_unknown",[],0,"JSON可读取但不是识别的TVBox/节点格式"

    if not text.strip():
        return "empty",[],0,"正文为空"

    return "unknown",[],0,"未识别为节点、M3U、TVBox或Base64"

# ================= CSV =================
def get_urls(text):
    out=[]
    for row in csv.reader(text.splitlines()):
        if row and row[0].strip().startswith(("http://","https://")):
            out.append(row[0].strip())
    return list(dict.fromkeys(out))

# ================= HTTP =================
async def fetch(session,url,old,sem):
    async with sem:
        h=dict(HEADERS)
        if old.get("etag"):h["If-None-Match"]=old["etag"]
        if old.get("last_modified"):h["If-Modified-Since"]=old["last_modified"]

        try:
            async with session.get(url,headers=h,timeout=TIMEOUT,allow_redirects=True) as r:
                etag=r.headers.get("ETag","")
                lm=r.headers.get("Last-Modified","")

                if r.status==304:
                    return {"status":"not_modified","http":304,"etag":etag or old.get("etag",""),
                            "lm":lm or old.get("last_modified","")}

                if r.status!=200:
                    return {"status":"failed","http":r.status,"reason":f"HTTP {r.status}",
                            "etag":etag,"lm":lm}

                data=await r.content.read(MAX_SIZE+1)

                if len(data)>MAX_SIZE:
                    return {"status":"failed","http":r.status,
                            "reason":f"文件超过 {MAX_SIZE//1024//1024}MB",
                            "etag":etag,"lm":lm}

                digest=sha(data)

                if old.get("sha256")==digest:
                    return {"status":"unchanged","http":r.status,
                            "etag":etag,"lm":lm,"sha256":digest}

                return {"status":"changed","http":r.status,
                        "etag":etag,"lm":lm,"sha256":digest,
                        "text":data.decode("utf-8","ignore")}

        except asyncio.TimeoutError:
            return {"status":"failed","http":0,"reason":"timeout"}
        except aiohttp.ClientError as e:
            return {"status":"failed","http":0,"reason":f"http:{type(e).__name__}"}
        except Exception as e:
            return {"status":"failed","http":0,"reason":f"{type(e).__name__}:{e}"}

# ================= 地理位置 =================
def has_geo(name):
    if FLAGS.search(name):return True
    low=str(name).lower()
    return any(x.lower() in low for x in COUNTRY_WORDS)

def get_server(n):
    return str(n.get("server","")).strip()

async def resolve_host(host,dns_cache):
    if not host:return ""
    try:
        ipaddress.ip_address(host)
        return host
    except:pass

    if host in dns_cache:return dns_cache[host]

    try:
        infos=await asyncio.to_thread(socket.getaddrinfo,host,None,socket.AF_INET)
        ip=infos[0][4][0] if infos else ""
        if ip:dns_cache[host]=ip
        return ip
    except:return ""

async def geo_one(session,ip,sem):
    async with sem:
        try:
            url=f"http://ip-api.com/json/{ip}?lang=zh-CN&fields=status,country,countryCode,regionName,city,query"
            async with session.get(url,headers=HEADERS,timeout=10) as r:
                if r.status!=200:return None
                x=await r.json(content_type=None)
                if x.get("status")!="success":return None
                return {
                    "ip":ip,"country":x.get("country",""),
                    "countryCode":x.get("countryCode",""),
                    "region":x.get("regionName",""),
                    "city":x.get("city","")
                }
        except:return None

async def enrich_geo(nodes):
    geo=loadj(GEO_CACHE,{})
    dns=loadj(DNS_CACHE,{})
    hosts=list({get_server(n) for n in nodes if get_server(n)})

    host_ip={}
    for h in hosts:
        host_ip[h]=await resolve_host(h,dns)

    savej(DNS_CACHE,dns)

    # geo里保存失败标记，避免反复查询
    need=[ip for ip in set(host_ip.values()) if ip and ip not in geo]

    if need:
        sem=asyncio.Semaphore(GEO_CONCURRENCY)
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=GEO_CONCURRENCY)
        ) as s:
            rs=await asyncio.gather(*(geo_one(s,ip,sem) for ip in need))

        for ip,g in zip(need,rs):
            geo[ip]=g if g else {"failed":True}

        savej(GEO_CACHE,geo)

    for n in nodes:
        name=str(n.get("name",""))
        if has_geo(name):continue

        ip=host_ip.get(get_server(n),"")
        g=geo.get(ip)

        if not g or g.get("failed"):continue

        country=g.get("country","")
        city=g.get("city","")
        label=f"{country}-{city}" if country and city else country or city

        if label:n["name"]=f"{clean_name(name)} [{label}]"

# ================= 输出 =================
async def rebuild(records):
    nodes=[]; m3u=[]; tv=[]

    for r in records.values():
        if r["type"]=="nodes":nodes+=r["items"]
        elif r["type"]=="m3u":m3u+=r["items"]
        elif r["type"]=="tvbox":tv.append(r["items"])

    # 节点去重
    nd=[]; seen=set()
    for n in nodes:
        if not isinstance(n,dict):continue
        z=dict(n); z.pop("name",None)
        k=sha(json.dumps(z,sort_keys=True,ensure_ascii=False))
        if k in seen:continue
        seen.add(k); nd.append(dict(n))

    await enrich_geo(nd)

    used=set()
    for n in nd:
        n["name"]=unique_name(n.get("name") or n.get("server"),used)

    # M3U去重
    mo=[]; seen=set()
    for x in m3u:
        if not isinstance(x,dict):continue
        k=sha(f'{x.get("url","")}|{x.get("name","")}')
        if k in seen:continue
        seen.add(k); mo.append(dict(x))

    used=set()
    for x in mo:x["name"]=unique_name(x.get("name"),used)

    # TVBox合并
    merged={}
    for obj in tv:
        if not isinstance(obj,dict):continue
        for k,v in obj.items():
            if isinstance(v,list):merged.setdefault(k,[]).extend(v)
            elif k not in merged:merged[k]=v

    for k in ("sites","lives","parses"):
        if isinstance(merged.get(k),list):
            a=[]; seen=set()
            for x in merged[k]:
                z=sha(json.dumps(x,sort_keys=True,ensure_ascii=False))
                if z not in seen:seen.add(z);a.append(x)
            merged[k]=a

    with open(OUT/"nodes.yaml","w",encoding="utf-8") as f:
        yaml.safe_dump({"proxies":nd},f,allow_unicode=True,sort_keys=False)

    with open(OUT/"playlist.m3u","w",encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for x in mo:
            f.write(x.get("extinf",f'#EXTINF:-1,{x.get("name","Unnamed")}')+"\n")
            f.write(x.get("url","")+"\n")

    with open(OUT/"tvbox.json","w",encoding="utf-8") as f:
        json.dump(merged,f,ensure_ascii=False,indent=2)

    return len(nd),len(mo),merged

# ================= 主程序 =================
async def main_async():
    print("="*60)
    print(f"开始时间：{now()} 北京时间")
    print("="*60)

    state=loadj(STATE,{})

    # CSV
    print("读取 Google Drive CSV...")
    sem=asyncio.Semaphore(2)

    async with aiohttp.ClientSession() as s:
        r=await fetch(s,DRIVE_CSV,{},sem)

    if not r.get("text"):
        raise RuntimeError("Google Drive CSV读取失败："+r.get("reason","unknown"))

    urls=get_urls(r["text"])
    print(f"发现来源 URL：{len(urls)}")

    # 抓取
    sem=asyncio.Semaphore(CONCURRENCY)

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            limit=CONCURRENCY,ttl_dns_cache=300
        )
    ) as s:
        results=await asyncio.gather(*[
            fetch(s,u,state.get(u,{}),sem) for u in urls
        ])

    new_state={}
    stats=[]

    for url,r in zip(urls,results):
        old=state.get(url,{})
        st=r.get("status")

        # 以前有缓存，现在没变化
        if st in ("not_modified","unchanged"):
            rec=dict(old)
            rec["last_checked"]=now()

            if r.get("etag"):rec["etag"]=r["etag"]
            if r.get("lm"):rec["last_modified"]=r["lm"]

            new_state[url]=rec

            stats.append({
                "url":url,"status":st,"http_status":r.get("http",200),
                "type":rec.get("type",""),
                "count":rec.get("count",0),
                "reason":"",
                "last_modified":rec.get("last_modified",""),
                "last_updated":rec.get("last_updated",""),
                "sha256":rec.get("sha256","")
            })
            continue

        # 失败
        if st=="failed":
            rec=dict(old)
            rec["last_checked"]=now()
            rec["last_error"]=r.get("reason","unknown")

            if not rec:
                rec={
                    "type":"failed","count":0,
                    "last_checked":now(),
                    "last_error":r.get("reason","unknown")
                }

            new_state[url]=rec

            stats.append({
                "url":url,"status":"failed","http_status":r.get("http",0),
                "type":rec.get("type","failed"),
                "count":rec.get("count",0),
                "reason":rec.get("last_error",""),
                "last_modified":rec.get("last_modified",""),
                "last_updated":rec.get("last_updated",""),
                "sha256":rec.get("sha256","")
            })
            continue

        # 新内容
        typ,items,count,reason=parse_content(r.get("text",""))
        cache_name=sha(url)+".json"

        savej(
            CACHE/cache_name,
            {
                "url":url,"type":typ,
                "items":items,"count":count,
                "reason":reason
            }
        )

        rec={
            "type":typ,
            "count":count,
            "reason":reason,
            "etag":r.get("etag",""),
            "last_modified":r.get("lm",""),
            "sha256":r.get("sha256",""),
            "last_checked":now(),
            "last_updated":now(),
            "cache":cache_name
        }

        new_state[url]=rec

        stats.append({
            "url":url,
            "status":"new" if not old else "changed",
            "http_status":r.get("http",200),
            "type":typ,
            "count":count,
            "reason":reason,
            "last_modified":r.get("lm",""),
            "last_updated":rec["last_updated"],
            "sha256":r.get("sha256","")
        })

    # 只保留当前CSV中的URL
    removed=set(state)-set(urls)
    for u in removed:
        print(f"移除来源：{u}")

    savej(STATE,new_state)

    # 从缓存恢复
    records={}

    for url,rec in new_state.items():
        c=rec.get("cache")
        if not c:continue

        p=CACHE/c
        if not p.exists():continue

        x=loadj(p,{})
        if x:
            records[url]={
                "type":x.get("type",""),
                "items":x.get("items",[]),
                "count":x.get("count",0),
                "reason":x.get("reason","")
            }

    print("重新整理全部缓存内容...")

    node_count,m3u_count,tv=await rebuild(records)

    # statistics
    fields=[
        "url","status","http_status","type","count","reason",
        "last_modified","last_updated","sha256"
    ]

    with open(OUT/"statistics.csv","w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        w.writeheader();w.writerows(stats)

    with open(OUT/"failed.csv","w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        w.writeheader()
        for x in stats:
            if x["count"]==0 or x["status"]=="failed":
                w.writerow(x)

    # 汇总
    c={}
    for x in stats:c[x["status"]]=c.get(x["status"],0)+1

    types={}
    for x in stats:types[x["type"]]=types.get(x["type"],0)+1

    print()
    print("="*60)
    print("运行完成")
    print("="*60)
    print(f"北京时间：{now()}")
    print(f"来源 URL ：{len(urls)}")
    print(f"新增     ：{c.get('new',0)}")
    print(f"内容变化 ：{c.get('changed',0)}")
    print(f"304跳过  ：{c.get('not_modified',0)}")
    print(f"哈希未变 ：{c.get('unchanged',0)}")
    print(f"失败     ：{c.get('failed',0)}")
    print("-"*60)
    print(f"节点     ：{node_count}")
    print(f"M3U节目  ：{m3u_count}")
    print(
        f"TVBox源  ："
        f"{sum(len(tv.get(k,[])) for k in ('sites','lives','parses') if isinstance(tv.get(k),list))}"
    )
    print("-"*60)
    print("来源类型：")
    for k,v in sorted(types.items()):
        print(f"  {k:<16}{v}")
    print("-"*60)
    print(f"完成时间：{now()} 北京时间")
    print("="*60)

def main():
    asyncio.run(main_async())

if __name__=="__main__":
    main()
