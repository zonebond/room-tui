# 工作流四：Session-Driven 生成（大文档推荐）

> **你是纯编排者，不是生成器。** 在 Session 模式下：CLI 内部的 ContextStore 决定每个 prompt 装什么；**prompt 的执行由子代理（Task 工具）完成，不由你完成**。你自己的上下文只承载：命令、状态报告、决策。你**绝不**读取 prompt 文件或响应文件的正文内容。

当文档 Section 数量多（>15）、输入资料体积大、或需要断点续传时，使用 Session-Driven 模式。

## 🔴 核心原则一：结构完整性优先

**输出文档必须包含模板定义的全部 Section，一个都不能少。**

- `session init` 自动从模板构建完整 Section 骨架（初始 `status: empty`）
- 每个 Section 都必须经过生成（资料不足也输出 placeholder，绝不 skip/omit）
- `session assemble` 合并全部 Section，不会缺失
- 遇到 `feed_more` → 告知用户缺什么，但**仍用 placeholder 生成该 Section**

## 🔴 核心原则二：上下文纪律（防 token 爆炸的铁律）

这是本工作流能跑完大文档的前提，违反任意一条都会导致中途撑爆上下文：

1. **主 Agent 绝不执行任何 prompt。** `session prompt` / `session feed` / `session summarize` / `input register` / `gen *` 构造的 prompt，一律下放子代理执行。原因：单个 prompt 受 `--budget` 约束（默认 60000），体积可达数万 token；若在主上下文执行，prompt 正文 + 生成结果双双堆积，十几个 Section 必然超限，且 auto-compact 会把跨节精确状态摘丢。
2. **prompt 与响应一律走文件，不走 stdout。** 用 `--out` 把 prompt 写入文件，主 Agent 只读到一行路径描述符。
3. **主 Agent 绝不 `cat`/读取 `prompts/*` 或 `responses/*` 的正文。** 你只读 `--parse` 的状态、`session next`、`session status` 的结果。需要排查失败时，起一个子代理去读并回报要点。
4. **Section 之间的状态全在磁盘（ContextStore + checkpoint），不在你的对话里。**

## 子代理执行协议（本工作流中执行 prompt 的唯一方式）

只要某个命令的**非 `--parse` 形式**会构造 prompt，就套用此协议。以 Section 生成为例：

**① 落盘 prompt（主 Agent 不看内容）**

```bash
$PAPER_DERIVED_BIN session prompt -s $SID --section <id> --out prompts/<id>.md
# stdout 仅摘要：{"status":"prompt_written","prompt_file":"prompts/<id>.md","prompt_tokens":18000}
```

> prompt 文件是纯文本（`==== SYSTEM ====` / `==== USER ====` 两段，真实换行），子代理 Read 可完整读取，不存在 JSON 单行超长被截断的问题。

**② 用 Task 工具起子代理执行**（工具权限仅需 Read/Write）。交给它的指令：

```
读取 prompts/<id>.md：`==== SYSTEM ====` 之后是你的系统指令，`==== USER ====` 之后是任务。
文件较大时分段 Read（offset/limit），务必读完整。
严格按二者要求生成本 Section 内容——输出的目标格式已在 prompt 内部定义。
把你的完整响应原样写入 responses/<id>.json。
完成后只回复一行：DONE <id>
不要把正文输出到对话里。
```

大 prompt-in 和生成的正文-out 全部发生在子代理的独立上下文窗口，结束即释放。主编排上下文本步只增长几十 token。

**③ 主 Agent 解析（只看状态）**

```bash
$PAPER_DERIVED_BIN session prompt -s $SID --section <id> --parse responses/<id>.json
# → {"status":"ok","section_id":"<id>","progress":"3/38","all_done":false}
```

**④ 摘要（默认执行，见下）** —— 同样走子代理：

```bash
$PAPER_DERIVED_BIN session summarize -s $SID --section <id> --out prompts/sum-<id>.md
# → 子代理读 prompts/sum-<id>.md 执行 → 写 responses/sum-<id>.json → 回 DONE
$PAPER_DERIVED_BIN session summarize -s $SID --section <id> --parse responses/sum-<id>.json
# → {"status":"stored","section_id":"<id>"}
```

## 状态机

