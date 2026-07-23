# Room

**Room** 是本地项目工程间 TUI（CLI：`room`）。在项目目录启动后，可完成文档派生、会话续跑、以及 Grok 风格的 Agent 对话与工具时间线。

当前主引擎为 **paper-derived**（`paper-derived` CLI，通常与本仓库并列安装）；LLM Worker 使用本机 **pi**（不绑定单一云厂商）。

| | |
|---|---|
| **CLI** | `room` |
| **Python 包** | `room_tui` |
| **项目 / 分发名** | `room-tui` |
| **Python** | ≥ 3.10 |
| **版本** | 0.1.0 |

---

## 功能概览

- **工作台 TUI**：左侧消息列表 + 输入框，右侧任务进度 / 大纲
- **文档流水线**：模板注册、资料导入、Session 生成、`output.md` 导出；支持中断后续跑
- **Agent 对话**：工具时间线（Thinking / Thought / Read / bash / Fetch…）、可折叠长输出
- **交互对齐 Grok Build**（当前主路径）
  - 用户消息吸顶（滚动时 sticky）
  - 拖选消息自动复制（系统剪贴板 + OSC 52）
  - 多行输入（**Shift+Enter** 换行，**Enter** 发送）
  - 提示词历史（↑↓）、斜杠命令 / Skill、EscEsc Rewind
- **无 UI 批处理**：`room run` / `room resume` 适合脚本与 CI

---

## 依赖

