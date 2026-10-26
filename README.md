# FlashPack（快闪）v1

把项目打包成**一份给网页 AI 阅读的、受 token 预算约束的单文件快照**。本地运行、离线、纯静态分析，不调用任何 AI API。

默认输出 **XML**（解析更准），可切换 Markdown。文件清单使用连续 FID（`F001…`），按被引用次数 `refs` 降序排列；正文与清单、FID 严格对齐。

> **English:** FlashPack turns a project into a single, budget-aware context that a web AI can read directly, cite by FID, and prioritize under a token budget — so it builds a global picture faster with fewer repeated uploads and less wasted reading.

```bash
python -m flashpack ./my-project
# -> ./flashpack-snapshot.xml

python -m flashpack ./my-project --format markdown --output snapshot.md
```

## 它有什么用

FlashPack 不是“把代码压缩存起来”，而是**给网页版 AI 准备一份可读、可引用的项目上下文**。

网页版 AI 通常不能直接遍历你的本地仓库。你要么逐个文件上传，要么粘贴一大段代码；前者慢，后者容易撑爆上下文，而且模型不知道哪些文件重要。FlashPack 在本地完成这些事：

- 找出项目里的代码和关键构建/依赖配置；
- 按被引用次数 `refs` 排序，给每个文件一个稳定 ID（`F001…`）；
- 在 token 预算内保留最值得读的正文，并标明 `full / skeleton / truncated / omitted`；
- 把目录树、依赖关系、阅读协议、被排除文件原因一起打包。

结果是一份约 32k token 的结构化快照，而不是几十万 token 的仓库。把它上传或粘贴到网页 AI，模型就能在这个上下文里回答问题、做 code review、定位 bug、给出修改方案。

适合：接手陌生项目、跨文件 bug 定位、代码审查、方案讨论、让 AI 先建立全局观再动手。

不适合：需要真正运行项目、需要完整二进制/资源、要求逐字节无损备份整个仓库。

## 给网页版 AI 的推荐用法

1. 在本地生成快照：
   ```bash
   python -m flashpack ./my-project --budget 32000 --output snapshot.xml
   ```
2. 把 `snapshot.xml` 或 `snapshot.md` 上传/粘贴到网页 AI。
3. 先让 AI 阅读快照最前面的“阅读协议 / 文件清单 / 依赖索引”，再提问。
4. 需要细节时，让 AI 指出具体 FID，你再展开对应文件，而不是一次性把所有代码塞进去。

推荐开场 prompt：

```text
你是一名资深软件工程师。请先阅读这份 FlashPack 快照的
“阅读协议 / 文件清单 / 依赖索引”，再回答我的问题。

规则：
- 引用文件时使用 FID（如 F003），不要只写路径。
- full 可以直接分析；skeleton/truncated 只能基于可见签名或片段推断，
  并明确说明不确定性；omitted 不要臆测内容。
- 如果需要更多细节，请告诉我应该展开哪个 FID，
  或需要我补充哪个文件。

我的问题：……
```

实际对话示例：

```text
请先看 F001、F003、F006，解释这个请求从入口到返回经过了哪些层。
如果 skeleton 里看不到关键实现，请指出需要我补充哪个文件。
```

这样 AI 的注意力集中在真正相关的文件上，而不是把上下文浪费在锁文件、生成代码、无关文档上。

## 快照里的“阅读协议”是做什么的

生成文件最前面的 `<protocol>` / `## 0. 阅读协议` 不是装饰，它是给 AI 的**阅读约束**：

- **coverage**：覆盖了多少文件、哪些被排除、是否有预算省略；
- **status legend**：`full / skeleton / truncated / omitted` 分别代表什么；
- **reference rule**：引用文件请用 FID，路径只供人类阅读；
- **reading order**：正文按 `refs` 降序，`F001` 是最核心文件；
- **warning**：skeleton/truncated 的实现细节没有提供，不要凭空推测。

它把“应该怎么读这份材料”提前告诉模型，减少模型乱翻文件、重复提问、把 `omitted` 当成“项目里没有”的问题。你不需要每次对话都重复解释这些规则。

