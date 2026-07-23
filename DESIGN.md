# room-tui 设计文档

> **Room** — 本地项目工程间（TUI）  
> CLI：`room` · 包：`room_tui` · 项目：`room-tui`  
> 当前引擎：`paper-derived`（可扩展更多能力）  
> LLM Worker：本机 `pi`（UI 不暴露品牌）

| 项 | 值 |
|---|---|
| 状态 | Draft v0.3（产品更名 Room） |
| 日期 | 2026-07-18 |
| 仓库 | `room-tui`（与 `../paper-derived` 并列） |
| 引擎版本对齐 | paper-derived ≥ 0.2.0 |
| Worker | 本机 `pi` CLI（隐藏 Agent 品牌） |
| 交互主范式 | Grok 式左侧 scrollback+prompt + 右侧固定步骤/章节栏 |

---

## 1. 背景与目标

### 1.1 现状

`paper-derived` 已把文档派生拆成清晰两层：

| 层 | 职责 | 特点 |
|---|---|---|
| **Engine CLI** | 构造 prompt、解析响应、Session 状态机、模板/资产存储、导出 | 确定性、不调 LLM、文件契约 |
| **Agent Skill** | 按 workflows 编排：`build → LLM → parse` | 依赖聊天宿主（Claude Code 等），长任务脆弱 |

Skill 的正确原则（上下文纪律、子代理执行大 prompt、状态落盘）已被验证；问题在于 **Claude Code 作为宿主太重**：

- 启动与默认上下文负担大
- 模型/鉴权绑 Anthropic 生态
- 编排仍活在对话里，Dashboard / 人在环 / 续跑体验难产品化
- 与本地多模型矩阵（bailian / deepseek / ollama / lm_studio）不契合

### 1.2 产品目标

做一个 **本地 TUI 工作台**，让用户在不打开通用 Coding Agent 的情况下完成：

1. 模板注册与管理  
2. 资料导入与覆盖度预检  
3. Session-Driven / 常规文档生成（可视化进度）  
4. 抽取/质检等人在环审校  
5. 局部修订与交付导出  

### 1.3 非目标（明确不做）

| 非目标 | 原因 |
|---|---|
| 通用 Coding IDE / 第二个 Pi 交互壳 | 产品是文档派生工作台 |
| 让 Pi 读 `SKILL.md` 当主编排 | 回到概率编排与上下文爆炸 |
| 把 LLM 调用塞进引擎核心 | 破坏引擎确定性边界 |
| 强制单一云厂商 | 必须可切换本地/多 provider |
| 重写 Extraction/Structure 等 prompt 逻辑 | 引擎已有，TUI 只编排 |

### 1.4 成功标准（M0 验收）

在用户已安装 `paper-derived` 与 `pi`、且 Pi 至少一个 provider 可用的前提下：

1. 选择已有模板 + ≥1 份输入资料  
2. TUI（或同等 headless 编排入口）跑完 Session 循环  
3. 产出 `output.md`（或用户指定路径）  
4. 全程 **不** 启动 Claude Code、**不** 依赖 Skill 主编排  
5. 中断后可用同一 `session_id` 续跑  

---

## 2. 核心决策

### 决策 D1 — 编排者是 TUI，不是 Agent

| 角色 | 实现 |
|---|---|
| **Orchestrator** | `room-tui` 内 Python 状态机（移植 skill workflows） |
| **Engine** | `paper-derived` CLI（subprocess；后续可选 in-process） |
| **LLM Worker** | Pi Agent（默认 print/RPC，`--no-tools`） |

Skill 中的「主 Agent / 子代理」映射为：

```
Skill 主 Agent     →  TUI Orchestrator（确定性代码）
Skill Task 子代理  →  PiRunner.execute(...)
Skill 文件契约     →  工作区 .pd/（保持不变）
```

### 决策 D2 — Pi 只做瘦 Worker，不做总指挥

Pi 的调用形态：

- **默认**：`--no-tools --no-session --no-skills --no-extensions --no-context-files`
- **输入**：引擎 `--out` 写出的 prompt 文件（`==== SYSTEM ====` / `==== USER ====`）
- **输出**：响应文件 → 引擎 `--parse`
- **禁止**：给 Pi 全工具后说「你去跑 paper-derived 生成整份文档」

### 决策 D3 — 磁盘契约是真相源

- UI 状态刷新来自 `session status` / `session next` / parse 摘要 / 本地 event log  
- 默认 **不** 把 prompt 正文、响应正文加载进 Orchestrator 内存或主 UI  
- 调试视图可按需只读打开单文件  

### 决策 D4 — 引擎接入优先 CLI 契约

- M0/M1：subprocess 调用已安装的 `paper-derived`  
- 与 skill / installs 二进制兼容，版本可用 `paper-derived version` 探测  
- 不在 client 内复制 engine 的 prompt 模板  

