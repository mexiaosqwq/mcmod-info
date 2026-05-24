# Result Schema — 返回字段说明

所有搜索函数返回 `list[dict]`。注意区分**搜索结果**（轻量）和**详情结果**（完整元数据）。

---

## 通用字段（所有平台）

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | str | 显示名称 |
| `name_en` | str | 英文名称 |
| `name_zh` | str | 中文名称 |
| `url` | str | 页面链接 |
| `source` | str | 来源平台：`mcmod.cn` / `modrinth` / `minecraft.wiki` / `minecraft.wiki/zh`；融合模式下为 `\|` 分隔的多平台字符串 |
| `source_id` | str | 平台内 ID（如 class ID、slug、pageid） |
| `type` | str | 项目类型：`mod` / `item` / `modpack` / `shader` / `resourcepack` / `wiki` |
| `is_primary` | bool | 融合模式下存在，是否为本体模组 |

---

## MC百科 — `search_mcmod` 搜索结果

| 字段 | 说明 |
|------|------|
| `name` / `name_en` / `name_zh` | 模组名称 |
| `url` | `https://www.mcmod.cn/class/{id}.html` |
| `source` | `mcmod.cn` |
| `source_id` | class ID（如 `2655`） |
| `type` | `mod` 或 `item` |
| `description` | 模组描述（已清洗，去除 "介绍"/"概述" 等残留） |
| `status` | 状态（如 `活跃`） |
| `source_type` | `open_source` / `closed_source` |
| `author` | 作者名 |
| `categories` | 分类列表 |
| `tags` | 标签列表 |
| `supported_versions` | 支持的版本列表（MC百科 detail 才有，搜索结果不含） |
| `cover_image` | 封面图 URL |
| `screenshots` | 截图 URL 列表 |
| `relationships.requires` | 前置 Mod 列表（MC百科 detail 才有；解析失败时含 `_error: "parse_failed"`，无关系时为 `{}`） |
| `relationships.integrates` | 联动 Mod 列表（MC百科 detail 才有；解析失败时含 `_error: "parse_failed"`，无关系时为 `{}`） |

> **注意**：当 HTML 存在但关系解析失败时，`relationships` 字段会包含 `{"_error": "parse_failed"}` 错误信号，而非简单的 `{}`。
| `has_changelog` | 是否有更新日志布尔值（MC百科 detail 才有） |
| `is_vanilla` | 是否为 MC百科原版内容分类（URL 含 `/class/1.html`，仅 MC百科 mod 搜索结果包含） |
| `external_links` | 外部平台链接字典（无时为 null）：`official` / `curseforge` / `modrinth` / `github` / `wiki` / `discord` / `jenkins` / `mcbbs` |
| `author_team` | 作者团队列表（无时为 null），包含每个作者的姓名和分工，见下方说明 |
| `community_stats` | 社区统计数据（无时为 null），包含评级、浏览量等，见下方说明 |
| `content_list` | MC百科资料列表（无时为 null），见下方说明 |

### `content_list` 字段结构

当模组在 MC百科 有资料列表时，返回如下结构：

```json
{
  "content_list": {
    "1": {"label": "物品/方块", "count": 1016, "url": "https://www.mcmod.cn/item/list/2021-1.html"},
    "4": {"label": "生物/实体", "count": 2, "url": "https://www.mcmod.cn/item/list/2021-4.html"},
    "5": {"label": "附魔/魔咒", "count": 2, "url": "https://www.mcmod.cn/item/list/2021-5.html"}
  }
}
```

| type_id | 类型 | 说明 |
|---------|------|------|
| `1` | 物品/方块 | 游戏中的物品和方块 |
| `2` | 游戏内设置 | 游戏机制相关配置 |
| `3` | 世界生成 | 世界生成相关（备用） |
| `4` | 生物/实体 | 游戏中的生物和实体 |
| `5` | 附魔/魔咒 | 附魔系统相关 |
| `6` | BUFF/DEBUFF | 状态效果相关 |
| `7` | 多方块结构 | 多方块建筑结构 |
| `8` | 自然生成 | 自然生成的结构/特征 |
| `9` | 绑定热键 | 键盘快捷键绑定 |
| `10` | 游戏设定 | 游戏配置选项 |

