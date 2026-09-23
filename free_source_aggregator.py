#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio,base64,csv,hashlib,json,re,socket,ipaddress,io,requests
from datetime import datetime,timezone,timedelta
from pathlib import Path
from urllib.parse import urlparse,parse_qs,unquote

import aiohttp,yaml

# ================= 配置 =================
DRIVE_CSV="https://drive.google.com/uc?export=download&id=1aGnaL6MH5tg9DEbRUsrYmpHWThRyVzcq"

OUT=Path("output")
CACHE=OUT/"cache"
OUT.mkdir(exist_ok=True)
CACHE.mkdir(exist_ok=True)

STATE=OUT/"source_state.json"
GEO_CACHE=OUT/"geo_cache.json"
DNS_CACHE=OUT/"dns_cache.json"

CONCURRENCY=50
GEO_CONCURRENCY=15
TIMEOUT=15
MAX_SIZE=5*1024*1024
BJ=timezone(timedelta(hours=8))

HEADERS={
    "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
    "Accept":"*/*",
    "Accept-Language":"zh-CN,zh;q=0.9,en;q=0.8",
    "Connection":"keep-alive"
}

# ================= 基础 =================
def now():
    return datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S")

def sha(x):
    return hashlib.sha256(
        x if isinstance(x,bytes) else x.encode()
    ).hexdigest()

def loadj(p,default):
    try:
        return json.loads(
            p.read_text(encoding="utf-8")
        )
    except:
        return default

