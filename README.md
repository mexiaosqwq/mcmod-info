# mc-search

AI Agent 优先的 Minecraft 聚合搜索 Skill，五平台并行，自动融合去重。

[![Version](https://img.shields.io/github/v/release/mexiaosqwq/mc-search-skill)](https://github.com/mexiaosqwq/mc-search-skill/releases)
[![License](https://img.shields.io/github/license/mexiaosqwq/mc-search-skill)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8+-blue)](https://www.python.org/)
[![Skill](https://img.shields.io/badge/Claude%20Code-Skill-orange)](skills/mc-search/SKILL.md)

[English Documentation →](README.en.md)

## 项目简介

mc-search 是为 **Claude Code Agent** 设计的 Minecraft 内容搜索 Skill，五平台并行搜索：

| 平台 | 内容 | 访问方式 |
|------|------|---------|
| **MC百科** (mcmod.cn) | 中文模组/物品/整合包 | HTML 解析 + CDN 绕过 |
| **Modrinth** | 英文 mod/光影/材质包/整合包 | REST API |
| **bbsmc.net** | 中文名+简介补充源 | Modrinth 兼容 API |
| **minecraft.wiki** (EN/ZH) | 原版游戏 wiki | MediaWiki API + CDN 绕过 |

## 核心特性

- **跨语言桥接**：中文关键词自动提取英文名去 Modrinth 补搜，Agent 无感知
- **本体判别**：`is_primary: true` 自动标记本体模组（C→B→A→兜底 四级联）
- **字段级融合**：按字段逐源选取权威数据（name_zh→MC百科, name_en→Modrinth, downloads→Modrinth, relationships→MC百科）
- **bbsmc 中文回填**：Modrinth 结果自动补中文名和简介
- **WAF/CDN 自动降级**：MC百科 被拦截时回退搜索页数据，不阻断搜索

## 安装

```bash
git clone https://github.com/mexiaosqwq/mc-search-skill.git && \
  cp -r mc-search-skill/skills/mc-search ~/.claude/skills/ && \
  cd ~/.claude/skills/mc-search && pip install -e . && \
  cd ~ && rm -rf mc-search-skill
```

依赖：Python 3.8+，`curl_cffi>=0.15.0`。验证：`mc-search --json search JEI -n 1 --platform mcmod`

## 快速使用

Agent 通过 Python API 调用（详见 [SKILL.md](skills/mc-search/SKILL.md)），CLI 用于手动测试：

```bash
mc-search --json search 机械动力                # 搜索
mc-search --json show sodium --full              # 详情
mc-search --json show sodium --deps              # 依赖
mc-search --json wiki 红石 -r                    # wiki 搜+读
```

`search` 支持 `--type mod/item/modpack/shader/resourcepack/vanilla/entity/biome/dimension`，`--author` 按作者搜索，`--cache` 启用缓存。

## 项目结构

```
mc-search-skill/
├── skills/mc-search/
│   ├── SKILL.md               # Agent 调用定义
│   ├── scripts/
│   │   ├── core.py             # 搜索/解析/融合/缓存 (~4074 行)
│   │   └── cli.py              # CLI 入口 (~1348 行)
│   └── references/            # 错误码/平台对比/结果Schema
├── README.md
└── README.en.md
```

## 许可证

MIT License

## 致谢

- [MC 百科](https://www.mcmod.cn/) — 中文 Minecraft 模组百科
- [Modrinth](https://modrinth.com/) — Minecraft 模组平台
- [bbsmc.net](https://bbsmc.net/) — Modrinth 中国社区复刻
- [Minecraft Wiki](https://minecraft.wiki/) — 原版游戏 wiki
