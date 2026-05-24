#!/usr/bin/env python3
"""mc-search 核心搜索模块：五平台搜索 + 统一结果格式 + 智能路由"""

# ── 标准库导入 ─────────────────────────────────────────
import base64
import functools
import hashlib
import html as html_module  # 别名：与 html变量名区分
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent import futures as futures_module  # ThreadPoolExecutor
from enum import IntEnum
from pathlib import Path

# 注：MC百科所有子域名 (www + search + 其他) 和 minecraft.wiki 使用 curl_cffi；其余平台使用标准库

# 配置日志
logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


class SearchError(Exception):
    """搜索过程中的可区分错误基类。"""
    pass


HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


# 常量定义
MIN_HTML_LEN = 1000         # 来源: 正常页面3-8KB，错误页<500B；核心检测阈值
MIN_HTML_LEN_ITEM = 500     # 来源: 物品页无侧边栏，结构更紧凑
_MIN_PARAGRAPH_LEN = 20     # 来源: wiki解析，过滤导航/广告短文本
_MIN_PARAGRAPH_LEN_ZH = 8   # 来源: 中文信息密度高，20字符对中文过严
_MIN_SHORT_TEXT_LEN = 35    # 来源: 低于此长度视为无意义内容
_MIN_DESCRIPTIVE_LI_LEN = 50  # 来源: 列表项需有足够描述性内容
_MIN_DESCRIPTION_LINE_LEN = 10  # 来源: 描述文字单行最小长度
_MIN_SECTION_MARKER_DISTANCE = 200  # section marker 最小距离
_MAX_SECTION_PARAGRAPHS = 100  # 每 wiki 章节最多段落数
_MIN_TABLE_CELL_LEN = 2             # 来源: 表格单元格最小有意义内容长度
_MAX_TABLE_ITEMS = 50               # 来源: 单个表格最大处理行数（性能保护）
_MAX_VERSION_GROUPS = 5
_MAX_CHANGELOGS = 5
_MAX_FETCH_WORKERS = 4
_BBSMC_API = "https://api.bbsmc.net/v3"
_MODRINTH_API = "https://api.modrinth.com/v2"
_MAX_GALLERY = 0            # 默认不返回画廊（可配置）
_EMPTY_MODRINTH_RESULT = {"results": [], "total": 0, "returned": 0}  # 平台搜索失败时的空信封
_MAX_TAG_SECTION_LEN = 500
_EXTERNAL_LINK_EXCLUDE_DOMAINS = ["curseforge", "modrinth", "github", "discord", "wikipedia", "mcbbs", "jenkins", "archive"]
# MC百科外部链接分类规则：(匹配函数, key)。按顺序匹配第一个命中，key 已存在则跳过
_SIMPLE_LINK_RULES = [
    (lambda u: "modrinth.com" in u, "modrinth"),
    (lambda u: "wiki" in u.lower() and "github.com" not in u, "wiki"),
    (lambda u: "discord.gg" in u or "discord.com/invite" in u, "discord"),
    (lambda u: "jenkins" in u.lower() or "ci." in u, "jenkins"),
    (lambda u: "mcbbs" in u, "mcbbs"),
]
_MAX_TAG_TEXT_LEN = 20
_MAX_SEARCH_SEGMENT = 2000
_MAX_DESCRIPTION_SEGMENT = 70000
_MAX_SEARCH_DESC_CHARS = 500
_MAX_AUTHOR_SECTION = 50000
_MAX_INFO_TABLE_SECTION = 2000
_MAX_VERSION_SECTION_LEN = 3000  # 版本检索区域长度
_MAX_VERSIONS_FETCH = 200
# 注：WAF 签名需保守选择。"折翼喵"在 MC百科 正常页脚中出现，此处不收录
_WAF_SIGNATURES = ["AIWAFCDN", "防火墙拦截", "访问被拒绝"]
_WAF_CC_CHECK = "CC check"               # MC百科 CDN 盾检测关键词
_MIN_TOKEN_PAGE_LEN = 500                # yxd_token 页面长度阈值
_MAX_CC_PAGE_LEN = 10000                 # CC check 页面最大长度阈值
_MCMOD_RETRY_CODES = (403, 502, 503)     # MC百科可重试的 HTTP 状态码
_SEARCH_CHANGELOG_LIMIT = 3
_SKIP_MCMOD_ORG_NAMES = {"CaffeineMC"}  # 排除的非作者组织名
_DEFAULT_RESULTS_PER_PLATFORM = 10  # AI-first: Agent 场景默认，cli.py 也有一份同值常量

# Wiki 解析
_WIKI_SNIPPET_SEGMENT_LEN = 5000
_WIKI_FALLBACK_SEGMENT_LEN = 20000
_WIKI_FULL_SCAN_LEN = 60000       # 英文 wiki infobox 可达 30000+ 字符
_WIKI_FIRST_TABLE_SEGMENT_LEN = 10000
_MIN_SNIPPET_LINE_LEN = 30
_MIN_CJK_SEGMENT_LEN = 8
_MAX_CJK_FALLBACK_SEGMENTS = 3
_MAX_WIKI_SECTIONS = 20
_MAX_TABLES_PER_SECTION = 10
_MAX_MCMOD_AUTHORS = 10
_KNOWN_LOADERS = {"fabric", "forge", "neoforge", "quilt"}
_WIKI_SNIPPET_REPLACE_THRESHOLD = 50   # 直接命中 snippet 低于此长度时用 API snippet 替换
_WIKI_SNIPPET_KEEP_THRESHOLD = 60       # API snippet 低于此长度不替换
_MAX_WIKI_INTRO_PARAGRAPHS = 5

# CDN 绕过配置
_CURL_IMPERSONATE = "chrome124"               # curl_cffi 模拟的浏览器 TLS 指纹版本
_MCMOD_CDN_SHIELD = "https://www.mcmod.cn/cdn-shield/check"  # CDN 盾验证端点
_CC_CHECK_FIELDS = ["navigator", "userAgent", "windowWidth", "performance", "callPhantom"]
_CDN_BYPASS_RETRIES = 3                  # CDN 绕过外层重试次数
_CDN_RETRY_ATTEMPTS = 2                  # CC check 后重试原请求次数


# ═══════════════════════════════════════════════════════════════
# MC百科 网络层（CDN绕过 + curl封装）
# ═══════════════════════════════════════════════════════════════

def _is_mcmod_blocked(html: str) -> bool:
    """检测页面是否被 MC百科 WAF/防火墙/验证码拦截。"""
    if not html:
        return True
    # 503 + AIWAFCDN 是明确的 WAF 错误页
    if "Error Code: 503" in html and "AIWAFCDN" in html:
        return True
    # 短页面（<1000B）含可疑签名 → 被阻断
    if len(html) < MIN_HTML_LEN and any(sig in html for sig in _WAF_SIGNATURES):
        return True
    # Captcha/限流页面（通常 15KB+，不含 WAF 签名，需检测标题）
    title_m = re.search(r'<title>([^<]+)</title>', html)
    if title_m:
        title = title_m.group(1)
        if title in ("安全验证", "安全验证中", "访问间隔过短，请稍后再试"):
            return True
    return False


def _url_tail_key(url: str) -> str:
    """从 URL 提取尾部 ID 用于去重比较。
    /class/2785.html?foo=bar -> 2785
    """
    return url.split("?")[0].rstrip("/").rsplit("/", 1)[-1].lower()


def _extract_mcmod_id(url: str, prefix: str) -> str:
    """从MC百科URL提取数字ID。prefix: 'class'/'item'/'modpack'"""
    if not url:
        return ""
    m = re.search(rf'/{prefix}/(\d+)', url)
    return m.group(1) if m else ""


def _build_mcmod_fallback_result(url: str, name: str, meta: dict | None = None,
                                  content_type: str = "mod") -> dict:
    """当详情页被 WAF 拦截时，从搜索数据构建最小结果。"""
    if meta is None:
        meta = {}

    # 解析名称（格式："中文名 (English Name)" 或 "English Name"）
    name_zh = name
    name_en = ""
    m = re.match(r'^(.+?)\s*\(([^)]+)\)\s*$', name)
    if m:
        name_zh = m.group(1).strip()
        name_en = m.group(2).strip()

    # 确定类型和 source_id
    if content_type == "item":
        source_id = _extract_mcmod_id(url, "item")
        type_name = "item"
    elif content_type == "modpack":
        source_id = _extract_mcmod_id(url, "modpack")
        type_name = "modpack"
    else:
        source_id = _extract_mcmod_id(url, "class")
        type_name = "mod"

    # 分类：从 meta 中提取
    categories = []
    if meta.get("category"):
        try:
            categories = [int(meta["category"])]
        except (ValueError, TypeError):
            categories = [meta["category"]]

    # 描述：优先用 meta 中的，否则尝试从名称中提取（如无）
    description = meta.get("description", "")

    result = {
        "name": name_zh or name,
        "name_en": name_en,
        "name_zh": name_zh or name,
        "url": url,
        "source": "mcmod.cn",
        "source_id": source_id,
        "type": type_name,
        "is_vanilla": bool(re.search(r"/class/1\.html", url)),
        "cover_image": "",
        "screenshots": [],
        "supported_versions": [],
        "categories": categories,
        "tags": [],
        "author": None,
        "author_team": None,
        "community_stats": None,
        "status": None,
        "source_type": None,
        "description": description,
        "relationships": {"_error": "parse_failed"},
        "has_changelog": False,
        "external_links": None,
        "content_list": None,
    }
    return result


def _build_truncated_meta(description: str,
                          max_chars: int,
                          screenshots_meta: dict | None = None) -> dict | None:
    """构建截断元信息。无截断时返回 None。

    参数:
        description: 描述文本
        max_chars: 最大字符数
        screenshots_meta: 截图截断元信息（可选，默认 None）
    """
    truncated = dict(screenshots_meta) if screenshots_meta else {}
    if description and len(description) > max_chars:
        truncated["description"] = {"returned": max_chars, "total": len(description)}
    return truncated or None


def _clean_mcmod_html(content: str) -> str:
    """清理 MC百科 HTML：移除 script/style/img 标签，转换 br/p 为换行。"""
    content = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL)
    content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL)
    content = re.sub(r"<img[^>]*>", "", content)
    content = re.sub(r"<br\s*/?>", "\n", content)
    content = re.sub(r"<p[^>]*>", "\n", content)
    return content


# 搜索评分常量 - 使用枚举类组织
class MatchScore(IntEnum):
    """搜索结果匹配度评分权重"""
    # 精确匹配
    EXACT_MATCH_BASE = 100
    EXACT_MATCH_MAX_BONUS = 20
    EXACT_MATCH_BONUS_FACTOR = 2

    # 前缀匹配
    PREFIX_BASE = 60
    PREFIX_MAX_BONUS = 15
    PREFIX_BONUS_FACTOR = 2

    # 全词匹配（词边界检查，防止 "OreSpawn" 匹配 "spawn"）
    WHOLE_WORD_BASE = 45

    # 包含匹配
    CONTAINS_BASE = 30
    CONTAINS_MAX_POS_BONUS = 10
    CONTAINED_IN_QUERY = 20

    # 辅助规则
    MIN_LENGTH_FOR_CONTAINED = 2
    SECONDARY_PENALTY = 10
    SECONDARY_MIN = 10

    # 特殊加分
    SNIPPET_BONUS = 5
    WIKI_ITEM_BONUS = 5
    MULTI_PLATFORM_BONUS = 10

# Wiki 过滤关键词
_WIKI_SNIPPET_SKIP_KEYWORDS = [
    'disambiguation', 'may refer to',
    '本條目介紹的是', '本条目介绍的是',
    '關於其他用法', '关于其他用法',
    '消歧義', '消歧义',
    '請在加入後', '请在加入后',
    '具體要求', '具体要求',
]

# Wiki Heading 跳过 ID
_WIKI_HEADING_SKIP_IDS = {
    "mw-toc-heading", "References", "Navigation", "Videos", "Trivia",
    "p-personal-label", "p-navigation-label", "p-tb-label"
}
_WIKI_ZH_HEADING_SKIP_IDS = _WIKI_HEADING_SKIP_IDS | {
    "参考资料", "参考", "导航", "视频", "琐事",
    "p-interaction-label", "p-print-label", "p-toolbox-label"
}

# MC 百科搜索过滤器
_MCMOD_FILTER_MOD = "0"
_MCMOD_FILTER_ITEM = "3"
_MCMOD_FILTER_MODPACK_ZH = "2"
_MCMOD_FILTER_MODPACK_ALT = "20"
_MCMOD_FILTER_MODPACK_OLD = "10"    # 旧版整合包过滤（较少结果）

# MC百科描述过滤 — 公共跳过前缀（item 和 class 页面共用）
_MCMOD_COMMON_SKIP_PREFIXES = (
    "MC百科的目标是", "MC百科(mcmod.cn)的目标",
    "提供Minecraft(我的世界)MOD(模组)物品资料介绍",
)

# MC百科整合包多 filter 策略（按优先级）
_MCMOD_MODPACK_FILTERS = [
    _MCMOD_FILTER_MODPACK_ZH,   # 中文关键词效果最佳
    _MCMOD_FILTER_MOD,           # 模组搜索（补充）
    _MCMOD_FILTER_MODPACK_ALT,   # 另一种整合包过滤
    _MCMOD_FILTER_MODPACK_OLD,   # 旧版过滤（较少结果）
]

# === 项目类型常量 ===
# 文本类内容类型（MC百科 + Modrinth 都支持）
_TEXT_CONTENT_TYPES = frozenset({"mod", "item", "modpack"})
_VISUAL_CONTENT_TYPES = frozenset({"shader", "resourcepack"})
_MODRINTH_CONTENT_TYPES = _TEXT_CONTENT_TYPES | _VISUAL_CONTENT_TYPES

# === 平台优先级（数字越小越权威）===
# 默认优先级：MC百科 > Modrinth > Wiki（适用于 mod 和 item）
# 其他类型：Wiki > MC百科 > Modrinth（适用于 entity/biome/block/mechanic/dimension）
_CONTENT_PLATFORM_PRIORITY = {
    "default": {"mcmod.cn": 0, "modrinth": 1, "minecraft.wiki": 2, "minecraft.wiki/zh": 3},
    "other": {"minecraft.wiki": 0, "minecraft.wiki/zh": 1, "mcmod.cn": 2, "modrinth": 3},
}

# Wiki 解析辅助（read_wiki / read_wiki_zh 共用）

_EN_CONNECTORS_RE = re.compile(
    r"\b(and|which|that|for|with|to|is|are|was|were|has|have|been|"
    r"add|added|chang|fixed|updated|removed|introduced|included|"
    r"prevent|allow|make|made|increas|decreas|affect)\b",
    re.IGNORECASE,
)
_ZH_CONNECTORS_RE = re.compile(
    r"(和|与|或|但|是|为|有|在|被|由|可|会|能|将|已|使)",
)
# 匹配论坛元数据，如 (7)Mod讨论 (2) 或 Mod讨论 (19)
_MOD_META_PAT = re.compile(r"^(?:\(\d+\)\s*)?Mod(?:讨论|教程)\s*\(\d+\)")


def _clean_html_text(html_fragment: str, preserve_nl: bool = False) -> str:
    """去除所有 HTML 标签，转义实体，合并空白。

    preserve_nl=True 时保留换行符（仅合并水平空白），用于段落/列表等需要保留行结构的场景。
    """
    text = re.sub(r"<[^>]+>", "", html_fragment)
    text = html_module.unescape(text)
    if preserve_nl:
        text = re.sub(r"[ \t\r]+", " ", text).strip()
    else:
        text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_valid_paragraph(text: str, lang: str = "en") -> bool:
    """判断是否为有意义的正文段落。lang="zh"时检测中文连接词。"""
    min_len = _MIN_PARAGRAPH_LEN_ZH if lang == "zh" else _MIN_PARAGRAPH_LEN
    if not text or len(text) < min_len:
        return False
    if re.match(r"^[\#\.\[\/\{]", text):
        return False
    if text.startswith("{") and text.count('"') >= 4 and ":" in text:
        return False
    # 过滤维护模板/消歧义提示
    text_lower = text.lower()
    if any(kw.lower() in text_lower for kw in _WIKI_SNIPPET_SKIP_KEYWORDS):
        return False
    if len(text) > _MIN_SHORT_TEXT_LEN:
        return True
    # 短文本：需含连接词
    if lang == "zh":
        return bool(_EN_CONNECTORS_RE.search(text) or _ZH_CONNECTORS_RE.search(text))
    return bool(_EN_CONNECTORS_RE.search(text))



# 缓存系统

_cache_enabled = False
_cache_ttl = 3600  # 默认 1 小时


def _cache_dir() -> Path:
    return Path(os.path.expanduser("~/.cache/mc-search"))


def _cache_key(*parts: str) -> str:
    """生成缓存 key。"""
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _cache_get(cache_type: str, key: str) -> dict | None:
    """读取缓存，成功返回 dict，失败/过期返回 None。"""
    if not _cache_enabled:
        return None
    p = _cache_dir() / cache_type / f"{key}.json"
    if not p.exists():
        return None
    try:
        age = time.time() - p.stat().st_mtime
        if age > _cache_ttl:
            return None
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.debug(f"Cache read failed: {e}")
        return None


def _cache_set(cache_type: str, key: str, data: dict):
    """写入缓存。"""
    if not _cache_enabled:
        return
    try:
        d = _cache_dir() / cache_type
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{key}.json"
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError as e:
        logger.debug(f"Cache write failed: {e}")


def _html_cache_key(url: str) -> str:
    """为 HTML 缓存生成 key（基于完整 URL）。"""
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def _html_cache_get(url: str) -> str | None:
    """读取 HTML 缓存，命中返回 HTML 字符串，未命中返回 None。"""
    if not _cache_enabled:
        return None
    p = _cache_dir() / "html" / f"{_html_cache_key(url)}.html"
    if not p.exists():
        return None
    try:
        if time.time() - p.stat().st_mtime > _cache_ttl:
            return None
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        logger.debug(f"HTML cache read failed: {e}")
        return None


def _html_cache_set(url: str, html: str):
    """写入 HTML 缓存。"""
    if not _cache_enabled:
        return
    try:
        d = _cache_dir() / "html"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{_html_cache_key(url)}.html"
        with open(p, "w", encoding="utf-8") as f:
            f.write(html)
    except OSError as e:
        logger.debug(f"HTML cache write failed: {e}")


