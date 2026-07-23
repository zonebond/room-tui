---
name: paper-derived
description: 从样例模板和输入资料派生结构化开发文档。支持模板注册、文档生成/修改、Session-Driven 大文档生成。当用户请求生成设计文档、整理资料为格式文档、注册模板或修改文档章节时使用。
---

# Paper Derived

从样例模板和输入资料派生结构化开发文档。

## 触发条件

当用户请求以下任意一项时触发本 skill：
- 「生成一份 XX 设计文档」
- 「把这些资料整理成 XX 格式的文档」
- 「给我一份样例文档，帮我注册成模板」
- 「修改这份文档的某个章节」
- 任何涉及从输入素材生成结构化文档的请求

## 你的角色

你是**编排者**。paper-derived 引擎是你的工具——它**不调 LLM**，只负责构造 prompt 和解析结果。

关键：引擎构造的 prompt 需要一个 LLM 去执行，但**执行者不应是你（主编排上下文）**。凡是需要执行 prompt 来生成/抽取内容的地方，都通过 **Task 工具下放给子代理**执行；你只负责发命令、落盘 prompt、收状态、做决策。理由见下方「上下文纪律」。

## 引擎路径

```bash
PAPER_DERIVED_BIN="./paper-derived"
```

> 路径相对于 skill 目录 `paper-derived/skill/`。通过 `cd $(dirname $PAPER_DERIVED_BIN)` 切换后执行。

每条命令的通用模式：

```bash
$PAPER_DERIVED_BIN <command> <args>                 # 构造 prompt（全量打 stdout，禁止在编排中使用）
$PAPER_DERIVED_BIN <command> <args> --out p.md       # 构造 prompt 并写入文本文件（必用，避免灌主上下文）
$PAPER_DERIVED_BIN <command> <args> --parse r.json   # 解析子代理产出的响应
```

`--out` 写出的是**纯文本文件**（不是 JSON）：`==== SYSTEM ====` 之后是系统指令，`==== USER ====` 之后是任务。真实换行、无超长单行，子代理用 Read 工具可完整读取。stdout 只回一行摘要（含 `prompt_tokens` 估算）。

## 🔴 上下文纪律（全流程铁律，防 token 爆炸）

本 skill 的每个流程本质是「引擎构造 prompt → 某个 LLM 执行 → 引擎解析」。爆上下文的唯一根源，是让**主编排上下文**去执行那些体积巨大的 prompt（受 `--budget` 约束可达数万 token）+ 承接生成结果，跨十几个 Section 累加必然超限，且 auto-compact 会摘丢跨节精确状态。因此：

1. **主 Agent 绝不亲自执行引擎输出的 prompt。** 一律用 Task 工具起子代理执行（工具权限仅需 Read/Write）。
2. **prompt 与响应一律走文件。** 用 `--out` 把 prompt 写入 `prompts/`；子代理把响应写入 `responses/`；主 Agent 只对 `responses/*.json` 跑 `--parse` 并读取其**状态**。解析产物大的命令（`input register`、`gen extract`、`gen generate`）一律加 `-O <file>` 落盘，stdout 只回状态摘要。
3. **主 Agent 绝不读取 `prompts/*`、`responses/*`、输入资产原文的正文内容。** 需要排查时，派子代理去读并回报要点。
4. **中间状态全部落盘**（ContextStore、checkpoint、prompts/responses），不驻留在你的对话里。这样即使 `/clear` 也能凭 `session_id` 续传。

### 子代理执行协议（通用）

对任何构造 prompt 的命令：

```bash
$PAPER_DERIVED_BIN <cmd> <args> --out prompts/<key>.md       # ① 落盘 prompt（文本格式）
# ② 起子代理，指令：读 prompts/<key>.md（==== SYSTEM ==== 之后是系统指令，==== USER ==== 之后是任务），
#    严格按其要求生成，把完整响应原样写入 responses/<key>.json，只回 DONE <key>，不输出正文到对话
$PAPER_DERIVED_BIN <cmd> <args> --parse responses/<key>.json # ③ 主 Agent 只看返回状态
```