## 它如何节省 token

节省来自“选择 + 结构化”，而不是无脑压缩：

1. **单文件聚合**：一次上下文，替代多次上传/粘贴多个文件。
2. **过滤**：二进制、锁文件、生成代码、被排除文件不进入正文。
3. **压缩**：去注释/空行、import 归一化、骨架、截断，优先保留结构与签名。
4. **按 refs 排序**：高引用文件优先全文，边缘文件只留清单。
5. **预算硬约束**：超过预算先降级低 refs 文件；最终仍超才进入清单模式并退出 4。
6. **按需追问**：先看骨架，需要时再让 AI 要求展开某个 FID，避免一次性塞满。

例如一个约 300k token 的仓库，经过过滤、骨架和预算控制后可能变成 32k 量级的快照。具体节省取决于项目结构、语言和预算；FlashPack 不承诺“信息无损”，它承诺的是**在预算内保留最值得读的部分，并把所有省略明确记账**。

## 快速开始

要求 Python 3.11+，核心功能零第三方依赖。

```bash
# 直接运行
python -m flashpack ./my-project

# 指定预算与输出
python -m flashpack ./my-project --budget 32000 --output snapshot.xml

# Markdown
python -m flashpack ./my-project --format markdown --output snapshot.md

# 覆盖默认语言白名单（逗号分隔，可重复）
python -m flashpack ./my-project \
  --include "*.py,*.ts,*.json" \
  --exclude "**/generated/**,*.min.js" \
  --output snapshot.xml

# 关闭骨架 / 关闭密钥扫描 / 带时间戳
python -m flashpack ./my-project --no-skeleton --no-secret-scan --timestamp
```

安装 CLI：

```bash
pip install -e .
flashpack --help
```

可选依赖：

```bash
pip install -e ".[tokens]"  # tiktoken cl100k_base 精确计数
pip install -e ".[ast]"     # tree-sitter + 语言包
pip install -e ".[all]"

# JS/TS 骨架使用的 grammar 需要在安装阶段预取一次；FlashPack 运行时不会联网。
python -c "import tree_sitter_language_pack as p; p.download(['javascript','typescript'])"
```

如果 grammars 未预取，FlashPack 会离线回退到基础压缩，并在 stderr 警告一次。

## 输出结构（XML，默认）

```xml
<snapshot project="my-project" files_included="42" files_total="45"
          total_lines="12400" estimated_tokens="28400" budget="32000"
          sort="refs-desc" skeleton="on">
  <protocol>
    <coverage>...</coverage>
    <status-legend>...</status-legend>
    <reference-rule>...</reference-rule>
    <reading-order>...</reading-order>
    <warning>...</warning>
  </protocol>
  <tree>...</tree>
  <file-list>
    <file id="F001" path="src/core/types.py" lang="python" lines="85"
          tokens="720" status="full" refs="8" depends-on=""/>
  </file-list>
  <contents>
    <file id="F001" path="src/core/types.py" status="full">
      <![CDATA[...]]>
    </file>
  </contents>
  <skipped>
    <file path="assets/logo.png" reason="binary"/>
  </skipped>
</snapshot>
```

严格约束：

- `<file-list>` 每个 `<file>` 固定 8 个属性，顺序为 `id / path / lang / lines / tokens / status / refs / depends-on`。
- `depends-on` 是**无空格**逗号分隔 FID；无依赖为空字符串。
- `<contents>` 每个 `<file>` 只保留 `id / path / status` 三个属性。
- 代码用 CDATA；内容含 `]]>` 时自动拆成多段 CDATA。
- `<skipped>` 可选，位于 `</contents>` 之后；每个条目只有 `path / reason`。
- 默认不写 `generated_at`；只有 `--timestamp` 才写 ISO 8601 UTC 秒级时间。

## 输出结构（Markdown）

