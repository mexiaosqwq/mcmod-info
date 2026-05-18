# 平台特性对比

不同平台的数据来源和适用场景对比。

---

## 平台特性

| 平台 | 优势 | 适用场景 | 数据来源 |
|------|------|----------|----------|
| **MC百科** | 中文详细，联动信息全 | 中文用户、找联动模组 | HTML 解析（官方页面） |
| **Modrinth** | 英文官方，依赖准确 | 依赖查询、版本信息、光影/材质包 | REST API（官方接口） |
| **bbsmc.net** | 中文名+简介补充 | Modrinth 结果中文回填 | Modrinth 兼容 API |
| **minecraft.wiki** | 原版百科（英文） | 合成表、游戏机制 | MediaWiki API |
| **minecraft.wiki/zh** | 原版百科（中文） | 中文原版内容查询 | MediaWiki API |

---

## 不同类型内容的差异

### 模组 (mod)

**搜索范围**: MC百科 + Modrinth

**返回字段**:
- 中英文信息（MC百科优先中文）
- 依赖关系（Modrinth 前置 + MC百科联动）
- 支持版本列表
- 开发团队信息（MC百科）

**适用场景**: 查找 Fabric/Forge/NeoForge 模组信息

---

### 光影包 (shader)

**搜索范围**: **仅 Modrinth**

**返回字段**:
- 英文信息（Modrinth 专有）
- 截图画廊
- 下载量统计
- Minecraft 版本支持

**适用场景**: 查找 OptiFine/Iris 兼容光影包

**注意**: MC百科不收录光影包数据

---

### 材质包 (resourcepack)

**搜索范围**: **仅 Modrinth**

**返回字段**:
- 英文信息（Modrinth 专有）
- 预览图列表
- 分辨率规格
- Minecraft 版本支持

**适用场景**: 查找原版/模组兼容材质包

**注意**: MC百科不收录材质包数据

---

### 整合包 (modpack)

**搜索范围**: MC百科 + Modrinth

**返回字段**:
- 包含模组列表（MC百科）
- 下载链接
- 整合包配置说明
- 支持版本

**适用场景**: 查找现成配置的模组集合

---

### Wiki 内容（原版游戏）

**搜索范围**: minecraft.wiki（中英双站）

**返回字段**:
- 游戏机制说明
- 合成表（方块/物品）
- 生物信息
- 世界生成规则

**适用场景**: 查原版游戏内容（不包含模组）

**重要提示**: minecraft.wiki **只收录原版内容**，模组相关请用 `search` 或 `show` 命令。

---

## 数据质量差异

| 字段 | MC百科 | Modrinth | minecraft.wiki |
|------|--------|----------|----------------|
| 中文名称 | ✅ 完整（搜索） | ❌ 无 | ✅ 有（中文站） |
| 英文名称 | ✅ 有（搜索） | ✅ 完整 | ✅ 完整 |
| 依赖关系 | ⚠️ WAF 阻断时受限 | ✅ 前置准确 | ❌ 无 |
| 版本信息 | ❌ 详情页受限（搜索不可用） | ✅ 详细版本分组 | ❌ 无 |
| 截图数量 | ❌ 详情页受限（搜索不可用） | ⚠️ 默认 10 张限制 | ❌ 无 |
| 更新日志 | ❌ 详情页受限（搜索不可用） | ✅ 最近 5 条 | ❌ 无 |
| 开发团队 | ❌ 详情页受限（搜索不可用） | ❌ 仅作者名 | ❌ 无 |
| 社区统计 | ❌ 详情页受限（搜索不可用） | ✅ 下载/关注数 | ❌ 无 |

---

## 跨平台融合策略

当使用 `search` 命令时，系统会根据内容类型自动选择最佳平台：

### 融合优先级

| 内容类型 | 平台优先级 | 说明 |
|----------|------------|------|
| `mod` / `item` / `modpack` | MC百科 > Modrinth | 中文用户优先 MC百科 |
| `shader` / `resourcepack` | Modrinth（唯一） | 仅 Modrinth 有数据 |
| `entity` / `biome` / `dimension` / `vanilla` | minecraft.wiki > minecraft.wiki/zh > MC百科 > Modrinth | 原版内容优先 wiki |

### 融合字段 `_sources`

当多平台匹配同一项目时，返回字段 `_sources` 标识融合来源：

```json
{
  "name": "钠",
  "source": "mcmod.cn|modrinth",
  "_sources": ["mcmod.cn", "modrinth"]
}
```

**显示字段取自优先级最高的平台**，其他平台数据可通过 `show --full` 查看。

---

## 使用建议

### 查模组信息

- 中文用户 → `search <中文名>` 或 `show <中文名> --full`
- 英文用户 → `search <英文名> --platform modrinth`

### 查光影/材质包

- **必须用 Modrinth** → `search <名称> --shader` 或 `search <名称> --resourcepack`

### 查原版内容

- **必须用 wiki** → `wiki <关键词>`（如"附魔台"、"下界合金"）

### 查依赖关系

- **Modrinth 更准确** → `show <模组名> --deps`（快捷路径）

### 查联动模组

- **MC百科 搜索可用** → 详情页 WAF 阻断时自动回退搜索页数据，联动信息通过 `_fuse_results` 融合同步

---

## 注意事项

1. **平台限制**：
   - 光影包/材质包仅 Modrinth 有数据，使用 `--shader` / `--resourcepack` 自动限定
   - 原版内容仅 wiki 有数据，使用 `wiki` 命令

2. **版本一致性**：
   - MC百科版本列表可能滞后，建议用 Modrinth 确认最新版本
   - `show --full` 会显示 Modrinth 版本信息；MC百科版本列表暂不可用

3. **网络稳定性**：
   - 四平台并行搜索，单个失败不影响其他结果
   - Modrinth API 更稳定（360 请求/小时限制）
   - MC百科 HTML 解析可能被限流（详见 troubleshooting.md）


---



## 常见误区

### ❌ 误用 wiki 查模组

```bash
mc-search wiki 机械动力  # ❌ wiki 不收录模组
```

**正确做法**:
```bash
mc-search search 机械动力  # ✅ 用 search 命令
```

### ❌ 搜光影包用默认平台

```bash
mc-search search BSL  # ❌ MC百科无光影包数据
```

**正确做法**:
```bash
mc-search search BSL --shader  # ✅ 自动限定 Modrinth
```

### ❌ 忽略 `--full` 的双平台优势

```bash
mc-search show 钠  # ❌ 仅显示 MC百科信息，无依赖
```

**正确做法**:
```bash
mc-search show 钠 --full  # ✅ MC百科 + Modrinth + 依赖关系
```