def _cached(make_key):
    """缓存装饰器：自动检查/写入缓存，消除重复的 cache get/set 模式。

    make_key(*args, **kwargs) 返回 (cache_type: str, cache_key: str)。
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            cache_type, key = make_key(*args, **kwargs)
            cached = _cache_get(cache_type, key)
            if cached is not None:
                return cached
            result = func(*args, **kwargs)
            _cache_set(cache_type, key, result)
            return result
        return wrapper
    return decorator


def set_cache(enabled: bool, ttl: int = 3600):
    """由 CLI 调用启用缓存。"""
    global _cache_enabled, _cache_ttl
    _cache_enabled = enabled
    _cache_ttl = ttl


# 平台开关

_platform_enabled = {"mcmod.cn": True, "modrinth": True, "minecraft.wiki": True, "minecraft.wiki/zh": True}


def set_platform_enabled(mcmod: bool = True, modrinth: bool = True, wiki: bool = True, wiki_zh: bool = True):
    """由 CLI 调用控制哪些平台启用。"""
    global _platform_enabled
    _platform_enabled = {
        "mcmod.cn": mcmod,
        "modrinth": modrinth,
        "minecraft.wiki": wiki,
        "minecraft.wiki/zh": wiki_zh,
    }


# ── MC百科 CDN 绕过状态 ──────────────────────────────
# 使用 curl_cffi 模拟浏览器 TLS 指纹，绕过 www.mcmod.cn 的 CDN 盾
_MCMOD_SESSION = None
_MCMOD_BYPASSED = False
_MCMOD_LOCK = threading.RLock()


def _mcmod_host(url: str) -> str:
    """从 MC百科 URL 提取主机名，用于 cookie 域名。"""
    m = re.match(r'https?://([^/]+)', url)
    return m.group(1) if m else "www.mcmod.cn"


def _do_cdn_shield_post(session, base_url: str, headers: dict, timeout: int) -> None:
    """POST /cdn-shield/check 完成 CDN 验证，跟随 Location 重定向。"""
    data = {k: "false" for k in _CC_CHECK_FIELDS}
    data["v1"] = ""
    ch = {**headers, "Content-Type": "application/x-www-form-urlencoded",
          "Referer": base_url + "/", "Origin": base_url}
    r = session.post(
        base_url + "/cdn-shield/check", data=data,
        impersonate=_CURL_IMPERSONATE, headers=ch, allow_redirects=False, timeout=timeout
    )
    loc = r.headers.get("Location")
    if loc:
        session.get(
            urllib.parse.urljoin(base_url, loc),
            impersonate=_CURL_IMPERSONATE, headers=headers, timeout=timeout
        )


def _handle_yxd_token(session, text: str, base_url: str, headers: dict, timeout: int) -> str:
    """处理 yxd_token 页面：提取 token、设置 cookie、跟随重定向。
    返回重定向后的 HTML，失败返回空字符串。"""
    token_m = re.search(r"yxd_token=([^;'\"\s]+)", text)
    if not token_m:
        return ""
    host = _mcmod_host(base_url)
    session.cookies.set("yxd_token", token_m.group(1), domain=host, path="/")
    href_m = re.search(r"window\.location\.href='([^']+)'", text)
    if not href_m:
        return ""
    target = urllib.parse.urljoin(base_url, href_m.group(1))
    try:
        r = session.get(target, impersonate=_CURL_IMPERSONATE, headers=headers, timeout=timeout)
        return r.text
    except Exception as e:
        logger.warning(f"yxd_token redirect failed: {e}")
        return ""


def _bypass_mcmod_cdn(timeout: int = 15) -> bool:
    """绕过 www.mcmod.cn 的 CDN 盾（一次性 cookie 交换 + yxd_token + CC check）。"""
    global _MCMOD_SESSION, _MCMOD_BYPASSED
    if _MCMOD_BYPASSED:
        return True

    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        logger.error("curl_cffi 未安装，无法访问 MC百科 (www.mcmod.cn)")
        return False

    with _MCMOD_LOCK:
        if _MCMOD_BYPASSED:
            return True
        if _MCMOD_SESSION is None:
            _MCMOD_SESSION = curl_requests.Session()

    headers = {
        "User-Agent": HTTP_HEADERS["User-Agent"],
        "Accept": HTTP_HEADERS["Accept"],
        "Accept-Language": HTTP_HEADERS["Accept-Language"],
    }

    try:
        r = _MCMOD_SESSION.get(
            "https://www.mcmod.cn/", impersonate=_CURL_IMPERSONATE, headers=headers, timeout=timeout
        )

        # 处理 yxd_token 页面（JS 设置 cookie 后重定向）—— 需要先于 CC check
        page_text = r.text
        if 'yxd_token=' in page_text and len(page_text) < _MIN_TOKEN_PAGE_LEN:
            page_text = _handle_yxd_token(_MCMOD_SESSION, page_text, "https://www.mcmod.cn", headers, timeout)
            if not page_text:
                return False

        if "CC check" not in page_text:
            _MCMOD_BYPASSED = True
            return True

        # POST 浏览器指纹数据完成验证
        _do_cdn_shield_post(_MCMOD_SESSION, "https://www.mcmod.cn", headers, timeout)
        _MCMOD_BYPASSED = True
        return True
    except Exception as e:
        logger.warning(f"MC百科 CDN 绕过失败: {e}")
        return False


def _curl_mcmod(url: str, timeout: int = 10) -> str:
    """使用 curl_cffi 请求 *.mcmod.cn，自动绕过 CDN 盾（各子域名独立绕过）。"""
    global _MCMOD_BYPASSED, _MCMOD_SESSION

    headers = {
        "User-Agent": HTTP_HEADERS["User-Agent"],
        "Accept": HTTP_HEADERS["Accept"],
        "Accept-Language": HTTP_HEADERS["Accept-Language"],
    }

    for attempt in range(_CDN_BYPASS_RETRIES):
        if not _MCMOD_BYPASSED:
            if not _bypass_mcmod_cdn(timeout=timeout):
                if attempt == 0:
                    with _MCMOD_LOCK:
                        _MCMOD_SESSION = None
                    continue
                return ""

        try:
            with _MCMOD_LOCK:
                if _MCMOD_SESSION is None:
                    from curl_cffi import requests as curl_requests
                    _MCMOD_SESSION = curl_requests.Session()
                r = _MCMOD_SESSION.get(url, impersonate=_CURL_IMPERSONATE, headers=headers, timeout=timeout)
        except Exception as e:
            logger.warning(f"MC百科请求失败 ({url}): {e}")
            with _MCMOD_LOCK:
                _MCMOD_BYPASSED = False
                _MCMOD_SESSION = None
            continue

        text = r.text

        # Captcha / 限流页面：退避后重试（立即重试必撞墙）
        if '<title>安全验证</title>' in text[:2000] or '<title>访问间隔过短' in text[:2000]:
            with _MCMOD_LOCK:
                _MCMOD_BYPASSED = False
                _MCMOD_SESSION = None
            if attempt < _CDN_BYPASS_RETRIES - 1:
                time.sleep(1.0 + attempt)  # 1s, 2s 递增退避
            continue

        # 处理 yxd_token 页面（JS 设置 cookie 后重定向）
        if 'yxd_token=' in text and len(text) < _MIN_TOKEN_PAGE_LEN:
            html = _handle_yxd_token(_MCMOD_SESSION, text, url, headers, timeout)
            if html:
                return html
            return ""

        # CC check：为该子域名单独绕过 CDN 盾（各子域名隔离）
        if _WAF_CC_CHECK in text and len(text) < _MAX_CC_PAGE_LEN:
            host = _mcmod_host(url)
            base = f"https://{host}"
            try:
                _do_cdn_shield_post(_MCMOD_SESSION, base, headers, timeout)
                for _ in range(_CDN_RETRY_ATTEMPTS):
                    r = _MCMOD_SESSION.get(url, impersonate=_CURL_IMPERSONATE, headers=headers, timeout=timeout)
                    if _WAF_CC_CHECK not in r.text or len(r.text) >= _MAX_CC_PAGE_LEN:
                        return r.text
            except Exception as e:
                logger.warning(f"CDN bypass post failed for {host}: {e}")
            with _MCMOD_LOCK:
                _MCMOD_BYPASSED = False
                _MCMOD_SESSION = None
            continue

        return text

    return ""


def _curl_wiki(url: str, timeout: int = 10) -> str:
    """使用 curl_cffi 请求 minecraft.wiki（绕过反爬虫拦截）。"""
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        logger.error("curl_cffi 未安装，无法访问 minecraft.wiki")
        return ""
    headers = {
        "User-Agent": HTTP_HEADERS["User-Agent"],
        "Accept": HTTP_HEADERS["Accept"],
        "Accept-Language": HTTP_HEADERS["Accept-Language"],
    }
    try:
        r = curl_requests.get(url, impersonate=_CURL_IMPERSONATE, headers=headers, timeout=timeout)
        return r.text
    except Exception as e:
        logger.warning(f"Wiki 请求失败 ({url}): {e}")
        return ""


def curl(url: str, timeout: int = 10) -> str:
    """发起HTTP请求，返回HTML内容（失败返回空字符串）。

    - *.mcmod.cn：使用 curl_cffi + CDN 绕过
    - minecraft.wiki / zh.minecraft.wiki：使用 curl_cffi 绕过反爬
    - 其他 URL：标准 urllib.request
    """
    # MC百科详情页 HTML 缓存（最贵请求，绕过 CDN 前先查缓存）
    if _cache_enabled and "://www.mcmod.cn/class/" in url:
        cached = _html_cache_get(url)
        if cached is not None:
            return cached

    # MC百科所有子域名需要 CDN 绕过（www + search）
    if "://www.mcmod.cn/" in url or "://search.mcmod.cn/" in url:
        html = _curl_mcmod(url, timeout)
        # 成功获取的 MC百科详情页写入 HTML 缓存
        if html and _cache_enabled and "://www.mcmod.cn/class/" in url:
            _html_cache_set(url, html)
        return html
    # minecraft.wiki 需要 curl_cffi 绕过反爬
    if "://minecraft.wiki/" in url or "://zh.minecraft.wiki/" in url:
        return _curl_wiki(url, timeout)

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": HTTP_HEADERS["User-Agent"],
                "Accept": HTTP_HEADERS["Accept"],
                "Accept-Language": HTTP_HEADERS["Accept-Language"],
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if "mcmod.cn" in url and e.code in _MCMOD_RETRY_CODES:
            logger.error(f"MC百科 (mcmod.cn) 服务暂时不可用 (HTTP {e.code})，可能正在维护或遭受攻击。建议稍后重试或使用 --platform modrinth 仅搜索 Modrinth。")
        else:
            logger.warning(f"HTTP {e.code} for {url}: {e.reason}")
        return ""
    except urllib.error.URLError as e:
        if "mcmod.cn" in url:
            logger.error(f"无法连接到 MC百科 (mcmod.cn)：{e.reason}。建议检查网络或使用 --platform modrinth。")
        else:
            logger.warning(f"URL error for {url}: {e.reason}")
        return ""
    except TimeoutError as e:
        if "mcmod.cn" in url:
            logger.warning(f"MC百科请求超时。建议稍后重试或使用 --platform modrinth 仅搜索 Modrinth。")
        else:
            logger.warning(f"Request timeout for {url}")
        return ""


def _fetch_json(url: str, default=None) -> dict | list | None:
    """统一处理 JSON 获取，失败返回默认值。"""
    try:
        raw = curl(url)
        if not raw:
            return default if default is not None else {}
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse failed for {url}: {e}")
        return default if default is not None else {}


# ═══════════════════════════════════════════════════════════════
# MC百科 页面解析（item/mod/modpack/作者）
# ═══════════════════════════════════════════════════════════════

def _parse_mcmod_item_result(html: str, url: str, name: str) -> dict:
    """从 MC百科 item 页面解析。物品页面结构与 class 页面完全不同。"""
    if _is_mcmod_blocked(html):
        return _build_mcmod_fallback_result(url, name, None, "item")

    m = re.search(r"<title>([^<]+)</title>", html)
    raw_title = m.group(1).strip() if m else name

    name_zh, name_en = _parse_mcmod_title(raw_title)

    # 封面图 + 截图（复用通用提取函数）
    cover_image, screenshots = _extract_mcmod_cover(html)

    # 资料分类 / 最大耐久 / 最大堆叠（从 item-info-table 提取）
    category = ""
    max_durability = None
    max_stack = None
    mod_name = ""
    mod_url = ""

    info_idx = html.find('item-info-table"')
    if info_idx >= 0:
        info_section = html[info_idx:info_idx + _MAX_INFO_TABLE_SECTION]
        # 资料分类
        cat_m = re.search(r'资料分类：</td><td[^>]*>(?:<a[^>]*?>)?([^<]+)', info_section)
        if cat_m:
            category = cat_m.group(1).strip()
        # 最大耐久
        dur_m = re.search(r'最大耐久：</td><td[^>]*>([\d,]+)', info_section)
        if dur_m:
            max_durability = int(dur_m.group(1).replace(",", ""))
        # 最大堆叠
        stack_m = re.search(r'最大堆叠：</td><td[^>]*>([\d,]+)', info_section)
        if stack_m:
            max_stack = int(stack_m.group(1).replace(",", ""))
        # 所属模组
        mod_links = re.findall(r'href="(/class/\d+\.html)"[^>]*>([^<]+)<', html)
        if mod_links:
            mod_url = "https://www.mcmod.cn" + mod_links[0][0]
            mod_name = mod_links[0][1].strip()

    # 物品介绍（item-content common-text font14 div）
    # 使用 regex 匹配完整 <div> 标签，然后用 depth 计数找闭合标签
    description = ""
    tag_m = re.search(r'<div[^>]*class="[^"]*item-content[^"]*font14[^"]*"[^>]*>', html)
    if tag_m:
        tag_end = tag_m.end()  # position of '>' in opening tag
        search = html[tag_end:tag_end + _MAX_SEARCH_SEGMENT]
        depth = 1  # already inside the div
        for i in range(len(search)):
            if search[i:i+4] == '<div':
                depth += 1
            elif search[i:i+6] == '</div>':
                depth -= 1
                if depth == 0:
                    segment = search[:i]
                    segment = re.sub(r"<br\s*/?>", "\n", segment)
                    segment = re.sub(r"</p>", "\n", segment)
                    text = _clean_html_text(segment, preserve_nl=True)
                    skip_prefixes = list(_MCMOD_COMMON_SKIP_PREFIXES) + [
                        "暂无简介，欢迎协助完善",
                        "MCmod does not have a description with this game data yet",
                        "This page still working because",
                        "player can edit description, instead of navigation",
                        "for navigation",
                        "<!--", "-->",
                    ]
                    lines = []
                    for line in text.split("\n"):
                        line = line.strip()
                        if len(line) < _MIN_DESCRIPTION_LINE_LEN:
                            continue
                        if any(line.startswith(p) for p in skip_prefixes):
                            continue
                        if any(p in line for p in ("MCmod does not have a description", "for navigation", "player can edit description")):
                            continue
                        lines.append(line)
                    description = "\n".join(lines)  # 不限制段落数
                    break

    # 截图截断信息
    result = {
        "name": name_zh or raw_title or name,
        "name_en": name_en,
        "name_zh": name_zh or raw_title or name,
        "url": url,
        "source": "mcmod.cn",
        "source_id": re.search(r"/item/(\d+)", url).group(1) if url else "",
        "type": "item",
        "cover_image": cover_image,
        "screenshots": [],
        "category": category,
        "max_durability": max_durability,
        "max_stack": max_stack,
        "source_mod_name": mod_name,
        "source_mod_url": mod_url,
        "description": description[:_MAX_SEARCH_DESC_CHARS] if description else "",
        "has_recipe": "recipe" in html.lower() or "合成" in html,
    }

    # 截断元信息
    truncated = _build_truncated_meta(description, _MAX_SEARCH_DESC_CHARS)
    if truncated:
        result["_truncated"] = truncated

    return result

# 模组解析（MC百科 /class/ 页面）



def _extract_mcmod_cover(html: str) -> tuple[str, list[str]]:
    """提取封面图。返回 (cover_image, [])。"""
    cover_m = re.search(r'class="class-cover-image"[^>]*>.*?<img[^>]+src="([^"]+)"', html, re.DOTALL)
    cover_image = cover_m.group(1) if cover_m else ""
    return cover_image, []


def _extract_mcmod_modpack_metadata(html: str) -> tuple[str, str, str, str, list[str]]:
    """提取整合包元数据。返回 (name_zh, name_en, author, status, categories)。"""
    # 标题解析
    m = re.search(r"<title>([^<]+)</title>", html)
    raw_title = m.group(1).strip() if m else ""

    name_zh, name_en = _parse_mcmod_title(raw_title)

    # 使用通用函数提取作者和状态
    author = _extract_mcmod_field(html, "作者")
    status = _extract_mcmod_field(html, "状态")

    # 分类
    categories = re.findall(r'href="/modpack/category/[^"]*"[^>]*>([^<]+)</a>', html)

    return name_zh, name_en, author, status, categories


def _extract_mcmod_modpack_description(html: str) -> str:
    """提取整合包描述文本。"""
    intro_idx = html.find("整合包介绍")
    if intro_idx < 0:
        return ""

    segment = html[intro_idx:intro_idx + _MAX_DESCRIPTION_SEGMENT]
    section_markers = ["整合包下载", "版本列表", "包含模组", "相关链接"]
    end = len(segment)
    for marker in section_markers:
        idx = segment.find(marker)
        if idx > _MIN_SECTION_MARKER_DISTANCE:
            end = min(end, idx)

    content = _clean_mcmod_html(segment[:end])
    text = _clean_html_text(content, preserve_nl=True)

    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if len(line) < _MIN_DESCRIPTION_LINE_LEN:
            continue
        lines.append(line)

    return "\n".join(lines)


def _extract_mcmod_modpack_versions(html: str) -> list[str]:
    """提取整合包支持的游戏版本列表。"""
    supported_versions = []
    version_section_idx = html.find("版本列表")
    if version_section_idx >= 0:
        version_section = html[version_section_idx:version_section_idx + _MAX_VERSION_SECTION_LEN]
        versions = re.findall(r'(?:Minecraft\s+)?(\d+\.\d+(?:\.\d+)?)', version_section)
        supported_versions = list(set(versions))

    return supported_versions


def _parse_mcmod_modpack_result(html: str, url: str, name: str) -> dict:
    """从 MC百科整合包页面解析。整合包页面结构与 class 页面类似但有差异。"""
    if _is_mcmod_blocked(html):
        return _build_mcmod_fallback_result(url, name, None, "modpack")

    # 提取元数据
    name_zh, name_en, author, status, categories = _extract_mcmod_modpack_metadata(html)

    # 封面图和截图
    cover_image, screenshots = _extract_mcmod_cover(html)

    # 描述
    description = _extract_mcmod_modpack_description(html)

    # 统计信息（仅版本列表）
    supported_versions = _extract_mcmod_modpack_versions(html)

    # 整合包类型判定（是否为 MC百科官方收录的整合包）
    is_official_modpack = bool(re.search(r'/modpack/\d+\.html', url))

    result = {
        "name": name_zh or name,
        "name_en": name_en,
        "name_zh": name_zh or name,
        "url": url,
        "source": "mcmod.cn",
        "source_id": re.search(r"/modpack/(\d+)", url).group(1) if url else "",
        "type": "modpack",
        "is_official": is_official_modpack,
        "cover_image": cover_image,
        "screenshots": [],
        "supported_versions": supported_versions,
        "categories": categories,
        "author": author,
        "status": status,
        "description": description[:_MAX_SEARCH_DESC_CHARS] if description else "",
        "snippet": description[:_MAX_SEARCH_DESC_CHARS] if description else "",  # 与 search 接口保持一致
        "downloads": 0,  # MC百科整合包通常不提供下载量统计
    }

    # 截断元信息
    truncated = _build_truncated_meta(description, _MAX_SEARCH_DESC_CHARS)
    if truncated:
        result["_truncated"] = truncated

    return result


def _extract_mcmod_versions(html: str) -> list[str]:
    """从版本检索区提取支持的游戏版本列表。"""
    ver_idx = html.find("版本检索")
    ver_section = html[ver_idx:ver_idx + _MAX_VERSION_SECTION_LEN] if ver_idx >= 0 else ""
    return list(set(re.findall(r'mcver=(\d+\.\d+(?:\.\d+)?)', ver_section)))


def _is_valid_tag_text(text: str) -> bool:
    """判断文本是否为有效标签（过滤过长文本和冒号结尾的标签名）。"""
    t = text.strip()
    return bool(t and len(t) < _MAX_TAG_TEXT_LEN and not t.endswith(':'))


def _extract_mcmod_categories(html: str) -> tuple[list[str], list[str]]:
    """提取分类（面包屑）和模组标签。返回 (categories, tags)。"""
    categories = re.findall(r'href="/class/category/\d+-1\.html"[^>]*>([^<]+)</a>', html)
    tags_idx = html.find("模组标签:")
    tags = []
    if tags_idx >= 0:
        tag_section = html[tags_idx:tags_idx + _MAX_TAG_SECTION_LEN]
        # 查找标签容器内的链接文本
        tags = re.findall(r'<a[^>]*class="[^"]*tag[^"]*"[^>]*>([^<]+)</a>', tag_section, re.IGNORECASE)
        if not tags:
            # 备用：提取尖括号内的文本，过滤掉非标签内容
            tags = [t.strip() for t in re.findall(r'>([^<]+)<', tag_section) if _is_valid_tag_text(t)]
    return categories, tags


def _extract_mcmod_description(html: str) -> str:
    """提取 Mod 介绍正文描述。"""
    intro_idx = html.find("Mod介绍")
    if intro_idx < 0:
        return ""
    segment = html[intro_idx:intro_idx + _MAX_DESCRIPTION_SEGMENT]
    section_markers = ["配方", "Mod关系", "Mod前置", "Mod联动",
                       "更新日志", "常见问题", "排行榜", "相关链接",
                       "text-area-post", "class-post-list"]
    end = len(segment)
    for marker in section_markers:
        idx = segment.find(marker)
        if idx > _MIN_SECTION_MARKER_DISTANCE:
            end = min(end, idx)
    content = _clean_mcmod_html(segment[:end])
    content = re.sub(r"</li>", "\n", content)  # 列表项单独一行
    text = _clean_html_text(content, preserve_nl=True)
    prefix_pat = r"^(?:Mod(?:介绍|教程|下载|讨论|特性|关系)|模组介绍|配方|前置Mod|联动Mod|更新日志|介绍)\s*"
    prev = None
    for _ in range(10):  # 安全上限，防止无限循环
        if prev == text:
            break
        prev = text
        text = re.sub(prefix_pat, "", text).strip()
    skip_fragments = list(_MCMOD_COMMON_SKIP_PREFIXES) + [
        "关于百科", "百科帮助", "开发日志", "捐赠百科",
        "联系百科", "意见反馈", "©Copyright MC百科",
        "mcmod.cn | ", "鄂ICP备", "鄂公网安备",
    ]
    # contains 过滤：这些字符串可能出现在行中任何位置（非仅行首）
    skip_contains = ["©Copyright MC百科", "鄂ICP备", "鄂公网安备", "mcmod.cn | ", "百科帮助", "开发日志"]
    para_title_pat = r"^(?:概述|简介|正文)\s*"
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        line = re.sub(para_title_pat, "", line).strip()
        line = re.sub(r"[。！？]\s*概述(?=[^\s])", lambda m: m.group(0)[0], line)
        if len(line) < _MIN_DESCRIPTION_LINE_LEN:
            continue
        if any(line.startswith(p) for p in skip_fragments):
            continue
        if _MOD_META_PAT.match(line):
            continue
        if re.search(r"MC百科\s*\(mcmod\.cn\)\s*的?目标是", line):
            line = re.sub(r"MC百科\s*\(mcmod\.cn\)\s*的?目标是.*", "", line).strip()
        if len(line) < _MIN_DESCRIPTION_LINE_LEN:
            continue
        if any(p in line for p in skip_contains):
            continue
        # 过滤 HTML 残留（如 <li data-id=...）
        if re.search(r"<[a-z]+[\s>]", line, re.IGNORECASE):
            continue
        lines.append(line)
    # 不限制段落数，返回完整描述（JSON 模式下用户可自行处理）
    return "\n".join(lines)


def _extract_mcmod_relationships(html: str) -> dict:
    """提取前置Mod和联动Mod关系。返回 {"requires": [], "integrates": [], "_parse_attempted": bool}。"""
    relationships = {"requires": [], "integrates": []}
    parse_attempted = False
    seen_requires = set()
    seen_integrates = set()
    for m in re.finditer(r'(前置Mod|联动的Mod):</span><ul>(.*?)</ul>', html, re.DOTALL):
        parse_attempted = True
        label = m.group(1)
        ul = m.group(2)
        links = re.findall(r'href="(/class/(\d+)\.html)"[^>]*>([^<]+)</a>', ul)
        for _, cid, raw in links:
            if label == "前置Mod":
                if cid in seen_requires:
                    continue
                seen_requires.add(cid)
            else:
                if cid in seen_integrates:
                    continue
                seen_integrates.add(cid)
            raw = raw.strip()
            parts = re.match(r'(.+?)\s*\(([^)]+)\)\s*$', raw)
            if parts:
                zh, en = parts.group(1).strip(), parts.group(2).strip()
            else:
                zh, en = raw, ''
            entry = {"id": cid, "name_zh": zh, "name_en": en, "url": f"https://www.mcmod.cn/class/{cid}.html"}
            if label == "前置Mod":
                relationships["requires"].append(entry)
            else:
                relationships["integrates"].append(entry)
    relationships["_parse_attempted"] = parse_attempted
    return relationships


def _extract_mcmod_author_status(html: str) -> tuple[str | None, str | None, str | None, bool]:
    """提取作者、状态、开源属性。返回 (author, status, source_type)。"""
    # 使用通用函数提取作者
    author = _extract_mcmod_field(html, "Mod作者/开发团队") or _extract_mcmod_field(html, "作者")

    # 提取状态：新版MC百科使用 <div class="class-status"> 结构
    status = None
    status_match = re.search(r'class="class-status[^"]*">([^<]+)', html)
    if status_match:
        status = status_match.group(1).strip()
    else:
        # 降级：尝试旧版表格结构
        status = _extract_mcmod_field(html, "状态")
        if not status:
            status = None

    # 如果作者字段为空，尝试从 title 属性提取
    if not author:
        author_idx = html.find("Mod作者/开发团队")
        if author_idx >= 0:
            auth_section = html[author_idx:author_idx + _MAX_TAG_SECTION_LEN]
            author_m = re.search(r'title="([^"-]+)', auth_section)
            if author_m:
                author = author_m.group(1).strip()

    log_idx = html.find("更新日志")
    has_changelog = False
    if log_idx >= 0:
        has_changelog = "暂无日志" not in html[log_idx:log_idx + _MAX_TAG_SECTION_LEN]

    source_type = None
    src_m = re.search(r'class="class-source[^"]*"[^>]*>([^<]+)<', html)
    if src_m:
        st = src_m.group(1).strip()
        source_type = "open_source" if ("开源" in st or "open" in st.lower()) else "closed_source"

    return author if author else None, status if status else None, source_type, has_changelog


def _parse_mcmod_title(raw_title: str) -> tuple[str, str]:
    """从 MC百科 <title> 解析中文名和英文名。返回 (name_zh, name_en)。"""
    name_zh = raw_title
    name_en = ""
    title_match = re.match(r"^(.+?)\s*(?:\(([^)]+)\))?\s*-", raw_title)
    if title_match:
        name_zh = title_match.group(1).strip()
        name_en = title_match.group(2).strip() if title_match.group(2) else ""
    return name_zh, name_en


def _extract_mcmod_author_team(html: str) -> list[dict]:
    """从MC百科HTML提取作者团队信息。返回 [{"name": "...", "roles": ["..."]}]，最多10人。"""
    authors = []
    author_idx = html.find("Mod作者/开发团队")
    if author_idx < 0:
        return authors

    # 提取作者区域（在 li 标签内）
    auth_section_start = author_idx
    # 找到 ul/列表区域的结束
    auth_section_end = html.find("</ul>", auth_section_start)
    if auth_section_end < 0:
        auth_section_end = auth_section_start + _MAX_AUTHOR_SECTION
    auth_section = html[auth_section_start:auth_section_end]

    # 查找所有 <li> 条目
    li_blocks = re.findall(r'<li>(.*?)</li>', auth_section, re.DOTALL)

    # 需要过滤的组织/团队名称（不是真实作者）
    # 包含：组织名、团队名、工作室名、以及含有特定关键词的名称
    skip_keywords = [
        "Mods", "Studio", "Studios", "Team", "Development",
        "开发团队", "工作室", "团队", "官方",
        "Minecraft Mods", "Pixel Studios"
    ]

    for li in li_blocks:
        # 提取作者名（简化正则）
        name_m = re.search(r'class="name"><a[^>]*>([^<]+)</a>', li)
        # 提取分工（从 title 属性）
        position_m = re.search(r'title="([^"]+)" class="position"', li)

        if name_m:
            name = name_m.group(1).strip()
            # 清理名称（去除可能的备注部分）
            name = re.split(r'\s*[-–]\s*', name)[0].strip()

            # 过滤组织名称（精确匹配或包含关键词）
            is_org = name in _SKIP_MCMOD_ORG_NAMES
            if not is_org:
                for keyword in skip_keywords:
                    if keyword in name:
                        is_org = True
                        break

            if is_org:
                continue

            # 解析分工
            roles = []
            if position_m:
                roles_str = position_m.group(1).strip()
                if roles_str:
                    roles = re.split(r'[、/，,]', roles_str)
                    roles = [r.strip() for r in roles if r.strip() and len(r.strip()) <= 10]

            # 添加作者（没有分工则默认为"开发者"）
            if name:
                authors.append({
                    "name": name,
                    "roles": roles if roles else ["开发者"]
                })

    # 限制最多返回 10 人（避免输出过长）
    return authors[:_MAX_MCMOD_AUTHORS]


def _extract_mcmod_community_stats(html: str) -> dict:
    """提取社区统计数据。返回 {"rating": 5.0, "page_views": 22200, ...}。"""
    stats = {
        "rating": 0,
        "rating_text": "",
        "positive_rate": 0,
        "page_views": 0,
        "favorites": 0,
        "downloads": 0,
        "integrations_count": 0,
        "last_updated": "",
        "revision_count": 0
    }

    # 评级和好评率
    rating_section = html.find("综合评级")
    if rating_section >= 0:
        section = html[rating_section:rating_section + _MAX_TAG_SECTION_LEN]

        # 评分数字
        rating_m = re.search(r'(\d+\.\d+)', section)
        if rating_m:
            stats["rating"] = float(rating_m.group(1))

        # 评级文字（如"名扬天下"）
        rating_text_m = re.search(r'"([^"]*?评价[^"]*?)"', section)
        if rating_text_m:
            stats["rating_text"] = rating_text_m.group(1)

        # 好评率
        rate_m = re.search(r'(\d+)%', section)
        if rate_m:
            stats["positive_rate"] = int(rate_m.group(1))

    # 页面浏览量
    views_m = re.search(r'页面浏览量[:：]?\s*([\d,\.]+)', html)
    if views_m:
        stats["page_views"] = int(float(views_m.group(1).replace(',', '')))

    # 收藏数
    fav_m = re.search(r'收藏[:：]?\s*([\d,\.]+)', html)
    if fav_m:
        stats["favorites"] = int(float(fav_m.group(1).replace(',', '')))

    # 整合包引用数
    integration_m = re.search(r'整合包引用[:：]?\s*(\d+)', html)
    if integration_m:
        stats["integrations_count"] = int(integration_m.group(1))

    # 修订次数
    revision_m = re.search(r'修订[:：]?\s*(\d+)', html)
    if revision_m:
        stats["revision_count"] = int(revision_m.group(1))

    # 最后更新时间
    update_m = re.search(r'(?:更新|更新在)\s*[:：]?\s*([\d]+[天小时日之前周月年前])', html)
    if update_m:
        stats["last_updated"] = update_m.group(1)

    return stats


def _decode_mcmod_obfuscated_link(encoded: str) -> str:
    """解码 MC百科的 Base64 混淆链接。失败返回空字符串。"""
    try:
        padding = 4 - len(encoded) % 4
        if padding != 4:
            encoded += "=" * padding
        return base64.b64decode(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as e:
        logger.debug(f"Link decode failed: {e}")
        return ""


def _add_cross_platform_ids(links: dict) -> None:
    """从已提取链接中解析跨平台 slug。在原地修改 links。"""
    cross_platform_ids = {}
    if "curseforge" in links:
        cf_slug = re.search(r'/minecraft/mc-mods/([^/\s"<>\)]+)', links["curseforge"])
        if cf_slug:
            cross_platform_ids["curseforge_slug"] = cf_slug.group(1)
    if "modrinth" in links:
        mr_slug = re.search(r'/(?:mod|shader|resourcepack|modpack)/([^/\s"<>\)]+)', links["modrinth"])
        if mr_slug:
            cross_platform_ids["modrinth_slug"] = mr_slug.group(1)
    if cross_platform_ids:
        links["cross_platform_ids"] = cross_platform_ids


def _extract_mcmod_external_links(html: str) -> dict:
    """提取模组的外部平台链接。返回 {"official": "...", "curseforge": "...", ...}。"""
    links = {}

    # 收集所有解码后的链接
    all_decoded = []
    obfuscated = re.findall(r'link\.mcmod\.cn/target/([A-Za-z0-9+/=]+)', html)
    for encoded in obfuscated:
        url = _decode_mcmod_obfuscated_link(encoded)
        if url and url.startswith("http"):
            all_decoded.append(url)

    # 分类存储链接
    curseforge_links = []
    github_links = []

    for url in all_decoded:
        # 官方网站（非已知平台的独立域名，仅设一次）
        if "official" not in links:
            if not any(x in url for x in _EXTERNAL_LINK_EXCLUDE_DOMAINS):
                links["official"] = url

        # CurseForge 收集（后续选最优）
        if "curseforge.com" in url:
            curseforge_links.append(url)
            continue

        # GitHub 收集（过滤 wiki/issues/pull，后续选主仓库）
        if "github.com" in url:
            if not any(x in url for x in ["/blob/", "/wiki", "/issues", "/pull/"]):
                github_links.append(url)
            continue

        # 其余平台：按规则表匹配，每个 key 只存第一个
        for pattern, key in _SIMPLE_LINK_RULES:
            if pattern(url) and key not in links:
                links[key] = url
                break

    # 选择 CurseForge 链接：优先 mc-mods，其次最短
    if curseforge_links:
        mc_mods_links = [u for u in curseforge_links if "/mc-mods/" in u]
        if mc_mods_links:
            links["curseforge"] = min(mc_mods_links, key=len)
        else:
            links["curseforge"] = min(curseforge_links, key=len)

    # 选择最短的 GitHub 链接（通常是主仓库）
    if github_links:
        links["github"] = min(github_links, key=len)

    # 跨平台 ID（用于精确关联 Modrinth/CurseForge）
    _add_cross_platform_ids(links)

    return links


def _extract_mcmod_field(html: str, field_label: str = "作者") -> str:
    """通用提取MC百科字段。返回字段值（带链接优先，否则纯文本）。"""
    # 先尝试提取带链接的值
    pattern = rf'{field_label}：</td><td[^>]*><a[^>]*>([^<]+)</a>'
    m = re.search(pattern, html)
    if m:
        return m.group(1).strip()

    # 降级为纯文本
    pattern = rf'{field_label}：</td><td[^>]*>([^<]+)</td>'
    m = re.search(pattern, html)
    return m.group(1).strip() if m else ""


def _extract_mcmod_content_list(html: str, class_id: str) -> dict:
    """提取模组资料列表。返回 {"1": {"label": "物品/方块", "count": 1016, "url": "..."}}。"""
    # 预定义映射（仅作 fallback，优先使用页面标题）
    content_types = {
        "1": "物品/方块",
        "4": "生物/实体",
        "5": "附魔/魔咒",
        "6": "BUFF/DEBUFF",
        "7": "多方块结构",
        "8": "自然生成",
        "9": "绑定热键",
        "10": "游戏设定",
    }

    result = {}

    # 查找所有 item/list 链接（严格匹配当前结构）
    pattern = rf'href="/item/list/{class_id}-(\d+)\.html"[^>]*>.*?<span class="title">([^<]+)</span>.*?<span class="count">\((\d+)条\)</span>'
    matches = re.findall(pattern, html, re.DOTALL)

    # Fallback: 宽松正则（兼容结构变化）
    if not matches:
        fallback_pattern = rf'href="/item/list/{class_id}-(\d+)\.html"[^>]*>(.*?)</a>'
        fallback_matches = re.findall(fallback_pattern, html, re.DOTALL)
        for type_id, inner_html in fallback_matches:
            type_id = type_id.strip()
            title_m = re.search(r'<span[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)</span>', inner_html)
            count_m = re.search(r'(\d+)\s*条', inner_html)
            if title_m and count_m:
                matches.append((type_id, title_m.group(1), count_m.group(1)))

    for type_id, title, count in matches:
        type_id = type_id.strip()
        count = int(count.strip())
        if count > 0:
            label = title.strip() or content_types.get(type_id, f"类型{type_id}")
            result[type_id] = {
                "label": label,
                "count": count,
                "url": f"https://www.mcmod.cn/item/list/{class_id}-{type_id}.html",
            }

    return result


def _parse_mcmod_mod_result(html: str, url: str, name: str) -> dict:
    """从 MC百科 class 页面解析。name 来自搜索页，html 仅用于提取扩展字段。"""
    if _is_mcmod_blocked(html):
        return _build_mcmod_fallback_result(url, name, None, "mod")

    m = re.search(r"<title>([^<]+)</title>", html)
    raw_title = m.group(1).strip() if m else name

    # 从 <title> 提取中英文名（格式："中文名 (English) - MC百科|..."）
    zh_from_title, en_from_title = _parse_mcmod_title(raw_title)

    # 副标题 h4 作为英文名后备
    name_en = en_from_title
    if not name_en:
        h4_m = re.search(r'<h4[^>]*>\s*([^<\s][^<]*?)\s*</h4>', html)
        if h4_m:
            en_raw = h4_m.group(1).strip()
            if en_raw and en_raw != zh_from_title:
                name_en = en_raw

    # 中文名直接取自 title 解析结果
    name_zh = zh_from_title

    # 调用辅助函数提取各字段
    cover_image, screenshots = _extract_mcmod_cover(html)
    supported_versions = _extract_mcmod_versions(html)
    categories, tags = _extract_mcmod_categories(html)
    description = _extract_mcmod_description(html)
    relationships_raw = _extract_mcmod_relationships(html)
    parse_attempted = relationships_raw.pop("_parse_attempted", False)
    relationships = None
    if relationships_raw["requires"] or relationships_raw["integrates"]:
        relationships = {"requires": relationships_raw["requires"], "integrates": relationships_raw["integrates"]}
    elif parse_attempted:
        relationships = {"_error": "parse_failed"}
    author, status, source_type, has_changelog = _extract_mcmod_author_status(html)
    external_links = _extract_mcmod_external_links(html)

    # 新增：提取完整作者团队和社区数据
    author_team = _extract_mcmod_author_team(html)
    community_stats = _extract_mcmod_community_stats(html)

    # 提取 class_id 并获取资料列表
    class_id = re.search(r"/class/(\d+)", url).group(1) if url else ""
    content_list = _extract_mcmod_content_list(html, class_id) if class_id else {}

    # 原版内容识别：class/1 是 MC百科"原版内容"分类
    is_vanilla = bool(re.search(r"/class/1\.html", url))

    result = {
        "name": name_zh or raw_title or name,
        "name_en": name_en,
        "name_zh": name_zh or raw_title or name,
        "url": url,
        "source": "mcmod.cn",
        "source_id": re.search(r"/class/(\d+)", url).group(1) if url else "",
        "type": "mod",
        "is_vanilla": is_vanilla,
        "cover_image": cover_image,
        "screenshots": [],
        "supported_versions": supported_versions,
        "categories": categories,
        "tags": tags,
        "author": author,  # 兼容性：保留单一作者
        "author_team": author_team if author_team else None,  # 新增：完整作者团队
        "community_stats": community_stats if any(community_stats.values()) else None,  # 新增：社区数据
        "status": status,
        "source_type": source_type,
        "description": description,  # 返回完整描述（由调用方决定是否截断）
        "relationships": relationships,
        "has_changelog": has_changelog,
        "external_links": external_links if external_links else None,
        "content_list": content_list or None,
    }

    # 截断元信息
    truncated = _build_truncated_meta(description, _MAX_SEARCH_DESC_CHARS)
    if truncated:
        result["_truncated"] = truncated

    return result


# ═══════════════════════════════════════════════════════════════
# MC百科 搜索管线（URL构建 → 搜索页 → 详情抓取）
# ═══════════════════════════════════════════════════════════════

def _parallel_fetch_with_fallback(items: list, fetch_func: callable, max_workers: int,
                                   filter_none: bool = True) -> list:
    """并行抓取带降级。返回结果列表（可选过滤None）。"""
    with futures_module.ThreadPoolExecutor(max_workers=max_workers) as ex:
        results = []
        try:
            results = list(ex.map(fetch_func, items))
        except Exception as e:
            logger.warning(f"Parallel fetch failed: {e}, falling back to sequential")
            # 回退到逐个抓取，跳过失败项
            for item in items:
                try:
                    results.append(fetch_func(item))
                except (SearchError, OSError) as e:
                    logger.warning(f"Fetch failed for item: {e}")
                    continue

    if filter_none:
        results = [r for r in results if r is not None]
    return results


def _build_mcmod_search_urls(keyword: str, content_type: str) -> list[str]:
    """构建MC百科搜索URL列表"""
    # filter 映射
    filter_map = {"mod": _MCMOD_FILTER_MOD, "item": _MCMOD_FILTER_ITEM}
    if content_type not in filter_map and content_type != "modpack":
        raise ValueError(f"search_mcmod 不支持的 content_type: {content_type}。仅支持 'mod' / 'item' / 'modpack'")

    q = urllib.parse.quote(keyword)

    # 物品用 /item/ URL，模组用 /class/ URL
    if content_type == "item":
        return [f"https://search.mcmod.cn/s?key={q}&filter={_MCMOD_FILTER_ITEM}"]
    else:
        return [f"https://search.mcmod.cn/s?key={q}&filter={_MCMOD_FILTER_MOD}"]


def _extract_mcmod_search_section(html: str, raise_on_empty: bool = True) -> str | None:
    """从 MC 百科搜索结果页提取 search-result-list 区域（不含 pagination）。

    Args:
        html: 完整页面 HTML
        raise_on_empty: 未找到 search-result-list 时是否抛出异常

    Returns:
        提取的 section 内容（已移除 <em> 标签），未找到时返回 None 或抛出异常
    """
    idx = html.find("search-result-list")
    if idx == -1:
        if raise_on_empty:
            raise SearchError("MC 百科 搜索结果页结构变化（无 search-result-list）")
        return None

    end_idx = html.find('class="pagination"', idx)
    if end_idx == -1:
        end_idx = len(html)
    section = html[idx:end_idx]
    return re.sub(r"<em[^>]*>|</em>", "", section)


def _parse_mcmod_search_results(html: str, content_type: str, keyword: str) -> list[tuple[str, str]]:
    """解析MC百科搜索结果页面，提取URL和名称对"""
    section = _extract_mcmod_search_section(html)  # raise_on_empty=True → 结构变化时直接抛异常
    clean = section

    # 物品用 /item/ URL，模组用 /class/ URL，整合包用 /modpack/ URL
    if content_type == "item":
        pairs = re.findall(
            r'href="(https://www\.mcmod\.cn/item/\d+\.html)">([^<]+)</a>',
            clean,
        )
    elif content_type == "modpack":
        pairs = re.findall(
            r'href="(https://www\.mcmod\.cn/modpack/\d+\.html)">([^<]+)</a>',
            clean,
        )
    else:
        pairs = re.findall(
            r'href="(https://www\.mcmod\.cn/class/\d+\.html)">([^<]+)</a>',
            clean,
        )

    if not pairs:
        raise SearchError(f"MC百科 无结果（{content_type}）：{keyword}")

    # 去重
    seen = set()
    all_pairs = []
    for raw_url, name in pairs:
        name = name.strip()
        if name and raw_url not in seen and not name.startswith("www."):
            seen.add(raw_url)
            all_pairs.append((raw_url, name))

    return all_pairs


def _extract_search_result_metadata(html: str) -> dict[str, dict]:
    """从搜索结果页提取每个结果的描述和分类 ID。
    返回 {url: {"description": "...", "category": N}}。
    """
    section = _extract_mcmod_search_section(html, raise_on_empty=False)
    if section is None:
        return {}

    # 按 result-item 分割
    items = section.split('class="result-item"')
    metadata = {}

    for item in items[1:]:  # 跳过第一个空段
        # 提取 URL
        url_m = re.search(
            r'href="(https://www\.mcmod\.cn/(?:class|item|modpack)/\d+\.html)"',
            item
        )
        if not url_m:
            continue
        url = url_m.group(1)

        # 提取描述（body div）
        body_m = re.search(r'<div class="body">(.*?)</div>', item, re.DOTALL)
        if body_m:
            raw = body_m.group(1)
            raw = _clean_html_text(raw)
            metadata.setdefault(url, {})["description"] = raw[:_MAX_SEARCH_DESC_CHARS]

        # 提取分类 ID（class="c_N" 中的 N）
        cat_m = re.search(r'class="c_(\d+)"', item)
        if cat_m:
            try:
                metadata.setdefault(url, {})["category"] = int(cat_m.group(1))
            except ValueError:
                pass

    return metadata


def _rank_by_name_match(pairs: list[tuple[str, str]], keyword: str) -> list[tuple[str, str]]:
    """按名称匹配度排序。精确匹配→前缀→包含→其余，每层内部保持原始顺序。"""
    keyword_lower = keyword.lower().replace(" ", "")

    def _match_tier(pair):
        name_lower = pair[1].lower().replace(" ", "")
        if name_lower == keyword_lower:
            return 0
        if name_lower.startswith(keyword_lower):
            return 1
        if keyword_lower in name_lower:
            return 2
        return 3

    tiers = {0: [], 1: [], 2: [], 3: []}
    for pair in pairs:
        tiers[_match_tier(pair)].append(pair)

    result = []
    for tier in [0, 1, 2, 3]:
        result.extend(tiers[tier])
    return result


def _fetch_mcmod_details(limited_pairs: list[tuple[str, str]], content_type: str,
                         search_metadata: dict[str, dict] | None = None) -> list[dict]:
    """并行抓取模组详情页。若被 WAF 拦截，回退到搜索数据构建最小结果。"""
    if not limited_pairs:
        return []

    if search_metadata is None:
        search_metadata = {}

    def _fetch_one(args):
        raw_url, name = args
        detail_key = _cache_key("mcmod_detail", raw_url, content_type)
        cached = _cache_get("mcmod_detail", detail_key)
        if cached is not None:
            return cached

        page_html = curl(raw_url)

        # 检测 WAF 拦截 → 用搜索数据回退（不缓存回退结果）
        if _is_mcmod_blocked(page_html):
            meta = search_metadata.get(raw_url, {})
            return _build_mcmod_fallback_result(raw_url, name, meta, content_type)

        if content_type == "item":
            result = _parse_mcmod_item_result(page_html, raw_url, name)
        elif content_type == "modpack":
            result = _parse_mcmod_modpack_result(page_html, raw_url, name)
        else:
            result = _parse_mcmod_mod_result(page_html, raw_url, name)
        _cache_set("mcmod_detail", detail_key, result)
        return result

    results = _parallel_fetch_with_fallback(
        limited_pairs, _fetch_one,
        max_workers=min(len(limited_pairs), _MAX_FETCH_WORKERS)
    )
    return results


@_cached(lambda keyword, max_results=5, content_type="mod": ("search", _cache_key("mcmod", keyword, max_results, content_type)))
def search_mcmod(keyword: str, max_results: int = 5, content_type: str = "mod") -> list[dict]:
    """
    MC百科 搜索。

    content_type: "mod" | "item" | "modpack"
      - "mod"     → filter=0  → /class/ 页面（综合排序，主模组更靠前）
      - "item"    → filter=3  → /item/  页面（物品/方块）
      - "modpack" → 使用多 filter 策略搜索整合包
    """
    # 整合包使用专用搜索函数（多 filter 策略）
    if content_type == "modpack":
        return search_mcmod_modpack(keyword, max_results)

    # 1. 构建搜索URL
    urls = _build_mcmod_search_urls(keyword, content_type)

    # 2. 执行搜索
    html = curl(urls[0])
    if not html:
        raise SearchError(f"MC百科 (mcmod.cn) 当前无法访问，可能正在维护。建议使用 --platform modrinth 搜索 Modrinth 或稍后重试。")

    # 3. 解析结果
    all_pairs = _parse_mcmod_search_results(html, content_type, keyword)
    search_metadata = _extract_search_result_metadata(html)

    # 4. 按匹配度排序
    reordered = _rank_by_name_match(all_pairs, keyword)

    # 5. 截断到 max_results
    limited_pairs = reordered[:max_results]

    # 6. 抓取详情（WAF 拦截时自动回退到搜索数据）
    results = _fetch_mcmod_details(limited_pairs, content_type, search_metadata)

    # 7. 截断描述（控制 token 消耗）
    for r in results:
        if r.get('description') and len(r['description']) > _MAX_SEARCH_DESC_CHARS:
            full_len = len(r['description'])
            r['description'] = r['description'][:_MAX_SEARCH_DESC_CHARS]
            r.setdefault('_truncated', {})['description'] = {"returned": _MAX_SEARCH_DESC_CHARS, "total": full_len}

    return results


@_cached(lambda author_name, max_mods=20: ("search", _cache_key("mcmod_author", author_name, max_mods)))
def search_mcmod_author(author_name: str, max_mods: int = 20) -> list[dict]:
    """MC百科按作者搜索。返回模组列表。"""
    q = urllib.parse.quote(author_name)
    html = curl(f"https://search.mcmod.cn/s?key={q}&filter=0")
    if not html or len(html) < MIN_HTML_LEN:
        raise SearchError(f"MC百科 作者搜索网络失败：{author_name}")

    idx = html.find("search-result-list")
    if idx == -1:
        raise SearchError(f"MC百科 作者搜索结果页结构变化：{author_name}")

    section = html[idx:idx + _MAX_AUTHOR_SECTION]
    clean = re.sub(r"<em[^>]*>|</em>", "", section)

    # 找 /author/ URL（搜索词精确匹配作者名时会出现）
    author_urls = re.findall(r'href="(https://www\.mcmod\.cn/author/\d+\.html)"', clean)
    if not author_urls:
        raise SearchError(f"MC百科 未找到作者 [{author_name}] 的页面（作者名需精确匹配）")

    author_url = author_urls[0]

    # 解析作者页面，获取所有模组
    page_html = curl(author_url)
    if _is_mcmod_blocked(page_html):
        raise SearchError(f"MC百科 作者页面被防火墙拦截：{author_name}。MC百科当前限制了页面访问，请稍后重试或使用 --platform modrinth。")
    if not page_html or len(page_html) < MIN_HTML_LEN:
        raise SearchError(f"MC百科 作者页面获取失败：{author_name}")

    # 从作者页面提取所有 /class/ 链接
    mod_links = re.findall(r'href="(/class/\d+\.html)"[^>]*>([^<]+)</a>', page_html)
    # 去重
    seen = set()
    unique_mods = []
    for url, name in mod_links:
        if url not in seen and name.strip() and not name.startswith("www."):
            seen.add(url)
            unique_mods.append((url, name.strip()))

    # 并行解析每个模组页面
    def _fetch_mod(args):
        url, name = args
        full_url = f"https://www.mcmod.cn{url}"
        page = curl(full_url)
        if _is_mcmod_blocked(page):
            return _build_mcmod_fallback_result(full_url, name, None, "mod")
        if page and len(page) >= MIN_HTML_LEN:
            return _parse_mcmod_mod_result(page, full_url, name)
        return {"name": name, "url": full_url, "source": "mcmod.cn", "_error": "page_fetch_failed"}

    limited_mods = unique_mods[:max_mods]
    results = _parallel_fetch_with_fallback(
        limited_mods, _fetch_mod,
        max_workers=min(len(limited_mods), _MAX_FETCH_WORKERS)
    )

    return results


@_cached(lambda keyword, max_results=5: ("search", _cache_key("mcmod_modpack", keyword, max_results)))
def search_mcmod_modpack(keyword: str, max_results: int = 5) -> list[dict]:
    """MC百科整合包搜索。尝试多个filter策略，返回结果列表。"""
    q = urllib.parse.quote(keyword)

    # 多 filter 策略：按优先级尝试不同的 filter 值
    all_pairs = []
    seen = set()
    all_metadata = {}  # 跨 filter 累积搜索元数据

    for filter_val in _MCMOD_MODPACK_FILTERS:
        html = curl(f"https://search.mcmod.cn/s?key={q}&filter={filter_val}")
        if not html:
            continue

        idx = html.find("search-result-list")
        if idx == -1:
            continue

        # 累积搜索元数据（用于 WAF 回退）
        page_meta = _extract_search_result_metadata(html)
        all_metadata.update(page_meta)

        # 找到结果区域的结束位置（分页区域）
        end_idx = html.find('class="pagination"', idx)
        if end_idx == -1:
            end_idx = len(html)
        section = html[idx:end_idx]
        clean = re.sub(r"<em[^>]*>|</em>", "", section)

        # 提取整合包 URL（/modpack/ 路径）
        pairs = re.findall(
            r'href="(https://www\.mcmod\.cn/modpack/\d+\.html)">([^<]+)</a>',
            clean,
        )

        # 去重并添加到结果集
        for raw_url, name in pairs:
            name = name.strip()
            if name and raw_url not in seen and not name.startswith("www."):
                seen.add(raw_url)
                all_pairs.append((raw_url, name))

        # 如果已经找到足够结果，提前结束
        if len(all_pairs) >= max_results:
            break

    if not all_pairs:
        return []

    # 重新排序：名称匹配度优先（复用模组排序逻辑）
    reordered = _rank_by_name_match(all_pairs, keyword)

    # 截断到 max_results
    limited_pairs = reordered[:max_results]

    # 并行抓取详情页（WAF 拦截时自动回退到搜索数据）
    results = _fetch_mcmod_details(limited_pairs, "modpack", all_metadata)

    return results


# ═══════════════════════════════════════════════════════════════
# Modrinth API + bbsmc 回填
# ═══════════════════════════════════════════════════════════════

def _build_modrinth_url(slug: str, project_type: str) -> str:
    """构建Modrinth URL。返回 "https://modrinth.com/{type}/{slug}"。"""
    return f"https://modrinth.com/{project_type or 'mod'}/{slug}"


def _backfill_bbsmc_names(results: list[dict]):
    """按 slug 批量回填 bbsmc 中文名和简介到 Modrinth 结果（原地修改）。"""
    slugs = [r["source_id"] for r in results if r.get("source_id")]
    if not slugs:
        return
    bbsmc_data = {}
    for slug, bbsmc_val in _parallel_fetch_with_fallback(
        [(s,) for s in slugs],
        lambda args: (args[0], _fetch_bbsmc_project(args[0])),
        max_workers=min(len(slugs), _MAX_FETCH_WORKERS), filter_none=False
    ):
        if slug and bbsmc_val:
            bbsmc_data[slug] = bbsmc_val
    for result in results:
        slug = result.get("source_id", "")
        bd = bbsmc_data.get(slug, {})
        if bd:
            bbsmc_name = bd.get("name", "")
            result["name_zh"] = bbsmc_name
            cn_m = re.match(r'^(.+?)\s*[-–—]\s*\S+', bbsmc_name)
            if cn_m and _is_cjk(cn_m.group(1)):
                result["_name_zh_cn"] = cn_m.group(1).strip()
            # 修复 bbsmc 双语名污染 name_en（如 "机械动力 - Create"、"机械动力 – Create"）
            if _is_cjk(result.get("name_en", "")):
                parts = re.split(r'\s*[-–—]\s*', bbsmc_name, 1)
                if len(parts) == 2 and not _is_cjk(parts[1]):
                    result["name_en"] = parts[1].strip()
                    result["name"] = parts[1].strip()
            bbsmc_summary = bd.get("summary", "")
            if bbsmc_summary and result["description"] in ("", result.get("snippet", "")):
                result["description"] = bbsmc_summary[:_MAX_SEARCH_DESC_CHARS]
                if len(bbsmc_summary) > _MAX_SEARCH_DESC_CHARS:
                    result.setdefault("_truncated", {})["description"] = {
                        "returned": _MAX_SEARCH_DESC_CHARS, "total": len(bbsmc_summary)
                    }


def _fetch_bbsmc_project(slug: str) -> dict | None:
    """查询 bbsmc.net 项目。返回 {"name": "...", "summary": "..."} 或 None。"""
    try:
        data = _fetch_json(f"{_BBSMC_API}/project/{slug}")
        if data and data.get("name"):
            return {"name": data.get("name", ""), "summary": data.get("summary", "")}
    except Exception as e:
        logger.debug(f"bbsmc fetch failed for {slug}: {e}")
    return None


@_cached(lambda keyword, max_results=5, project_type="mod": ("search", _cache_key("modrinth", keyword, max_results, project_type)))
def search_modrinth(keyword: str, max_results: int = 5, project_type: str = "mod") -> dict:
    """Modrinth搜索。返回 {"results": [...], "total": N, "returned": M}。

    每个结果包含完整description（与MC百科齐平）。详情（body+changelogs）并行获取。
    """
    q = urllib.parse.quote(keyword)
    url = f"{_MODRINTH_API}/search?query={q}&index=relevance&limit={max_results}"
    data = _fetch_json(url, {"hits": []})
    if not data or "hits" not in data:
        return {"results": [], "total": 0, "returned": 0}

    # 1. 收集匹配的 hits
    matched_hits = []
    for hit in data.get("hits", []):
        proj_type = hit.get("project_type", "")
        if project_type and proj_type and proj_type != project_type:
            continue
        matched_hits.append((hit, hit.get("slug", "")))

    if not matched_hits:
        # 分类容错：project_type 过滤无结果时，去过滤重试（如 Iris 被归为 mod 非 shader）
        if project_type and project_type != "mod":
            for hit in data.get("hits", []):
                matched_hits.append((hit, hit.get("slug", "")))
        if not matched_hits:
            # CJK 关键词回退：直接搜 bbsmc（中文搜索兼容）
            if _is_cjk(keyword):
                bbsmc_url = f"{_BBSMC_API}/search?query={q}&limit={max_results}"
                bbsmc_data = _fetch_json(bbsmc_url, {"hits": []})
                for bhit in bbsmc_data.get("hits", []):
                    # 标准化为 Modrinth hit 格式
                    pt_list = bhit.get("project_types")
                    project_type = pt_list[0] if pt_list else bhit.get("project_type", "mod")
                    bbsmc_title = bhit.get("name", bhit.get("title", ""))
                    # 从双语名 "中文 - English" 中提取纯英文 name_en
                    name_en = bbsmc_title
                    if _is_cjk(bbsmc_title):
                        parts = re.split(r'\s*[-–—]\s*', bbsmc_title, 1)
                        name_en = parts[1].strip() if len(parts) == 2 and not _is_cjk(parts[1]) else ""
                    normalized = {
                        "title": bbsmc_title,
                        "slug": bhit.get("slug", ""),
                        "project_type": project_type,
                        "description": bhit.get("summary", bhit.get("description", "")),
                        "downloads": bhit.get("downloads", 0),
                        "followers": bhit.get("follows", bhit.get("followers", 0)),
                        "icon_url": bhit.get("icon_url", ""),
                        "author": bhit.get("author", ""),
                        "versions": bhit.get("versions", []),
                        "_name_en": name_en,  # 预提取的纯英文名，供结果构建使用
                    }
                    matched_hits.append((normalized, normalized["slug"]))
            if not matched_hits:
                return {"results": [], "total": data.get("total_hits", 0), "returned": 0}

    # 2. 并行获取详情（body + changelogs）
    def _fetch_detail(args):
        hit, slug = args
        if not slug:
            return (hit, None)
        try:
            return (hit, fetch_mod_info(slug, no_limit=True))
        except Exception as e:
            logger.debug(f"detail fetch failed for {hit.get('slug')}: {e}")
            hit["_body_error"] = "fetch_failed"
            return (hit, None)

    details = _parallel_fetch_with_fallback(
        matched_hits, _fetch_detail,
        max_workers=min(len(matched_hits), _MAX_FETCH_WORKERS)
    )

    # 3. 构建结果（保持搜索 API 的顺序）
    results = []
    for hit, full_info in details:
        proj_type = hit.get("project_type", "")
        slug = hit.get("slug", "")
        description = hit.get("description", "")
        changelogs = []
        if full_info:
            body = full_info.get("body", "")
            if body:
                description = body[:_MAX_SEARCH_DESC_CHARS] + ("..." if len(body) > _MAX_SEARCH_DESC_CHARS else "")
            cl_list = full_info.get("changelogs", [])
            changelogs = cl_list[:_SEARCH_CHANGELOG_LIMIT]

        result = {
            "name": hit.get("title", ""),
            "name_en": hit.get("_name_en") or hit.get("title", ""),
            "name_zh": "",
            "url": _build_modrinth_url(slug, proj_type or project_type or "mod"),
            "source": "modrinth",
            "source_id": slug,
            "type": proj_type or project_type or "mod",
            "snippet": hit.get("description", ""),
            "description": description,
            "downloads": hit.get("downloads", 0),
            "followers": hit.get("followers", 0),
            "icon_url": hit.get("icon_url", ""),
            "author": hit.get("author", ""),
            "supported_versions": hit.get("versions", []),
            "changelogs": changelogs,
        }
        results.append(result)

    # 4. bbsmc 回填中文名和中文简介
    if results:
        _backfill_bbsmc_names(results)

    total = data.get("total_hits", 0)
    return {"results": results, "total": total, "returned": len(results)}


def _parse_modrinth_license(raw_license: dict | str) -> tuple[str, str, str]:
    """解析 Modrinth 许可证字段。返回 (id, name, url)。"""
    if isinstance(raw_license, dict):
        return (
            raw_license.get("id", ""),
            raw_license.get("name", ""),
            raw_license.get("url", ""),
        )
    return raw_license or "", "", ""


def _parse_modrinth_donations(data: dict) -> list[dict]:
    """解析 Modrinth 捐赠链接列表。"""
    return [
        {"platform": d.get("platform", ""), "url": d.get("url", "")}
        for d in data.get("donation_urls", [])
    ]


def _html_to_text(html: str) -> str:
    """将 HTML 转换为纯文本。

    处理常见的 HTML 标签：
    - <p>, <div>, <br>, <h1-h6> -> 换行
    - <a> -> 保留链接文本
    - <iframe> -> 提取 YouTube 链接
    - 去除 HTML 实体
    """
    if not html:
        return html

    text = html

    # 1. 提取 YouTube iframe 链接
    def replace_iframe(m):
        attrs = m.group(1)
        src_match = re.search(r'src="([^"]+)"', attrs)
        if src_match:
            src = src_match.group(1)
            if 'youtube' in src:
                return f'\n\n[YouTube 视频]({src})\n\n'
        return ''

    text = re.sub(r'<iframe([^>]*)>', replace_iframe, text, flags=re.IGNORECASE)

    # 2. 处理链接：<a href="...">text</a> -> text
    text = re.sub(r'<a[^>]*>(.*?)</a>', r'\1', text, flags=re.DOTALL | re.IGNORECASE)

    # 3. 处理图片：<img alt="..." src="..."> -> ![alt](src)
    def replace_img(m):
        alt_match = re.search(r'alt="([^"]*)"', m.group(0))
        src_match = re.search(r'src="([^"]*)"', m.group(0))
        alt = alt_match.group(1) if alt_match else ''
        src = src_match.group(1) if src_match else ''
        if alt and src:
            return f'![{alt}]({src})'
        return ''

    text = re.sub(r'<img[^>]*/?>', replace_img, text, flags=re.IGNORECASE)

    # 4. 处理标题标签 -> 加 ## 前缀
    text = re.sub(r'<h[1-6][^>]*>', '\n## ', text, flags=re.IGNORECASE)
    text = re.sub(r'</h[1-6]>', '\n', text, flags=re.IGNORECASE)

    # 5. 处理段落和换行
    text = re.sub(r'<(p|div|br|hr|blockquote)[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</(p|div|br|hr|blockquote)>', '\n', text, flags=re.IGNORECASE)

    # 6. 处理列表
    text = re.sub(r'<li[^>]*>', '\n- ', text, flags=re.IGNORECASE)
    text = re.sub(r'</li>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</?(ul|ol)[^>]*>', '\n', text, flags=re.IGNORECASE)

    # 7. 处理代码块
    text = re.sub(r'<pre[^>]*>(.*?)</pre>', r'```\n\1\n```\n', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', text, flags=re.DOTALL | re.IGNORECASE)

    # 8. 处理粗体和斜体
    text = re.sub(r'<(strong|b)[^>]*>(.*?)</\1>', r'**\2**', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<(em|i)[^>]*>(.*?)</\1>', r'*\2*', text, flags=re.DOTALL | re.IGNORECASE)

    # 9. 移除所有剩余的 HTML 标签
    text = re.sub(r'<[^>]+>', '', text)

    # 10. 处理 HTML 实体
    text = html_module.unescape(text)

    # 11. 清理多余空行（超过2个连续空行 -> 2个）
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 12. 将 \xa0 (nbsp) 替换为普通空格
    text = text.replace('\xa0', ' ')

    # 13. 去除首尾空白
    text = text.strip()

    return text


def _clean_modrinth_body(body: str) -> str:
    """清洗 Modrinth body 字段：HTML 转文本 + 移除赞助者名单。

    步骤：
    1. 将 HTML 转换为纯文本
    2. 截断到 "Our Patrons" 等标记处
    """
    if not body:
        return body

    # 1. 先转换 HTML 为纯文本
    text = _html_to_text(body)

    # 2. 定义多个可能的截断标记（按优先级排序）
    cut_markers = [
        "## Our Patrons",
        "### Our Patrons",
        "Our Patrons",
        "## Patrons",
        "### Patrons",
        "## Supporters",
        "### Supporters",
    ]

    best_cut_pos = len(text)  # 默认不截断

    for marker in cut_markers:
        pos = text.find(marker)
        if pos != -1 and pos < best_cut_pos:
            best_cut_pos = pos

    # 如果找到了截断位置，截取并添加提示
    if best_cut_pos < len(text):
        cut_text = text[:best_cut_pos].rstrip()
        # 如果截取后为空，返回原文
        if not cut_text:
            return text
        return cut_text + "\n\n*(赞助者名单等冗长内容已省略)*"

    return text


def _build_modrinth_result(data: dict, project_id: str, body: str, gallery: list[str], ctx: dict) -> dict:
    """构建Modrinth结果字典。返回包含name/url/downloads等字段的dict。"""
    project_type = data.get("project_type", "mod")
    project_url = f"https://modrinth.com/{project_type}/{data.get('slug', '')}"

    return {
        "name": data.get("title", ""),
        "slug": data.get("slug", ""),
        "id": project_id,
        "description": data.get("description", ""),
        "body": body,
        "author": None,
        "license": ctx.get("license_id", ""),
        "license_name": ctx.get("license_name", ""),
        "license_url": ctx.get("license_url", ""),
        "categories": data.get("categories", []),
        "display_categories": data.get("display_categories", []),
        "client_side": data.get("client_side", ""),
        "server_side": data.get("server_side", ""),
        "source_url": data.get("source_url") or None,
        "wiki_url": data.get("wiki_url") or None,
        "issues_url": data.get("issues_url") or None,
        "discord_url": data.get("discord_url") or None,
        "donation_urls": ctx.get("donation_urls", []),
        "updated": data.get("updated", ""),
        "published": data.get("published", ""),
        "followers": data.get("followers", 0),
        "icon_url": data.get("icon_url") or "",
        "gallery": gallery,
        "latest_version": None,
        "game_versions": [],
        "loaders": [],
        "downloads": data.get("downloads", 0),
        "type": project_type,
        "source": "modrinth",
        "url": project_url,
    }


def _format_modrinth_versions(project_id: str, no_limit: bool) -> dict:
    """获取并格式化Modrinth版本信息"""
    versions = _fetch_json(f"{_MODRINTH_API}/project/{project_id}/version?max={_MAX_VERSIONS_FETCH}", [])
    if not versions:
        return {}

    # 获取最新版本信息
    latest = versions[0]
    result = {
        "latest_version": latest.get("version_number", ""),
        "game_versions": latest.get("game_versions", []),
        "loaders": latest.get("loaders", []),
    }

    # 按mod版本号分组（去掉loader前缀和mc<ver>-前缀）
    known_loaders = _KNOWN_LOADERS
    seen_mod_vers = {}
    for v in versions:
        vn = v.get("version_number", "")
        if not vn:
            continue
        stripped_ver = vn
        for loader in known_loaders:
            if stripped_ver.endswith(f"-{loader}"):
                stripped_ver = stripped_ver[:-len(loader) - 1]
                break
        mod_ver = re.sub(r'^mc[\d\.]+-', '', stripped_ver) or stripped_ver
        if mod_ver not in seen_mod_vers:
            seen_mod_vers[mod_ver] = {"game_versions": set(), "loaders": set()}
        seen_mod_vers[mod_ver]["game_versions"].update(v.get("game_versions", []))
        seen_mod_vers[mod_ver]["loaders"].update(v.get("loaders", []))

    items = [(k, {"game_versions": sorted(v["game_versions"]), "loaders": sorted(v["loaders"])})
             for k, v in seen_mod_vers.items()]

    version_total = len(items)
    result["version_groups"] = items if no_limit else items[:_MAX_VERSION_GROUPS]
    result["_version_total"] = version_total  # 用于截断元信息

    # changelog处理 - 根据 no_limit 标志区分数量
    # no_limit=True: 取前5个
    # no_limit=False (普通命令): 取前3个
    changelog_limit = _MAX_CHANGELOGS if no_limit else _SEARCH_CHANGELOG_LIMIT
    changelogs = []
    for v in versions[:changelog_limit]:
        cl = v.get("changelog", "").strip()
        if cl:
            changelogs.append({
                "version": v.get("version_number", ""),
                "date": (v.get("date_published") or "").split("T")[0],
                "changelog": cl,
            })
    changelog_total = sum(1 for v in versions if v.get("changelog", "").strip())
    result["changelogs"] = changelogs
    result["_changelog_total"] = changelog_total  # 用于截断元信息

    return result


def _fetch_modrinth_team_author(project_id: str) -> str:
    """从团队成员中获取作者"""
    team = _fetch_json(f"{_MODRINTH_API}/project/{project_id}/members", [])
    for m in team:
        if m.get("role") in ("Owner", "Developer", "Project Lead"):
            return m.get("user", {}).get("username") or m.get("user", {}).get("name", "")
    return ""


@_cached(lambda mod_id, no_limit=False: ("mod", _cache_key("modinfo", mod_id, "full" if no_limit else "limited")))
def fetch_mod_info(mod_id: str, no_limit: bool = False) -> dict | None:
    """
    获取 mod 完整信息（Modrinth）。
    mod_id 可以是 slug 或 project_id。
    no_limit: True 时返回完整数据，False 时使用默认限制并返回 _truncated 元信息。
    失败时返回 {"_error": "not_found"} 或 {"_error": "api_failed"}。
    """
    data, error = _fetch_modrinth_project(mod_id)
    if error:
        return {"_error": error}
    return _build_modrinth_info_result(data, no_limit)


def _fetch_modrinth_project(mod_id: str) -> tuple[dict | None, str | None]:
    """获取 Modrinth 项目原始数据。

    Returns:
        (data, error) 元组。成功时 (dict, None)；失败时 (None, "not_found"|"api_failed"|"parse_failed")。
    """
    raw = curl(f"{_MODRINTH_API}/project/{mod_id}")
    if raw is None:
        return None, "api_failed"
    if not raw:
        return None, "not_found"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError:
        return None, "parse_failed"


def _build_modrinth_info_result(data: dict, no_limit: bool = False) -> dict:
    """从 Modrinth 项目数据构建完整信息结果（含作者/版本/截断元信息）。"""
    project_id = data.get("id", "")

    # 解析许可证和捐赠
    license_id, license_name, license_url = _parse_modrinth_license(data.get("license"))
    donation_urls = _parse_modrinth_donations(data)

    # 处理 body 和 gallery
    raw_body = data.get("body") or ""
    body = _clean_modrinth_body(raw_body)
    raw_gallery = [g.get("url") for g in data.get("gallery", []) if g.get("url")]
    gallery_total = len(raw_gallery)

    # 构建基础结果
    ctx = {
        "license_id": license_id,
        "license_name": license_name,
        "license_url": license_url,
        "donation_urls": donation_urls,
    }
    result = _build_modrinth_result(data, project_id, body, raw_gallery, ctx)

    # 截断元信息
    truncated = {}
    if gallery_total > _MAX_GALLERY and not no_limit:
        truncated["gallery"] = {"returned": _MAX_GALLERY, "total": gallery_total}

    # 获取作者
    result["author"] = _fetch_modrinth_team_author(project_id)

    # 获取版本信息
    version_info = _format_modrinth_versions(project_id, no_limit)
    if version_info:
        result.update({
            "latest_version": version_info.get("latest_version"),
            "game_versions": version_info.get("game_versions"),
            "loaders": version_info.get("loaders"),
            "version_groups": version_info.get("version_groups"),
            "changelogs": version_info.get("changelogs"),
        })
        if not no_limit:
            version_total = version_info.get("_version_total", 0)
            changelog_total = version_info.get("_changelog_total", 0)
            if version_total > _MAX_VERSION_GROUPS:
                truncated["version_groups"] = {"returned": _MAX_VERSION_GROUPS, "total": version_total}
            if changelog_total > _MAX_CHANGELOGS:
                truncated["changelogs"] = {"returned": _MAX_CHANGELOGS, "total": changelog_total}

    if truncated:
        result["_truncated"] = truncated

    return result


@_cached(lambda username, max_results=10: ("search", _cache_key("author", username, max_results)))
def search_modrinth_author(username: str, max_results: int = 10) -> list[dict]:
    """Modrinth作者搜索。返回作者作品列表。"""
    q = urllib.parse.quote(username)
    # colon in filter=authors: must stay unencoded
    url = f"{_MODRINTH_API}/search?query={q}&filter=authors:{q}&index=relevance&limit={max_results}"
    data = _fetch_json(url)
    if not data or "hits" not in data:
        return []

    results = []
    for hit in data.get("hits", []):
        results.append({
            "name": hit.get("title", ""),
            "name_en": hit.get("title", ""),
            "name_zh": "",
            "url": f"https://modrinth.com/mod/{hit.get('slug', '')}",
            "source": "modrinth",
            "source_id": hit.get("slug", ""),
            "type": hit.get("project_type", "mod"),
            "snippet": hit.get("description", ""),
        })
    return results


@_cached(lambda mod_id, project_id=None: ("mod", _cache_key("deps", mod_id)))
def get_mod_dependencies(mod_id: str, project_id: str = None) -> dict:
    """
    获取 mod 正向依赖（从最新版本提取）。
    返回 {"deps": {mod_slug: {id, name, slug, client_side, server_side, url}}}
    失败时返回 {"deps": {}, "_error": "not_found"}
    """
    if not project_id:
        proj = _fetch_json(f"{_MODRINTH_API}/project/{mod_id}")
        if not proj:
            return {"deps": {}, "_error": "not_found"}
        project_id = proj.get("id", mod_id)

    # 获取最新版本的正向依赖（?limit=1 保证返回最新版本）
    versions = _fetch_json(
        f"{_MODRINTH_API}/project/{project_id}/version?limit=1", default=[])
    if not versions:
        return {"deps": {}, "_error": "not_found"}

    latest = versions[0] if isinstance(versions, list) else versions
    dep_entries = latest.get("dependencies", [])

    # 过滤：仅保留正向依赖（排除 incompatible）
    valid_ids = []
    for dep in dep_entries:
        if dep.get("dependency_type", "") in ("required", "optional", "embedded"):
            pid = dep.get("project_id", "")
            if pid:
                valid_ids.append(pid)

    if not valid_ids:
        return {"deps": {}}

    # 批量获取依赖项目元数据（1 次 API 调用替代 N 次）
    ids_json = json.dumps(valid_ids)
    dep_projects = _fetch_json(
        f"{_MODRINTH_API}/projects?ids={urllib.parse.quote(ids_json)}",
        default=[])
    if not dep_projects:
        return {"deps": {}}

    deps = {}
    for dp in dep_projects:
        slug = dp.get("slug", "")
        dep_id = dp.get("id", "")
        key = slug or dep_id
        deps[key] = {
            "name": dp.get("title", slug or dep_id),
            "slug": slug,
            "id": dep_id,
            "client_side": dp.get("client_side", "unknown"),
            "server_side": dp.get("server_side", "unknown"),
            "url": f"https://modrinth.com/mod/{slug}" if slug else None,
        }

    return {"deps": deps}


# ═══════════════════════════════════════════════════════════════
# Wiki 解析（搜索/读取/infobox/段落）
# ═══════════════════════════════════════════════════════════════

def _clean_wiki_segment(segment: str) -> str:
    """清理 wiki HTML 片段：移除脚本/样式/媒体标签和 wiki 标记，返回纯文本。"""
    segment = re.sub(r'<script[^>]*>.*?</script>', ' ', segment, flags=re.DOTALL)
    segment = re.sub(r'<style[^>]*>.*?</style>', ' ', segment, flags=re.DOTALL)
    segment = re.sub(r'<img[^>]*/?>', ' ', segment, flags=re.IGNORECASE)
    segment = re.sub(r'<source[^>]*/?>', ' ', segment, flags=re.IGNORECASE)
    segment = re.sub(r'<picture[^>]*/?>', ' ', segment, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', segment)
    text = re.sub(r'<[a-zA-Z]\w*\s[^>]*', ' ', text)
    text = re.sub(r'\[\[[^\]]*\]\]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _wiki_extract_snippet(html: str, start: int, source: str = "") -> tuple[str, str]:
    """从 wiki 页面提取描述 snippet。返回 (snippet, snippet_source)。

    直接解析 <p> 标签内容，而非将整个 HTML 压缩为单行，
    以避免 infobox/nav 等非正文内容混入。
    """
    snippet = ""
    snippet_source = ""

    # 限制搜索范围，避免扫描整页
    segment = html[start:start+_WIKI_FALLBACK_SEGMENT_LEN]

    # 判断 <p> 标签内容是否为有效描述文本
    def _is_valid_snippet_para(text: str) -> bool:
        if not text or len(text) < _MIN_SNIPPET_LINE_LEN:
            return False
        # 跳过 JSON/模板数据（英文 wiki infobox 用 <p> 包裹 JSON）
        if text.startswith('{') or text.startswith('['):
            return False
        # 跳过消歧义行
        if any(kw in text.lower() for kw in _WIKI_SNIPPET_SKIP_KEYWORDS):
            return False
        return True

    # 方法1：intro 区域（第一个 heading 之前）的 <p> 标签
    heading_m = re.search(r'<h[234][^>]*id="[^"]+"[^>]*>', segment)
    intro_html = segment[:heading_m.start()] if heading_m else segment[:_WIKI_SNIPPET_SEGMENT_LEN]

    snippet_parts = []
    for p in re.findall(r"<p[^>]*>(.*?)</p>", intro_html, re.DOTALL):
        # 移除 <style> 块（保留其余内容）
        p = re.sub(r"<style[^>]*>.*?</style>", "", p, flags=re.DOTALL | re.IGNORECASE)
        if re.search(r"<script|application/ld\+json", p, re.IGNORECASE):
            continue
        clean = _clean_html_text(p)
        if _is_valid_snippet_para(clean):
            snippet_parts.append(clean)
            if sum(len(p) for p in snippet_parts) >= _MAX_SEARCH_DESC_CHARS:
                break

    # 方法2：intro 区域无有效段落时，扫描更大范围的所有 <p>（跳过 infobox）
    # 英文 wiki 页面的描述性段落常在 infobox/TOC 之后（可达 30000+ 字符）
    if not snippet_parts:
        large_segment = html[start:start+_WIKI_FULL_SCAN_LEN]
        for p in re.findall(r"<p[^>]*>(.*?)</p>", large_segment, re.DOTALL):
            p = re.sub(r"<style[^>]*>.*?</style>", "", p, flags=re.DOTALL | re.IGNORECASE)
            if re.search(r"<script|application/ld\+json", p, re.IGNORECASE):
                continue
            clean = _clean_html_text(p)
            if _is_valid_snippet_para(clean):
                snippet_parts.append(clean)
                if sum(len(p) for p in snippet_parts) >= _MAX_SEARCH_DESC_CHARS:
                    break

    if snippet_parts:
        snippet = ' '.join(snippet_parts)[:_MAX_SEARCH_DESC_CHARS]
        snippet_source = "intro"

    # Fallback：CJK 连续片段（中文 wiki）
    if not snippet:
        large_text = _clean_wiki_segment(html[start:start+_WIKI_FALLBACK_SEGMENT_LEN])
        cjk_segments = re.findall(r'[\u4e00-\u9fff]{10,}', large_text)
        if cjk_segments:
            for seg in cjk_segments[:_MAX_CJK_FALLBACK_SEGMENTS]:
                if len(seg) > _MIN_CJK_SEGMENT_LEN:
                    snippet = seg[:_MAX_SEARCH_DESC_CHARS]
                    snippet_source = "fallback"
                    break

    # Fallback：英文句子片段（英文 wiki 页面内容主要在表格/列表时，<p> 标签可能为空）
    if not snippet:
        large_text = _clean_wiki_segment(html[start:start+_WIKI_FULL_SCAN_LEN])
        # 找第一个包含字母的连续英文片段（至少 40 字符）
        en_segments = re.findall(r'[A-Za-z][\w\s,;:\-\'\"()]{40,}', large_text)
        for seg in en_segments[:3]:
            seg = seg.strip()
            # 排除 CSS/JS 残留
            if not any(kw in seg.lower() for kw in ('font-style', 'display:', 'margin-', 'padding-', 'background')):
                snippet = seg[:_MAX_SEARCH_DESC_CHARS]
                snippet_source = "fallback"
                break
    return snippet, snippet_source


def _add_variant_param(url: str) -> str:
    """为中文 wiki URL 添加 variant=zh-hans 参数。"""
    url = re.sub(r"[&?](?:amp;)?variant=zh-[a-z]+", "", url)
    separator = "&" if "?" in url else "?"
    return url + separator + "variant=zh-hans"


def _build_wiki_result(name, url, source, source_id, snippet, sections,
                       title_field=""):
    """构造 wiki 搜索结果 dict。title_field 决定哪个 name 字段填入标题。"""
    return {
        "name": name,
        "name_en": name if title_field == "name_en" else "",
        "name_zh": name if title_field == "name_zh" else "",
        "url": url,
        "source": source,
        "source_id": source_id,
        "type": "wiki",
        "sections": sections,
        "snippet": snippet,
    }


def _wiki_direct_access(
    html: str,
    base_url: str,
    source: str,
    title_field: str,
    add_variant: bool,
) -> list[dict] | None:
    """处理 wiki 直接访问（页面已找到）。返回结果列表或 None。"""
    m_title = re.search(r"<title>([^<]+)</title>", html)
    title_text = m_title.group(1) if m_title else ""
    is_direct = (
        'id="firstHeading"' in html
        and "Special:Search" not in title_text
        and "Search results" not in title_text
        and "的搜索结果" not in title_text
    )
    if not is_direct:
        return None

    canon_m = re.search(r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"', html)
    og_m = re.search(r'<meta[^>]+property="og:url"[^>]+content="([^"]+)"', html)
    article_url = canon_m.group(1) if canon_m else (og_m.group(1) if og_m else None)
    page_title = re.sub(r"\s*[–-]\s*(中文 )?Minecraft Wiki.*", "", title_text).strip()

    # 提取 h2 和 h3 标题
    headings = re.findall(r"<h([23])[^>]*>(.*?)</h\1>", html, re.DOTALL)
    sections = []
    for level, content in headings[:_MAX_WIKI_SECTIONS]:
        clean = re.sub(r"<[^>]+>", "", content).strip()
        if clean:
            prefix = "▸ " if level == "2" else "  - "
            sections.append(f"{prefix}{clean}")

    # 提取 snippet
    snippet = ""
    parser_output = re.search(r'<div[^>]+class="[^"]*mw-parser-output[^"]*"[^>]*>', html)
    if parser_output:
        snippet, _ = _wiki_extract_snippet(html, parser_output.end(), source)
    # 消歧义页：snippet 为空时补充提示
    if not snippet:
        meta_desc = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]+)"', html)
        if meta_desc and 'may refer to' in meta_desc.group(1):
            snippet = "Disambiguation page — lists entries sharing this name."

    if add_variant and article_url:
        article_url = _add_variant_param(article_url)

    result = _build_wiki_result(
        name=page_title,
        url=article_url or "",
        source=source,
        source_id=article_url.split("/")[-1] if article_url else "",
        snippet=snippet,
        sections=sections,
        title_field=title_field,
    )
    result["_direct_match"] = True
    return [result]


def _wiki_api_search(
    keyword: str,
    base_url: str,
    source: str,
    title_field: str,
    add_variant: bool,
    max_results: int,
) -> list[dict]:
    """MediaWiki API 搜索。返回结果列表。"""
    results = []
    q = urllib.parse.quote(keyword)
    api_url = f"{base_url}/api.php?action=query&list=search&srsearch={q}&format=json&srlimit={max_results}"
    raw = curl(api_url)
    if not raw:
        return results
    try:
        data = json.loads(raw)
        hits = data.get("query", {}).get("search", [])
        for hit in hits[:max_results]:
            title = hit.get("title", "")
            page_id = hit.get("pageid", 0)
            snippet = hit.get("snippet", "")
            clean_snippet = re.sub(r'<[^>]+>', '', snippet) if snippet else ""
            # 清理 MediaWiki 标记残留（[[|]]、[[...]] 等）
            clean_snippet = re.sub(r'\[\[[^]]*\|([^\]]*)\]\]', r'\1', clean_snippet)  # [[a|b]] → b
            clean_snippet = re.sub(r'\[\[([^\]]*)\]\]', r'\1', clean_snippet)          # [[a]] → a
            clean_snippet = re.sub(r'\[\[?\|?\]?\]?', '', clean_snippet)               # 残留 [[|]]
            clean_snippet = re.sub(r'\s+', ' ', clean_snippet).strip()
            article_url = f"{base_url}/w/{urllib.parse.quote(title.replace(' ', '_'))}"
            if add_variant:
                article_url = _add_variant_param(article_url)
            results.append(_build_wiki_result(
                name=title,
                url=article_url,
                source=source,
                source_id=str(page_id),
                snippet=clean_snippet,
                sections=[],
                title_field=title_field,
            ))
    except (json.JSONDecodeError, KeyError, AttributeError) as e:
        logger.warning(f"Wiki search parse failed for {keyword}: {e}")
    return results


@_cached(lambda keyword, base_url, cache_prefix, source, title_field, add_variant, max_results=5: ("search", _cache_key(cache_prefix, keyword, max_results)))
def _search_wiki_impl(
    keyword: str,
    base_url: str,
    cache_prefix: str,
    source: str,
    title_field: str,
    add_variant: bool,
    max_results: int = 5,
) -> list[dict]:
    """
    minecraft.wiki 搜索通用实现。

    参数:
        base_url: wiki 根 URL
        cache_prefix: 缓存 key 前缀
        source: source 字段值
        title_field: 标题填入哪个字段（"name_en" / "name_zh" / ""）
        add_variant: 是否添加 ?variant=zh-hans
    """
    results = []
    q = urllib.parse.quote(keyword)

    # 方法1：尝试直接访问（精确匹配，信息更丰富：有 sections、有真实 snippet）
    html = curl(f"{base_url}/w/Special:Search?search={q}&go=Go")
    if html and len(html) >= MIN_HTML_LEN:
        direct = _wiki_direct_access(
            html, base_url, source, title_field, add_variant)
        if direct:
            results.extend(direct)

    # 方法2：MediaWiki API 搜索（补充更多相关结果）
    api_results = _wiki_api_search(
        keyword, base_url, source, title_field, add_variant, max_results)

    # 去重：API 结果中与直接访问结果同名的跳过（URL 去重，避免繁简体同名问题）
    # 若直接命中 snippet 过短，用 API 结果的 snippet 补充
    if results:
        direct_urls = {_url_tail_key(r.get("url", ""))
                       for r in results}
        for r in api_results:
            r_url_key = _url_tail_key(r.get("url", ""))
            if r_url_key in direct_urls:
                # 同一页面：若直接命中 snippet 过短，用 API snippet 替换
                if len(r.get("snippet", "")) > _WIKI_SNIPPET_REPLACE_THRESHOLD:
                    for dr in results:
                        dr_url = _url_tail_key(dr.get("url", ""))
                        if dr_url == r_url_key and len(dr.get("snippet", "")) < _WIKI_SNIPPET_KEEP_THRESHOLD:
                            dr["snippet"] = r["snippet"]
            else:
                results.append(r)
    else:
        results = api_results

    return results


def search_wiki(keyword: str, max_results: int = 5) -> list[dict]:
    """minecraft.wiki 搜索（英文）。"""
    return _search_wiki_impl(
        keyword=keyword,
        base_url="https://minecraft.wiki",
        cache_prefix="wiki",
        source="minecraft.wiki",
        title_field="name_en",
        add_variant=False,
        max_results=max_results,
    )


def search_wiki_zh(keyword: str, max_results: int = 5) -> list[dict]:
    """minecraft.wiki/zh 中文 wiki 搜索。"""
    return _search_wiki_impl(
        keyword=keyword,
        base_url="https://zh.minecraft.wiki",
        cache_prefix="wiki_zh",
        source="minecraft.wiki/zh",
        title_field="name_zh",
        add_variant=True,
        max_results=max_results,
    )


def _extract_wiki_infobox(html: str) -> dict:
    """
    提取 wiki infobox 结构化数据（优先 table.infobox，支持多种格式）。

    尝试顺序:
    1. table.infobox（标准格式）
    2. div.infobox 内嵌套表格
    3. mw-parser-output 后第一个带 th 的表格
    4. 中文 wiki 分散表格（合并提取）
    """
    # 格式1: table.infobox（最可靠）
    infobox_html = _try_extract_standard_infobox(html)
    if infobox_html:
        return _parse_infobox_table(infobox_html)

    # 格式2: div.infobox 内有嵌套表格
    infobox_html = _try_extract_div_infobox(html)
    if infobox_html:
        return _parse_infobox_table(infobox_html)

    # 格式3: mw-parser-output 后的第一个表格
    infobox_html = _try_extract_first_table(html)
    if infobox_html:
        return _parse_infobox_table(infobox_html)

    # 格式4: div.infobox-rows（中文 wiki wiki.gg 新布局）
    zh_data = _try_extract_div_infobox_rows(html)
    if zh_data:
        return zh_data

    # 格式5: 中文 wiki 的分散表格（旧格式回退）
    zh_data = _try_extract_chinese_wiki_tables(html)
    if zh_data:
        return zh_data

    return {}


def _try_extract_standard_infobox(html: str) -> str | None:
    """尝试提取标准 table.infobox 格式。"""
    match = re.search(r'<table[^>]+class="[^"]*infobox[^"]*"[^>]*>(.*?)</table>', html, re.DOTALL)
    return match.group(1) if match else None


def _try_extract_div_infobox(html: str) -> str | None:
    """尝试提取 div.infobox 内嵌套的表格。"""
    div_match = re.search(r'<div[^>]+class="[^"]*infobox[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
    if not div_match:
        return None

    div_content = div_match.group(1)
    table_in_div = re.search(r'<table[^>]*>(.*?)</table>', div_content, re.DOTALL)
    return table_in_div.group(1) if table_in_div else None


def _try_extract_first_table(html: str) -> str | None:
    """尝试提取 mw-parser-output 后的第一个带 th 的表格。"""
    parser = re.search(r'<div[^>]+class="mw-content-ltr mw-parser-output"', html)
    if not parser:
        parser = re.search(r'<div[^>]+class="[^"]*mw-parser-output[^"]*"[^>]*>', html)
    if not parser:
        return None

    segment = html[parser.end():parser.end()+_WIKI_FIRST_TABLE_SEGMENT_LEN]
    first_table = re.search(r'<table[^>]*>(.*?)</table>', segment, re.DOTALL)
    if first_table and '<th' in first_table.group(0):
        return first_table.group(1)
    return None


def _try_extract_div_infobox_rows(html: str) -> dict | None:
    """提取中文 wiki div.infobox-rows 格式（wiki.gg 新布局）。

    结构: <div class="infobox-rows"> → <div class="infobox-row">
           → <div class="infobox-row-label">标签</div>
           → <div class="infobox-row-field">值</div>
    """
    rows_start = re.search(r'<div[^>]+class="[^"]*infobox-rows[^"]*"[^>]*>', html)
    if not rows_start:
        return None

    # 从 infobox-rows 起，逐层匹配找到闭合标签
    pos = rows_start.start()
    depth = 0
    rows_html = ""
    for m in re.finditer(r'</?div[^>]*>', html[pos:], re.IGNORECASE):
        if m.group().startswith('</'):
            depth -= 1
        else:
            depth += 1
        if depth == 0:
            rows_html = html[pos + len(rows_start.group()):pos + m.start()]
            break

    if not rows_html:
        return None

    # 从 rows_html 中提取每个 infobox-row 的标签和值
    data = {}
    for row_start in re.finditer(r'<div[^>]+class="[^"]*infobox-row\b[^"]*"[^>]*>', rows_html):
        rpos = row_start.end()
        rdepth = 1
        row_inner = ""
        for m in re.finditer(r'</?div[^>]*>', rows_html[rpos:], re.IGNORECASE):
            if m.group().startswith('</'):
                rdepth -= 1
            else:
                rdepth += 1
            if rdepth == 0:
                row_inner = rows_html[rpos:rpos + m.start()]
                break

        label_m = re.search(r'<div[^>]+class="[^"]*infobox-row-label[^"]*"[^>]*>(.*?)</div>', row_inner, re.DOTALL)
        field_m = re.search(r'<div[^>]+class="[^"]*infobox-row-field[^"]*"[^>]*>(.*?)</div>', row_inner, re.DOTALL)
        if label_m:
            key = _clean_html_text(label_m.group(1))
            value = _clean_html_text(field_m.group(1)) if field_m else ""
            if key and len(key) < 30 and not key.startswith(('{{', '{|')):
                data[key] = value
    return data if data else None


def _try_extract_chinese_wiki_tables(html: str) -> dict | None:
    """尝试提取中文 wiki 的分散表格（合并多个相关表格）。"""
    if not any(marker in html for marker in ['zh-Hant', 'zh-Hans', '中文']):
        return None

    key_fields = ['名稱', '稀有度', '耐久度', '攻擊', '防御', '生命值']
    all_tables = re.findall(r'<table[^>]*>(.*?)</table>', html, re.DOTALL)

    data = {}
    for table in all_tables:
        if not any(kw in table for kw in key_fields) or '<th' not in table:
            continue

        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table, re.DOTALL)
        for row in rows:
            cells = re.findall(r'<(th|td)[^>]*>(.*?)</\1>', row, re.DOTALL)
            if len(cells) == 2:
                key = _clean_html_text(cells[0][1])
                value = _clean_html_text(cells[1][1])
                if key and not key.startswith(('{{', '{|', 'Module:')) and len(key) < 30:
                    data[key] = value

    return data if data else None


def _parse_infobox_table(infobox_html: str) -> dict:
    """解析 infobox 表格的 key-value 对。"""
    data = {}
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', infobox_html, re.DOTALL)
    for row in rows:
        cells = re.findall(r'<(th|td)[^>]*>(.*?)</\1>', row, re.DOTALL)
        if len(cells) == 2:
            key = _clean_html_text(cells[0][1])
            value = _clean_html_text(cells[1][1])
            if key and not key.startswith(('{{', '{|', 'Module:')):
                data[key] = value
    return data


def _extract_main_image(html: str) -> str:
    """提取页面主要图片（infobox 图片）。"""
    img = re.search(
        r'<div[^>]+class="[^"]*infobox[^"]*"[^>]*>.*?'
        r'<img[^>]+src="([^"]+)"',
        html, re.DOTALL
    )
    if img:
        return img.group(1)

    img = re.search(
        r'<div[^>]+id="mw-content-text"[^>]*>.*?'
        r'<img[^>]+src="([^"]+)"',
        html, re.DOTALL
    )
    return img.group(1) if img else ""


def _strip_infobox_region(html: str) -> str:
    """切除 div.infobox / div.notaninfobox 区域（避免 rail 模块数据泄漏）。"""
    infobox_start = re.search(r'<div[^>]+class="[^"]*(?:infobox|notaninfobox)[^"]*"[^>]*>', html)
    if not infobox_start:
        return html
    # 从 infobox div 起逐层匹配闭合标签
    pos = infobox_start.start()
    depth = 0
    for m in re.finditer(r'</?div[^>]*>', html[pos:], re.IGNORECASE):
        if m.group().startswith('</'):
            depth -= 1
        else:
            depth += 1
        if depth == 0:
            return html[:pos] + html[pos + m.end():]
    return html


def _extract_intro_paragraphs(content_html: str, para_skip_prefixes: tuple, source: str) -> list[str]:
    """提取 wiki 页面首段介绍（第一个 heading 之前的内容）。"""
    # 解析 heading 位置
    heading_map = []
    for m in re.finditer(r'<h([234])[^>]*id="([^"]+)"[^>]*>(.*?)</h\1>', content_html, re.DOTALL):
        heading_map.append(m.start())

    if not heading_map:
        return []

    # 提取第一个 heading 之前的段落
    first_heading_pos = heading_map[0]
    pre_heading_html = content_html[:first_heading_pos]

    # 切除 infobox/notaninfobox 区域（避免 rail 模块数据泄漏到首段）
    pre_heading_html = _strip_infobox_region(pre_heading_html)

    intro_paragraphs = []

    # 提取 hatnote/msgbox 消歧义提示（如 "本条目介绍的是..."）
    for note_div in re.findall(
        r'<div[^>]+(?:class="[^"]*(?:hatnote|msgbox|ambox)[^"]*"|role="note")[^>]*>(.*?)</div>',
        pre_heading_html, re.DOTALL
    ):
        clean = _clean_html_text(note_div)
        if len(clean) >= 4 and not clean.startswith("Wiki上"):
            intro_paragraphs.append(clean)

    for p in re.findall(r"<p[^>]*>(.*?)</p>", pre_heading_html, re.DOTALL):
        if re.search(r"<script|application/ld\+json", p, re.IGNORECASE):
            continue
        clean = _clean_html_text(p)
        if any(clean.startswith(prefix) for prefix in para_skip_prefixes):
            continue
        if _is_valid_paragraph(clean, lang="en" if source == "minecraft.wiki" else "zh"):
            intro_paragraphs.append(clean)
            if len(intro_paragraphs) >= _MAX_WIKI_INTRO_PARAGRAPHS:
                break

    return intro_paragraphs


def _extract_sections(
    content_html: str,
    heading_skip_ids: set[str],
    para_skip_prefixes: tuple[str, ...],
    source: str,
    intro_paragraphs: list[str],
    max_paragraphs: int
) -> tuple[list[dict], list[str]]:
    """解析 heading 并提取所有章节内容。"""
    # 解析 heading 映射
    heading_map = []
    for m in re.finditer(r'<h([234])[^>]*id="([^"]+)"[^>]*>(.*?)</h\1>', content_html, re.DOTALL):
        lvl = int(m.group(1))
        h_id = m.group(2)
        h_text = re.sub(r"<[^>]+>", "", m.group(3)).strip()
        heading_map.append((lvl, h_id, h_text, m.start()))

    sections_output = []
    paragraphs = list(intro_paragraphs)  # 先添加首段
    current_h2 = None

    for i, (lvl, h_id, h_text, h_start) in enumerate(heading_map):
        if h_id in heading_skip_ids:
            continue
        if lvl == 2:
            current_h2 = h_text
            # h2 也提取内容（之前跳过了 h2，导致"生成""用途"等大节内容丢失）

        next_start = heading_map[i + 1][3] if i + 1 < len(heading_map) else len(content_html)
        section_html = content_html[h_start:next_start]

        # 提取章节段落
        section_paragraphs = _extract_section_paragraphs(
            section_html, para_skip_prefixes, source
        )

        # 提取表格行
        table_rows = _extract_table_items_from_section(section_html, source, len(section_paragraphs))

        # 构建章节输出
        section_lines = section_paragraphs[:_MAX_SECTION_PARAGRAPHS]
        if table_rows and not section_paragraphs:
            # 纯表格章节：直接展开表格行
            section_lines = table_rows[:_MAX_SECTION_PARAGRAPHS]
        elif table_rows:
            # 有段落也有表格：追加表格行
            section_lines.extend(table_rows[:_MAX_SECTION_PARAGRAPHS - len(section_lines)])

        if section_lines:
            sections_output.append({
                "heading": h_text,
                "parent": current_h2,
                "content": section_lines,
            })
            paragraphs.extend(section_lines)

        # 支持-1表示无限制
        if max_paragraphs > 0 and len(paragraphs) >= max_paragraphs:
            paragraphs = paragraphs[:max_paragraphs]
            break

    return sections_output, paragraphs


def _extract_section_paragraphs(section_html: str, para_skip_prefixes: tuple, source: str) -> list[str]:
    """提取单个章节的段落内容。"""
    section_paragraphs = []
    for p in re.findall(r"<p[^>]*>(.*?)</p>", section_html, re.DOTALL):
        if re.search(r"<script|application/ld\+json", p, re.IGNORECASE):
            continue
        clean = _clean_html_text(p)
        if any(clean.startswith(prefix) for prefix in para_skip_prefixes):
            continue
        if _is_valid_paragraph(clean, lang="en" if source == "minecraft.wiki" else "zh"):
            section_paragraphs.append(clean)
            if len(section_paragraphs) >= _MAX_SECTION_PARAGRAPHS:
                break

    # 英文 wiki：从 <li> 中提取描述性条目（始终提取，不仅回退）
    if source == "minecraft.wiki":
        for li in re.findall(r"<li[^>]*>(.*?)</li>", section_html, re.DOTALL):
            clean = _clean_html_text(li)
            if len(clean) >= _MIN_DESCRIPTIVE_LI_LEN and re.match(
                    r"^(Added|Changed|Fixed|Updated|Removed|Introduced|Can now|Made|New|Affects?|Allows?|Prevents?|Makes?|Provides?)", clean):
                section_paragraphs.append(clean)
                if len(section_paragraphs) >= _MAX_SECTION_PARAGRAPHS:
                    break

    # 中文 wiki：始终从 <li> 提取（不少章节用 <ul>/<li> 承载主要内容）
    if source != "minecraft.wiki":
        for li in re.findall(r"<li[^>]*>(.*?)</li>", section_html, re.DOTALL):
            clean = _clean_html_text(li)
            if len(clean) >= 4 and not clean.startswith(("[", "编辑", "注：")):
                section_paragraphs.append(clean)
                if len(section_paragraphs) >= _MAX_SECTION_PARAGRAPHS:
                    break

    # <dd> 提取：命令参考页用 <dl>/<dd> 承载主要语法内容
    for dd in re.findall(r"<dd[^>]*>(.*?)</dd>", section_html, re.DOTALL):
        clean = _clean_html_text(dd)
        if len(clean) >= 4 and not clean.startswith(("[", "编辑", "注：")):
            section_paragraphs.append(clean)
            if len(section_paragraphs) >= _MAX_SECTION_PARAGRAPHS:
                break

    # <dt> 提取：定义列表术语（如命令参数名、附魔等级标签）
    for dt in re.findall(r"<dt[^>]*>(.*?)</dt>", section_html, re.DOTALL):
        clean = _clean_html_text(dt)
        if len(clean) >= 3 and not clean.startswith(("[", "编辑", "注：")):
            section_paragraphs.append(clean)
            if len(section_paragraphs) >= _MAX_SECTION_PARAGRAPHS:
                break

    # <pre> 提取：代码块、数据组件 JSON、战利品表
    for pre in re.findall(r"<pre[^>]*>(.*?)</pre>", section_html, re.DOTALL):
        clean = _clean_html_text(pre)
        if len(clean) >= 20:
            section_paragraphs.append(clean)
            if len(section_paragraphs) >= _MAX_SECTION_PARAGRAPHS:
                break

    return section_paragraphs


def _extract_table_items_from_section(section_html: str, source: str, current_para_count: int) -> list[str]:
    """从章节中提取表格行内容（每行所有列，用 | 分隔）。"""
    table_rows = []
    if current_para_count < _MAX_SECTION_PARAGRAPHS:
        tables = re.findall(r'<table[^>]*class="[^"]*(?:wikitable|id-table|datatable)[^"]*"[^>]*>.*?</table>', section_html, re.DOTALL)
        for tbl in tables[:_MAX_TABLES_PER_SECTION]:
            rows = _extract_table_rows(tbl, max_rows=_MAX_TABLE_ITEMS)
            table_rows.extend(rows)
    return table_rows


def _extract_table_rows(table_html: str, max_rows: int = 50) -> list[str]:
    """从 wiki table 中提取每行完整内容（多列用 | 分隔）。"""
    rows_out = []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.DOTALL)
    for row in rows[1:]:  # 跳过表头行
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
        if not cells:
            continue
        cleaned = []
        for cell in cells:
            text = _clean_html_text(cell).strip()
            # 清理 MediaWiki 残留标记
            text = re.sub(r'\[\[[^]]*\|([^\]]*)\]\]', r'\1', text)  # [[a|b]] → b
            text = re.sub(r'\[\[([^\]]*)\]\]', r'\1', text)          # [[a]] → a
            text = re.sub(r'\[\[?\|?\]?\]?', '', text)               # 残留 [[|]]
            text = text.strip()
            if not text or re.match(r"^[\s\-\d]+$", text):
                continue
            # 过滤 CSS/样式代码泄漏
            if re.match(r"\.\w+-", text) or "{" in text or "display:" in text:
                continue
            cleaned.append(text)
        if not cleaned:
            continue
        # 单列直接输出，多列用 | 分隔
        if len(cleaned) == 1:
            rows_out.append(cleaned[0])
        else:
            rows_out.append(" | ".join(cleaned))
        if len(rows_out) >= max_rows:
            break
    return rows_out


def _extract_disambig_links(content_html: str, source: str) -> list[str]:
    """从消歧义页面提取 <li> 链接条目。"""
    links = []
    # 移除 TOC（id="toc"）
    content = re.sub(r'<div[^>]+id="toc"[^>]*>.*?</div>', '', content_html, flags=re.DOTALL)
    for li in re.findall(r'<li[^>]*>(.*?)</li>', content, re.DOTALL):
        # 跳过 TOC 条目（包含 tocnumber / toctext）
        if 'tocnumber' in li or 'toctext' in li:
            continue
        clean = _clean_html_text(li)
        if len(clean) >= 10:
            links.append(clean)
    return links


def _read_wiki_impl(url: str, max_paragraphs: int,
                    para_skip_prefixes: tuple[str, ...],
                    heading_skip_ids: set[str],
                    source: str,
                    include_infobox: bool = True) -> dict:
    """
    读取 wiki 页面正文（英文 / 中文共用实现）。

    参数：
      para_skip_prefixes: 段落前缀跳过词（如 "History of", "v ", "历史", "编辑"）
      heading_skip_ids:   heading id 跳过集合
      source:             返回结果的 source 字段值
      include_infobox:    是否在结果中包含 infobox 数据
    """
    cache_key = _cache_key("wiki_read", url, str(max_paragraphs), source)
    cached = _cache_get("wiki_read", cache_key)
    if cached is not None:
        return cached

    html = curl(url)
    if not html or len(html) < MIN_HTML_LEN_ITEM:
        return {"_error": "no_content"}

    m_title = re.search(r'<h1[^>]*id="firstHeading"[^>]*>(.*?)</h1>', html, re.DOTALL)
    title = _clean_html_text(m_title.group(1)) if m_title else "UNKNOWN"

    # 提取 infobox 结构化数据（在移除之前）
    infobox_data = _extract_wiki_infobox(html)

    # 提取主要图片
    main_image = _extract_main_image(html)

    m_content = re.search(
        r'<div[^>]+id="mw-content-text"[^>]*>(.*?)'
        r'(?:<div[^>]+class="[^"]*(?<![a-z-])navbox(?![a-z-])[^"]*"|<div[^>]+id="catlinks|<div[^>]+class="[^"]*printfooter)',
        html, re.DOTALL
    )
    if not m_content:
        return {"_error": "no_content"}

    content_html = m_content.group(1)

    content_html = re.sub(
        r'<script[^>]+type="application/ld\+json"[^>]*>.*?</script>',
        "", content_html, flags=re.DOTALL
    )
    # 移除 navbox（infobox 已提取，不再需要特殊处理）
    content_html = re.sub(
        r'<table[^>]+class="[^"]*navbox[^"]*"[^>]*>.*?</table>',
        "", content_html, flags=re.DOTALL
    )

    # 提取首段介绍
    intro_paragraphs = _extract_intro_paragraphs(content_html, para_skip_prefixes, source)

    # 解析 heading 并提取章节内容
    sections_output, paragraphs = _extract_sections(
        content_html, heading_skip_ids, para_skip_prefixes,
        source, intro_paragraphs, max_paragraphs
    )

    # 消歧义页面：无段落/章节时提取 <li> 链接列表
    is_disambig = False
    if not paragraphs and not sections_output:
        meta_desc = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]+)"', html)
        if meta_desc and 'may refer to' in meta_desc.group(1):
            is_disambig = True
            paragraphs = _extract_disambig_links(content_html, source)
            if paragraphs:
                sections_output = [{"heading": "Disambiguation", "parent": None, "content": paragraphs}]

    result = {
        "name": title,
        "url": url,
        "source": source,
        "language": "zh" if "minecraft.wiki/zh" in source or "zh.minecraft.wiki" in source else "en",
        "content": paragraphs,
        "_sections": sections_output,
    }

    # 添加 infobox 结构化数据（如果有）
    if infobox_data:
        result["infobox"] = infobox_data

    # 添加主要图片（如果有）
    if main_image:
        result["main_image"] = main_image

    if is_disambig:
        result["is_disambiguation"] = True
    if not include_infobox and "infobox" in result:
        del result["infobox"]

    _cache_set("wiki_read", cache_key, result)
    return result


def read_wiki(url: str, max_paragraphs: int = -1, include_infobox: bool = True) -> dict:
    """读取minecraft.wiki英文页面正文。"""
    return _read_wiki_impl(
        url, max_paragraphs,
        para_skip_prefixes=("History of", "v ", "[edit"),
        heading_skip_ids=_WIKI_HEADING_SKIP_IDS,
        source="minecraft.wiki",
        include_infobox=include_infobox,
    )


def read_wiki_zh(url: str, max_paragraphs: int = -1, include_infobox: bool = True) -> dict:
    """读取minecraft.wiki/zh中文wiki页面正文。"""
    url = _add_variant_param(url)  # 确保返回简体中文
    return _read_wiki_impl(
        url, max_paragraphs,
        para_skip_prefixes=("历史", "编辑", "请帮助", "History of", "v ", "[edit"),
        heading_skip_ids=_WIKI_ZH_HEADING_SKIP_IDS,
        source="minecraft.wiki/zh",
        include_infobox=include_infobox,
    )


# ═══════════════════════════════════════════════════════════════
# 聚合搜索 + 桥接 + 融合管线
# ═══════════════════════════════════════════════════════════════

def search_all(keyword: str, max_per_source: int | None = None, timeout: int = 15,
               content_type: str = "mod", fuse: bool = True) -> dict:
    """
    四平台并行搜索，返回统一格式。
    timeout: 整体超时秒数
    content_type: "mod" | "item" | "modpack" | "vanilla" | "entity" | "biome" | "dimension" | "shader" | "resourcepack"
      - 同时决定每平台最大结果数（_DEFAULT_RESULTS_PER_PLATFORM）
      - shader/resourcepack 仅搜索 Modrinth
      - modpack 仅搜索 MC百科 + Modrinth
    fuse: True 时返回 {"results": [...融合列表...], "platform_stats": {platform: {total, returned}}}
         False 时返回 {platform: [results]}（向后兼容）
    """
    if not keyword or not keyword.strip():
        return {"results": [], "platform_stats": {}}

    per_source = max_per_source if max_per_source is not None else _DEFAULT_RESULTS_PER_PLATFORM

    # 1. 并行调度各平台搜索
    results, stats = _dispatch_platform_search(keyword, per_source, content_type, timeout)

    # 2. CJK 跨语言桥接（中文关键词 → MC百科 name_en → Modrinth 补搜）
    if fuse and _is_cjk(keyword):
        _apply_cjk_bridge(results, stats, keyword, per_source)

    # 3. 结果融合或原样返回
    if fuse:
        fused = _fuse_results(results, content_type=content_type, query_keyword=keyword)
        return {"results": fused, "platform_stats": stats}
    return results


def _dispatch_platform_search(keyword: str, per_source: int, content_type: str, timeout: int
                              ) -> tuple[dict, dict]:
    """并行调度各平台搜索，返回 (results, stats)。"""
    results = {"mcmod.cn": [], "modrinth": [], "minecraft.wiki": [], "minecraft.wiki/zh": []}
    stats = {p: {"total": 0, "returned": 0} for p in results}

    pe = _platform_enabled.copy()
    if content_type in _VISUAL_CONTENT_TYPES:
        pe["mcmod.cn"] = pe["minecraft.wiki"] = pe["minecraft.wiki/zh"] = False
    elif content_type in ("mod", "modpack"):
        pe["minecraft.wiki"] = pe["minecraft.wiki/zh"] = False
    else:
        # vanilla / entity / biome / dimension → 仅 wiki
        pe["mcmod.cn"] = pe["modrinth"] = False

    def _wrap_mcmod():
        try:
            ct = content_type if content_type in _TEXT_CONTENT_TYPES else "mod"
            if content_type not in _TEXT_CONTENT_TYPES:
                logger.debug(f"MC百科不支持 content_type={content_type}，降级为 mod")
            return search_mcmod(keyword, per_source, content_type=ct)
        except (SearchError, OSError) as e:
            logger.warning(f"MC百科搜索失败: {e}")
            return []

    def _wrap_modrinth():
        try:
            mr_type = content_type if content_type in _MODRINTH_CONTENT_TYPES else "mod"
            return search_modrinth(keyword, per_source, project_type=mr_type)
        except (SearchError, OSError) as e:
            logger.warning(f"Modrinth搜索失败: {e}")
            return _EMPTY_MODRINTH_RESULT

    def _wrap_wiki():
        try:
            return search_wiki(keyword, per_source)
        except (SearchError, OSError) as e:
            logger.warning(f"Wiki搜索失败: {e}")
            return []

    def _wrap_wiki_zh():
        try:
            return search_wiki_zh(keyword, per_source)
        except (SearchError, OSError) as e:
            logger.warning(f"中文Wiki搜索失败: {e}")
            return []

    workers = []
    futures_map = {}
    with futures_module.ThreadPoolExecutor(max_workers=_MAX_FETCH_WORKERS) as ex:
        if pe.get("mcmod.cn", False):
            f = ex.submit(_wrap_mcmod)
            futures_map[f] = "mcmod.cn"
            workers.append(f)
        if pe.get("modrinth", False):
            f = ex.submit(_wrap_modrinth)
            futures_map[f] = "modrinth"
            workers.append(f)
        if pe.get("minecraft.wiki", False):
            f = ex.submit(_wrap_wiki)
            futures_map[f] = "minecraft.wiki"
            workers.append(f)
        if pe.get("minecraft.wiki/zh", False):
            f = ex.submit(_wrap_wiki_zh)
            futures_map[f] = "minecraft.wiki/zh"
            workers.append(f)

        for future in futures_module.as_completed(workers):
            key = futures_map[future]
            try:
                raw = future.result(timeout=timeout)
            except (futures_module.TimeoutError, OSError, SearchError) as e:
                logger.warning(f"平台 {key} 获取结果失败: {e}")
                raw = [] if key != "modrinth" else _EMPTY_MODRINTH_RESULT

            if key == "modrinth" and isinstance(raw, dict):
                results[key] = raw.get("results", [])
                stats[key] = {"total": raw.get("total", 0), "returned": raw.get("returned", 0)}
            else:
                results[key] = raw if isinstance(raw, list) else []
                stats[key] = {"total": len(results[key]), "returned": len(results[key])}

        for f in workers:
            f.cancel()

    return results, stats


def _apply_cjk_bridge(results: dict, stats: dict, keyword: str, per_source: int):
    """中文关键词用 MC百科 name_en 补搜 Modrinth，去重后合并。"""
    bridge_hits = _cross_language_bridge(results["mcmod.cn"], results["modrinth"], keyword, per_source)
    if bridge_hits:
        existing_slugs = {h.get("source_id", "") for h in results["modrinth"]}
        new_hits = [h for h in bridge_hits if h.get("source_id", "") not in existing_slugs]
        results["modrinth"].extend(new_hits)
        stats["modrinth"]["total"] = stats["modrinth"]["returned"] = len(results["modrinth"])


def _is_cjk(text: str) -> bool:
    """检测文本是否包含 CJK 字符。"""
    return bool(re.search(r'[\u4e00-\u9fff]', text))


def _cross_language_bridge(mcmod_hits: list, mr_hits: list, keyword: str, per_source: int) -> list:
    """从 MC百科 + bbsmc(MR) 结果提取英文名去 Modrinth 补搜。"""
    if not mcmod_hits and not mr_hits:
        return []

    # 提取英文名候选（去重，最多 per_source 个）
    en_names = set()
    # 源1: MC百科 name_en
    for hit in mcmod_hits:
        en = (hit.get("name_en") or "").strip()
        if en:
            en_names.add(en.lower())
    # 源2: bbsmc 双语名 "中文名 - EnglishName" → 提取英文部分
    for hit in mr_hits:
        name_zh = (hit.get("name_zh") or "").strip()
        if " - " in name_zh:
            en_part = name_zh.rsplit(" - ", 1)[-1].strip()
            if en_part and not _is_cjk(en_part):
                en_names.add(en_part.lower())
    if not en_names:
        logger.debug("Cross-language bridge: no English names extracted")
        return []

    # 每个英文名搜 Modrinth（限 2 结果/名，控制请求量 + 去重）
    all_hits = {}
    mr_limit = min(per_source, 2)
    for en_name in list(en_names)[:per_source]:
        try:
            mr_result = search_modrinth(en_name, max_results=mr_limit, project_type="mod")
        except (SearchError, OSError) as e:
            logger.debug(f"Bridge Modrinth search failed for {en_name}: {e}")
            continue
        for hit in mr_result.get("results", []):
            slug = hit.get("source_id", "")
            if slug and slug not in all_hits:
                all_hits[slug] = hit

    if all_hits:
        logger.debug(f"Cross-language bridge: {len(en_names)} en names -> {len(all_hits)} Modrinth hits")
    return list(all_hits.values())


def _calc_name_score(name_lc: str, query_lc: str) -> int:
    """
    计算单个名称字段的相关性分数（使用常量）。

    评分逻辑:
    - 精确匹配: 100 + 短名称奖励
    - 前缀匹配: 60 + 短名称奖励（ASCII 查询需词边界，防止 "spawn" 匹配 "spawning"）
    - 全词匹配: 45（仅 ASCII，防止 "OreSpawn" 匹配 "spawn"）
    - 包含查询词: 30 + 位置奖励
    - 名称被包含: 20
    """
    if not name_lc or not query_lc:
        return 0

    # 连字符归一化：将 - 替换为空格，使 "fabric-api" 能匹配 "Fabric API"
    name_norm = name_lc.replace("-", " ")
    query_norm = query_lc.replace("-", " ")

    # 1. 精确匹配（同时检查原始和归一化版本）
    if name_lc == query_lc or name_norm == query_norm:
        bonus = max(0, MatchScore.EXACT_MATCH_MAX_BONUS - len(name_lc) * MatchScore.EXACT_MATCH_BONUS_FACTOR)
        return MatchScore.EXACT_MATCH_BASE + bonus

    # 词边界检查仅对纯 ASCII 查询生效（CJK 无空格分界概念）
    _ascii = query_lc.isascii()

    # 2. 前缀匹配（归一化版本：连字符→空格）
    if name_norm.startswith(query_norm):
        if not _ascii or len(query_norm) >= len(name_norm) or not name_norm[len(query_norm)].isalnum():
            bonus = max(0, MatchScore.PREFIX_MAX_BONUS - len(query_lc) * MatchScore.PREFIX_BONUS_FACTOR)
            return MatchScore.PREFIX_BASE + bonus

    # 2.5 全词匹配（归一化版本）
    if _ascii:
        word_pat = re.compile(r'(?<![a-z0-9])' + re.escape(query_norm) + r'(?![a-z0-9])')
        if word_pat.search(name_norm):
            return MatchScore.WHOLE_WORD_BASE

    # 3. 包含查询词（归一化版本）
    pos = name_norm.find(query_norm)
    if pos >= 0:
        if not _ascii or (
            (pos == 0 or not name_norm[pos - 1].isalnum()) and
            (pos + len(query_norm) >= len(name_norm) or not name_norm[pos + len(query_norm)].isalnum())
        ):
            pos_bonus = max(0, MatchScore.CONTAINS_MAX_POS_BONUS - pos)
            return MatchScore.CONTAINS_BASE + pos_bonus

    # 4. 名称被包含
    if len(name_lc) >= MatchScore.MIN_LENGTH_FOR_CONTAINED and name_lc in query_lc:
        return MatchScore.CONTAINED_IN_QUERY

    return 0


def _score_relevance(query: str, hit: dict, content_type: str = "mod") -> float:
    """
    计算单条搜索结果与查询词的相关性分数（优化版，0-150+）。

    评分规则:
      - 主字段精确匹配: 100 + 短名称奖励(最多+20)
      - 主字段前缀匹配: 60 + 短名称奖励(最多+15)
      - 主字段包含查询词: 30 + 位置奖励(最多+10)
      - 主字段被包含于查询词: 20 (适合缩写搜索)
      - 次字段匹配: 同级别 -10 分
      - Snippet 包含查询词: +5
      - Wiki item 来源: +5
      - 多平台命中: 每多一个平台 +10 (在 _fuse_results 中计算)
    """
    if not query or not hit:
        return 0.0

    # 直接命中的 wiki 页面（通过 go=Go 跳转），给予高基础分
    if hit.get("_direct_match"):
        return MatchScore.EXACT_MATCH_BASE

    name_zh = (hit.get("name_zh") or "").lower()
    name_en = (hit.get("name_en") or "").lower()
    q = query.strip().lower()
    if not q:
        return 0.0

    # 1. 选择主要/次要评分字段
    primary = name_zh if _is_cjk(q) else name_en
    secondary = name_en if primary == name_zh else name_zh
    if not primary:
        primary, secondary = secondary, ""

    # 2. 计算名称分数
    score = _calc_name_score(primary, q)
    if score == 0 and secondary:
        score = _calc_name_score(secondary, q)
        if score > 0:
            score = max(score - MatchScore.SECONDARY_PENALTY, MatchScore.SECONDARY_MIN)

    # 3. Snippet 加分
    snippet = (hit.get("snippet") or "").lower()
    if snippet and q in snippet:
        score += MatchScore.SNIPPET_BONUS

    # 4. Wiki item 来源加分
    platform = hit.get("_platform", hit.get("source", ""))
    if content_type == "item" and platform in ("minecraft.wiki", "minecraft.wiki/zh"):
        score += MatchScore.WIKI_ITEM_BONUS

    # 5. MC百科 类别加权：冒险/装饰类常因名字巧合匹配，降低权重
    cats = hit.get("categories", [])
    if cats:
        for cat in cats:
            if cat in ("冒险Mod", "装饰Mod"):
                score -= 10
                break

    return score


def _fuse_results(results: dict, content_type: str = "mod", query_keyword: str = "") -> list[dict]:
    """
    跨平台去重合并，按相关性分数排序。

    排序规则：相关性分数 DESC → 多平台命中加权 → 平台优先级 ASC（tiebreaker）
    content_type 用于调整不同类型内容的平台优先级。
    """
    if content_type is None:
        content_type = "mod"

    # 步骤1: 打分并过滤
    scored = _score_and_filter(results, content_type, query_keyword)

    # 步骤2: 统计平台命中
    name_platform_count = _count_platform_hits(scored)

    # 步骤3: 去重
    by_name = _deduplicate_by_name(scored, name_platform_count)

    # 步骤4: 排序
    sorted_entries = _sort_entries(by_name)

    # 步骤5: 构建输出
    fused = _build_fused_output(sorted_entries, scored)

    # 步骤6: 标记本体（C→B→A 级联）
    fused = _mark_primary(fused, query_keyword)

    return fused


def _score_and_filter(results: dict, content_type: str, query_keyword: str) -> list[dict]:
    """步骤1: 给所有结果打分，同时过滤无关结果。"""
    prio_key = "default" if content_type in ("mod", "item") else "other"
    platform_prio = _CONTENT_PLATFORM_PRIORITY[prio_key]

    scored = []
    for platform, hits in results.items():
        for h in hits:
            # 过滤 MC百科 安全验证/限流空数据
            h_name = h.get("name_zh") or h.get("name") or ""
            if platform == "mcmod.cn" and h_name in ("安全验证", "安全验证中", "访问间隔过短，请稍后再试"):
                continue

            score = _score_relevance(query_keyword, h, content_type=content_type)
            # 过滤 wiki 无匹配结果（分数为 0）
            if content_type == "mod" and platform in ("minecraft.wiki", "minecraft.wiki/zh"):
                if score == 0:
                    continue

            priority = platform_prio.get(platform, 99)
            scored.append({**h, "_platform": platform, "_score": score, "_priority": priority})

    return scored


def _entry_name_keys(entry: dict) -> set[str]:
    """返回所有可用名称的标准化 key 集合（多候选，跨语言匹配）。"""
    keys = set()
    for field in ('name_zh', 'name_en', 'name', '_name_zh_cn'):
        v = entry.get(field)
        if v and isinstance(v, str) and v.strip():
            keys.add(v.strip().lower())
    return keys


def _count_platform_hits(scored: list[dict]) -> dict[frozenset, set]:
    """步骤2: 统计每个名称组在多少个平台出现。"""
    name_platform_count = {}
    for entry in scored:
        keys = _entry_name_keys(entry)
        if not keys:
            continue
        frozen = frozenset(keys)
        if frozen not in name_platform_count:
            name_platform_count[frozen] = set()
        name_platform_count[frozen].add(entry["_platform"])
    return name_platform_count


def _merge_entry_fields(entries: list[dict]) -> dict:
    """按字段级权威源合并同一实体的多个平台条目。"""
    if len(entries) == 1:
        entry = entries[0]
        if entry.get("relationships") is None:
            entry["relationships"] = {}
        return entry

    by_platform = {}
    for e in entries:
        src = e.get("_platform") or e.get("source", "")
        by_platform[src] = e

    def _field_from(primary_src, fallback_src, field):
        v = (by_platform.get(primary_src) or {}).get(field) or ""
        if field == "name_en" and _is_cjk(v):
            v = ""  # 拒绝含 CJK 的 name_en，回退到备选源
        return v or (by_platform.get(fallback_src) or {}).get(field) or ""

    # 以最高分条目为基础，覆盖权威字段
    base = max(entries, key=lambda e: e.get("_score", 0))
    merged = {
        "name_zh": _field_from("mcmod.cn", "modrinth", "name_zh"),
        "name_en": _field_from("modrinth", "mcmod.cn", "name_en"),
        "description": _field_from("modrinth", "mcmod.cn", "description"),
        "downloads": (by_platform.get("modrinth") or {}).get("downloads",
                     (by_platform.get("mcmod.cn") or {}).get("downloads", 0)),
        "followers": (by_platform.get("modrinth") or {}).get("followers",
                      (by_platform.get("mcmod.cn") or {}).get("followers", 0)),
        "relationships": (by_platform.get("mcmod.cn") or {}).get("relationships") or {},
        "snippet": _field_from("modrinth", "mcmod.cn", "snippet"),
        "icon_url": _field_from("modrinth", "mcmod.cn", "icon_url"),
        "changelogs": (by_platform.get("modrinth") or {}).get("changelogs")
                       or (by_platform.get("mcmod.cn") or {}).get("changelogs") or [],
        "supported_versions": _field_from("modrinth", "mcmod.cn", "supported_versions"),
        "author": _field_from("modrinth", "mcmod.cn", "author"),
    }
    return {**base, **merged}


def _deduplicate_by_name(scored: list[dict], name_platform_count: dict) -> dict[str, dict]:
    """步骤3: 多候选 key 去重。两结果任一 key 命中即视为同一内容。按字段级权威源合并。"""
    key_to_canonical = {}         # individual key → canonical key
    entries_by_canonical = {}     # canonical_key → [entry, ...]

    for entry in scored:
        entry_keys = _entry_name_keys(entry)
        if not entry_keys:
            continue

        canonical_key = None
        for k in entry_keys:
            if k in key_to_canonical:
                canonical_key = key_to_canonical[k]
                break

        if canonical_key is None:
            canonical_key = min(entry_keys)
            entries_by_canonical[canonical_key] = [entry]
            for k in entry_keys:
                key_to_canonical[k] = canonical_key
            continue

        entries_by_canonical[canonical_key].append(entry)
        for k in entry_keys:
            if k not in key_to_canonical:
                key_to_canonical[k] = canonical_key

    # 多平台命中加权 + 字段级权威源合并
    by_name = {}
    for canonical_key, entries in entries_by_canonical.items():
        # 多平台加权（对组内所有条目计分）
        all_keys = {k for k, c in key_to_canonical.items() if c == canonical_key}
        all_platforms = set()
        for k in all_keys:
            all_platforms.update(name_platform_count.get(frozenset([k]), set()))
        platform_count = len(all_platforms)

        merged = _merge_entry_fields(entries)
        if platform_count > 1:
            merged["_score"] += (platform_count - 1) * MatchScore.MULTI_PLATFORM_BONUS
        by_name[canonical_key] = merged

    return by_name


def _sort_entries(by_name: dict[str, dict]) -> list[dict]:
    """步骤4: 排序（分数 DESC，同分时 priority ASC 即高优先级在前）。"""
    return sorted(by_name.values(), key=lambda e: (e["_score"], -e["_priority"]), reverse=True)


def _build_fused_output(sorted_entries: list[dict], scored: list[dict]) -> list[dict]:
    """步骤5: 构建融合结果输出。"""
    fused = []
    for entry in sorted_entries:
        # 保留分数 + 截断元信息，移除其他 _ 字段
        merged = {k: v for k, v in entry.items()
                  if not k.startswith("_") or k in ("_score", "_sources", "_truncated")}

        # 收集所有 key 重叠结果的平台（多候选 key 交集匹配）
        entry_keys = _entry_name_keys(entry)
        platforms = [e["_platform"] for e in scored
                     if _entry_name_keys(e) & entry_keys]
        merged["_sources"] = list(dict.fromkeys(platforms))

        if len(merged["_sources"]) > 1:
            # 多平台同名结果：组合 source 字段（如 "mcmod.cn|modrinth"）
            merged["source"] = "|".join(merged["_sources"])

        fused.append(merged)
    return fused


def _mark_primary(fused: list[dict], query_keyword: str) -> list[dict]:
    """标记融合结果中的本体模组（C→B→A 级联判断）。"""
    if not fused:
        return fused

    q = (query_keyword or "").strip().lower()
    if not q:
        return fused

    # ── 级联 C: 前置关系 ──
    required_by_others = set()   # name → 被其他条目依赖（name_zh / name_en）
    for hit in fused:
        rel = hit.get("relationships", {})
        if isinstance(rel, dict) and not rel.get("_error"):
            for req in rel.get("requires", []):
                req_name = (req.get("name_zh") or req.get("name_en") or "").strip().lower()
                if req_name:
                    required_by_others.add(req_name)
    if required_by_others:
        for hit in fused:
            hit_name = (hit.get("name_zh") or hit.get("name") or "").strip().lower()
            hit_en = (hit.get("name_en") or "").strip().lower()
            if hit_name in required_by_others or hit_en in required_by_others:
                # 同时检查自身不是仅被自己依赖（排除 requires 列表指向自己的循环引用）
                requires_self = False
                rel = hit.get("relationships", {})
                if isinstance(rel, dict) and not rel.get("_error"):
                    for req in rel.get("requires", []):
                        rn = (req.get("name_zh") or req.get("name_en") or "").strip().lower()
                        if rn == hit_name or rn == hit_en:
                            requires_self = True
                            break
                if not requires_self:
                    hit["is_primary"] = True
        if any(h.get("is_primary") for h in fused):
            return fused

    # ── 级联 B: 精确名匹配 + 最高下载量 ──
    exact_matches = [h for h in fused
                     if (h.get("name_zh") or h.get("name") or "").strip().lower() == q
                     or (h.get("name_en") or "").strip().lower() == q]
    if exact_matches:
        max_dl = max((h.get("downloads", 0) for h in exact_matches), default=0)
        if max_dl > 0:
            for h in exact_matches:
                if h.get("downloads", 0) == max_dl:
                    h["is_primary"] = True
            return fused

    # ── 级联 A: 最高下载量 ──
    max_dl = max((h.get("downloads", 0) for h in fused), default=0)
    if max_dl > 0:
        for h in fused:
            if h.get("downloads", 0) == max_dl:
                h["is_primary"] = True

    # ── 兜底: 无人命中则最高分 ──
    if not any(h.get("is_primary") for h in fused):
        best = max(fused, key=lambda h: h.get("_score", 0))
        best["is_primary"] = True

    return fused
