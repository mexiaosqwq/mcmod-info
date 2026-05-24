# mc-search

AI-Agent-first Minecraft content search Skill — five-platform parallel, auto-fuse and deduplicate.

[![Version](https://img.shields.io/github/v/release/mexiaosqwq/mc-search-skill)](https://github.com/mexiaosqwq/mc-search-skill/releases)
[![License](https://img.shields.io/github/license/mexiaosqwq/mc-search-skill)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8+-blue)](https://www.python.org/)
[![Skill](https://img.shields.io/badge/Claude%20Code-Skill-orange)](skills/mc-search/SKILL.md)

[中文文档 →](README.md)

## Overview

mc-search is a Minecraft content search **Skill for Claude Code Agent**, searching five platforms in parallel:

| Platform | Content | Access |
|----------|---------|--------|
| **MC百科** (mcmod.cn) | Chinese mods/items/modpacks | HTML parsing + CDN bypass |
| **Modrinth** | English mods/shaders/resourcepacks/modpacks | REST API |
| **bbsmc.net** | Chinese name/description supplement | Modrinth-compatible API |
| **minecraft.wiki** (EN/ZH) | Vanilla game wiki | MediaWiki API + CDN bypass |

## Core Features

- **Cross-language bridge**: CJK keywords auto-extract English names to search Modrinth — transparent to Agent
- **Primary detection**: `is_primary: true` flags the main mod (C→B→A→fallback cascade)
- **Field-level fusion**: authority source per field (name_zh→MC百科, name_en→Modrinth, downloads→Modrinth, relationships→MC百科)
- **bbsmc Chinese backfill**: Modrinth results auto-enriched with Chinese names and descriptions
- **WAF/CDN auto-fallback**: MC百科 blocking gracefully degrades to search page data

## Install

```bash
git clone https://github.com/mexiaosqwq/mc-search-skill.git && \
  cp -r mc-search-skill/skills/mc-search ~/.claude/skills/ && \
  cd ~/.claude/skills/mc-search && pip install -e . && \
  cd ~ && rm -rf mc-search-skill
```

Requires: Python 3.8+, `curl_cffi>=0.15.0`. Verify: `mc-search --json search JEI -n 1 --platform mcmod`

## Quick Usage

Agent calls via Python API (see [SKILL.md](skills/mc-search/SKILL.md)), CLI for manual testing:

```bash
mc-search --json search sodium                    # Search
mc-search --json show sodium --full               # Details
mc-search --json show sodium --deps               # Dependencies
mc-search --json wiki enchanting -r               # Wiki search+read
```

`search` supports `--type mod/item/modpack/shader/resourcepack/vanilla/entity/biome/dimension`, `--cache` for caching.

## Project Structure

```
mc-search-skill/
├── skills/mc-search/
│   ├── SKILL.md               # Agent invocation definition
│   ├── scripts/
│   │   ├── core.py             # Search/parse/fuse/cache (~4000 lines)
│   │   └── cli.py              # CLI entry (~1350 lines)
│   └── references/            # Error codes/platform comparison/result schema
├── README.md
└── README.en.md
```

## License

MIT

## Acknowledgments

- [MC 百科](https://www.mcmod.cn/) — Chinese Minecraft mod wiki
- [Modrinth](https://modrinth.com/) — Minecraft mod platform
- [bbsmc.net](https://bbsmc.net/) — Modrinth Chinese community fork
- [Minecraft Wiki](https://minecraft.wiki/) — Vanilla game wiki