### 决策 D5 — 与引擎直驱模式的关系

引擎 0.2.0 已提供：

- `paper-derived llm exec <prompt> --api-base ... -o response`
- `paper-derived session run -s ... --api-base ...`

| 模式 | 谁编排 | 谁调 LLM | 适用 |
|---|---|---|---|
| **TUI 主路径** | client Orchestrator | PiRunner（TUI 侧） | 需要 Dashboard、门闸、取消、模型分档 |
| **引擎直驱** | `session run` | 引擎内 OpenAI 兼容 / claude-cli | 无 UI 批处理、脚本 |
| **未来可选** | 引擎 | `api-base=pi-cli`（cmd provider） | 把 Pi 下沉为引擎 provider，TUI 只包一层 |

**本设计主路径是 TUI 编排 + PiRunner。**  
`session run` 可作为无 UI 回退与对照基线，不替代 Dashboard 产品路径。  
若后续引擎正式支持 `cmd-provider` / `pi-cli`，PiRunner 可改为薄封装 `llm exec --api-base pi-cli`，对外 Protocol 不变。

### 决策 D6 — Claude Code 降为可选兼容宿主

- 不阻塞、不进入 M0 依赖  
- 原 skill 仍可给喜欢聊天的用户使用  
- 与 TUI **共享引擎与 `.pd` 契约**，互不依赖对方编排器  

---

## 3. 架构

### 3.1 逻辑分层

```
┌──────────────────────────────────────────────────────────────┐
│  Presentation — Textual App                                   │
│  Home · Wizard · SessionDashboard · Gates · Templates · Set  │
└────────────────────────────┬─────────────────────────────────┘
                             │ ViewModels / Commands / Events
┌────────────────────────────▼─────────────────────────────────┐
│  Application — Orchestrator                                   │
│  RegisterWorkflow · GenerateWorkflow · SessionWorkflow ·     │
│  ReviseWorkflow · RetryPolicy · HumanGateBroker               │
└──────────────┬───────────────────────────────┬───────────────┘
               │                               │
┌──────────────▼──────────────┐   ┌────────────▼───────────────┐
│  EngineAdapter              │   │  LlmRunner (Protocol)      │
│  CLI subprocess wrapper     │   │  PiRunner (default)        │
│  build / parse / session_*  │   │  OpenAICompatRunner (opt)  │
│  template / input / gen /   │   │  MockRunner (tests)        │
│  revise / doc               │   │                            │
└──────────────┬──────────────┘   └────────────┬───────────────┘
               │                               │
               └───────────────┬───────────────┘
                               ▼
                 Workspace (.pd/ + deliverables)
                 prompts · responses · assets · tui/
```

### 3.2 进程模型

```
┌─ room (Python / Textual) ─────────────────────────────┐
│                                                          │
│  spawn: paper-derived <cmd> ...     (短生命周期，可并发)   │
│  spawn: pi ...                      (print: 每步一进程)   │
│     或 long-lived: pi --mode rpc    (Session 循环复用)    │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

约束：

- 不把引擎嵌进 Pi 的 tool 循环作为隐式依赖  
- 取消：Orchestrator 设 cancel flag → 杀当前 pi 子进程 / RPC abort → section 回到 ready  
- 并行：`session next` 返回 `parallel_batch` 时，最多 N 路 Pi（默认 2，可配；上限对齐引擎「最多 6」）

### 3.3 工作区布局

所有过程文件进工作区 `.pd/`，最终交付物在用户指定路径（默认 cwd）：

```text
<workspace>/
├── .pd/
│   ├── prompts/              # 引擎 --out
│   ├── responses/            # Pi 写出 / Runner 落盘
│   ├── assets/               # input register -O
│   ├── extract-result.json
│   ├── doc.json
│   ├── output.json
│   └── tui/
│       ├── run-manifest.json # 本次运行配置快照
│       ├── events.jsonl      # 编排事件流（可恢复 UI）
│       └── gates/            # 人在环决策记录
├── output.md                 # 交付物（示例）
└── .gitignore                # 建议忽略 .pd/
```

`run-manifest.json` 建议字段：

```json
{
  "client_version": "0.1.0",
  "engine_version": "0.2.0",
  "template_id": "srs-gjb438c",
  "session_id": "…",
  "workspace": "/path",
  "pi": { "provider": "bailian", "model": "qwen3.6-35b-a3b", "mode": "rpc" },
  "budget": 40000,
  "strategy": "session",
  "gates": { "review_extract": true, "review_validate": true },
  "created_at": "…"
}
```

---

## 4. 组件设计

### 4.1 EngineAdapter

**职责**：把引擎 CLI 收成类型化 API；统一 cwd、env、超时、JSON 解析。

```python
class EngineAdapter(Protocol):
    def version(self) -> EngineVersion: ...

    # template
    def template_list(self) -> list[TemplateSummary]: ...
    def template_show(self, template_id: str) -> TemplateDetail: ...
    def template_delete(self, template_id: str) -> None: ...

    # 通用 build/parse 形态
    def build(self, request: EngineRequest) -> PromptHandle:
        """执行命令 + --out → PromptHandle(path, prompt_tokens, key)"""
        ...

    def parse(self, request: EngineRequest, response: Path, *, output: Path | None = None) -> ParseResult:
        """执行命令 + --parse [+ -O] → 状态摘要（非大正文）"""
        ...

    # 无需 LLM
    def session_init(self, template_id: str, *, budget: int, output: Path | None = None) -> SessionInit: ...
    def session_next(self, session_id: str) -> NextAction: ...
    def session_status(self, session_id: str) -> SessionStatus: ...
    def session_assemble(self, session_id: str, *, output: Path, fmt: str) -> AssembleResult: ...
    def gen_outline(self, template_id: str, **kwargs) -> Path: ...
    def gen_preflight_static(self, ...) -> PreflightReport | None: ...  # 若仅 build 需 LLM 则走 build
