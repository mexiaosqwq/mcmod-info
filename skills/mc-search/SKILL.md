---
name: mc-search
version: "5.4.0-dev"
description: >
  Minecraft 聚合搜索：五平台并行搜索模组、整合包、光影、材质包、原版 Wiki 攻略，自动融合去重后返回结构化结果。
  当用户询问任何 Minecraft 相关内容（模组/整合包/光影/材质包/MC百科/Modrinth/原版 wiki/我的世界攻略/下载量/依赖关系）时使用此 skill。
  不要用 web search（Tavily、Brave 等）搜索 Minecraft 内容——此 skill 直接调用平台 API，更快更完整，自动处理中文→英文桥接。
  触发短语："Minecraft 模组"、"整合包"、"光影"、"材质"、"MC百科"、"Modrinth"、"原版wiki"、"我的世界"、"minecraft mod"、"mc mod"、"模组依赖"、"模组下载量"。
license: MIT
context: fork
user-invocable: true
allowed-tools: [Bash]
---

# mc-search — AI Agent 优先的 Minecraft 聚合搜索

**核心定位**：此 skill 为 AI Agent 设计，Agent 直接通过 Python API 调用，不依赖 CLI。

四平台并行搜索 — MC百科(mcmod.cn)、Modrinth、minecraft.wiki(EN)、minecraft.wiki(ZH)。bbsmc.net 作为 Modrinth 的中文补充源。
结果自动融合去重，字段级权威源选取，跨语言桥接（中文关键词→英文补搜 Modrinth）对 Agent 透明。

## Agent 使用方式（Python API）

```python
import sys
sys.path.insert(0, 'skills/mc-search')
from scripts import core

# 搜索模组（最常用）
r = core.search_all("机械动力", max_per_source=10, content_type="mod", fuse=True)
# → {"results": [{name, name_zh, name_en, url, _sources, _score, is_primary, description, downloads, ...}], "platform_stats": {...}}

# 英文搜索同样
r = core.search_all("sodium", max_per_source=10, content_type="mod", fuse=True)

# 其他 content_type
r = core.search_all("钻石", max_per_source=10, content_type="item", fuse=True)       # 物品
r = core.search_all("SkyFactory", max_per_source=10, content_type="modpack", fuse=True)  # 整合包
r = core.search_all("Complementary", max_per_source=10, content_type="shader", fuse=True) # 光影

# Modrinth 详情
info = core.fetch_mod_info("sodium")
# → {name, description, body, downloads, author, changelogs, dependencies, ...}

# 依赖树
deps = core.get_mod_dependencies("sodium")
# → {"deps": {slug: {name, slug, client_side, server_side, url}}}

# Wiki 搜索/读取
pages = core.search_wiki("enchanting", max_results=5)
article = core.read_wiki("https://minecraft.wiki/w/Enchanting")
```

## 搜索路由

| Content Type | 搜索平台 | 说明 |
|-------------|---------|------|
| `mod` | MC百科 + Modrinth | 不搜 wiki（无模组数据，引入噪音） |
| `item` | MC百科 + Modrinth + wiki | wiki 有原版物品数据 |
| `modpack` | MC百科 + Modrinth | 整合包搜这两个平台 |
| `shader` / `resourcepack` | Modrinth | 视听内容 Modrinth 独占 |
| `vanilla` / `entity` / `biome` / `dimension` | wiki | 原版知识 wiki 为主 |
| `mod` (CJK 关键词) | MC百科 + Modrinth(桥接) | 中文关键词自动用 MC百科英文名补搜 Modrinth |

## 返回字段

每个 hit 核心字段：`name`, `name_zh`, `name_en`, `url`, `source`, `source_id`, `_sources`(来源平台列表), `_score`(相关性 0-150+), `is_primary`(是否本体模组), `description`, `downloads`, `author`, `categories`, `supported_versions`, `dependencies`, `relationships`, `icon_url`, `changelogs`。

错误信号（不用 None）：`{"_error": "not_found"}` / `{"_error": "api_failed"}` / `{"_error": "parse_failed"}`。

## 关键行为

- **跨语言桥接**：中文关键词自动从 MC百科 提取英文名去 Modrinth 补搜，Agent 无感知，结果自动融合
- **本体判别**：`is_primary: true` 标记本体模组（C→B→A 级联判断：前置关系 → 精确名+下载 → 纯下载量）
- **字段级融合**：name_zh 取 MC百科，name_en 取 Modrinth，downloads 取 Modrinth，relationships 取 MC百科
- **bbsmc 中文回填**：Modrinth 搜索结果自动从 bbsmc 补中文名和中文简介
- **WAF 自动回退**：MC百科 被防火墙拦截时自动降级到搜索页数据，不阻断搜索
- **MC百科 CDN 绕过**：curl_cffi + Chrome124 TLS 指纹绕过 Cloudflare

## 缓存

```python
core.set_cache(True)  # 启用，TTL 1 小时，位置 ~/.cache/mc-search/
```

## 故障排查

| 现象 | 原因 | 对策 |
|------|------|------|
| MC百科 结果标记 `_error: parse_failed` | CDN/WAF 拦截 | 正常降级，搜索页数据已返回 |
| Modrinth CJK 关键词无结果 | 桥接受阻 | 检查 MC百科 是否返回英文名，bbsmc 是否可达 |
| 搜索结果过多 | 未限制 | 设 `max_per_source=3` |
| Wiki 无结果 | wiki 不支持 mod 搜索 | mod/item 类型不会搜 wiki，用 content_type="vanilla" |
