# FlashPack v1 实现设计

> 对应冻结规格 D1–D19 / M1–M17。默认 XML、32000 tokens、500 KB、目录树 4 层、骨架默认开启。

## 1. 决策落地

| 决策 | 实现 |
|---|---|
| D1 本地离线 CLI | `flashpack.cli`，无网络、无 AI API |
| D2 单文件快照 | 一个 XML 或 Markdown 文件 |
| D3 默认 XML | `config.DEFAULT_FORMAT = "xml"`；`--format markdown` |
| D4 表格清单 | Markdown 七列表格；XML `<file-list>` |
| D5 F001… | `budget.assign_fids` |
| D6 四态 | `model.Status` 仅 FULL / SKELETON / TRUNCATED / OMITTED |
| D7 树深 4 | `tree.render_tree`，超出 `...` |
| D8 refs + depends-on | `deps.analyze`；XML 8 属性 |
| D9 正文锚点 | `### [Fxxx] path` |
| D10 阅读协议 | `<protocol>` / `## 0. 阅读协议` 最前 |
| D11 密钥扫描 | `sources._secret_hits`，命中阻止 |
| D12 压缩链 | `compress.compress`：M11a→b→c→d |
| D13 refs 降序 | `(-refs, path)` |
| D14 skeleton 默认 | `Config.skeleton=True`，`--no-skeleton` |
| D15 MD 依赖索引 | `## 2.5 依赖索引`，表格不含 depends-on |
| D16 确定性 | 默认无 `generated_at`；`--timestamp` 才写；所有集合排序 |
| D17 跳过文件 | `Skipped` + `<skipped>` / `## 4`；无 FID |
| D18 JS/TS 骨架可选 | 仅 tree-sitter；缺失时 warning 一次、状态非 skeleton |
| D19 两阶段预算 | `budget.apply_budget`：逐文件 omitted → 清单模式 |

## 2. 流水线

```text
M1 CLI
 └ M2 配置（.flashpack.toml）
   └ M3 发现（.gitignore / 黑名单 / 符号链接）
     └ M4 过滤（二进制 / 大小 / include / exclude / 锁文件）
       ├ M5 密钥扫描（命中 -> skipped）
       └ M6 解码（UTF-8 / latin-1 / BOM / 换行）
         ├ M7 依赖（Python ast / JS-TS regex / 通用 fallback）
         ├ M8 token（cl100k_base / len//4）
         ├ M11 压缩（a 去注释，b 骨架，c import 归一化，d 截断）
         └ M12 状态
           └ M13 预算
             └ M10 FID + 清单（refs 降序）
               ├ M9 目录树
               ├ M14 阅读协议
               └ M15/M16 组装与 XML/Markdown 渲染
                 └ M17 退出码
```

## 3. 数据模型

```python
Status = FULL | SKELETON | TRUNCATED | OMITTED

File:
  path, abs_path, lang, lines, size
  status, text (original/prefix), content (rendered), tokens
  refs, depends_on (FIDs), fid, reason
  encoding

Skipped:
  path, reason   # binary / empty / lockfile / excluded / secret / encoding-error / budget
```

- `File.tokens` 保留压缩后候选 token；即使因预算变 `omitted`，清单仍显示该值。
- `Skipped` 不分配 FID，按 path 排序。
- `Snapshot` 保存 `files_total / ignored_dirs / list_mode / budget_* / js_fallback`。

## 4. 关键实现选择（规格未明示处）

1. **默认 include 是白名单**：未匹配文件不进入清单、不计入 `files_total`、不写 skipped；这样避免文档/数据文件把快照撑大。显式 `--exclude` 仍然记 skipped。
2. **基础压缩后的 full**：M11a/M11c 默认执行，因此 `full` 表示“未骨架化、未截断、未省略”，注释/空行和 import 块可能已被压缩；状态图例仍按规格固定描述。
3. **截断行数**：`max_file_size` 是主阈值；内部 `DEFAULT_MAX_LINES = 2000` 是规格未给 `max_lines` 时的保守值。超过任一阈值即截断。
4. **单文件超预算**：在全局降级前先截断到预算的一半左右，保留 `truncated`，避免单个大文件直接变 `omitted`。
5. **Skipped 的 budget 条目**：阶段 1 逐项列出；进入清单模式（阶段 2 或 budget<1000）后移除，只在 coverage 里计数。
6. **空文件**：按 D17 跳过，reason=empty，不分配 FID。
7. **编码失败**：进入清单并标 `omitted`，同时写 skipped reason=encoding-error；由于 latin-1 兜底，实际极少触发。
8. **JS/TS 回退**：无 tree-sitter 语言语法时（或语法未预取）不产生 skeleton；只在 stderr 警告一次，并在 protocol warning 追加固定说明。运行时不自动下载语法包，保持 D1 离线。
9. **默认无 generated_at**：`--timestamp` 才写入秒级 UTC；默认满足逐字节一致。
10. **tree-sitter 语法预取**：`pip install -e ".[ast]"` 后需在安装阶段执行一次 `tree_sitter_language_pack.download([...])`；运行时不联网。

## 5. 预算算法（M13 / D19）

```text
if budget < 1000:
    清单模式：all omitted, contents empty, skipped 去掉 budget
    exit = 4 if output_tokens > budget else 0

阶段 1:
    单文件超 budget -> truncate
    while output_tokens > budget and 还有非 omitted:
        选 victim:
          key = (status == skeleton, refs, path)
          即先丢非 skeleton、低 refs、路径靠前的文件
        victim = omitted + skipped(budget)
    若 output_tokens <= budget: exit 0

阶段 2（清单模式）:
    all omitted; contents empty; 移除 skipped(budget)
    coverage 说明 budget 省略数量
    exit = 4 if output_tokens > budget else 0
```

- skeleton 文件最后被丢。
- 退出码 4 只在最终输出仍超预算时出现；仍写出文件。
- 预算计数使用最终渲染文本，而不是仅正文。

## 6. 确定性（D16）

- 文件发现后路径排序；FID 排序固定。
- `depends-on` 转 FID 后按数字排序。
- `skipped`、树、协议统计、依赖索引均排序或按固定 FID。
- 默认无时间戳；`--timestamp` 只影响 `generated_at`。
- 只输出相对路径与目标目录名。
- 警告只走 stderr。
- 无数值浮点输出。

## 7. 输出不变量

### XML

- 根：`snapshot`；`generated_at` 条件属性。
- 顺序：`protocol → tree → file-list → contents → skipped`。
- file-list 8 属性固定顺序。
- contents 3 属性固定顺序。
- skipped 2 属性固定顺序。
- CDATA 拆分 `]]>`。

### Markdown

- 顺序：标题 → 0 → 1 → 2 → 2.5 → 3 → 4。
- 表格固定七列。
- 围栏至少 4 个反引号。
- `omitted` 不展开正文。

## 8. 测试

`tests/test_spec.py` 覆盖 1–20 条验收与 D16–D19；`tests/test_units.py` 覆盖压缩、树、默认白名单、空项目。

```bash
python -m unittest discover -s tests -v
```

当前环境未安装 `tiktoken` 与 tree-sitter，因此：
- token 计数走 `len // 4` 并给出固定 warning；
- JS/TS 走基础压缩，不产生 skeleton，并给出固定 warning；
- Python AST 骨架、预算、四态、双格式、CDATA、确定性和退出码均实测。
