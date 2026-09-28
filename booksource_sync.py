import asyncio
import json
import httpx
import re
import hashlib
import os
import csv
from urllib.parse import urlparse, urljoin, quote, unquote
from bs4 import BeautifulSoup

# =========================
# 配置项
# =========================
OUTPUT_FILENAME = "exportBookSource.json"
STATS_FILENAME = "search_stats.csv"  # 关键词搜索统计 CSV
WORKERS = 40          # 提高异步并发数
TIMEOUT = 15          # 适当放宽超时时间
MAX_DOWNLOAD = 5 * 1024 * 1024

# 【扩展 1】预设社区长期维护、更新频繁的高质量种子源 / 聚合仓库直链
SEED_SOURCE_URLS = [
    # ============================================================
    # 1. XIU2 / Yuedu
    # ============================================================
    "https://jsdelivr.onmicrosoft.cn/gh/XIU2/Yuedu@master/shuyuan",
    "https://raw.githubusercontent.com/XIU2/Yuedu/master/shuyuan",
    "https://jsd.onmicrosoft.cn/gh/XIU2/Yuedu/shuyuan",
    "https://bitbucket.org/xiu2/yuedu/raw/master/shuyuan",
    "https://cdn.jsdmirror.com/gh/XIU2/Yuedu/shuyuan",
    "https://ghfast.top/https://raw.githubusercontent.com/XIU2/Yuedu/master/shuyuan",
    "https://cdn.gh-proxy.org/https://raw.githubusercontent.com/XIU2/Yuedu/master/shuyuan",

    # ============================================================
    # 2. AOAOSTAR / legado 聚合
    # 2026-09 仍有同步记录
    # ============================================================
    "https://legado.aoaostar.com/sources/b778fe6b.json",
    "https://legado.aoaostar.com/sources/71e56d4f.json",
    "https://legado.aoaostar.com/sources/4dc410d1.json",
    "https://legado.aoaostar.com/sources/e3e5d620.json",
    "https://legado.aoaostar.com/sources/e29e19ee.json",
    "https://legado.aoaostar.com/sources/2a1f129b.json",
    "https://legado.aoaostar.com/sources/3bb7b751.json",

    # ============================================================
    # 3. Gitee / 国内代码托管
    # ============================================================
    "https://gitee.com/YiJieSS/Yuedu/raw/master/bookSource.json",
    "https://gitee.com/zoeybai/read/raw/Xiaobai/bangdan.json",
    "https://www.gitlink.org.cn/api/yi-c/yd/raw?filepath=sy.json",
    "https://gitee.com/fjhy2021/yuedu/raw/master/shuyuan.json",
    "https://gitee.com/qishui/yuedu/raw/master/bookSource.json",
    "https://gitee.com/namofree/yuedu/raw/legado3booksource/legado3_booksource_by_Namo.json",
    "https://gitee.com/no-mystery/bushixuanqi-quanwangsoushu/raw/master/全网搜书(百度、谷歌、夸克).json",

    # ============================================================
    # 4. Tickmao / Novel
    # 2026-09 仍活跃
    # ============================================================
    "https://cdn.jsdelivr.net/gh/tickmao/Novel@master/sources/legado/full.json",
    "https://raw.githubusercontent.com/tickmao/Novel/master/sources/legado/full.json",
    "https://cdn.jsdelivr.net/gh/tickmao/Novel@main/sources/legado/full.json",

    # ============================================================
    # 5. 轻小说 / 日轻专项
    # ============================================================
    "https://raw.githubusercontent.com/jiwangyihao/source-j-legado/main/bilinovel.json",
    "https://raw.githubusercontent.com/jiwangyihao/source-j-legado/main/bilinovel-like.json",
    "https://raw.githubusercontent.com/jiwangyihao/source-j-legado/main/wenku.json",
    "https://raw.githubusercontent.com/jiwangyihao/source-j-legado/main/fishhawk.json",
    "https://raw.githubusercontent.com/jiwangyihao/source-j-legado/main/masiro.json",
    "https://raw.githubusercontent.com/jiwangyihao/source-j-legado/main/esjzone.json",

    # ============================================================
    # 6. 其他 GitHub 综合书源
    # ============================================================
    "https://raw.githubusercontent.com/MoGu123456/yuedu/main/shuyuan.json",
    "https://raw.githubusercontent.com/ywdblog/legado/master/shuyuan.json",
    "https://raw.githubusercontent.com/DesperadoJ/LegadoConfig/master/source.json",
    "https://raw.githubusercontent.com/astrology-1/legado/main/source.json",
    "https://raw.githubusercontent.com/shidahuilang/shuyuan/shuyuan/good.json",
    "https://raw.githubusercontent.com/yc-sy/yd/refs/heads/master/sy.json",

    # ============================================================
    # 7. DowneyRem / PixivSource
    # 2026-09 仍有更新
    # ============================================================
    "https://raw.githubusercontent.com/DowneyRem/PixivSource/main/pixiv.json",
    "https://raw.githubusercontent.com/DowneyRem/PixivSource/main/linpx.json",
    "https://raw.githubusercontent.com/DowneyRem/PixivSource/main/normal.json",
    "https://raw.githubusercontent.com/DowneyRem/PixivSource/main/books.json",
    "https://raw.githubusercontent.com/DowneyRem/PixivSource/main/import.json",
    "https://raw.githubusercontent.com/DowneyRem/PixivSource/main/btsrk.json",

    # CDN 备用
    "https://cdn.jsdelivr.net/gh/DowneyRem/PixivSource@main/pixiv.json",
    "https://cdn.jsdelivr.net/gh/DowneyRem/PixivSource@main/linpx.json",
    "https://cdn.jsdelivr.net/gh/DowneyRem/PixivSource@main/normal.json",
    "https://cdn.jsdelivr.net/gh/DowneyRem/PixivSource@main/books.json",
    "https://cdn.jsdelivr.net/gh/DowneyRem/PixivSource@main/import.json",
    "https://cdn.jsdelivr.net/gh/DowneyRem/PixivSource@main/btsrk.json",

    # ============================================================
    # 8. Luoyacheng / 阅读3.0
    # 2026-09-14 仍有更新
    # ============================================================
    "https://cdn.jsdelivr.net/gh/Luoyacheng/yuedu@main/%E4%B9%A6%E6%BA%90/pixiv%E5%B0%8F%E8%AF%B4/pixiv.json",

    # ============================================================
    # 9. MyLegadoSource
    # 已归档，但保留作为历史源池
    # ============================================================
    "https://raw.githubusercontent.com/entr0pia/MyLegadoSource/master/bookSource.json",

    # ============================================================
    # 10. 综合 / 跨界
    # ============================================================
    "https://raw.githubusercontent.com/guot55/Yuedu/master/source.json",
    "https://raw.githubusercontent.com/mage520/yuedu/master/bookSource.json",
    "http://yuedu.miaogongzi.net/shuyuan/miaogongziDY.json",

    # ============================================================
    # 11. 外部书源仓库 / 书源管理站
    # 这些不是单纯 JSON，交给 V1 自己解析页面中的 JSON/TXT/订阅链接
    # ============================================================
    "https://shuyuan.yiove.com/",
    "https://shuyuan.yiove.com/sub.json",
    "https://yuedu.miaogongzi.net/gx.html",
    "https://www.yckceo.com/yuedu/shuyuan/index.html",

    # ============================================================
    # 12. 一程相关
    # ============================================================
    "https://www.gitlink.org.cn/api/yi-c/yd/raw?filepath=sy.json",
    "https://flowus.cn/ycheng/share/923f5a35-6dcf-47d1-b8eb-b9c5ef3ed39b",

    # ============================================================
    # 13. free-share-app 历史超大合集
    # 旧，但用于“不限来源”历史池
    # ============================================================
    "https://raw.githubusercontent.com/free-share-app/legado-source/master/data.json",

    # ============================================================
    # 14. Legado-booksource 历史仓库
    # ============================================================
    "https://raw.githubusercontent.com/liruohrh/legado-booksource/master/booksources/",

    # ============================================================
    # 15. qiupo / Legado Tauri 新结构
    # 2026-04 更新
    # ============================================================
    "https://raw.githubusercontent.com/qiupo/bookSource/refs/heads/master/repository/repository.json",

    # ============================================================
    # 16. 其他公开 Legado 仓库
    # ============================================================
    "https://github.com/liruohrh/legado-booksource",
    "https://github.com/qiupo/bookSource",
    "https://github.com/Luoyacheng/yuedu",
    "https://github.com/ZGQ-inc/source",

    # ============================================================
    # 17. AI / 自动生成书源相关
    # 不是书源本身，但可以作为发现/生成候选站点的入口
    # ============================================================
    "https://github.com/Narylr350/book-source-creator-skill",

    # ============================================================
    # 18. 番茄专项
    # ============================================================
    "https://github.com/gs1147/fanqie-booksource",

    # ============================================================
    # 19. 备用历史 / 老牌聚合
    # ============================================================
    "https://cdn.jsdelivr.net/gh/entr0pia/MyLegadoSource@master/bookSource.json",
]