```

实现要点：

| 项 | 约定 |
|---|---|
| 二进制 | `PAPER_DERIVED_BIN` env 或 PATH 中 `paper-derived` |
| cwd | 始终为 workspace 根 |
| 输出 | 优先 JSON 可解析的 stdout 摘要；大结果强制 `-O` |
| 错误 | 非 0 exit → `EngineError(code, stderr, cmd)` |
| 探测 | 启动时 `version`，检查 capabilities 含 `out-text-prompt` |

`EngineRequest` 用枚举表达命令族，避免 UI 拼字符串：

```text
TemplateRegister | InputRegister | GenPreflight | GenExtract |
GenGenerate | GenValidate | SessionFeed | SessionPrompt |
SessionSummarize | ReviseSection | ReviseGlobal | ...
```

### 4.2 LlmRunner / PiRunner

```python
@dataclass
class WorkerRequest:
    key: str                 # 如 "sec-3.2" / "feed-1" / "sum-3.2"
    prompt_file: Path        # .pd/prompts/<key>.md
    response_file: Path      # .pd/responses/<key>.raw 或 .json
    tier: ModelTier          # fast | default | strong
    tools: ToolsMode         # none | write_response_only
    timeout_s: float | None

@dataclass
class WorkerResult:
    ok: bool
    response_file: Path
    bytes_written: int
    latency_ms: int
    model: str
    provider: str
    usage: dict | None       # tokens/cost if available
    error: str | None

class LlmRunner(Protocol):
    async def execute(self, req: WorkerRequest, *,
                      on_event: Callable[[RunnerEvent], None] | None = None,
                      cancel: asyncio.Event | None = None) -> WorkerResult: ...
```

#### 4.2.1 Prompt 文件解析

引擎 `--out` 文本格式：

```text
==== SYSTEM ====
...system...
==== USER ====
...user...
```

Runner 解析为 `system` + `user`。若缺分隔符，整文件当 user，system 用最小 worker 指令。

#### 4.2.2 Pi 调用形态

**A. Print 模式（M0 默认，实现简单）**

```bash
pi -p \
  --no-tools \
  --no-session \
  --no-skills \
  --no-extensions \
  --no-context-files \
  --thinking off \
  --provider "$PROVIDER" \
  --model "$MODEL" \
  --system-prompt "$SYSTEM" \
  "$USER"
```

- stdout → 写入 `response_file`  
- stderr → 记入 events（不进 parse）  
- 退出码非 0 → 失败  

**B. RPC 模式（M1 推荐，长 Session）**

```bash
pi --mode rpc --no-tools --no-skills --no-extensions --no-context-files \
   --provider ... --model ...
```

生命周期：

1. TUI 进入 Dashboard 时拉起 RPC 进程（或首节懒加载）  
2. 每节：设置 system（若 API 支持）+ `prompt` user  
3. 订阅 stream 事件 → Dashboard  
4. 收齐最终文本 → 落盘  
5. `abort` 支持取消  
6. 离开 Dashboard / 空闲超时 → 关进程  

> 具体 RPC 帧格式以实现时 Pi 版本文档为准；封装在 `pi_rpc_client.py`，业务层只看 `WorkerResult`。

**C. 工具策略**

| ToolsMode | Pi flags | 用途 |
|---|---|---|
| `none` | `--no-tools` | 默认：纯补全 |
| `write_response_only` | `-t read,write` + 强 system 约束 | 仅当 print stdout 不可靠时的后备 |

**禁止**默认开启 `bash`。

#### 4.2.3 模型分档

| Tier | 用途 | 配置键示例 |
|---|---|---|
| `fast` | summarize、简单 fixable 修复 | deepseek-v4-flash / 本地小模型 |
| `default` | section generate、extract、feed | qwen3.6-35b / glm |
| `strong` | template register、连续失败升级 | deepseek-v4-pro / 更大模型 |

Settings 示例（`~/.config/room-tui/config.toml` 或 workspace `.pd/tui/config.toml`）：

```toml
[pi]
mode = "rpc"          # print | rpc
default_tier = "default"

