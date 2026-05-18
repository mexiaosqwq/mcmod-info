# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

**mc-search** 是 AI Agent 优先的 Minecraft 内容搜索 Skill。AI Agent 通过 Python API 直接调用，不依赖 CLI。五平台并行：MC百科 / Modrinth / bbsmc.net / minecraft.wiki EN / minecraft.wiki ZH。

核心特性：
- **跨语言桥接**：中文关键词自动从 MC百科 提取 `name_en` 去 Modrinth 补搜，Agent 透明
- **本体判别**：`is_primary: true` C→B→A→兜底 四级联标记本体模组
- **字段级权威源融合**：`_merge_entry_fields()` 逐字段选源，不按单一平台优先
- **错误信号透明**：统一 `_error` 键区分 `not_found`/`api_failed`/`parse_failed`，不用 `None`

## 架构

```
Agent 调用 → core.py API → 并行平台搜索 → 跨语言桥接(CJK) → 融合(_fuse_results) → 统一 JSON
                                    └─ MC百科 → name_en → Modrinth 补搜（透明）
```

两个核心文件（均在 `skills/mc-search/scripts/`）：
- `core.py` — 全部搜索逻辑（API 调用、HTML 解析、结果融合、缓存），约 3850 行
- `cli.py` — argparse 薄壳（Agent 不使用，仅人类调试用），约 1290 行

参考文档：`skills/mc-search/references/` — 错误码、平台对比、返回字段 Schema

## Agent 使用方式

Agent 应直接 `import` core 模块，不通过 CLI。

### 搜索：`search_all()`

```python
import sys
sys.path.insert(0, 'skills/mc-search')
from scripts import core

# 多平台搜索（推荐）
result = core.search_all("机械动力", max_per_source=10, content_type="mod", fuse=True)
# → {"results": [...], "platform_stats": {"mcmod.cn": {...}, "modrinth": {...}, ...}}

# 单平台搜索（通过 content_type 路由，不裸调 set_platform_enabled）
# core.set_platform_enabled(mcmod=False, wiki=False, wiki_zh=False)  # 仅需完全自定义时用
result = core.search_all("sodium", max_per_source=10, content_type="mod")
```

`content_type` 可选：`mod` / `item` / `modpack` / `shader` / `resourcepack` / `vanilla` / `entity` / `biome` / `dimension`。

返回的每个 hit 关键字段：`name`、`name_zh`、`name_en`、`url`、`source`、`source_id`、`_score`、`_sources`、`is_primary`、`snippet`/`description`。

### 详情：`fetch_mod_info()` / `get_mod_dependencies()`

```python
# Modrinth 模组详情
info = core.fetch_mod_info("sodium")  # slug 或 project_id
# → dict 含 name, description, body(Markdown), downloads, author, supported_versions, changelogs...

# 依赖树
deps = core.get_mod_dependencies("sodium")
# → {"deps": {slug: {id, name, slug, client_side, server_side, url}}}
```

### MC百科：`search_mcmod()` / `_parse_mcmod_mod_result()`

```python
hits = core.search_mcmod("机械动力", max_results=3, content_type="mod")
# → list[dict]，每项含 name_zh, name_en, description, author, supported_versions, relationships...
```

MC百科 详情页可能被 WAF 拦截，此时自动回退到搜索页数据构建最小结果。

### Wiki：`search_wiki()` / `search_wiki_zh()` / `read_wiki()`

```python
pages = core.search_wiki("enchanting", max_results=5)
# → list[dict]，每项含 name, url, snippet, sections

article = core.read_wiki("https://minecraft.wiki/w/Enchanting", max_paragraphs=-1)
# → dict 含 name, url, content([段落]), infobox(结构化数据), main_image
```

### 作者搜索：`search_mcmod_author()` / `search_modrinth_author()`

```python
mcmod_works = core.search_mcmod_author("Simibubi", max_mods=10)
mr_works = core.search_modrinth_author("jellysquid3", max_results=10)
```

### 缓存

```python
core.set_cache(True)  # 启用，TTL 1 小时
# 缓存位置：~/.cache/mc-search/
# 详情页 HTML 缓存可显著加速 MC百科 二次访问
```

### 平台开关

```python
core.set_platform_enabled(mcmod=True, modrinth=True, wiki=True, wiki_zh=True)
# Agent 不应裸调 set_platform_enabled，应通过 search_all 的 content_type 自动路由
```

## JSON 返回格式

| 函数 | `results` 类型 | 附加字段 |
|------|---------------|---------|
| `search_all(fuse=True)` | `[{hit}]` | `platform_stats` |
| `search_all(fuse=False)` | `{mcmod.cn: [...], modrinth: {...}, ...}` | `platform_stats` |
| `search_modrinth` | `{results: [...], total: N, returned: M}` | — |
| `fetch_mod_info()` | `{dict}` | — |
| `get_mod_dependencies()` | `{deps: {...}}` | — |