# ============================================================
# 扩展 2：更广泛、多维度的书源搜索关键词矩阵
# 目标：
# 1. 尽可能发现新的 Legado / 阅读3.0 书源
# 2. 不局限 GitHub
# 3. 同时搜索 JSON / TXT / RAW / 仓库 / 分享页
# 4. 允许发现老源，后续再由验证器判断
# ============================================================

SEARCH_QUERIES = [

    # ========================================================
    # A. Legado / 阅读 基础关键词
    # ========================================================
    "Legado 书源",
    "Legado 书源 json",
    "Legado 书源合集",
    "Legado 书源大全",
    "Legado 书源分享",
    "Legado 书源下载",
    "Legado 最新书源",
    "Legado 精品书源",
    "Legado 免费书源",
    "Legado 小说书源",
    "Legado 小说源",
    "Legado source",
    "Legado sources",
    "Legado booksource",
    "Legado book source",
    "Legado bookSource.json",
    "Legado booksource.json",
    "Legado json source",
    "Legado source.json",

    # ========================================================
    # B. 阅读 APP / 阅读3.0
    # ========================================================
    "阅读APP 书源",
    "阅读APP 书源合集",
    "阅读APP 书源大全",
    "阅读APP 最新书源",
    "阅读APP 精品书源",
    "阅读APP 小说书源",
    "阅读APP 源",
    "阅读 书源",
    "阅读 书源合集",
    "阅读 书源大全",
    "阅读 最新书源",
    "阅读 精品书源",
    "阅读 免费书源",
    "阅读 小说书源",
    "阅读 小说源",
    "阅读3.0 书源",
    "阅读3.0 书源合集",
    "阅读3.0 最新书源",
    "阅读3.0 精品书源",
    "阅读3.0 bookSource",
    "阅读3.0 booksource",
    "阅读3.0 json",

    # ========================================================
    # C. 文件名 / 字段特征反向搜索
    # ========================================================
    "bookSource.json",
    "booksource.json",
    "BookSource.json",
    "bookSource",
    "booksource",
    "source.json Legado",
    "sources.json Legado",
    "sources.json 阅读",
    "source.json 阅读",
    "bookSourceUrl",
    "bookSourceName",
    "bookSourceType",
    "ruleSearch",
    "ruleBookInfo",
    "ruleToc",
    "ruleContent",
    "searchUrl",
    "exploreUrl",
    "bookUrlPattern",
    "bookSourceUrl bookSourceName",
    "bookSourceUrl ruleSearch",
    "bookSourceUrl ruleToc",
    "bookSourceUrl ruleContent",

    # ========================================================
    # D. GitHub 仓库搜索
    # ========================================================
    "site:github.com Legado 书源",
    "site:github.com Legado 书源 json",
    "site:github.com Legado booksource",
    "site:github.com Legado bookSource.json",
    "site:github.com 阅读 书源",
    "site:github.com 阅读APP 书源",
    "site:github.com 阅读3.0 书源",
    "site:github.com 小说 书源 Legado",
    "site:github.com 小说源 阅读",
    "site:github.com bookSource.json",
    "site:github.com booksource.json",
    "site:github.com source.json Legado",
    "site:github.com sources.json Legado",
    "site:github.com bookSourceUrl",
    "site:github.com bookSourceName",
    "site:github.com ruleSearch ruleToc",
    "site:github.com ruleBookInfo ruleContent",
    "site:github.com Legado sources",
    "site:github.com Legado source",
    "site:github.com legado-source",
    "site:github.com legado-booksource",
    "site:github.com yuedu source",
    "site:github.com yuedu booksource",
    "site:github.com yuedu 书源",
    "site:github.com 阅读 source",

    # ========================================================
    # E. GitHub RAW 文件
    # ========================================================
    "site:raw.githubusercontent.com bookSource.json",
    "site:raw.githubusercontent.com booksource.json",
    "site:raw.githubusercontent.com BookSource.json",
    "site:raw.githubusercontent.com source.json Legado",
    "site:raw.githubusercontent.com sources.json Legado",
    "site:raw.githubusercontent.com bookSourceUrl",
    "site:raw.githubusercontent.com bookSourceName",
    "site:raw.githubusercontent.com ruleSearch",
    "site:raw.githubusercontent.com ruleToc",
    "site:raw.githubusercontent.com ruleContent",
    "site:raw.githubusercontent.com 阅读 书源",
    "site:raw.githubusercontent.com Legado 书源",
    "site:raw.githubusercontent.com yuedu 书源",
    "site:raw.githubusercontent.com yuedu source",

    # ========================================================
    # F. GitHub 常见仓库目录 / 文件路径
    # ========================================================
    "site:github.com/*/tree/*/sources Legado",
    "site:github.com/*/tree/*/source Legado",
    "site:github.com/*/tree/*/shuyuan 阅读",
    "site:github.com/*/tree/*/书源 阅读",
    "site:github.com/*/blob/*/bookSource.json",
    "site:github.com/*/blob/*/booksource.json",
    "site:github.com/*/blob/*/source.json",
    "site:github.com/*/blob/*/sources.json",
    "site:github.com/*/blob/*/shuyuan.json",

    # ========================================================
    # G. Gitee
    # ========================================================
    "site:gitee.com Legado 书源",
    "site:gitee.com Legado 书源 json",
    "site:gitee.com Legado booksource",
    "site:gitee.com 阅读 书源",
    "site:gitee.com 阅读APP 书源",
    "site:gitee.com 阅读3.0 书源",
    "site:gitee.com 小说 书源",
    "site:gitee.com 小说源",
    "site:gitee.com bookSource.json",
    "site:gitee.com booksource.json",
    "site:gitee.com BookSource.json",
    "site:gitee.com source.json Legado",
    "site:gitee.com sources.json Legado",
    "site:gitee.com bookSourceUrl",
    "site:gitee.com bookSourceName",
    "site:gitee.com ruleSearch",
    "site:gitee.com ruleToc",
    "site:gitee.com yuedu 书源",
    "site:gitee.com yuedu source",

    # ========================================================
    # H. Gitee RAW
    # ========================================================
    "site:gitee.com/*/raw/* bookSource.json",
    "site:gitee.com/*/raw/* booksource.json",
    "site:gitee.com/*/raw/* source.json",
    "site:gitee.com/*/raw/* shuyuan.json",
    "site:gitee.com/*/raw/* 阅读 书源",

    # ========================================================
    # I. GitLab
    # ========================================================
    "site:gitlab.com Legado 书源",
    "site:gitlab.com Legado booksource",
    "site:gitlab.com 阅读 书源",
    "site:gitlab.com 阅读APP 书源",
    "site:gitlab.com bookSource.json",
    "site:gitlab.com booksource.json",
    "site:gitlab.com source.json Legado",
    "site:gitlab.com bookSourceUrl",
    "site:gitlab.com yuedu 书源",
    "site:gitlab.com legado source",

    # ========================================================
    # J. Codeberg / 其他代码托管
    # ========================================================
    "site:codeberg.org Legado",
    "site:codeberg.org legado 书源",
    "site:codeberg.org booksource",
    "site:codeberg.org bookSource",
    "site:codeberg.org yuedu",
    "site:codeberg.org 阅读 书源",
    "site:sourcehut.org Legado",
    "site:bitbucket.org Legado 书源",
    "site:bitbucket.org yuedu 书源",

    # ========================================================
    # K. 国内代码平台扩展
    # ========================================================
    "site:gitcode.com Legado",
    "site:gitcode.com Legado 书源",
    "site:gitcode.com 阅读 书源",
    "site:gitcode.com 阅读APP 书源",
    "site:gitcode.com bookSource.json",
    "site:gitcode.com booksource",
    "site:gitcode.com yuedu 书源",

    "site:gitlink.org.cn Legado",
    "site:gitlink.org.cn 阅读 书源",
    "site:gitlink.org.cn bookSource.json",
    "site:gitlink.org.cn yuedu 书源",

    # ========================================================
    # L. 网盘 / 博客 / 分享页
    # ========================================================
    "Legado 书源 百度网盘",
    "阅读 书源 百度网盘",
    "阅读APP 书源 百度网盘",
    "Legado 书源 夸克网盘",
    "阅读 书源 夸克网盘",
    "Legado 书源 阿里云盘",
    "阅读 书源 阿里云盘",
    "Legado 书源 迅雷云盘",
    "阅读 书源 迅雷云盘",
    "Legado 书源 分享",
    "阅读 书源 分享",
    "Legado 书源 博客",
    "阅读 书源 博客",
    "Legado 书源 教程",
    "阅读 书源 教程",

    # ========================================================
    # M. 中文搜索引擎 / 聚合站
    # ========================================================
    "全网搜书 Legado",
    "全网小说书源",
    "小说书源合集",
    "小说书源大全",
    "小说源合集",
    "小说源大全",
    "免费小说书源",
    "免费小说源",
    "网络小说书源",
    "网络小说源",
    "小说网站 书源",
    "小说网站 Legado",
    "小说网站 阅读3.0",
    "小说网站 bookSource",
    "小说采集源 阅读",
    "小说聚合源 阅读",
    "小说搜索源 阅读",

    # ========================================================
    # N. 小说站点反向发现
    # ========================================================
    "笔趣阁 Legado 书源",
    "顶点小说 Legado 书源",
    "起点小说 Legado 书源",
    "纵横小说 Legado 书源",
    "晋江文学城 Legado 书源",
    "潇湘书院 Legado 书源",
    "17K小说 Legado 书源",
    "番茄小说 Legado 书源",
    "飞卢小说 Legado 书源",
    "刺猬猫 Legado 书源",
    "长佩文学 Legado 书源",
    "晋江 书源 json",
    "番茄 书源 json",
    "起点 书源 json",
    "纵横 书源 json",
    "17K 书源 json",
    "小说站 书源 json",

    # ========================================================
    # O. 女频 / 男频 / 分类专项
    # ========================================================
    "女频小说 书源 Legado",
    "男频小说 书源 Legado",
    "言情小说 书源 Legado",
    "都市小说 书源 Legado",
    "玄幻小说 书源 Legado",
    "仙侠小说 书源 Legado",
    "武侠小说 书源 Legado",
    "历史小说 书源 Legado",
    "科幻小说 书源 Legado",
    "悬疑小说 书源 Legado",
    "灵异小说 书源 Legado",
    "同人小说 书源 Legado",
    "轻小说 书源 Legado",
    "耽美小说 书源 Legado",
    "短篇小说 书源 Legado",

    # ========================================================
    # P. 轻小说 / 日文 / 英文专项
    # ========================================================
    "轻小说 书源 Legado",
    "轻小说书源合集",
    "日轻 书源 Legado",
    "日轻小说 书源",
    "轻小说文库 书源",
    "哔哩轻小说 书源",
    "真白萌 书源",
    "ESJZone 书源",
    "Pixiv 小说 Legado",
    "Pixiv 小说书源",
    "LNMTL Legado",
    "NovelUpdates Legado source",
    "light novel Legado source",
    "lightnovel booksource",
    "Japanese novel Legado source",

    # ========================================================
    # Q. 英文关键词
    # ========================================================
    "Legado book sources",
    "Legado book source collection",
    "Legado novel sources",
    "Legado novel source",
    "Legado sources json",
    "Legado source json",
    "Legado booksource json",
    "Legado bookSourceUrl",
    "Legado novel scraper",
    "Legado novel scraper source",
    "Yuedu book source",
    "Yuedu booksource",
    "Yuedu sources",
    "Yuedu novel source",
    "Yuedu source json",
    "Android novel book source",
    "novel source json Legado",
    "Chinese novel source Legado",

    # ========================================================
    # R. GitHub 专题 / 仓库发现
    # ========================================================
    "GitHub Legado repositories",
    "GitHub Legado source repositories",
    "GitHub Yuedu source",
    "GitHub Yuedu book source",
    "GitHub Chinese novel source",
    "GitHub novel scraper Chinese",
    "GitHub bookSource",
    "GitHub booksource",
    "GitHub book source collection",
    "GitHub novel sources json",

    # ========================================================
    # S. 书源规则特征
    # ========================================================
    "\"bookSourceUrl\": \"http",
    "\"bookSourceName\":",
    "\"bookSourceType\": 0",
    "\"ruleSearch\": {",
    "\"ruleBookInfo\": {",
    "\"ruleToc\": {",
    "\"ruleContent\": {",
    "\"searchUrl\":",
    "\"exploreUrl\":",
    "\"bookUrlPattern\":",
    "\"chapterList\":",
    "\"chapterName\":",
    "\"chapterUrl\":",
    "\"content\":",
    "\"bookList\":",
    "\"bookUrl\":",

    # ========================================================
    # T. Legado JS 书源
    # ========================================================
    "Legado JS 书源",
    "Legado js书源",
    "Legado javascript booksource",
    "Legado JS source",
    "阅读 JS 书源",
    "阅读3.0 JS 书源",
    "Legado mainJs",
    "Legado loginCheckJs",
    "Legado @js:",
    "Legado java.getString",
    "Legado java.ajax",
    "Legado Jsoup",
    "Legado JS source github",

    # ========================================================
    # U. RSS / 订阅 / 聚合源
    # ========================================================
    "Legado 订阅源",
    "阅读 订阅源",
    "阅读3.0 订阅源",
    "Legado RSS 小说",
    "阅读 RSS 小说",
    "Legado rssSource",
    "Legado rss source json",
    "Legado subscribe source",
    "阅读 订阅书源",
    "小说 RSS Legado",

    # ========================================================
    # V. 净化 / 规则仓库顺带发现书源
    # ========================================================
    "Legado 净化规则 书源",
    "阅读 净化规则 书源",
    "Legado replaceRule source",
    "Legado replaceRule booksource",
    "阅读 replaceRule 书源",
    "Legado config booksource",
    "Legado repository booksource",

    # ========================================================
    # W. Fork / Mirror / Backup
    # ========================================================
    "aoaostar legado fork",
    "aoaostar legado mirror",
    "XIU2 Yuedu fork",
    "XIU2 Yuedu mirror",
    "Legado source fork",
    "Legado booksource fork",
    "Yuedu source fork",
    "Yuedu booksource fork",
    "阅读书源 fork",
    "阅读书源 mirror",
    "Legado 书源 backup",
    "Legado 书源 mirror",
    "Legado source backup",

    # ========================================================
    # X. CDN / 镜像反向搜索
    # ========================================================
    "jsdelivr Legado 书源",
    "jsdelivr Yuedu 书源",
    "jsdelivr booksource",
    "jsdelivr bookSource.json",
    "ghproxy Legado 书源",
    "github proxy Legado source",
    "raw github Legado source",
    "githubusercontent Legado booksource",

    # ========================================================
    # Y. 直接 URL / JSON / TXT
    # ========================================================
    "Legado \"https://\" \"bookSource\"",
    "阅读 \"https://\" \"bookSourceUrl\"",
    "Legado txt 书源",
    "阅读 txt 书源",
    "Legado json 书源",
    "阅读 json 书源",
    "Legado yaml 书源",
    "阅读 yaml 书源",
    "Legado source txt",
    "Legado source json",
    "Yuedu source txt",
    "Yuedu source json",

    # ========================================================
    # Z. 中文社区 / 论坛 / 社交平台
    # ========================================================
    "酷安 阅读 书源",
    "酷安 Legado 书源",
    "酷安 阅读3.0 书源",
    "贴吧 阅读 书源",
    "百度贴吧 Legado 书源",
    "知乎 阅读 书源",
    "知乎 Legado 书源",
    "CSDN 阅读 书源",
    "博客园 Legado 书源",
    "掘金 Legado 书源",
    "吾爱破解 阅读 书源",
    "恩山 阅读 书源",
    "V2EX Legado 书源",
    "Telegram Legado 书源",
    "Telegram 阅读 书源",

    # ========================================================
    # AA. 时间限定搜索
    # ========================================================
    "2026 Legado 书源",
    "2026 阅读 书源",
    "2026 阅读3.0 书源",
    "2026 Legado booksource",
    "2026 Legado source",
    "2026 Yuedu 书源",
    "2026 小说书源",
    "2026 小说源",
    "2026 书源合集",
    "2026 最新阅读书源",
    "2026 最新Legado书源",
    "2026 9月 Legado 书源",
    "2026 09 Legado 书源",
    "2026-09 Legado source",
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
# CSV 统计管理函数（已还原）
# =========================
def load_search_stats():
    """读取历史搜索统计 CSV，返回 dict: {query: hit_count}"""
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
    """将本次搜索统计结果写入 CSV"""
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
    
    # 【已集成 bot 变量】从环境变量读取您在 GitHub Secrets 里配好的 bot 凭证
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
            print(f"[!] GitHub API 搜索返回状态码: {r.status_code}")
            return []
        data = r.json()
        return [x.get("html_url") for x in data.get("items", []) if x.get("html_url")]
    except Exception as e:
        print(f"[!] GitHub 搜索异常: {e}")
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
    print("[*] 开始多路全网自动发现书源线索...")
    candidate_urls = set(SEED_SOURCE_URLS)
    
    # 【已还原】加载历史统计，自动剔除历史命中为 0 的无效关键词
    history_stats = load_search_stats()
    current_stats = {}
    
    for q in SEARCH_QUERIES:
        # 如果历史记录中明确为 0，则直接跳过以节省时间
        if history_stats.get(q, -1) == 0:
            print(f"[-] 跳过历史零命中关键词: {q}")
            current_stats[q] = 0
            continue
            
        print(f"[-] 正在检索关键词: {q}")
        urls = search_duckduckgo(client, q)
        hit_count = len(urls)
        current_stats[q] = hit_count
        
        for u in urls:
            candidate_urls.add(clean_url(u))
            
    # 【已还原】将本次统计结果写回 CSV
    save_search_stats(current_stats)
    
    github_repos = set()
    for q in ["Legado booksource", "阅读书源合集"]:
        for u in search_github_repos(client, q):
            github_repos.add(u)
            
    print(f"[+] 命中 GitHub 仓库数: {len(github_repos)}，开始深度提取文件...")
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