[pi.tiers.fast]
provider = "deepseek"
model = "deepseek-v4-flash"
thinking = "off"

[pi.tiers.default]
provider = "bailian"
model = "qwen3.6-35b-a3b"
thinking = "off"

[pi.tiers.strong]
provider = "deepseek"
model = "deepseek-v4-pro"
thinking = "low"
```

#### 4.2.4 响应落盘与校验

1. 若 `response_file` 已存在 → 删除（清场）  
2. 写入完整响应  
3. `bytes_written > 0`  
4. 可选：启发式检查是否像 JSON / 是否含引擎期望结构（严格校验交给 `--parse`）  
5. parse 失败不在 Runner 内静默修复；返回 Orchestrator 按 RetryPolicy 处理  

### 4.3 Orchestrator

#### 4.3.1 工作流路由

```text
用户意图
  ├─ 注册模板     → RegisterWorkflow
  ├─ 修改已有文档 → ReviseWorkflow
  └─ 生成文档
        ├─ Section 数 > 15 或 多资料/大资料 或 复杂依赖 或 用户要暂停续传
        │     → SessionWorkflow
        └─ 否则 → GenerateWorkflow
```

路由默认自动；Wizard 允许用户强制 Session。

#### 4.3.2 通用 LLM 步骤协议

对应 skill「子代理执行协议」，代码化：

```text
1. engine.build(req) → PromptHandle
2. runner.execute(WorkerRequest) → response_file
3. engine.parse(req, response_file, output=…) → ParseResult(status…)
4. emit Event; 若 fail → RetryPolicy
```

Orchestrator **禁止** `read_text(prompt_file)` / `read_text(response_file)` 进入业务逻辑（测试与 debug 工具除外）。

#### 4.3.3 SessionWorkflow（核心）

状态机对齐引擎：

```text
init → feeding → generating → assembling → complete
```

主循环伪代码：

```python
async def run_session(ctx: RunContext):
    sid = engine.session_init(ctx.template_id, budget=ctx.budget, output=ctx.output)

    for asset in ctx.inputs:
        await step_llm(InputRegister(asset))   # 若尚未注册
        await step_llm(SessionFeed(sid, asset))

    while True:
        if ctx.cancel.is_set():
            persist_pause(sid); return

        nxt = engine.session_next(sid)

        match nxt.action:
            case "assemble":
                engine.session_assemble(sid, output=ctx.output, fmt=ctx.fmt)
                break
            case "feed_more":
                decision = await gates.ask_feed_more(nxt)
                if decision.add_files:
                    ...
                elif decision.continue_with_placeholders:
                    # 依赖引擎 placeholder 行为 / 用户确认后继续策略
                    ...
                else:
                    persist_pause(sid); return
            case "wait":
                await asyncio.sleep(0.2)
            case "generate":
                sections = nxt.parallel_batch or [nxt.section_id]
                await map_limited(sections, gen_one, limit=ctx.parallelism)

    await maybe_validate_and_gate(ctx, sid)
    mark_complete(sid)


async def gen_one(section_id: str):
    await step_llm(SessionPrompt(sid, section_id), tier=DEFAULT)
    if ctx.summarize:
        await step_llm(SessionSummarize(sid, section_id), tier=FAST)