def savej(p,x):
    p.write_text(
        json.dumps(
            x,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

def b64d(s):
    try:
        s=str(s).strip()
        s=re.sub(r"\s+","",s)

        if not s:
            return ""

        s+="="*((4-len(s)%4)%4)

        return base64.urlsafe_b64decode(s).decode(
            "utf-8",
            "ignore"
        )

    except:
        return ""

def clean_name(x):
    x=str(x or "Unnamed").strip()

    return re.sub(
        r'[\x00-\x1f<>:"/\\|?*]',
        '_',
        x
    )[:120]

def unique_name(x,used):
    x=clean_name(x)

    if not x:
        x="Unnamed"

    n=x
    i=2

    while n in used:
        n=f"{x}-{i}"
        i+=1

    used.add(n)

    return n

# ================= Google Drive CSV =================
def download_drive_csv():
    try:
        r=requests.get(
            DRIVE_CSV,
            headers=HEADERS,
            timeout=60,
            allow_redirects=True
        )

        r.raise_for_status()

        data=r.content

        text=data.decode(
            "utf-8-sig",
            "replace"
        )

        print()
        print("Google Drive CSV下载结果：")
        print(f"最终URL：{r.url}")
        print(f"HTTP状态：{r.status_code}")
        print(
            f"Content-Length："
            f"{r.headers.get('Content-Length','')}"
        )
        print(
            f"实际字节：{len(data):,}"
        )
        print(
            f"实际行数："
            f"{len(text.splitlines()):,}"
        )
        print(
            f"前500字符：\n{text[:500]}"
        )

        return text

    except Exception as e:
        raise RuntimeError(
            "Google Drive CSV读取失败："
            f"{type(e).__name__}: {e}"
        )

# ================= CSV：严格按记录读取第一列 =================
def get_urls(text):
    rows=[]
    out=[]
    empty_rows=0
    header_rows=0
    nonempty_first=0
    http_urls=0
    other_values=0

    try:
        reader=csv.reader(
            io.StringIO(text),
            skipinitialspace=False
        )

        for row_no,row in enumerate(reader,1):

            if not row:
                empty_rows+=1
                continue

            rows.append(row)

            u=str(row[0]).strip()
            u=u.lstrip("\ufeff").strip()

            if len(u)>=2 and (
                (u[0]=='"' and u[-1]=='"') or
                (u[0]=="'" and u[-1]=="'")
            ):
                u=u[1:-1].strip()

            if not u:
                empty_rows+=1
                continue

            if row_no==1 and u.lower() in (
                "url",
                "source",
                "source_url",
                "link",
                "网址",
                "链接",
                "地址",
                "来源",
                "来源url",
                "来源网址"
            ):
                header_rows+=1
                continue

            nonempty_first+=1

            if re.match(
                r"^https?://",
                u,
                re.I
            ):
                http_urls+=1
            else:
                other_values+=1

            out.append(u)

    except Exception as e:
        print(
            f"❌ CSV解析异常："
            f"{type(e).__name__}: {e}"
        )

    unique=list(dict.fromkeys(out))

    print()
    print("="*70)
    print("CSV读取诊断")
    print("="*70)
    print(
        f"CSV字符数：{len(text):,}"
    )
    print(
        f"CSV物理行数："
        f"{len(text.splitlines()):,}"
    )
    print(
        f"CSV实际记录数：{len(rows):,}"
    )
    print(
        f"空记录：{empty_rows:,}"
    )
    print(
        f"表头：{header_rows:,}"
    )
    print(
        f"第一列非空：{nonempty_first:,}"
    )
    print(
        f"第一列HTTP/HTTPS：{http_urls:,}"
    )
    print(
        f"第一列其它内容：{other_values:,}"
    )
    print(
        f"去重前URL：{len(out):,}"
    )
    print(
        f"去重后URL：{len(unique):,}"
    )
    print("="*70)

    if len(unique)<100:
        print()
        print(
            "⚠️ 当前读取到的URL少于100条。"
        )
        print(
            "CSV前2000字符："
        )
        print("-"*70)
        print(text[:2000])
        print("-"*70)

    return unique

# ================= URL =================
def normalize_url(u):
    u=str(u).strip()

    if u.startswith("//"):
        return "https:"+u

    if re.match(
        r"^[a-zA-Z][a-zA-Z0-9+.-]*://",
        u
    ):
        return u

    if re.match(
        r"^[\w.-]+\.[A-Za-z]{2,}([/:?#]|$)",
        u
    ):
        return "https://"+u

    return u

# ================= 内容类型 =================
def is_m3u(text):
    t=text.lstrip(
        "\ufeff \r\n\t"
    )
    head=t[:5000].upper()

    return (
        "#EXTM3U" in head
        or
        "#EXTINF" in head
    )

def is_tvbox(text):
    try:
        x=json.loads(text)

        return (
            isinstance(x,dict)
            and any(
                k in x
                for k in (
                    "sites",
                    "lives",
                    "spider",
                    "parses"
                )
            )
        )

    except:
        return False

def extract_node_uris(text):
    pat=(
        r'(?i)(?:'
        r'ss|ssr|vmess|vless|trojan|hysteria|'
        r'hysteria2|hy2|tuic|socks|socks5|'
        r'http|https|wireguard'
        r')://[^\s<>"\'`]+'
    )

    return re.findall(
        pat,
        text
    )

# ================= 节点解析 =================
def parse_vmess(u):
    try:
        x=json.loads(
            b64d(u[8:])
        )

        return {
            "type":"vmess",
            "server":x.get(
                "add",
                ""
            ),
            "port":int(
                x.get(
                    "port",
                    443
                )
            ),
            "uuid":x.get(
                "id",
                ""
            ),
            "alterId":int(
                x.get(
                    "aid",
                    0
                ) or 0
            ),
            "cipher":x.get(
                "scy",
                "auto"
            ),
            "tls":str(
                x.get(
                    "tls",
                    ""
                )
            ).lower() in (
                "tls",
                "1",
                "true"
            ),
            "network":x.get(
                "net",
                "tcp"
            ),
            "servername":x.get(
                "sni",
                ""
            ),
            "name":x.get(
                "ps",
                "vmess"
            )
        }

    except:
        return None

def parse_ss(u):
    try:
        p=urlparse(u)

        raw=unquote(
            p.username or ""
        )

        if ":" not in raw:
            raw=b64d(raw)

        if ":" not in raw:
            return None

        method,password=raw.split(
            ":",
            1
        )

        q=parse_qs(
            p.query
        )

        return {
            "type":"ss",
            "server":p.hostname or "",
            "port":p.port or 443,
            "cipher":method,
            "password":password,
            "name":unquote(
                q.get(
                    "remarks",
                    [""]
                )[0]
            ) or f"ss-{p.hostname}"
        }

    except:
        return None

def parse_uri(u):
    try:
        p=urlparse(u)
        s=p.scheme.lower()
        q=parse_qs(
            p.query
        )

        if s=="vmess":
            return parse_vmess(u)

        if s=="ss":
            return parse_ss(u)

        if s in (
            "vless",
            "trojan",
            "hysteria",
            "hysteria2",
            "hy2",
            "tuic"
        ):

            typ=(
                "hysteria2"
                if s in (
                    "hy2",
                    "hysteria2"
                )
                else s
            )

            d={
                "type":typ,
                "server":p.hostname or "",
                "port":p.port or 443
            }

            if s=="vless":
                d["uuid"]=unquote(
                    p.username or ""
                )

            elif s=="trojan":
                d["password"]=unquote(
                    p.username or ""
                )

            elif s in (
                "hysteria",
                "hysteria2",
                "hy2"
            ):
                d["password"]=unquote(
                    p.username or ""
                )

            elif s=="tuic":
                d["uuid"]=unquote(
                    p.username or ""
                )
                d["password"]=unquote(
                    p.password or ""
                )

            for k in (
                "security",
                "type",
                "flow",
                "sni",
                "servername",
                "alpn",
                "fp",
                "pbk",
                "sid",
                "spx",
                "path",
                "host",
                "headerType",
                "allowInsecure",
                "insecure",
                "obfs",
                "obfs-password"
            ):
                if k in q:
                    d[k]=q[k][0]

            d["name"]=unquote(
                q.get(
                    "remarks",
                    [""]
                )[0]
                or q.get(
                    "name",
                    [""]
                )[0]
                or f"{typ}-{p.hostname}"
            )

            return d

        if s in (
            "socks",
            "socks5",
            "http",
            "https"
        ):

            d={
                "type":(
                    "socks5"
                    if s.startswith("socks")
                    else "http"
                ),
                "server":p.hostname or "",
                "port":p.port or 443,
                "name":unquote(
                    q.get(
                        "remarks",
                        [""]
                    )[0]
                ) or f"{s}-{p.hostname}"
            }

            if p.username:
                d["username"]=unquote(
                    p.username
                )

            if p.password:
                d["password"]=unquote(
                    p.password
                )

            return d

    except:
        pass

    return None

def parse_yaml_nodes(text):
    try:
        x=yaml.safe_load(text)

    except:
        return []

    if not isinstance(
        x,
        dict
    ):
        return []

    if not isinstance(
        x.get("proxies"),
        list
    ):
        return []

    return [
        dict(n)
        for n in x["proxies"]
        if isinstance(n,dict)
        and n.get("type")
        and n.get("server")
    ]

def parse_json_nodes(text):
    try:
        x=json.loads(text)

    except:
        return []

    if not isinstance(
        x,
        dict
    ):
        return []

    if not isinstance(
        x.get("proxies"),
        list
    ):
        return []

    return [
        dict(n)
        for n in x["proxies"]
        if isinstance(n,dict)
        and n.get("type")
        and n.get("server")
    ]

def parse_nodes(text):

    x=parse_yaml_nodes(text)

    if x:
        return x

    x=parse_json_nodes(text)

    if x:
        return x

    out=[]

    for u in extract_node_uris(text):

        u=u.rstrip(
            ".,;)]}>"
        )

        n=parse_uri(u)

        if n:
            out.append(n)

    if out:
        return out

    d=b64d(
        text.strip()
    )

    if (
        d
        and d.strip()!=text.strip()
    ):
        return parse_nodes(d)

    return []

# ================= M3U =================
def parse_m3u(text):
    out=[]
    info=None

    for line in text.lstrip(
        "\ufeff"
    ).splitlines():

        line=line.strip()

        if not line:
            continue

        if line.upper().startswith(
            "#EXTINF"
        ):
            info=line
            continue

        if (
            line
            and not line.startswith("#")
            and info
        ):

            m=re.search(
                r"#EXTINF:[^,]*,(.*)",
                info
            )

            out.append({
                "name":(
                    m.group(1).strip()
                    if m
                    else "Unnamed"
                ),
                "url":line,
                "extinf":info
            })

            info=None

    return out

# ================= TVBox =================
def parse_tvbox(text):
    try:
        x=json.loads(text)

    except:
        return None

    if not isinstance(
        x,
        dict
    ):
        return None

    if any(
        k in x
        for k in (
            "sites",
            "lives",
            "spider",
            "parses"
        )
    ):
        return x

    return None

# ================= 内容实际识别 =================
def parse_content(text):

    text=text.lstrip(
        "\ufeff"
    )

    if not text.strip():
        return (
            "empty",
            [],
            0,
            "正文为空"
        )

    if is_m3u(text):

        x=parse_m3u(text)

        if x:
            return (
                "m3u",
                x,
                len(x),
                ""
            )

        return (
            "m3u",
            [],
            0,
            "检测到M3U标记但没有有效节目"
        )

    x=parse_tvbox(text)

    if x:
        return (
            "tvbox",
            x,
            1,
            ""
        )

    x=parse_nodes(text)

    if x:
        return (
            "nodes",
            x,
            len(x),
            ""
        )

    try:
        json.loads(text)

        return (
            "json_unknown",
            [],
            0,
            "JSON存在，但不是可识别的TVBox或节点格式"
        )

    except:
        pass

    if re.search(
        r"(?i)^\s*#|```|\[[^\]]+\]\(",
        text,
        re.M
    ):
        return (
            "text",
            [],
            0,
            "文本/Markdown中未发现有效节点"
        )

    return (
        "unknown",
        [],
        0,
        "未识别为节点、M3U、TVBox或Base64"
    )

# ================= HTTP抓取 =================
async def fetch(
    session,
    url,
    old,
    sem
):
    async with sem:

        u=normalize_url(url)

        if not re.match(
            r"^https?://",
            u,
            re.I
        ):
            return {
                "status":"failed",
                "http":0,
                "reason":"URL不是可HTTP访问地址"
            }

        h=dict(HEADERS)

        if old.get("etag"):
            h["If-None-Match"]=old["etag"]

        if old.get("last_modified"):
            h["If-Modified-Since"]=old[
                "last_modified"
            ]

        try:

            async with session.get(
                u,
                headers=h,
                timeout=aiohttp.ClientTimeout(
                    total=TIMEOUT
                ),
                allow_redirects=True,
                ssl=False  # <--- 忽略自签名或不信任的 SSL 证书
            ) as r:

                etag=r.headers.get(
                    "ETag",
                    ""
                )

                lm=r.headers.get(
                    "Last-Modified",
                    ""
                )

                if r.status==304:

                    return {
                        "status":"not_modified",
                        "http":304,
                        "etag":(
                            etag
                            or old.get(
                                "etag",
                                ""
                            )
                        ),
                        "lm":(
                            lm
                            or old.get(
                                "last_modified",
                                ""
                            )
                        )
                    }

                if r.status!=200:

                    return {
                        "status":"failed",
                        "http":r.status,
                        "reason":f"HTTP {r.status}",
                        "etag":etag,
                        "lm":lm
                    }

                data=await r.content.read(
                    MAX_SIZE+1
                )

                if len(data)>MAX_SIZE:

                    return {
                        "status":"failed",
                        "http":r.status,
                        "reason":(
                            f"正文超过"
                            f"{MAX_SIZE//1024//1024}MB"
                        )
                    }

                digest=sha(data)

                if old.get(
                    "sha256"
                )==digest:

                    return {
                        "status":"unchanged",
                        "http":r.status,
                        "etag":(
                            etag
                            or old.get(
                                "etag",
                                ""
                            )
                        ),
                        "lm":(
                            lm
                            or old.get(
                                "last_modified",
                                ""
                            )
                        ),
                        "sha256":digest
                    }

                return {
                    "status":"changed",
                    "http":r.status,
                    "etag":etag,
                    "lm":lm,
                    "sha256":digest,
                    "text":data.decode(
                        "utf-8",
                        "ignore"
                    )
                }

        except asyncio.TimeoutError:

            return {
                "status":"failed",
                "http":0,
                "reason":"timeout"
            }

        except aiohttp.ClientError as e:

            return {
                "status":"failed",
                "http":0,
                "reason":(
                    f"http:{type(e).__name__}"
                )
            }

        except Exception as e:

            return {
                "status":"failed",
                "http":0,
                "reason":(
                    f"{type(e).__name__}:{e}"
                )
            }

# ================= 地理位置 =================
COUNTRY_WORDS=[
    "中国","大陆","香港","澳门","台湾","日本","韩国",
    "新加坡","美国","加拿大","英国","德国","法国",
    "荷兰","俄罗斯","印度","澳大利亚","新西兰","越南",
    "泰国","菲律宾","马来西亚","印度尼西亚","印尼",
    "土耳其","瑞士","瑞典","挪威","芬兰","丹麦","波兰",
    "乌克兰","西班牙","意大利","巴西","墨西哥",
    "Argentina","Hong Kong","Taiwan","Japan","Korea",
    "Singapore","United States","USA","Canada","UK",
    "Germany","France","Netherlands","Russia","India",
    "Australia","Vietnam","Thailand","Philippines",
    "Malaysia","Indonesia","Turkey","Switzerland","Sweden",
    "Norway","Finland","Poland","Ukraine","Spain","Italy",
    "Brazil","Mexico"
]

FLAGS=re.compile(
    r"[\U0001F1E6-\U0001F1FF]{2}"
)

def has_geo(name):

    if FLAGS.search(
        str(name)
    ):
        return True

    low=str(name).lower()

    if re.search(
        r"(?<![A-Za-z])"
        r"(?:US|UK|CA|DE|FR|NL|JP|KR|SG|HK|TW|"
        r"CN|RU|IN|AU|NZ|VN|TH|MY|ID|TR|CH|SE|NO|FI|DK|"
        r"PL|UA|ES|IT|BR|MX)"
        r"(?![A-Za-z])",
        low
    ):
        return True

    return any(
        x.lower() in low
        for x in COUNTRY_WORDS
    )

def get_server(n):
    return str(
        n.get(
            "server",
            ""
        )
    ).strip()

async def resolve_host(
    host,
    dns_cache
):

    if not host:
        return ""

    try:
        ipaddress.ip_address(host)
        return host

    except:
        pass

    if host in dns_cache:
        return dns_cache[host]

    try:

        infos=await asyncio.to_thread(
            socket.getaddrinfo,
            host,
            None,
            socket.AF_INET
        )

        ip=(
            infos[0][4][0]
            if infos
            else ""
        )

        if ip:
            dns_cache[host]=ip

        return ip

    except:
        return ""

async def geo_one(
    session,
    ip,
    sem
):

    async with sem:

        try:

            url=(
                f"http://ip-api.com/json/{ip}"
                "?lang=zh-CN&fields=status,country,"
                "countryCode,regionName,city,query"
            )

            async with session.get(
                url,
                headers=HEADERS,
                timeout=10
            ) as r:

                if r.status!=200:
                    return None

                x=await r.json(
                    content_type=None
                )

                if x.get(
                    "status"
                )!="success":
                    return None

                return {
                    "ip":ip,
                    "country":x.get(
                        "country",
                        ""
                    ),
                    "countryCode":x.get(
                        "countryCode",
                        ""
                    ),
                    "region":x.get(
                        "regionName",
                        ""
                    ),
                    "city":x.get(
                        "city",
                        ""
                    )
                }

        except:
            return None

async def enrich_geo(nodes):

    geo=loadj(
        GEO_CACHE,
        {}
    )

    dns=loadj(
        DNS_CACHE,
        {}
    )

    host_ip={}

    for n in nodes:

        if has_geo(
            n.get(
                "name",
                ""
            )
        ):
            continue

        h=get_server(n)

        if h and h not in host_ip:
            host_ip[h]=await resolve_host(
                h,
                dns
            )

    savej(
        DNS_CACHE,
        dns
    )

    need=[
        ip
        for ip in set(
            host_ip.values()
        )
        if ip
        and ip not in geo
    ]

    if need:

        sem=asyncio.Semaphore(
            GEO_CONCURRENCY
        )

        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(
                limit=GEO_CONCURRENCY
            )
        ) as s:

            rs=await asyncio.gather(
                *[
                    geo_one(
                        s,
                        ip,
                        sem
                    )
                    for ip in need
                ]
            )

        for ip,g in zip(
            need,
            rs
        ):
            geo[ip]=(
                g
                if g
                else {"failed":True}
            )

        savej(
            GEO_CACHE,
            geo
        )

    for n in nodes:

        name=str(
            n.get(
                "name",
                ""
            )
        )

        if has_geo(name):
            continue

        ip=host_ip.get(
            get_server(n),
            ""
        )

        g=geo.get(ip)

        if (
            not g
            or g.get("failed")
        ):
            continue

        country=g.get(
            "country",
            ""
        )

        city=g.get(
            "city",
            ""
        )

        label=(
            f"{country}-{city}"
            if country and city
            else (
                country
                or city
            )
        )

        if label:
            n["name"]=(
                f"{clean_name(name)} "
                f"[{label}]"
            )

# ================= 输出重建 =================
async def rebuild(records):

    nodes=[]
    m3u=[]
    tv=[]

    for r in records.values():

        typ=r.get("type")

        if typ=="nodes":
            nodes.extend(
                r.get(
                    "items",
                    []
                )
            )

        elif typ=="m3u":
            m3u.extend(
                r.get(
                    "items",
                    []
                )
            )

        elif typ=="tvbox":
            tv.append(
                r.get(
                    "items",
                    {}
                )
            )

    # ---------- 节点去重 ----------
    nd=[]
    seen=set()

    for n in nodes:

        if not isinstance(
            n,
            dict
        ):
            continue

        z=dict(n)

        z.pop(
            "name",
            None
        )

        k=sha(
            json.dumps(
                z,
                sort_keys=True,
                ensure_ascii=False
            )
        )

        if k in seen:
            continue

        seen.add(k)
        nd.append(
            dict(n)
        )

    await enrich_geo(nd)

    used=set()

    for n in nd:

        n["name"]=unique_name(
            n.get("name")
            or n.get("server"),
            used
        )

    # ---------- M3U去重 ----------
    mo=[]
    seen=set()

    for x in m3u:

        if not isinstance(
            x,
            dict
        ):
            continue

        k=sha(
            f'{x.get("name","")}|'
            f'{x.get("url","")}'
        )

        if k in seen:
            continue

        seen.add(k)
        mo.append(
            dict(x)
        )

    used=set()

    for x in mo:

        x["name"]=unique_name(
            x.get("name"),
            used
        )

    # ---------- TVBox ----------
    merged={}

    for obj in tv:

        if not isinstance(
            obj,
            dict
        ):
            continue

        for k,v in obj.items():

            if isinstance(
                v,
                list
            ):
                merged.setdefault(
                    k,
                    []
                ).extend(v)

            elif k not in merged:
                merged[k]=v

    for k in (
        "sites",
        "lives",
        "parses"
    ):

        if not isinstance(
            merged.get(k),
            list
        ):
            continue

        arr=[]
        seen=set()

        for x in merged[k]:

            z=sha(
                json.dumps(
                    x,
                    sort_keys=True,
                    ensure_ascii=False
                )
            )

            if z in seen:
                continue

            seen.add(z)
            arr.append(x)

        merged[k]=arr

    # ---------- 写节点 ----------
    with open(
        OUT/"nodes.yaml",
        "w",
        encoding="utf-8"
    ) as f:

        yaml.safe_dump(
            {
                "proxies":nd
            },
            f,
            allow_unicode=True,
            sort_keys=False
        )

    # ---------- 写M3U ----------
    with open(
        OUT/"playlist.m3u",
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            "#EXTM3U\n"
        )

        for x in mo:

            f.write(
                x.get(
                    "extinf",
                    f'#EXTINF:-1,{x.get("name","Unnamed")}'
                )
                +"\n"
            )

            f.write(
                x.get(
                    "url",
                    ""
                )
                +"\n"
            )

    # ---------- 写TVBox ----------
    with open(
        OUT/"tvbox.json",
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            merged,
            f,
            ensure_ascii=False,
            indent=2
        )

    return (
        len(nd),
        len(mo),
        merged
    )

# ================= 主程序 =================
async def main_async():

    print("="*70)
    print(
        f"开始时间：{now()} 北京时间"
    )
    print("="*70)

    state=loadj(
        STATE,
        {}
    )

    # =========================================================
    # 1. 完整下载Google Drive CSV
    # =========================================================
    print(
        "读取 Google Drive CSV..."
    )

    csv_text=download_drive_csv()

    # =========================================================
    # 2. CSV诊断
    # =========================================================
    urls=get_urls(
        csv_text
    )

    print()
    print(
        f"CSV逐行读取来源 URL：{len(urls):,}"
    )

    if len(urls)<100:

        print(
            "⚠️ 当前CSV解析得到的URL少于100条。"
        )

        print(
            "⚠️ 如果你确认原CSV有800+条，"
            "请检查上面的CSV下载结果。"
        )

    # =========================================================
    # 3. 并行检查全部来源
    # =========================================================
    sem=asyncio.Semaphore(
        CONCURRENCY
    )

    print()
    print(
        f"开始检查全部来源，并发数："
        f"{CONCURRENCY}"
    )

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            limit=CONCURRENCY,
            ttl_dns_cache=300
        )
    ) as s:

        tasks=[
            fetch(
                s,
                u,
                state.get(
                    u,
                    {}
                ),
                sem
            )
            for u in urls
        ]

        results=await asyncio.gather(
            *tasks
        )

    # =========================================================
    # 4. 更新状态
    # =========================================================
    new_state={}
    stats=[]

    for index,(url,r) in enumerate(
        zip(
            urls,
            results
        ),
        1
    ):

        old=state.get(
            url,
            {}
        )

        st=r.get(
            "status"
        )

        # ---------- 未变化 ----------
        if st in (
            "not_modified",
            "unchanged"
        ):

            rec=dict(old)

            rec["last_checked"]=now()

            if r.get("etag"):
                rec["etag"]=r[
                    "etag"
                ]

            if r.get("lm"):
                rec["last_modified"]=r[
                    "lm"
                ]

            new_state[url]=rec

            stats.append({
                "url":url,
                "status":st,
                "http_status":r.get(
                    "http",
                    200
                ),
                "type":rec.get(
                    "type",
                    ""
                ),
                "count":rec.get(
                    "count",
                    0
                ),
                "reason":"",
                "last_modified":rec.get(
                    "last_modified",
                    ""
                ),
                "last_checked":rec.get(
                    "last_checked",
                    ""
                ),
                "last_updated":rec.get(
                    "last_updated",
                    ""
                ),
                "sha256":rec.get(
                    "sha256",
                    ""
                )
            })

            continue

        # ---------- 失败 ----------
        if st=="failed":

            rec=dict(old)

            rec["last_checked"]=now()

            rec["last_error"]=r.get(
                "reason",
                "unknown"
            )

            if not rec:

                rec={
                    "type":"failed",
                    "count":0,
                    "last_checked":now(),
                    "last_error":r.get(
                        "reason",
                        "unknown"
                    )
                }

            new_state[url]=rec

            stats.append({
                "url":url,
                "status":"failed",
                "http_status":r.get(
                    "http",
                    0
                ),
                "type":rec.get(
                    "type",
                    "failed"
                ),
                "count":rec.get(
                    "count",
                    0
                ),
                "reason":rec.get(
                    "last_error",
                    ""
                ),
                "last_modified":rec.get(
                    "last_modified",
                    ""
                ),
                "last_checked":rec.get(
                    "last_checked",
                    ""
                ),
                "last_updated":rec.get(
                    "last_updated",
                    ""
                ),
                "sha256":rec.get(
                    "sha256",
                    ""
                )
            })

            continue

        # ---------- 新内容/内容变化 ----------
        typ,items,count,reason=parse_content(
            r.get(
                "text",
                ""
            )
        )

        cache_name=(
            sha(url)
            +".json"
        )

        savej(
            CACHE/cache_name,
            {
                "url":url,
                "type":typ,
                "items":items,
                "count":count,
                "reason":reason
            }
        )

        t=now()

        rec={
            "type":typ,
            "count":count,
            "reason":reason,
            "etag":r.get(
                "etag",
                ""
            ),
            "last_modified":r.get(
                "lm",
                ""
            ),
            "sha256":r.get(
                "sha256",
                ""
            ),
            "last_checked":t,
            "last_updated":t,
            "cache":cache_name
        }

        new_state[url]=rec

        stats.append({
            "url":url,
            "status":(
                "new"
                if not old
                else "changed"
            ),
            "http_status":r.get(
                "http",
                200
            ),
            "type":typ,
            "count":count,
            "reason":reason,
            "last_modified":r.get(
                "lm",
                ""
            ),
            "last_checked":t,
            "last_updated":t,
            "sha256":r.get(
                "sha256",
                ""
            )
        })

    # =========================================================
    # 5. 保存当前来源状态
    # =========================================================
    savej(
        STATE,
        new_state
    )

    # =========================================================
    # 6. 从所有缓存重新构建最终结果
    # =========================================================
    print()
    print(
        "重新整理全部缓存内容..."
    )

    records={}

    for url,rec in new_state.items():

        cache_name=rec.get(
            "cache"
        )

        if not cache_name:
            continue

        p=CACHE/cache_name

        if not p.exists():
            continue

        x=loadj(
            p,
            {}
        )

        if not x:
            continue

        records[url]={
            "type":x.get(
                "type",
                ""
            ),
            "items":x.get(
                "items",
                []
            ),
            "count":x.get(
                "count",
                0
            ),
            "reason":x.get(
                "reason",
                ""
            )
        }

    node_count,m3u_count,tv=await rebuild(
        records
    )

    # =========================================================
    # 7. statistics.csv
    # =========================================================
    fields=[
        "url",
        "status",
        "http_status",
        "type",
        "count",
        "reason",
        "last_modified",
        "last_checked",
        "last_updated",
        "sha256"
    ]

    with open(
        OUT/"statistics.csv",
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        w=csv.DictWriter(
            f,
            fieldnames=fields
        )

        w.writeheader()
        w.writerows(stats)

    # =========================================================
    # 8. failed.csv
    # =========================================================
    with open(
        OUT/"failed.csv",
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        w=csv.DictWriter(
            f,
            fieldnames=fields
        )

        w.writeheader()

        for x in stats:

            if (
                x["status"]=="failed"
                or x["count"]==0
            ):
                w.writerow(x)

    # =========================================================
    # 9. 汇总
    # =========================================================
    status_count={}

    for x in stats:

        k=x["status"]

        status_count[k]=(
            status_count.get(k,0)
            +1
        )

    type_count={}

    for x in stats:

        k=x["type"] or "unknown"

        type_count[k]=(
            type_count.get(k,0)
            +1
        )

    tv_sites=(
        len(tv.get("sites",[]))
        if isinstance(
            tv.get("sites"),
            list
        )
        else 0
    )

    tv_lives=(
        len(tv.get("lives",[]))
        if isinstance(
            tv.get("lives"),
            list
        )
        else 0
    )

    tv_parses=(
        len(tv.get("parses",[]))
        if isinstance(
            tv.get("parses"),
            list
        )
        else 0
    )

    print()
    print("="*70)
    print(
        "运行完成"
    )
    print("="*70)
    print(
        f"北京时间：{now()}"
    )

    print()
    print(
        "【来源】"
    )
    print(
        f"CSV逐行读取：{len(urls):,}"
    )
    print(
        f"新增：{status_count.get('new',0):,}"
    )
    print(
        f"变化：{status_count.get('changed',0):,}"
    )
    print(
        f"304：{status_count.get('not_modified',0):,}"
    )
    print(
        f"SHA未变化：{status_count.get('unchanged',0):,}"
    )
    print(
        f"失败：{status_count.get('failed',0):,}"
    )

    print()
    print(
        "【实际内容类型】"
    )

    for k,v in sorted(
        type_count.items()
    ):
        print(
            f"{k:<18}{v:,}"
        )

    print()
    print(
        "【最终合并】"
    )
    print(
        f"节点：{node_count:,}"
    )
    print(
        f"M3U：{m3u_count:,}"
    )
    print(
        f"TVBox sites：{tv_sites:,}"
    )
    print(
        f"TVBox lives：{tv_lives:,}"
    )
    print(
        f"TVBox parses：{tv_parses:,}"
    )

    print()
    print(
        "【输出】"
    )
    print(
        "output/nodes.yaml"
    )
    print(
        "output/playlist.m3u"
    )
    print(
        "output/tvbox.json"
    )
    print(
        "output/statistics.csv"
    )
    print(
        "output/failed.csv"
    )
    print(
        "output/source_state.json"
    )
    print(
        "output/geo_cache.json"
    )
    print(
        "output/dns_cache.json"
    )
    print(
        "output/cache/"
    )

    print()
    print(
        f"完成时间：{now()} 北京时间"
    )
    print("="*70)

def main():
    asyncio.run(
        main_async()
    )

if __name__=="__main__":
    main()