| 组件 | 用途 |
|------|------|
| **Python 3.10+** | 运行时 |
| **[uv](https://github.com/astral-sh/uv)**（推荐）或 venv + pip | 安装 |
| **`paper-derived`** | 文档引擎 CLI（需在 PATH） |
| **`pi`** | LLM Worker（至少一个可用 provider） |

可选：`pytest`（开发测试，见下方开发章节）。

---

## 安装

产品安装与开发安装 **分离**。同事 / 试用请走产品包；本仓库贡献者走开发安装。

### 产品安装（推荐给小团队 · 无需 Python）

手动获取 **Room 套件** zip（按平台）：

- `room-suite-<version>-macos-arm64.zip`
- `room-suite-<version>-macos-x86_64.zip`
- `room-suite-<version>-windows-x86_64.zip`

套件内含 **`room` + `paper-derived` 单文件二进制** 与一键安装脚本。

```bash
# macOS
unzip room-suite-….zip && cd room-suite-…
./install.sh

# Windows（PowerShell）
# powershell -ExecutionPolicy Bypass -File .\install.ps1
```

然后：

```bash
room doctor          # 体检（需本机另装 pi 才能调 LLM）
cd /path/to/project
room
```

用户向说明见套件内 `README.md` 或仓库 [`installs/README.md`](./installs/README.md)。  
打包与发版规格见 [`PACKAGING.md`](./PACKAGING.md)。

> **LLM**：套件不捆绑 `pi`（鉴权与版本独立）。安装 `pi` 并配置 provider 后，`room doctor` 才会全绿。

### 开发安装（仅本仓库）

```bash
cd room-tui
./scripts/install-global.sh          # editable .venv + ~/.local/bin shim
# 或: uv venv .venv && uv pip install -e ".[dev]"
export PATH="$HOME/.local/bin:$PATH"
room doctor
```

目录改名后若 venv 失效：`./scripts/install-global.sh --force`。

**不要**把开发 shim 当成产品分发路径；发同事请打套件 zip。

---

## 快速开始

```bash
cd /path/to/your-project    # 工作区 = 当前目录；过程文件在 ./.pd/
room                        # 打开 TUI
```

首次使用建议先体检环境：

```bash
room doctor
```

会检查 `paper-derived`、`pi`、模型配置与模板列表。

---

## CLI

| 命令 | 说明 |
|------|------|
| `room` | 在当前目录打开 TUI |
| `room -s <session_id>` | 打开并聚焦指定会话 |
| `room -w /path/to/ws` | 指定工作区（默认 cwd） |
| `room doctor` | 环境与依赖体检 |
| `room run -t <模板> -i <资料>` | 无 TUI 跑文档生成 |
| `room resume -s <session_id> [--tui]` | 续跑会话；`--tui` 进界面 |

### 模板注册（TUI · `/template`）

`/new` 只能**选用已注册模板**。注册入口统一用 **`/template`**：

```text
/template                              # 列表；无模板时显示帮助
/template register ./样例.docx 软件需求规格     # 完整：引擎 prompt → pi → 落盘
/template register --fast ./大纲.md 我的模板    # 快速：register-auto（结构扫描）
/template show <id>
/new                                   # 注册成功后手动选模板开始生成
```

- **完整**：质量优先，走 Room 的 pi 模型配置（Ctrl+M）  
- **快速**：引擎 `register-auto`；可选 `ROOM_API_BASE` / `ROOM_API_KEY`（OpenAI 兼容）。失败可改完整模式  
- 注册成功**只提示**下一步 `/new`，不自动打开向导  
- **`/new` 右侧**：`+ 注册` / `详情` / `删除`（注册成功后刷新列表并选中）

全局选项示例：

```bash
room --provider bailian --model qwen3.6-35b-a3b
room --bin /path/to/paper-derived --pi-bin /path/to/pi
room --version
```

工作区约定与 **paper-derived** 一致：状态与产物落在 `./.pd/`。

---

## 配置

优先级大致为：**CLI 参数 > 环境变量 > 配置文件 > `~/.pi/agent/settings.json` 回退**。

### 环境变量

| 变量 | 说明 |
|------|------|
| `ROOM_PROVIDER` / `ROOM_MODEL` | 默认 LLM |
| `ROOM_WORKSPACE` | 默认工作区（否则用 cwd） |
| `ROOM_CONFIG` | 配置文件路径 |
| `PAPER_DERIVED_BIN` / `PI_BIN` | 引擎 / pi 可执行文件 |
| `TEXTUAL_DISABLE_KITTY_KEY` | `1` 关闭 Kitty 键协议（偏 IME）；默认 Room 使用「温和 Kitty」以支持 Shift+Enter |
| `ROOM_KITTY_FULL` | `1` 使用 Textual 原版 Kitty 标志（调试） |

### 配置文件

默认路径：`~/.config/room-tui/config.toml`

```toml
[pi]
provider = "bailian"
model = "qwen3.6-35b-a3b"
thinking = "off"

# 可选分档
# fast_provider / fast_model
# strong_provider / strong_model
```

---

## TUI 快捷键（常用）

| 按键 | 作用 |
|------|------|
| **Enter** | 发送当前输入 |
| **Shift+Enter** | 换行（多行输入，最高约 8 行） |
| **Alt+Enter** / **Ctrl+J** | 换行（备用） |
| **↑ / ↓** | 提示词历史 / 斜杠菜单 |
| **/** | 斜杠命令与 Skill |
| **Ctrl+M** | 打开模型选择器（与 Grok 一致） |
| **/model list** | 列出当前 pi 可用模型 |
| **/model &lt;provider/model&gt;** | 切换模型（须为 pi 认识的） |
| **Esc Esc** | Rewind（回退到某条用户消息前） |
| **Ctrl+B** / **⌘B** | 折叠/展开右侧任务栏 |
| **Ctrl+L** | 清屏（消息列表） |
| **Ctrl+C** | 中断 / 再按退出（依上下文） |
| **Ctrl+Q** | 退出 |
| **e** | 展开/折叠附近可读内容块 |
| 拖选消息 | 松开后自动复制到系统剪贴板 |

斜杠内置示例：`/help`、`/new`、`/continue`、`/status`、`/rewind` 等（以界面 `/help` 为准）。

---

## 开发

```bash
cd room-tui
uv pip install -e ".[dev]"   # 或 scripts/install-global.sh
uv run pytest -q
```

### 打产品套件（发布者）

**macOS**

```bash
./scripts/build-binary.sh
./scripts/package-suite.sh --paper-derived /path/to/paper-derived
# → dist/suite/room-suite-<ver>-macos-*.zip
```

**Windows（虚拟机 / 真机）** — 详见 [installs/WINDOWS_VM.md](./installs/WINDOWS_VM.md)

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows-suite.ps1 `
  -PaperDerivedRepo C:\src\paper-derived
# → dist\suite\room-suite-<ver>-windows-x86_64.zip
```

完整规格见 [PACKAGING.md](./PACKAGING.md)。

主要布局：

```
src/room_tui/              # 应用源码
scripts/build-binary.sh    # 产品：打 room 二进制
scripts/package-suite.sh   # 产品：组装套件 zip
scripts/install-global.sh  # 开发：editable 安装
installs/                  # 产品一键安装器（打进 zip）
packaging/                 # PyInstaller spec / 配置样例
tests/
```

产品与架构细节见 [DESIGN.md](./DESIGN.md)、[PACKAGING.md](./PACKAGING.md)。

---

## 截图

仓库内 `运行截图/` 可放置运行界面示意（可选）。

---

## 许可证

以仓库内许可证文件为准（若未添加，默认仅限内部/约定使用）。