```

#### 4.3.4 RetryPolicy

对齐 `session-states.md`：

| 场景 | 策略 |
|---|---|
| attempt 1 失败 | 同参重试 |
| attempt 2 失败 | 重试；确保 summarize 已跑；可升 `strong` |
| attempt ≥ 3 | **HumanGate**：补输入 / 占位跳过 / 降 budget 重跑 / 中止 |
| JSON/parse 失败 | 重试；可选在 worker system 追加「只输出合法 JSON」修复指令（不读原响应进 orch 内存，仅文件级） |
| Pi 进程崩溃 | 记事件，重建 RPC，重试当前 key |
| 用户取消 | section → ready（引擎 next 会重置 generating），写 pause |

**拆小策略**（自动，优先于找用户）：

- feed 多资产失败 → 改为每次一个资产  
- register 超大 → `--chunk-size`  
- 单节反复失败 → 建议降低 `--budget` 后 `session` 侧配置（需引擎支持的范围内）  

#### 4.3.5 GenerateWorkflow（小文档）

路径：`input register* → preflight → extract → generate → validate → export`

每步仍走同一 LLM 协议。Section 少时可一次 `gen generate`；若 preflight 显示复杂依赖，升级 Session。

#### 4.3.6 RegisterWorkflow / ReviseWorkflow

- **Register**：样例文件 → `template register` build/parse → 展示四模块摘要 → 完成  
- **Revise**：选择 doc + section + 指令 → `revise section` → 可选 validate → 导出  

### 4.4 HumanGateBroker

人在环是一等公民，不是聊天追问。

| Gate ID | 触发 | UI | 阻塞 |
|---|---|---|---|
| `env_check` | 启动 / 运行前 | 缺 pi / 缺引擎 / 无模型 | 是 |
| `preflight_critical` | 覆盖度 critical | 缺口表；补资料 / 占位继续 | 可配置 |
| `review_extract` | 用户开启或低置信 | 实体表编辑 → 写 overrides | 可关 |
| `section_failed` | attempt≥3 | 四选一动作 | 是 |
| `feed_more` | session next | 说明 pending sections | 是 |
| `review_validate` | input_dependent 项 | 逐条确认 | 可关 |
| `deliverable` | assemble 后 | 预览路径、打开、清理 .pd 提示 | 否 |

所有决策写入 `.pd/tui/gates/<ts>-<id>.json`，便于审计与复盘。

### 4.5 Event Bus

`events.jsonl` 每行一个事件，供 UI 与崩溃恢复：

```json
{"ts":"…","type":"step_start","workflow":"session","key":"sec-3.2","section":"3.2"}
{"ts":"…","type":"worker_progress","key":"sec-3.2","chars":1200}
{"ts":"…","type":"step_ok","key":"sec-3.2","progress":"12/38"}
{"ts":"…","type":"gate_open","gate":"feed_more"}
{"ts":"…","type":"error","key":"sec-3.2","message":"parse failed"}
```

Dashboard 订阅内存队列；重启时 tail 文件恢复侧栏日志。

---

## 5. 交互设计（主界面壳 — 对齐中）

> 心智：像 **Agent CLI 工作台**，但 **Agent 身份对用户不可见**。  
> 主结构：**顶标题栏 · 中消息流 · 右固定侧栏（上步骤 / 下章节） · 底状态条**。

### 5.0 交互决策一览

| ID | 决策 | 状态 |
|---|---|---|
| IX-1 | 目录即项目；一项目一当前任务；无全局 session 列表 | ✅ 锁定 |
| IX-2 | **左侧对齐 Grok Build**：Scrollback + Prompt；`/` 命令 + 自然语言；内部 Pi 执行（UI 不露品牌） | ✅ 锁定 |
| IX-3 | **右侧固定宽度侧栏**（侧边菜单量级，约 28–36 列 / ~22–28% 宽，固定不随拖） | ✅ 锁定 |
| IX-4 | 右侧 **上下分栏**：上 = 任务执行步骤；下 = 生成章节结构 | ✅ 锁定 |
| IX-5 | 章节三态标记：`✓` 完成 · `●` 进行中 · `○` 待执行 | ✅ 锁定 |
| IX-6 | **极力隐藏 Agent**（不出现 Pi/Claude/Agent 字样） | ✅ 锁定 |
| IX-7 | **底栏**：项目名/路径 + 模型名 | ✅ 锁定 |
| IX-8 | **顶栏单行**（产品名 · 模式/进度 · 模板短名）；高度 = 1 行 | ✅ 锁定 |
| IX-8b | **底部 CLI 输入框**（Agent CLI 同款）：命令回车执行 | ✅ 锁定 |
| IX-8c | **Ctrl+C×2 才生效**：有任务→取消任务；无任务→退出应用；Ctrl+Q//quit 立即退出；强制杀进程=系统原生 | ✅ 锁定 |
| IX-9 | 空闲时右侧：收起 vs 显示「就绪」占位 | ⏳ 待议 |
| IX-10 | 门闸/修订入口如何嵌进消息流 | ⏳ 待议 |

### 5.1 主界面总布局（定稿方向）

```text
┌─ 标题栏 ─────────────────────────────────────────────────────────┐
│  文档派生          生成中 12/42          软件需求规格…            │
├──────────────────────────────────────┬──────────────────────────┤
│                                      │ ▌固定宽度侧栏             │
│   消息区（主）                        │ ├─ 上：执行步骤 ────────┤ │
│   像 Agent CLI 消息流往下滚           │ │  ✓ 选模板              │ │
│                                      │ │  ✓ 注册资料            │ │
│   · 已选择模板 …                     │ │  ● 生成章节  ←当前     │ │
│   · 资料注册完成                     │ │  ○ 组装文档            │ │
│   · 开始生成 §3.1 …                  │ │  ○ 完成                │ │
│   · §3.1 完成                        │ ├─ 下：章节结构 ────────┤ │
│   · 正在生成 §3.2 …                  │ │  ✓ 1 范围              │ │
│   · …                                │ │  ✓ 2 引用              │ │
│                                      │ │  ● 3.2 接口            │ │
│                                      │ │  ○ 3.3 性能            │ │
│                                      │ │  ○ 4 合格性            │ │
│                                      │ │  …                     │ │
├──────────────────────────────────────┴──────────────────────────┤
│  my-app  ·  ~/…/my-app                    模型  qwen3.6-35b…   │
└─────────────────────────────────────────────────────────────────┘
```

| 区域 | 宽度/高度 | 职责 |
|---|---|---|
| **标题栏** | 全宽 · 1～2 行 | 产品名、模式（空闲/生成中）、进度、模板短名 |
| **消息区** | 剩余宽度 · 主高度 | 过程消息流（步骤回显、成功/失败、引导） |
| **右侧栏** | **固定宽**（侧边菜单级） | 上：流水线步骤；下：章节大纲 |
| **底栏** | 全宽 · 1 行 | 项目 + 模型（不露 Agent） |

### 5.2 右侧栏规格（本次锁定）

#### 尺寸

- **固定宽度**，行为对齐常见侧边菜单：  
  - 目标约 **28～36 字符列**（或屏宽约 22～28%，取固定 ch/列，不随窗口拖成主栏）  
  - 窄终端：可整栏折叠为「按 `]` 展开」，但展开后仍是固定宽，**不**改成弹性大栏  

#### 结构：垂直二等分（或上约 40% / 下约 60%）

```text
┌─ 侧栏（固定宽）────────┐
│ 执行步骤               │  ← 上半
│  ✓ 1 选择模板          │
│  ✓ 2 注册资料          │
│  ● 3 生成章节          │
│  ○ 4 组装输出          │
│  ○ 5 完成              │
├────────────────────────┤
│ 章节结构               │  ← 下半（可独立滚动）
│  ✓ 范围                │
│    ✓ 标识              │
│    ✓ CSCI概述          │
│  ● 需求 · 接口定义     │
│  ○ 合格性规定          │
│  ○ 可追溯性            │
│  …                     │
└────────────────────────┘
```

| 半区 | 内容 | 数据来源（实现期） |
|---|---|---|
| **上：执行步骤** | 任务级 pipeline 状态（选模板→注册→喂入→生成→组装→完成） | Orchestrator 阶段机 |
| **下：章节结构** | 模板章节树 + 每节 `✓/●/○` | session section_progress + 模板结构 |

#### 章节标记（锁定）

| 标记 | 状态 |
|---|---|
| `✓` | 已完成 |
| `●` | 进行中 |
| `○` | 待执行 |

可选扩展（未锁）：`!` 失败 · `◇` 占位。

#### 上半「执行步骤」示例节点

```text
选择模板 → 注册资料 → 预检(可选) → 生成章节 → 组装 → 完成
```

当前步骤用 `●`，已做 `✓`，未到 `○`。  
生成章节阶段时，**细节进度看下半章节树**；上半只表示「正停留在生成阶段」。

### 5.3 消息区

- 行为对齐 Agent CLI：**只增不乱跳**的 transcript  
- 内容是系统/过程消息，**不是**角色扮演对话  
- 空闲：欢迎 + 下一步引导（新建/继续）可写在消息区  
- 运行：每步、每节开始/完成/失败追加一行  

### 5.4 标题栏（可放内容 · 细则待补）

| 建议放 | 不放 |
|---|---|
| 产品名「文档派生」 | Agent / Pi / Claude |
| 模式：空闲 / 生成中 / 已完成 | 完整绝对路径（路径在底栏） |
| 进度 `12/42` | provider 品牌 |
| 模板短名 | |

### 5.5 底栏（已锁定）

| 显示 | 不显示 |
|---|---|
| 项目名 · 路径（可截断） | Agent 类型 |
| 模型名 | 引擎/Worker 品牌 |

### 5.6 与旧方案关系

| 旧 | 新 |
|---|---|
| 中间任务大卡片为主 | **消息流为主** |
| Dashboard 三栏树+卡+事件 | **消息 + 右侧固定上下栏** |
| 章节树在左 | **章节结构在右下** |
| 状态卡在中 | 过程进消息区；步骤在右上 |

### 5.7 修订 / 门闸（后定）

- 门闸可插在消息区（审批块）或模态  
- 修订：焦点章节（右下选中）+ 指令表单 —— 待锁  

### 5.8 Wizard

新建仍可走短向导（模板 / 资料）；确认后进入主壳消息流 + 右侧栏开始刷状态。

---

## 6. 配置与环境

### 6.1 依赖

| 依赖 | 要求 |
|---|---|
| Python | ≥ 3.11（Textual 现代版本） |
| paper-derived | PATH 可执行，≥ 0.2.0 |
| pi | PATH 可执行 |
| 至少 1 个 Pi provider | API key 或本地服务 |

### 6.2 环境变量

| 变量 | 含义 |
|---|---|
| `PAPER_DERIVED_BIN` | 引擎路径覆盖 |
| `PI_BIN` | pi 路径覆盖 |
| `ROOM_WORKSPACE` | 默认工作区 |
| `ROOM_CONFIG` | 配置文件路径 |

### 6.3 启动体检（EnvDoctor）

启动或运行前执行：

1. `paper-derived version` → 解析 capabilities  
2. `pi --version`  
3. 读 Pi settings / 配置的 provider 是否像可用（可选：`pi -p --no-tools "回复pong"` 探活，Settings 里手动）  
4. workspace 可写  

失败 → Home 红徽章 + 修复指引，不进入 Wizard 执行步。

---

## 7. 包结构

```text
room-tui/
├── DESIGN.md                 # 本文件
├── README.md                 # 安装与使用（实现期补）
├── pyproject.toml
├── src/room_tui/
│   ├── __init__.py
│   ├── __main__.py           # python -m room_tui
│   ├── app.py                # Textual App
│   ├── config.py
│   ├── env_doctor.py
│   ├── models/               # UI/DTO，非引擎模型复制
│   │   ├── events.py
│   │   ├── gates.py
│   │   └── run_manifest.py
│   ├── engine/
│   │   ├── adapter.py
│   │   ├── requests.py
│   │   └── errors.py
│   ├── llm/
│   │   ├── base.py
│   │   ├── pi_runner.py
│   │   ├── pi_rpc.py
│   │   ├── prompt_format.py  # SYSTEM/USER 解析
│   │   └── mock.py
│   ├── orch/
│   │   ├── base.py
│   │   ├── session.py
│   │   ├── generate.py
│   │   ├── register.py
│   │   ├── revise.py
│   │   ├── retry.py
│   │   └── router.py
│   ├── workspace.py          # .pd 目录约定
│   └── screens/
│       ├── home.py
│       ├── wizard.py
│       ├── dashboard.py
│       ├── templates.py
│       ├── gates_extract.py
│       ├── gates_validate.py
│       ├── revise.py
│       └── settings.py
└── tests/
    ├── test_prompt_format.py
    ├── test_retry.py
    ├── test_session_orch.py  # MockRunner + 录制 engine 或 fake
    └── fixtures/