失败时返回 `{"_error": "not_found"}` / `{"_error": "api_failed"}`（Agent 端用 `_is_valid()` 统一判断）。

> `search_modrinth` 是唯一返回 `dict` 信封（含 `total`/`returned`）的搜索函数，`search_all` 内部会拆包。

## 重要实现细节

### 网络层
- MC百科 + minecraft.wiki：`curl_cffi` + Chrome124 TLS 指纹绕过 CDN/反爬
- Modrinth API：标准 `urllib.request` HTTP
- MC百科 各子域名 (www + search) 需独立 CDN 绕过
- WAF 检测：短页面 (<1000B) 含 AIWAFCDN/防火墙拦截 等签名视为被阻断

### MC百科 解析
- 纯正则 + 字符串操作，无 BeautifulSoup
- HTML 结构可能变化，解析较脆弱
- 详情页被 WAF 拦截时自动回退到搜索页数据（`_build_mcmod_fallback_result`）
- 物品页 (`/item/`) 与模组页 (`/class/`) 结构完全不同，分别解析

### 结果融合（`_fuse_results`）

6 步管线：
1. `_score_and_filter` — 打分 + 过滤无关结果
2. `_count_platform_hits` — 统计多平台命中
3. `_deduplicate_by_name` — 多候选 key 匹配去重（内部调用 `_merge_entry_fields`）
4. `_sort_entries` — 按分数 + 平台优先级排序
5. `_build_fused_output` — 构建 `_sources` + 清理内部字段
6. `_mark_primary` — C→B→A→兜底 四级联标记 `is_primary`

跨语言桥接在步骤 1 之前：CJK 关键词 → 从 MC百科 提取 `name_en` → Modrinth 补搜 → 合并进 `results["modrinth"]`。

### 错误信号

失败不用 `None`，统一用结构化 dict：
- `{"_error": "not_found"}` — API 404/空结果
- `{"_error": "api_failed"}` — 网络错误/超时
- `{"_error": "parse_failed"}` — HTML 存在但解析失败
- `"_body_error": "fetch_failed"` — 详情获取失败但搜索命中仍保留

CLI 端用 `_is_valid(info)` 统一判断（非 None + 不含 `_error` 键）。

## 性能注意事项

- `search_modrinth()` 内部对每个搜索结果并行获取详情（`_parallel_fetch_with_fallback`，最多 4 workers）
- `search_all()` 并行提交平台任务（最多 4 平台，由 content_type 决定）
- Modrinth API 速率限制：360 请求/小时
- MC百科 无速率限制但 CDN 可能限流

## 测试（Python API 方式）

修改代码后，在 Python 中验证：

```python
import sys
sys.path.insert(0, 'skills/mc-search')
import scripts.core as core

# 搜索
r = core.search_all("机械动力", max_per_source=1, content_type="mod", fuse=True)
assert len(r["results"]) > 0

# 详情
info = core.fetch_mod_info("sodium")
assert info and info["name"] == "Sodium"

# Wiki
pages = core.search_wiki("enchanting", max_results=1)
assert len(pages) > 0

# 缓存
core.set_cache(True)
r2 = core.search_all("sodium", max_per_source=1, fuse=True)
assert len(r2["results"]) > 0
```

## 依赖

- Python 3.8+
- `curl_cffi>=0.15.0`（MC百科 + wiki 必需）
- 其余标准库

## 行为准则

- **极简主义**：不添加任务外的功能/重构/抽象。三行相似优于过早抽象。不做半成品
- **编辑优先于新建**：功能迭代只改 core.py API，不改 CLI
- **信任内部代码**：仅在系统边界（用户输入、外部 API）验证。不处理内部不可能的场景
- **注释克制**：默认不写。仅 WHY 不明显时一行简注。删除过时注释
- **不留向后兼容包袱**：彻底删除无用代码，不做软废弃
- **专用工具优先**：文件操作用 Read/Edit/Write/Glob，Bash 仅用于 git/pip/curl
- **修改前瞻后顾**：改前 grep 所有引用，改后 Python API 验证
- **复杂任务先规划**：多文件修改走 workflow-execute-plans，分批+验证+暂停
- **验证后才报告完成**：测试 golden path + 边界情况。辅助工作派子 agent，不占主上下文
- **AI 优先**：默认参数针对 AI Agent 场景优化（少结果、合理超时）

## Agent skills

> `docs/agents/` 下文件为本地配置（gitignored）。

Issues 在 GitHub Issues `mexiaosqwq/mc-search-skill`。领域模型见 `CONTEXT.md`。
