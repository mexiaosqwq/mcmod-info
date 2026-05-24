# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

**mc-search** 是 AI Agent 优先的 Minecraft 内容搜索 Skill。AI Agent 通过 Python API 直接调用，不依赖 CLI。五平台并行：MC百科 / Modrinth / bbsmc.net / minecraft.wiki EN / minecraft.wiki ZH。

核心特性：
- **跨语言桥接**：中文关键词自动从 MC百科 提取 `name_en` 去 Modrinth 补搜，Agent 透明
- **本体判别**：`is_primary: true` C→B→A→兜底 四级联标记本体模组
- **字段级权威源融合**：`_merge_entry_fields()` 逐字段选源，不按单一平台优先
- **错误信号透明**：统一 `_error` 键区分 `not_found`/`api_failed`/`parse_failed`，不用 `None`

## 代码编辑流程

修改代码前必须按此流程执行，不可跳过步骤：

### 第一步：用 codegraph 建立全景认知

不要直接 Read/Grep 文件。先调 codegraph MCP：
- `codegraph_context` — 获取任务相关的符号关系图（callers/callees/文件分布）
- `codegraph_impact` — 如果要改核心函数，先看影响范围
- `codegraph_files` — 确认项目文件结构
- `codegraph_node` — 只看某个函数签名/位置

只在 codegraph 覆盖不到的细节（如某个函数内部的具体逻辑确认）时用 Read。

### 第二步：编写代码

- **单文件修改**：直接 Edit
- **跨文件修改/重构**：先 `EnterPlanMode` 规划，走 `workflow-execute-plans`
- **HTML 解析修改**（见下文安全规则）
- **融合管线修改**（见下文安全规则）

### 第三步：验证

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

先测 Python API 通路，再考虑 CLI。不满足断言就修复，不得跳过。

## 安全规则（修改特定区域的约束）

### HTML 解析（MC百科相关函数）

`skills/mc-search/scripts/core.py` 中所有 MC百科 解析函数使用纯正则 + 字符串操作（无 BeautifulSoup）。

修改时的约束：
- **正则修改后必须搜整个代码库**：同一个正则可能在多个函数中重复出现（或相近写法），改一处可能漏另一处。例：`class/\d+\.html` 模式在 `search_mcmod`、`_parse_mcmod_mod_result`、`_build_mcmod_fallback_result` 等多处独立出现
- **MC百科 URL 模式**：`/class/`（模组）、`/item/`（物品）、`/modpack/`（整合包）三种结构完全不同，各自独立解析。修改某一类型时检查是否动了其他类型的匹配
- **WAF 回退路径**：详情页被 WAF 拦截时走 `_build_mcmod_fallback_result`，这个路径的数据字段比完整解析少。如果你在加新字段，要同时加在 `_build_mcmod_fallback_result` 里

### 融合管线（`_fuse_results` 及其子函数）

融合管线 6 步按顺序执行，修改某一步时看这一步依赖上一步的哪些字段：

1. `_score_and_filter` — 打分和过滤（改打分看 `_score` 在后续步骤的使用）
2. `_count_platform_hits` — 统计（只读，改其他步骤不会影响它）
3. `_deduplicate_by_name` — 去重（内部调 `_merge_entry_fields` 做字段级融合）
4. `_sort_entries` — 排序
5. `_build_fused_output` — 构建 `_sources` + 清理内部字段
6. `_mark_primary` — C→B→A→兜底四级联

**关键约束**：
- `_deduplicate_by_name` 会调用 `_merge_entry_fields` 做字段级覆盖。如果你在搜索结果中引入新字段，要决定是否加入 `_MERGE_FIELD_RULES` 或 `_FIELD_PRIORITY`
- `_mark_primary` 的 C→B→A→兜底顺序固定，改判定逻辑时检查所有四级路径
- 跨语言桥接在第 1 步之前执行，桥接结果在 `results["modrinth"]` 中追加

### 全局变量和并发安全

`core.py` 中有以下全局状态：
- `_cache_enabled` / `_cache_ttl`（由 `_CACHE_LOCK` 保护）
- `_platform_enabled`（由 `_PLATFORM_LOCK` 保护）
- `_MCMOD_SESSION` / `_MCMOD_BYPASSED`（由 `_MCMOD_LOCK` 保护）

**修改任何全局变量时**：必须在锁内读写。例外：布尔只读检查可用 `_is_cache_enabled()` 封装。

**新增全局状态时**：同步添加对应的 `threading.Lock()`，保持命名 `_{NAME}_LOCK`。

### CLI 只改 argparse

`cli.py` 是纯 argparse 薄壳。功能迭代只改 `core.py` API，不改 CLI。CLI 改动的合理范围：
- 新增/修改命令行参数（`_build_parser`）
- 参数校验和错误提示
- 格式化输出（`_print_*` 函数）

## 提交

提交信息前缀与最近 commit 一致：
- `fix:` — bug 修复
- `refactor:` — 重构（无行为变化）
- `docs:` — 文档/注释修改
- `feat:` — 新功能
- 不写长标题，用短横线描述具体改动

例：`fix: 并发安全补全 + lambda提取具名函数 + CLI类型标注`

## 架构

```
Agent 调用 → core.py API → 并行平台搜索 → 跨语言桥接(CJK) → 融合(_fuse_results) → 统一 JSON
                                    └─ MC百科 → name_en → Modrinth 补搜（透明）
```

两个核心文件（均在 `skills/mc-search/scripts/`）：
- `core.py` — 全部搜索逻辑（API 调用、HTML 解析、结果融合、缓存），~4000 行
- `cli.py` — argparse 薄壳（Agent 不使用，仅人类调试用），~1350 行

参考文档：`skills/mc-search/references/` — 错误码、平台对比、返回字段 Schema
领域模型解释：`CONTEXT.md`

## Agent 使用方式

Agent 应直接 `import` core 模块，不通过 CLI。详细 API 示例见 `skills/mc-search/SKILL.md` 中 "Agent 使用方式（Python API）" 部分。

`content_type` 可选：`mod` / `item` / `modpack` / `shader` / `resourcepack` / `vanilla` / `entity` / `biome` / `dimension`。

## 依赖

- Python 3.8+
- `curl_cffi>=0.15.0`（MC百科 + wiki 必需）
- 其余标准库

## 行为准则

- **极简主义**：不添加任务外的功能/重构/抽象。三行相似优于过早抽象。不做半成品
- **编辑优先于新建**：功能迭代只改 core.py API，不改 CLI
- **注释有目的**：不写显而易见的内容（`_cache_dir()` 上不用写"返回缓存目录"）。以下情况**必须写**：正则意图、锁保护范围、回退路径触发条件、非常规做法的原因。写 WHY，不写 WHAT。删除过时注释
- **不留向后兼容包袱**：彻底删除无用代码，不做软废弃
- **codegraph 优先**：改代码前先用 codegraph 建立全景认知，不直接 Read/Grep 漫游
- **HTML 解析安全**：改正则后搜索整个代码库确认不遗漏同类 pattern
- **验证后才报告完成**：测 golden path + 边界情况，不满足断言不提交
- **分步推理**：每一步写清「已知 → 推导 → 结论」，步与步之间逻辑链完整，不留跳跃。每步结束时追加一句括号内的自检——「（此步隐含假设是___，验证通过/待确认）」