```

入口命令建议：`room`（setuptools script）。

---

## 8. 分阶段交付

### M0 — 可跑通垂直切片（无华丽 UI 也可）

**范围**

- EngineAdapter（session + input register + assemble 最小集）  
- PiRunner print + `--no-tools`  
- SessionWorkflow headless：`room run --template X --input a.docx --output out.md`  
- 写 `.pd/tui/events.jsonl` + run-manifest  
- 基础日志（rich console）  

**验收**

- 真实模板 ≥15 section 或测试模板若干节跑通  
- 杀进程后 `room resume --session SID` 能续  

**不做**：完整 Textual 多屏、RPC、人在环精修

### M1 — 工作台 UI

- Textual：Home / Wizard / Dashboard  
- RPC 模式 + 取消  
- Preflight 展示、Pause/Resume  
- Settings 模型三档  
- EnvDoctor  

### M2 — 门闸与修订

- Extract / Validate gates  
- Template 注册屏  
- Revise  
- 交付预览  

### M3 — 硬化与分发

- MockRunner 回放 CI  
- 并行 batch 调优  
- 安装脚本（可选捆绑 skill 仅文档）  
- 可选：引擎侧 `pi-cli` provider 对接，Runner 改走 `llm exec`  

---

## 9. 测试策略

| 层级 | 内容 |
|---|---|
| 单元 | prompt 分隔解析、RetryPolicy、路由、event 序列化 |
| 编排 | MockRunner 固定响应 + FakeEngine 或录制 CLI |
| 契约 | 对真实 `paper-derived` 的 build/parse 烟测（无网） |
| 集成（手工/夜间） | 真实 Pi + 小模板端到端 |
| 回归 | 保存一套 `.pd/responses` 金样，Mock 回放 assemble 结果 |

原则：**CI 默认不依赖真实 LLM**。

---

## 10. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| Pi RPC 协议随版本变化 | 集成破裂 | 协议隔离在 `pi_rpc.py`；M0 只用 print |
| 大模型输出非 JSON 导致 parse 失败 | 节失败 | Retry + strong tier + 格式修复提示；≥3 门闸 |
| 多 provider 输出质量不一 | 文档质量 | 分档 + 模板校验 + placeholder 保结构 |
| print 模式冷启动慢 | 40+ 节耗时 | M1 上 RPC；parallel_batch 有限并行 |
| 引擎 stdout 格式细微差异 | Adapter 脆 | 以 `version` capabilities 分支；集成测锁版本 |
| 用户误开 Pi 工具乱改仓库 | 安全/污染 | 默认 `--no-tools`；workspace 限定 |
| 与 skill 双编排抢同一 session | 状态错乱 | 文档约定：同一 session 同时只允许一个编排器；manifest 写 `orchestrator=tui` |

---

## 11. 与 paper-derived 上游的协作边界

| 属于引擎 | 属于 client |
|---|---|
| prompt 构造 / parse / session 状态 | 工作流编排 UI 化 |
| 模板与 ContextStore | Pi 进程管理与模型分档 |
| 格式读写与导出 | 人在环门闸 UX |
| `llm exec` / `session run` 直驱 | Dashboard 事件与可观测性 |

**可向上游提的增强（非 M0 阻塞）**

1. 稳定、文档化的 `cmd-provider` / `pi-cli` 作为 `--api-base`  
2. `session status --json` 对 TUI 更友好的 section 树  
3. 单节 export 便于 Dashboard 预览  
4. 事件钩子（可选）：引擎 JSONL 进度  

---

## 12. 关键时序

### 12.1 Session 单节

```text
UI/Orch                Engine                     Pi
  |                       |                        |
  |-- session prompt --out prompts/s.md ---------->|
  |<-- {prompt_written, tokens} -------------------|
  |                       |                        |
  |-- execute(prompt=s.md, response=s.raw) ------->|
  |                       |     pi -p --no-tools   |
  |                       |                        |-- LLM --|
  |<-- WorkerResult(ok) --|                        |
  |-- session prompt --parse s.raw --------------->|
  |<-- {status, progress} -------------------------|
  |-- session summarize --out ... (tier=fast) ---->|
  |-- execute / parse summarize ------------------>|
  |-- session next ------------------------------->|