```
  init ──→ feeding ──→ generating ──→ assembling ──→ complete
    │         │            │
    │         │            ├── Section done ──→ next
    │         │            ├── Section failed ──→ retry(子代理) / ask user
    │         │            └── data_gaps ──→ feed_more
    │         │
    │         └── 可多次 feed（增量填充）
    │
    └── 中断后恢复 ──→ session next（跳过 done，重置 generating → ready）
```

## Step 0: 确认模板

```bash
$PAPER_DERIVED_BIN template list --json
```

## Step 1: 初始化 Session

```bash
$PAPER_DERIVED_BIN session init -t <template-id> [--budget 60000]
```

无需 LLM。输出 `session_id` 等元信息，记住 `session_id`（下称 `$SID`）。

> **预算调优提示**：现在每个 prompt 都在子代理的独立 200K 窗口里执行，`--budget` 不再需要为「塞进主上下文」而妥协，但**越大 → 每个子代理负载越重、越慢越贵**。若质量允许，建议把每节预算降到 30000–60000，ContextStore 本就只挑相关上下文，多数模板够用。

## Step 2: 注册输入资产

照常用 `input register` 注册每份资料（详见 generate.md）。

> **⚠️ 同样受上下文纪律约束**：`input register` 的执行 prompt 里含有原始资料/分块原文（每块可达 30000 字符）。**这一步也必须走子代理**——否则光注册几份大资料就能在 Session 开始前撑爆主上下文。即用 `--out` 落盘 prompt → 子代理执行 → 写响应文件 → `--parse`。大文档务必叠加 `--chunk-size` + `--slim`。

## Step 3: 喂入上下文库

```bash
# 落盘 feed prompt（含各输入的 summary+entities，可能较大）
$PAPER_DERIVED_BIN session feed -s $SID -i input-1.json -i input-2.json --out prompts/feed.md
# → 子代理读 prompts/feed.md 执行 → 写 responses/feed.json → 回 DONE
$PAPER_DERIVED_BIN session feed -s $SID -i input-1.json -i input-2.json --parse responses/feed.json
```

输出**只有状态报告**：

```json
{
  "status": "ok",
  "entities_extracted": 42,
  "sections_ready": 15,
  "data_gaps": [{"section_id": "security", "hint": "缺少认证方案细节"}]
}
```

多份输入可多次 `session feed` 增量填充。

**data_gaps 处理**：有则告知用户缺什么（缺失 Section 仍生成 placeholder）；无则继续 Step 4。

## Step 4: 逐 Section 生成（循环）

由 `session next` 驱动（无需 LLM）：

```bash
$PAPER_DERIVED_BIN session next -s $SID
```

| action | 含义 | 主 Agent 行为 |
|--------|------|-----------|
| `generate` + `section_id` | 单 Section 可生成 | 走一次「子代理执行协议」 |
| `generate` + `parallel_batch` | 多 Section 可并行 | **并发起多个子代理**，一节一个 |
| `assemble` | 全部完成 | 跳到 Step 5 |
| `feed_more` | 有 Section 缺输入 | 告知用户，补充后 `session feed` |
| `wait` | 有 Section 正在生成 | 等待对应 `--parse` 完成 |

### 单 Section

完整套用上方「子代理执行协议」①→④。

### 并行 Section（`parallel_batch`）

各 Section 互不依赖时，**同时起多个子代理**（Claude Code 的 Task 工具支持并发，建议一批 ≤ 8）。先各自 `--out` 落盘 prompt，再并发派发子代理，各写各的 `responses/<id>.json`，最后逐个 `--parse`：

```bash
# 1) 落盘各 prompt
for S in scope overview terminology; do
  $PAPER_DERIVED_BIN session prompt -s $SID --section $S --out prompts/$S.md
done
# 2) 并发起 3 个子代理，分别处理 prompts/scope.md / overview.md / terminology.md
#    每个子代理只回 DONE，正文写入对应 responses/<id>.json
# 3) 逐个解析（只看状态）
for S in scope overview terminology; do
  $PAPER_DERIVED_BIN session prompt -s $SID --section $S --parse responses/$S.json
done
```

并行相比旧模式收益更大：以前并行也要把 N 份 prompt+正文塞进同一个主上下文，现在每份各占一个子代理窗口，主上下文只收 N 个 DONE。

### Section 摘要（默认执行，勿省）

> 从「可选」升级为**默认执行**。摘要写入 ContextStore 后，后续 Section 的上下文组装用 2–4 句摘要代替前序整节原文——这是控制**每个下游 prompt 体积**的关键闸门。跳过摘要 → ContextStore 只能塞更完整的前序内容 → 后面每个子代理拿到的 prompt 越来越大、越来越慢。仅当明确追求极致速度且模板 Section 基本独立时，才可跳过。