> 注：type_id 由 MC百科 动态定义，以上列出所有已知的类型ID。代码会优先从页面提取标题，回退到预定义映射。

> 注：type_id 可能还有其他值，具体以页面实际返回为准。代码会动态提取标题。

### `author_team` 字段结构（新增）

当模组在 MC百科 有完整的开发团队信息时，返回如下结构：

```json
{
  "author_team": [
    {"name": "药水棒冰", "roles": ["美术", "策划"]},
    {"name": "酒石酸菌", "roles": ["程序"]}
  ]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | str | 作者名称 |
| `roles` | list[str] | 该作者的分工角色（如"美术"、"策划"、"程序"等） |

> 注：最多返回 10 人，避免输出过长。组织名称（如"开发团队"、"工作室"等）会被自动过滤。

### `community_stats` 字段结构（新增）

当MC百科页面包含社区统计数据时，返回如下结构：

```json
{
  "community_stats": {
    "rating": 5.0,
    "rating_text": "名扬天下",
    "positive_rate": 100,
    "page_views": 22200,
    "integrations_count": 2,
    "revision_count": 7,
    "last_updated": "4天前"
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `rating` | float | 综合评分（0-5） |
| `rating_text` | str | 评级称号（如"名扬天下"） |
| `positive_rate` | int | 好评率（百分比） |
| `page_views` | int | 页面浏览量 |
| `integrations_count` | int | 关联整合包数量 |
| `revision_count` | int | 修订次数 |
| `last_updated` | str | 最后更新时间描述 |
| `favorites` | int | 收藏数 |

> 注：该字段可能为 null，如果MC百科页面上没有社区统计数据。

---

## MC百科 — 整合包搜索结果（`search_mcmod(content_type="modpack")`）

整合包搜索结果与模组类似，但包含整合包特有字段：

| 字段 | 说明 |
|------|------|
| `name` / `name_en` / `name_zh` | 整合包名称 |
| `url` | `https://www.mcmod.cn/modpack/{id}.html` |
| `source` | `mcmod.cn` |
| `source_id` | modpack ID（如 `123`） |
| `type` | `modpack` |
| `is_official` | 是否为 MC百科官方收录的整合包（URL 符合 `/modpack/\d+.html` 格式；仅 MC百科 整合包包含此字段） |

> **注意**：`is_official` 字段仅在 MC百科 整合包搜索结果中出现，Modrinth 整合包无此字段。
| `description` | 整合包描述 |
| `author` | 作者名 |
| `status` | 状态（如 `活跃`） |
| `categories` | 分类列表（如 `科技`、`魔法`、`冒险`） |
| `supported_versions` | 支持的游戏版本列表（从版本列表区域提取） |
| `cover_image` | 封面图 URL |
| `screenshots` | 截图 URL 列表（最多 6 张） |

> 注：`supported_versions` 字段可能为空列表，如果 MC百科页面没有版本列表区域。

---

## Modrinth — 整合包搜索结果（`search_modrinth(project_type="modpack")`）

Modrinth 整合包搜索结果结构与模组类似：

| 字段 | 说明 |
|------|------|
| `name` / `name_en` | 整合包名称 |
| `name_zh` | 空字符串（Modrinth 无中文名称） |
| `url` | `https://modrinth.com/modpack/{slug}` |
| `source` | `modrinth` |
| `source_id` | slug |
| `type` | `modpack` |
| `snippet` | 简短描述 |

---

## `_truncated` 元数据字段（可选）

当返回数据被截断时，`_truncated` 字段描述截断情况。

### 结构

```json
{
  "_truncated": {
    "{field_name}": {
      "returned": 5,
      "total": 62
    }
  }
}
```

### 可能被截断的字段

| 平台 | 字段 | 默认限制 | 说明 |
|------|------|----------|------|
| MC百科 | `screenshots` | 默认关闭 | 详情页截图（默认不返回） |
| Modrinth | `body` | 完整 | 项目描述（已赞助者名单清洗，不截断） |
| Modrinth | `gallery` | 10 张 | 项目截图 |
| Modrinth | `version_groups` | 5 组 | 版本分组 |
| Modrinth | `changelogs` | 5 条 | 更新日志 |
| 多平台 | `description` | 500 字符 | 描述文本截断（`_MAX_SEARCH_DESC_CHARS`） |

> **注意**：`show --full` 命令仅 Modrinth 数据无截断，MC百科截图默认关闭（不返回）。

---

## MC百科 — item 搜索结果（`_parse_mcmod_item_result`）

| 字段 | 说明 |
|------|------|
| `name` / `name_en` / `name_zh` | 物品名称 |
| `url` | MC百科物品页面 URL |
| `source` | `mcmod.cn` |
| `source_id` | item ID |
| `type` | `item` |
| `max_durability` | 最大耐久值（无则为 null） |
| `max_stack` | 最大堆叠数（无则为 null） |
| `category` | 资料分类 |
| `source_mod_name` | 所属模组名称 |
| `source_mod_url` | 所属模组页面链接 |
| `description` | 物品描述 |
| `has_recipe` | 是否有合成表 |

---

## MC百科 — 作者搜索 `search_mcmod_author`

与模组搜索返回字段相同，包含完整详情页字段：

| 字段 | 说明 |
|------|------|
| `name` / `name_en` / `name_zh` | 模组名称 |
| `url` | MC百科页面 URL |
| `source` | `mcmod.cn` |
| `source_id` | class ID |
| `type` | `mod` |
| `description` | 模组描述 |
| `status` | 状态 |
| `source_type` | `open_source` / `closed_source` |
| `author` | 作者名（与搜索参数一致） |

> **注意**：`search_mcmod_author` 的 `author` 字段与搜索参数完全一致，用于标识该模组属于哪个作者。
| `categories` | 分类列表 |
| `tags` | 标签列表 |
| `supported_versions` | 支持的版本列表 |
| `cover_image` | 封面图 URL |
| `screenshots` | 截图 URL 列表 |
| `relationships` | 前置/联动模组 |
| `has_changelog` | 是否有更新日志 |
| `is_vanilla` | 是否为原版内容 |

---

## Modrinth — `search_modrinth` 搜索结果（轻量）

`search_modrinth` 仅返回以下 8 个字段：

| 字段 | 说明 |
|------|------|
| `name` / `name_en` | 项目名称 |
| `name_zh` | 空字符串 |
| `url` | `https://modrinth.com/mod/{slug}` |
| `source` | `modrinth` |
| `source_id` | slug |
| `type` | `mod` / `shader` / `resourcepack` |
| `description` | 项目描述（来自详情 API） |
| `downloads` | 总下载量 |
| `followers` | 关注数 |
| `icon_url` | 图标 URL |
| `snippet` | 项目描述（来自详情 API，非搜索摘要） |

---

## Modrinth — `get_mod_info` 详情（完整元数据）

通过 `fetch_mod_info(mod_id)` 或 `show` 命令的 Modrinth 路径获取：

| 字段 | 说明 |
|------|------|
| `name` / `name_en` | 项目名称 |
| `name_zh` | 空字符串 |
| `slug` | URL slug |
| `id` | project_id |
| `url` | `https://modrinth.com/mod/{slug}` |
| `source` | `modrinth` |
| `source_id` | slug |
| `description` | 项目完整描述（来自详情 API，非搜索摘要） |
| `body` | 完整 Markdown 描述（完整正文，未经截断） |
| `type` | `mod` / `shader` / `resourcepack` |
| `author` | 作者用户名 |
| `license` | 许可证 ID |
| `license_name` | 许可证名称（如 "PolyForm Shield License 1.0.0"） |
| `license_url` | 许可证完整 URL |
| `categories` | 分类列表（如 ["optimization"]） |
| `display_categories` | 显示分类列表（UI 友好名称，如 ["优化"]） |
| `client_side` | 客户端支持：`required` / `optional` / `unsupported` |
| `server_side` | 服务端支持：`required` / `optional` / `unsupported` |
| `source_url` | GitHub 仓库链接（可无） |
| `wiki_url` | 官方 Wiki 链接（可无） |
| `issues_url` | Issues 链接（可无） |
| `discord_url` | Discord 链接（可无） |
| `donation_urls` | 捐赠链接列表：`{"platform": "Ko-fi", "url": "..."}` |
| `updated` | ISO 更新时间 |
| `published` | 发布时间 |
| `followers` | 关注数 |
| `icon_url` | 图标 URL |
| `gallery` | 截图 URL 列表（默认不返回，`show --full` 时返回全部） |
| `latest_version` | 最新版本号 |
| `game_versions` | 最新版本支持的游戏版本列表 |
| `loaders` | 最新版本支持的加载器（fabric / forge / neoforge / quilt） |
| `downloads` | 总下载量 |
| `version_groups` | 版本分组列表（**最多 5 组**，已聚合去重） |
| `changelogs` | 最近更新日志（**最多 5 条**，--json 专用） |

---

## 融合结果 — `search_all(..., fuse=True)` 或 `--json` 模式

当 `fuse=True` 或使用 `--json` 时，`search_all` 返回跨平台融合后的列表（按 content_type 调整平台优先级）。

**支持的 content_type**：`mod` / `item` / `modpack` / `shader` / `resourcepack` / `vanilla` / `entity` / `biome` / `dimension`

| 字段 | 类型 | 说明 |
|------|------|------|
| `source` | str | 来源平台，多平台时为 `\|` 分隔（如 `mcmod.cn\|modrinth`） |
| `_sources` | list[str] | 融合来源平台列表（**仅融合模式下存在**） |
| `is_primary` | bool | 是否为本体模组（C→B→A→兜底 四级联判定，至少一条结果标记） |
| 其余字段 | 依来源平台 | 来自优先级最高平台的结果 |

> **四级联判定规则**：
> - **C 级**（精确匹配）：平台搜索结果中存在与查询关键词完全一致的项目
> - **B 级**（前缀匹配）：项目名称以查询关键词开头
> - **A 级**（全词匹配）：项目名称包含完整查询词（非子串）
> - **兜底**：以上均不满足时，按相关性分数排序，首条结果标记为 `is_primary: true`

### `fuse=False` 模式

当 `fuse=False` 时，`search_all` 返回按平台分组的原始结果字典：

```json
{
  "mcmod.cn": [...],
  "modrinth": {"results": [...], "total": N, "returned": M},
  "minecraft.wiki": [...],
  "minecraft.wiki/zh": [...],
  "platform_stats": {
    "mcmod.cn": {"total": N, "returned": M},
    "modrinth": {"total": N, "returned": M},
    ...
  }
}
```

各平台返回格式与单平台搜索函数一致（Modrinth 为信封格式，其余为列表）。

**融合示例**：
```json
{
  "name": "钠",
  "name_en": "Sodium",
  "url": "https://www.mcmod.cn/class/2655.html",
  "source": "mcmod.cn|modrinth",
  "_sources": ["mcmod.cn", "modrinth"],
  "description": "现代渲染引擎和客户端优化模组...",
  "type": "mod"
}
```

**平台优先级**：
- entity/biome/dimension/vanilla → `minecraft.wiki` > `minecraft.wiki/zh` > `mcmod.cn` > `modrinth`
- mod/item/modpack → `mcmod.cn` > `modrinth`（仅这两个平台）
- shader/resourcepack → `modrinth`（仅 Modrinth 平台）

---

## minecraft.wiki — `search_wiki` 搜索结果

| 字段 | 说明 |
|------|------|
| `name` / `name_en` | 页面标题 |
| `name_zh` | 空字符串 |
| `url` | 页面 URL |
| `source` | `minecraft.wiki` |
| `source_id` | pageid |
| `type` | `"wiki"` |
| `snippet` | 页面摘要（从 intro 区域提取，CJK 页面使用 fallback 策略） |
| `sections` | 章节标题列表（直接访问文章时从 h2/h3 提取；MediaWiki API 降级路径返回空列表） |

---

## minecraft.wiki — `read_wiki` 读取正文

| 字段 | 说明 |
|------|------|
| `name` | 页面标题 |
| `url` | 页面 URL |
| `source` | `minecraft.wiki` |
| `content` | 正文段落列表（兼容旧接口；过滤 infobox、JSON-LD、CSS 片段后的纯文本） |
| `_sections` | 层级 section 列表（新版结构）：`{"heading", "parent", "content"}`，parent 为 null 表示顶级 h2，子节点为 h3/h4 |
| `infobox` | 结构化数据（`include_infobox=True` 时返回） |
| `main_image` | 页面主图 URL（`include_infobox=True` 时返回） |