```

### 12.2 失败与门闸

```text
parse fail → retry 1 → retry 2 (strong) → gate section_failed
                                              ├─ add input → feed → continue
                                              ├─ placeholder / skip policy
                                              ├─ lower budget & retry
                                              └─ abort run (resumable)
```

---

## 13. 开放问题（实现前建议拍板）

| # | 问题 | 建议默认 |
|---|---|---|
| Q1 | M0 是否先做 headless CLI 再铺 Textual？ | **是** — 先锁编排与 PiRunner |
| Q2 | 并行 section 默认几路？ | **2**（稳）；可配置上限 6 |
| Q3 | extract 审校默认开还是关？ | **关**（进阶开）；合规模板可建议开 |
| Q4 | Pi thinking 默认？ | **off**（结构化 JSON 输出更稳） |
| Q5 | 工作区是否允许与 git 项目共用？ | **允许**；自动提示 ignore `.pd/` |
| Q6 | 是否在 M3 前做引擎 `pi-cli` provider？ | **否**；TUI 侧 Runner 足够 |

---

## 14. 一句话总结

> **room-tui 把 skill 的编排协议编译成确定性 TUI 状态机；  
> paper-derived 继续做纯引擎；  
> Pi 以无工具 Worker 身份执行每一个 prompt 文件。**

Claude Code 不再是产品主路径——它只是引擎的众多可选聊天宿主之一。

---

## 附录 A — M0 CLI 草图

```bash
# 环境检查
room doctor

