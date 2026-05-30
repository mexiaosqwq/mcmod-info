---
name: mc-search
version: "5.4.0-dev"
description: >
  Minecraft 聚合搜索：五平台并行搜索模组、整合包、光影、材质包、原版 Wiki 攻略，
  自动融合去重后返回结构化结果。当用户询问任何 Minecraft 相关内容时使用此 skill。
  不要用 web search 搜索 Minecraft 内容。
  触发短语："Minecraft 模组"、"整合包"、"光影"、"材质"、"MC百科"、"Modrinth"、
  "原版wiki"、"我的世界"、"minecraft mod"、"mc mod"、"模组依赖"、"模组下载量"。
license: MIT
context: fork
user-invocable: true
allowed-tools: [Bash]
---

# mc-search — Claude Code Minecraft 搜索 Skill

**何时使用**：用户询问 Minecraft 模组、整合包、光影、材质包、原版 Wiki 攻略、依赖关系、下载量等。

四平台并行：MC百科(mcmod.cn)、Modrinth、minecraft.wiki(EN/ZH)。bbsmc.net 作为中文补充源。
结果自动融合去重，字段级权威源选取，跨语言桥接（中文→英文补搜）对 Agent 透明。

## Agent 使用方式（Python API）

```python
import sys
sys.path.insert(0, 'skills/mc-search')
from scripts import core

# 搜索模组（最常用）
r = core.search_all("机械动力", max_per_source=10, fuse=True)

# Modrinth 详情
info = core.fetch_mod_info("sodium")

# 依赖树
deps = core.get_mod_dependencies("sodium")

# Wiki 搜索/读取
pages = core.search_wiki("enchanting", max_results=5)
article = core.read_wiki("https://minecraft.wiki/w/Diamond_Sword", include_infobox=True)
```

> `content_type` 可选：`mod` / `item` / `modpack` / `shader` / `resourcepack` / `vanilla` / `entity` / `biome` / `dimension`

## 搜索路由

| Content Type | 搜索平台 | 说明 |
|-------------|---------|------|
| `mod` / `modpack` | MC百科 + Modrinth | 不搜 wiki（无模组数据） |
| `item` | MC百科 + Modrinth + wiki | wiki 有原版物品数据 |
| `shader` / `resourcepack` | Modrinth | 视听内容 Modrinth 独占 |
| `vanilla` / `entity` / `biome` / `dimension` | minecraft.wiki (EN/ZH) | 原版内容仅 wiki 有数据 |

中文关键词自动触发 CJK 桥接：从 MC百科 提取英文名去 Modrinth 补搜。

## 关键行为

- **跨语言桥接**：中文关键词自动从 MC百科 提取英文名去 Modrinth 补搜，Agent 无感知
- **本体判别**：`is_primary: true` 标记本体模组（C→B→A→兜底 四级联）：
  - **C 级**：前置关系检测 —— 被其他条目 `requires` 依赖
  - **B 级**：精确名匹配 + 最高下载量
  - **A 级**：最高下载量
  - **兜底**：相关性分数 `_score` 最高者
- **字段级融合**：name_zh→MC百科，name_en→Modrinth，downloads→Modrinth，relationships→MC百科
- **bbsmc 回填副作用**：若返回双语名（如 "机械动力 - Create"），会覆盖 `name_en` 字段
- **WAF 自动回退**：MC百科 被拦截时降级到搜索页数据，不阻断搜索
- **CDN 绕过**：curl_cffi + Chrome124 TLS 指纹绕过 Cloudflare

## 缓存

```python
core.set_cache(True)  # TTL 1 小时，~/.cache/mc-search/
```

## CLI（人类调试用）

```bash
mc-search --json search 机械动力
mc-search --json show sodium --full --deps
mc-search --json wiki enchanting -r
```

详见 [README.md](../../README.md)。

## 故障排查

| 现象 | 对策 |
|------|------|
| MC百科 `_error: parse_failed` | 正常降级，搜索页数据已返回 |
| Modrinth CJK 无结果 | 检查 MC百科是否返回英文名 |
| vanilla 返回空 | 仅搜 wiki，确认关键词匹配 |