`````markdown
# 项目快照：my-project

## 0. 阅读协议
- 覆盖 ...
- 状态：full=全文；skeleton=仅签名与docstring；truncated=已截断；omitted=仅列名
- 引用文件请用 ID（如 F003），路径仅供人类阅读。
- 正文已按被引用次数降序排列，F001 为最核心文件。
- 骨架/截断文件的实现细节未提供，请勿臆测。

## 1. 目录树
## 2. 文件清单
| ID | 路径 | 语言 | 行数 | Tokens | 状态 | 被引用次数 |

## 2.5 依赖索引
- F001 src/core/types.py → —

## 3. 文件内容
### [F001] src/core/types.py
````python
...
````

## 4. 被排除文件
- assets/logo.png — binary
`````

- 表格固定七列，`depends-on` 不进入 Markdown 表格（D15）。
- 正文代码围栏至少 4 个反引号；内容含 4 个反引号时自动升级为 5 个。
- `omitted` 文件不在正文展开，只在清单、依赖索引和第 4 节出现。
- 目录树最多 4 层，超出用 `...` 省略。

## 四态与 FID

| 状态 | 含义 |
|---|---|
| `full` | 全文（已应用基础压缩：去注释/空行、import 归一化） |
| `skeleton` | 仅签名与 docstring，函数体用 `# [body omitted, N lines]` 占位 |
| `truncated` | 已截断，末尾有 `# [truncated: showing X of Y lines]` |
| `omitted` | 只进清单；正文不展开 |

只有进入清单的文件才使用这四态。二进制、空文件、锁文件、glob 排除、密钥命中、编码失败等**不分配 FID**，只出现在 `skipped` / 第 4 节；超大文件进入清单并标 `truncated`。

FID 分配规则：`(-refs, path)` 排序，refs 高者 FID 小；refs 相同时按路径字母序。正文顺序、清单顺序、FID 顺序三者一致。

## 依赖分析

- Python：`ast` 解析 import/from-import，解析相对导入与内部模块。
- JavaScript/TypeScript：正则抓取 `import` / `export … from` / `require()` / 动态 `import()`，解析相对路径和扩展名/index。
- C/C++ 与 PHP/Ruby/Perl：保守的正则 fallback（`#include "..."`、`require`）。
- 外部库不映射 FID；只统计内部依赖。
- `refs` 是被多少个内部文件引用；`depends-on` 是依赖的 FID 列表（排序、无空格）。

## 压缩链（按序）

1. **去注释 + 去空行**：Python 用 `tokenize`（保留 docstring）；JS/TS 用字符串感知的扫描器；其他语言用通用规则。
2. **骨架**（默认开启）：Python 用 `ast`；JS/TS 仅在 tree-sitter 及语言包可导入时执行。解析失败/依赖缺失时回退基础压缩，**不伪造 JS/TS 骨架**。
3. **Import 归一化**：连续顶层 import 块压成一行，如 `# imports: os, sys, .core.engine`；`from x import *` 保持原样；函数体内 import 不处理。
4. **长文件截断**：超过 `--max-file-size`（默认 500 KB）或内部行数阈值时截断，Python 尽量落在顶层节点边界。

## 预算与极低预算

预算按最终快照文本计数（`tiktoken cl100k_base`；不可用时 `len // 4` 降级并警告）。

- **阶段 1**：按 refs 从低到高把边缘文件降级为 `omitted`，直到总 token ≤ budget；成功退出 0。skeleton 文件优先保留。
- **单文件超预算**：先强制截断，状态 `truncated`。
- **阶段 2（清单模式）**：所有正文都省略后仍超预算，则只输出 protocol/tree/file-list/skipped，`<contents/>` 为空；所有文件 `omitted`。不再逐项列 budget skipped，只在 coverage 说明数量。
- **budget < 1000**：直接进入清单模式，不执行逐文件降级。
- 清单模式下，若最终文本仍 > budget，退出码 **4**，但**仍写出结果**；若 ≤ budget，退出码 0。

## 密钥扫描

默认开启。扫描每个入选文件前 1 KB：