# 新运行（Session）
room run \
  --workspace ./my-doc \
  --template srs-demo \
  --input ./资料/任务书.docx \
  --input ./资料/接口.xls \
  --output ./SRS.md \
  --budget 40000 \
  --tier-default bailian/qwen3.6-35b-a3b

# 续跑
room resume --workspace ./my-doc --session <sid>

# 仅启动 TUI（M1+）
room
```

## 附录 B — 与 Skill 概念对照表

| Skill | Client |
|---|---|
| SKILL.md 编排者 | `orch/*.py` |
| Task 子代理 | `PiRunner` |
| `--out` prompts/ | `.pd/prompts/` |
| responses/ | `.pd/responses/` |
| session next 决策 | `SessionWorkflow` loop |
| 问用户 | `HumanGateBroker` + Screens |
| offline `session run` | 无 UI 基线；TUI 不替代其脚本价值 |
| Claude Code | 可选兼容，非依赖 |

## 附录 C — 参考资料（仓库内）

- `../paper-derived/README.md` — 引擎总览  
- `../paper-derived/skill/SKILL.md` — 编排纪律与路由  
- `../paper-derived/skill/workflows/session.md` — Session 工作流  
- `../paper-derived/skill/references/session-states.md` — 状态机与恢复  
- `../paper-derived/docs/whitepaper.md` — 产品与架构论述  
- 本机 `pi --help` — print / json / rpc / tools 开关  