**循环终止**：`session next` 返回 `{"action":"assemble"}`。

## Step 5: 组装最终文档

```bash
$PAPER_DERIVED_BIN session assemble -s $SID [-O output.md]
```

无需 LLM，确定性操作：替换 `{{ref:section-id}}` → 合并全部 Section → 写文件。

## Step 6: 质检与交付

对组装后的文档运行 `gen validate`（其执行 prompt **同样走子代理**，见 generate.md 的修订版）。

| 时机 | 操作 |
|------|------|
| 每 5–8 个 Section | `session status` 检查进度（无需 LLM） |
| 全部完成 | `session assemble` → `gen validate` |
| 生成中明显质量问题 | `revise section` 修后继续（走子代理） |
| ❌ 每节都 validate | 太慢，且单节无完整上下文 |

## 断点续传

每完成一个 Section 自动 checkpoint 到磁盘。中断后：

```bash
$PAPER_DERIVED_BIN session status -s $SID     # 查看状态
$PAPER_DERIVED_BIN session next -s $SID        # 自动跳过 done，重置卡住的 generating → ready
```

因为 prompt/响应都在 `prompts/`、`responses/` 落盘，主上下文即使被清空（`/clear`）也不影响续传——重开会话后凭 `$SID` 继续即可。

## 搜索上下文库

```bash
$PAPER_DERIVED_BIN session search -s $SID "认证方案" [--focus rule:jwt-auth]
```

`session search` 自带 token 预算防护，返回精简结果。**不要用 `cat`/`grep` 绕过它直接查原始文件。** 若某次 `--focus` 结果仍较大，让子代理去读并回报要点，别进主上下文。

## 错误恢复

| 条件 | 主 Agent 行为 |
|------|-----------|
| 子代理回的响应 `--parse` 失败（格式不对），attempt < 3 | 重派子代理执行同一 prompt（重试全程在子代理内） |
| Section 生成 failed，attempt ≥ 3 | 告知用户，建议补充输入或跳过 |
| `session next` 返回 `feed_more` | 告知用户缺什么，补充后 `session feed` |
| `session next` 返回 `wait` | 等 in_progress 的 section `--parse` 完成 |
| feed 后 data_gaps 非空 | 展示给用户决定 |
| token 预算不够 | `session init --budget` 调整，或拆分输入 |

## 交叉引用

生成时用 `{{ref:section-id}}` 占位符，`session assemble` 自动替换为带标题链接。**不要写死章节号。**

## 完整示例流程（纯编排 + 子代理执行）

```bash
SID="sess_abc123"

# 1. 初始化
$PAPER_DERIVED_BIN session init -t srs            # → 记录 $SID
mkdir -p prompts responses

# 2. 注册输入（每份都：--out 落盘 → 子代理执行 → --parse；大文档加 --chunk-size --slim）

# 3. 喂入
$PAPER_DERIVED_BIN session feed -s $SID -i requirements.json -i api-spec.json --out prompts/feed.md
#   → 子代理执行 prompts/feed.md → 写 responses/feed.json → DONE
$PAPER_DERIVED_BIN session feed -s $SID -i requirements.json -i api-spec.json --parse responses/feed.json

# 4. 循环生成（主 Agent 只发命令、收 DONE、看状态）
while true; do
  NEXT=$($PAPER_DERIVED_BIN session next -s $SID)
  ACTION=$(echo "$NEXT" | jq -r '.action')
  case "$ACTION" in
    generate)
      # 单节：取 section_id；并行：取 parallel_batch[]
      # 对每个 <id>：
      #   $BIN session prompt -s $SID --section <id> --out prompts/<id>.md
      #   → 起子代理执行 prompts/<id>.md → 写 responses/<id>.json → DONE
      #   $BIN session prompt -s $SID --section <id> --parse responses/<id>.json
      #   $BIN session summarize -s $SID --section <id> --out prompts/sum-<id>.md → 子代理 → --parse
      ;;
    assemble)  break ;;
    feed_more) echo "告知用户需要补充输入"; ;;
    wait)      echo "等待 in_progress 的 section 完成"; ;;
  esac
done

# 5. 组装
$PAPER_DERIVED_BIN session assemble -s $SID -O output.md
```