## 引擎路径下的命令

所有命令使用 `$PAPER_DERIVED_BIN`。命令清单见 `references/commands.md`。

## 工作流路由

根据用户请求，**读取**对应工作流文件后再执行：

```
用户请求类型？
  │
  ├── 注册模板 → workflows/register.md
  ├── 修改已有文档 → workflows/revise.md
  │
  └── 生成文档 → 评估以下条件：
        │
        ├── Section 数 > 15？
        ├── 输入资料 > 3 份 或 单份 > 30K 字符？
        ├── 用户提到"暂停/继续/分步"？
        ├── 模板有 section_dependencies？
        │
        ├── 任一为是 → workflows/session.md（Session-Driven 生成）
        └── 全部为否 → workflows/generate.md（常规生成）
```

> **必须先读取工作流文件再执行。**

## 通用约束

1. **你必须让 prompt 被完整执行，但执行者是子代理，不是你。** 见上「上下文纪律」。
2. **每个 Section 的 lineage 必须真实。** 内容来自哪份输入资产的哪个部分，要标注清楚。
3. **质检失败时区分规则类型。** fixable → 自动修（修复循环也走子代理）；input_dependent → 问用户。
4. **结构完整性铁律。** 输出文档必须包含模板定义的全部 Section——资料不足时以 `placeholder` 保留骨架和标题，绝对禁止 skip/omit 任何 Section。

## Session 模式行为约束

使用 Session-Driven 生成时（workflows/session.md），额外遵守：

- **你是纯编排者，不是生成器。** 每个 Section 的 prompt 由子代理执行，你只发命令、落盘 prompt、收 DONE、`--parse` 看状态。
- **不要手动管理上下文。** `session prompt` 自动组装上下文，你不决定哪些 entity 进 prompt，也不读 prompt 内容。
- **prompt 走 `--out`，响应走 `responses/`。** 绝不让 `session prompt/feed/summarize` 的大 prompt 打进主上下文（用 `--out` 或 `> file`）。
- **不要写死章节号。** 用 `{{ref:section-id}}` 占位符，`session assemble` 自动替换。
- **不要绕过 session 命令直接操作原始文件。** 禁止 `cat`/`grep` 输入资产原文拼上下文——这些重活封装在 CLI 内部；需查数据用 `session search`。
- **每个 Section 生成后默认执行 summarize（走子代理）。** 摘要存入 ContextStore，让后续每个 Section 的 prompt 用摘要代替前序整节原文，控制下游 prompt 体积。
- **input register / gen validate 等含大 prompt 的步骤同样走子代理**，不要因为它们「不在循环里」就在主上下文执行。

## 参考文件（按需读取）

| 文件 | 内容 | 何时读取 |
|------|------|----------|
| `references/commands.md` | 全部 CLI 命令速查（含 `--out` 用法） | 忘了命令用法时 |
| `references/data-models.md` | 数据模型字段定义 | 需要理解返回值结构时 |
| `references/session-states.md` | Session 状态机 + 错误恢复 + 预算调优 | Session 遇到错误或需调优时 |
| `references/large-doc-strategies.md` | 大文档策略对比（分块/分批/Session） | 拿不准该用哪种策略时 |
| `examples/api-design-workflow.md` | API 设计文档生成示例 | 首次使用时参考 |

## 安装

```bash
./scripts/build-cli.sh                                    # 先构建二进制
./skill/install.sh --adapter claude                       # Claude Code（用户级）
./skill/install.sh --adapter claude --project-dir ./my-project   # 项目级
./skill/install.sh --adapter copilot --project-dir ./my-project  # GitHub Copilot
./skill/install.sh --adapter opencode                     # OpenCode
```
