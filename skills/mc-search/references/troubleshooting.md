# 故障排查

## 快速诊断流程

```
命令执行失败
│
├─ 返回 "无相关结果"
│   ├─ 检查关键词拼写 → 尝试其他关键词
│   ├─ 检查搜索类型 → 搜模组用 search，搜物品用 --type item
│   └─ 检查平台过滤 → 是否使用了 --no-mcmod/--no-mr
│
├─ 网络错误
│   ├─ MC百科 服务不可用 → 可能维护中，使用 --platform modrinth 或稍后重试
│   └─ Modrinth API 错误 → 检查网络或稍后重试
│
└─ 解析错误
    ├─ 无法解析模组 ID → 使用 URL 直接查询
    └─ JSON 解析失败 → 检查 stderr 输出
```

---

## MC百科返回空结果

**症状**：`所有平台均无 [关键词] 相关结果`，但确认关键词存在

**排查步骤**：

1. **检查网络连接**：
   ```bash
   curl -s -H "User-Agent: Mozilla/5.0" "https://search.mcmod.cn/s?key=test&filter=0" | head -c 500
   ```

2. **判断是否被限流**：
   - 返回空 HTML 或 `<1000` 字符：被临时封禁
   - HTTP 429/503：服务器限流或维护

3. **检查搜索类型**：
   - 模组搜索：`search <关键词>`
   - 物品搜索：`search <关键词> --type item`

**解决方案**：
1. 稍后重试（限流通常持续 5-15 分钟）
2. 使用 `--cache` 利用缓存（TTL 1小时）
3. 更换搜索关键词（尝试中英文、缩写）

## 缓存管理

**查看缓存状态**：
```bash
ls -lh ~/.cache/mc-search/
```

**清理缓存**：
```bash
rm -rf ~/.cache/mc-search/
```

**缓存 TTL**：1 小时（所有类型，需 `--cache` 参数启用）

**适用场景**：
- 缓存中有正确结果，可立即响应
- 网络不稳定或被限流时
- 需要快速测试而不等待网络请求

## Modrinth API 错误

**症状**：`[mod_id] 查询依赖时网络错误` 或 `Modrinth API 请求失败`

**排查步骤**：

1. **检查网络连接**：
   ```bash
   curl -s "https://api.modrinth.com/v2/project/sodium" | python -m json.tool
   ```

2. **检查限流状态**：
   - HTTP 429：触发了速率限流
   - API 限制：360 请求/小时
   - 等待 1 小时自动重置

3. **检查返回内容**：
   - HTTP 403/500：服务端问题，稍后重试；wiki 403 请检查 `curl_cffi>=0.15.0` 是否安装
   - 返回空或 JSON 错误：检查网络或 User-Agent

**解决方案**：
1. 稍后重试（等待 5-15 分钟）
2. 使用 `--cache` 利用缓存数据
3. 减少频繁请求（特别是 `show --full` 命令）

## minecraft.wiki 搜索无结果

**症状**：`minecraft.wiki 无 [关键词] 相关结果`

**原因分析**：
1. minecraft.wiki **只收录原版内容**（方块、物品、生物、机制），不包含模组
2. Termux 环境下 minecraft.wiki 间歇性不可达
3. MediaWiki API 端点被防火墙阻止

**验证网络**：
```bash
curl -s -H "User-Agent: mc-search/5.4.0-dev" "https://minecraft.wiki/api.php?action=query&list=search&srsearch=Diamond&format=json" | head -c 300
```

**建议**：
- 模组相关 → 用 `search` 或 `full`（走 MC百科/Modrinth）
- 原版内容 → 用 `wiki`（minecraft.wiki 只收录原版游戏内容，如方块、物品、机制等）

## MC百科 class ID 解析失败

**症状**：`无法解析模组 ID` 或 `MC百科 搜索结果结构变化`

**原因**：
- MC百科搜索页面 HTML 结构变化
- 网络超时导致 HTML 截断
- 关键词在 MC百科 无结果

**解决方案**：
1. 直接使用 MC百科 URL：
   ```bash
   mc-search show https://www.mcmod.cn/class/18710.html
   ```
2. 改用 Modrinth 搜索：
   ```bash
   mc-search --json show <模组名> --full
   ```

## 速度问题

**性能基准**：
| 操作 | 预期耗时 | 说明 |
|------|----------|------|
| 搜索（四平台并行） | 2-5 秒 | 取决于最慢平台 |
| 详情查询（show --full） | 3-8 秒 | 需多次 API 请求 |
| 依赖查询（show --deps） | 1-3 秒 | 单次 API 请求 |
| Wiki 搜索 | 1-3 秒 | MediaWiki API |

**优化建议**：
- 使用 `--cache` 减少网络请求
- 避免频繁重复请求
- 超时时间：默认 15 秒，可适当调整

## Modrinth 搜索结果不准确

**症状**：搜索 "Spawn" 但返回 "Spawn Animations" 作为第 1 结果

**原因**：
- Modrinth API 使用自己的相关性排序（考虑下载量、热度等）
- 工具的搜索排序只在**融合结果**时生效

**解决方案**：
1. 使用 `full` 命令，它会先用原始关键词直搜 Modrinth 并精确匹配 slug
2. 使用更具体的关键词（如 "Spawn mod" 而非 "spawn"）
3. 检查融合结果中的 `source` 字段，确认是否来自正确平台

## 调试模式

**使用 `--json` 查看完整返回**：
```bash
mc-search --json search 关键词 2>&1 | python3 -m json.tool
```

**查看平台统计**：
```bash
mc-search --json search 关键词 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(json.dumps(d.get('platform_stats', {}), indent=2))
"
```

**检查相关性评分**（内部使用）：
```python
# 在 Python 中测试评分逻辑
import sys
sys.path.insert(0, 'skills/mc-search/scripts')
from core import _calc_name_score

query = "spawn"
names = ["spawn", "spawn animations", "orespawn"]
for name in names:
    score = _calc_name_score(name.lower(), query.lower())
    print(f"{name:30s} → score={score}")
```

## 常见问题 FAQ

### Q1: 为什么搜索结果不准确？

**A**: Modrinth/MC百科 API 使用热度排序（下载量、关注度）。建议：
- 使用精确模组名或 slug
- 使用 `show --full` 精确匹配

### Q2: 如何查看完整版本历史？

**A**: 使用 `show --full` 命令：
```bash
mc-search --json show sodium --full
```

### Q3: 如何判断数据是否完整？

**A**: 检查 `_truncated` 字段，详见 [result-schema.md](result-schema.md#_truncated-元数据字段)。

### Q4: 缓存会导致数据过时吗？

**A**: 缓存 TTL 1 小时，超过后自动失效。实时性要求高的场景不建议使用 `--cache`。

### Q5: 如何按作者搜索？

**A**: 使用 `--author` 选项：
```bash
mc-search --json search --author jellysquid_
```

---

## 详细错误码参考

完整的错误码定义和解决方案，请查看 **[errors.md](errors.md)**。