- AWS key、私钥头、`api_key` / token / password 赋值、高熵字符串、OpenAI key。
- 命中后**阻止打包该文件**，stderr 输出 `路径:行号 (类型)`，不输出密钥内容，并提示用 `--exclude` 排除。
- `--no-secret-scan` 强制关闭（此时密钥可能进入快照，慎用）。

## 配置

项目根目录可放 `.flashpack.toml`：

```toml
[flashpack]
format = "xml"
budget = 32000
include = ["*.py", "*.ts", "package.json"]
exclude = ["**/generated/**", "*.min.js"]
max_file_size = 512000
tree_depth = 4
secret_scan = true
skeleton = true
timestamp = false
```

优先级：CLI > 配置文件 > 默认值。TOML 格式错误只警告并回退默认值，不中断。`include` 一旦显式指定就**完全覆盖**默认白名单，不与默认列表合并。

## CLI

| 参数 | 默认 | 说明 |
|---|---|---|
| `target` | 必填 | 项目目录 |
| `--format` | `xml` | `xml` / `markdown` |
| `--budget` | `32000` | token 预算 |
| `--include` | 默认语言白名单 | 逗号分隔 glob；显式指定时覆盖默认 |
| `--exclude` | 空 | 逗号分隔 glob |
| `--max-file-size` | `500 * 1024` | 超过则截断，不直接跳过 |
| `--tree-depth` | `4` | 目录树最大层数 |
| `--output` | `./flashpack-snapshot.xml` 或 `.md` | 输出路径 |
| `--no-secret-scan` | 关闭扫描 | 关闭密钥扫描 |
| `--no-skeleton` | 关闭骨架 | 关闭骨架提取 |
| `--timestamp` | 关闭 | XML 写 `generated_at` |
| `--version` | — | 版本 |

默认 include 聚焦“程序代码 + 关键构建/依赖配置”，包含 Python/JS/TS/JVM/C/C++/Go/Rust/Ruby/PHP/Swift/Dart/Lua/Perl/R/Elixir/Erlang/Haskell/Clojure/F#/VB/Shell/Web/SQL，以及 `pyproject.toml`、`package.json`、`go.mod`、`Cargo.toml`、`Makefile`、`Dockerfile` 等；**不包含通用 `.md`、`.json`、`.yaml`、`.toml`、`.xml`**，避免把文档/数据卷入。需要时用 `--include` 显式添加。

## 退出码

| 退出码 | 含义 |
|---|---|
| 0 | 成功（含清单模式但最终 ≤ budget） |
| 1 | 参数错误 / 内部错误 |
| 2 | 目标目录无效 |
| 3 | 输出写入失败 |
| 4 | 最终输出仍超预算（仍写出结果） |

## 确定性

默认模式满足：同一输入、同一环境、同一依赖版本下输出**逐字节一致**。

- 文件发现后路径排序；FID 按 `(-refs, path)`。
- 所有 set/dict 在输出前排序；`depends-on` 按 FID 数字排序；`skipped` 按 path 排序。
- 默认不输出时间戳；`--timestamp` 仅额外改变 `generated_at`。
- 只输出相对路径；`project` 只取目标目录名。
- 不输出浮点数。
- 警告只写 stderr，不进入快照。

## 测试

```bash
python -m unittest discover -s tests -v
```

测试覆盖：XML/Markdown 结构、FID/refs/depends-on、四态、目录树深度、协议、密钥扫描、skipped、预算两阶段/清单模式、退出码、配置、确定性与 CDATA/围栏边界。

## 已知限制

- `.gitignore` 只读取项目根目录；嵌套 `.gitignore` 不处理。
- 默认 include 是白名单，未匹配文件不会进入清单，也不计入 `files_total`。
- JS/TS 骨架依赖 tree-sitter；当前环境未安装时只做基础压缩，并在 stderr/protocol 提示。
- 截断行数阈值（默认 2000 行）是规格未明确时的保守内部值；主要触发条件是 `--max-file-size` 或单文件超预算。
- 不解析 docx/pdf 等二进制正文；它们按二进制跳过。
- tiktoken 未安装时 `len // 4` 只是估算，预算可能偏乐观。
