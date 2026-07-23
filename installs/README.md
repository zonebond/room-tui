# Room 安装说明（产品包）

把本目录（或解压后的 `room-suite-…`）拷到目标电脑后，**一键安装**即可使用。  
**不需要**安装 Python，也**不需要**克隆源码仓库。

---

## 安装（选你的系统）

### macOS

```bash
cd room-suite-…          # 解压后的目录
chmod +x install.sh
./install.sh
```

安装位置：

- 程序：`~/.local/share/room/bin/`
- 命令：`~/.local/bin/room`、`~/.local/bin/paper-derived`

若提示找不到命令，把下面一行加到 `~/.zshrc` 后执行 `source ~/.zshrc`：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

### Windows

**A. Suite zip + `install.ps1`（推荐，完整）**

```powershell
cd room-suite-…
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

或双击 / 在 cmd 中：`install.bat`

安装脚本会：

1. 拷贝 `bin\room.exe` + **`bin\paper-derived.exe`** + `pi.exe`（若有）
2. 若套件含 **`tools\libreoffice\`**（推荐完整包）：一并安装并设置 `ROOM_LIBREOFFICE`（旧版 `.doc` 无需本机 Word）
3. 安装必装 skill **`paper-derived`** → `%USERPROFILE%\.config\room-tui\agent\skills\` 与产品 `skills\`
4. 设置用户环境变量 `ROOM_INSTALL_BIN` / `ROOM_PI_AGENT_DIR`（隔离系统 Pi）
5. 冒烟：`paper-derived version`；失败则安装报错退出

**B. `Room-Setup-*.exe`（一键安装器，推荐给同事）**

双击即可，**不需要**再手动装 skill / 引擎：

1. 解压完整套件到 `%LOCALAPPDATA%\Programs\Room`（含 `bin\paper-derived.exe` + `skills\paper-derived`）
2. **自动执行与 zip 相同的 `install.ps1`**（写入 pi-agent skill、PATH、环境变量、冒烟测试）
3. 安装结束前硬校验：缺引擎或缺 skill → **安装失败**，不会半残成功

由 `build-windows-suite.ps1` 编译；payload 缺文件则 **打包失败**。

安装后：**新开终端**运行 `room doctor`，期望 `engine OK` + `skills: ok required=paper-derived`。

---

## 安装后检查

```bash
room --version
room doctor
```

| 检查项 | 期望 |
|--------|------|
| `room` | 有版本号 |
| engine / paper-derived | OK |
| skills（必装 paper-derived） | OK |
| doc converter | 完整 Windows 包：`LibreOffice …/tools/libreoffice/…`；无则仅 Word/另存 docx |
| `pi` | 套件同目录或 PATH 上有可执行文件 |
| model | 已配置 provider/model（Room 密钥在 `~/.config/room-tui/agent`，**不是**系统 `~/.pi`） |

### 新装如何配置模型（必做一次）

Room **不会**继承系统 `~/.pi` 的 Key。支持的服务商（精简列表）：

| 类型 | 服务商 |
|------|--------|
| 云 | DeepSeek · MiniMax (CN) · GLM · 通义千问 Token Plan (CN) |
| 本机 | LM Studio · Ollama · vLLM（**密钥可选**：未开鉴权请留空） |

```bash
# A) 命令行向导（推荐）
room setup
# 云：选 provider + API Key；本机：确认 Base URL + 模型 id

# B) TUI
room
# 首次无密钥时会自动弹出「连接模型」；也可 Ctrl+M / /setup
```

写入位置（均在 Room 隔离目录）：

- 密钥：`~/.config/room-tui/agent/auth.json`
- 本机服务：`~/.config/room-tui/agent/models.json`
- 默认模型：`~/.config/room-tui/config.toml` + agent `settings.json`
| room agent | 与系统 Pi 隔离；`ROOM_CODING_AGENT_DIR` → `~/.config/room-tui/agent` |
| 内置 pi | **Room-branded**（`c-checkers/pi-room` 构建）；默认不读系统 `~/.pi` |

### 关于 `pi`（LLM）

完整产品套件应包含 **`bin/pi`（或 `pi.exe`）** 以及 **`bin/theme/`**（至少 `dark.json` / `light.json`），与 `room` 同目录安装。  
bun 编译的 pi 会从 **exe 旁边** 读主题；缺文件会直接崩溃。

若你的 zip 里没有 `bin/pi`（旧包或 `--allow-no-pi`）：

1. 向发布者要带 pi 的新套件，或本机自行安装 `pi`
2. 配置 provider（`~/.pi/agent/settings.json` 或环境变量）
3. 再跑 `room doctor`

**注意**：API Key / 登录态仍在用户本机（`~/.pi`），不会打进 zip。

可选：复制配置样例：

```bash
mkdir -p ~/.config/room-tui
cp config.example.toml ~/.config/room-tui/config.toml
# 编辑 provider / model
```

---

## 开始使用

```bash
cd /path/to/your-project
room
```

工作区状态在项目下的 `./.pd/`。

常用：

| 命令 | 说明 |
|------|------|
| `room` | 打开 TUI |
| `room doctor` | 环境体检 |
| `room run -t <模板> -i <资料>` | 无界面跑文档 |

---

## 升级

1. 下载新版 `room-suite-…zip`
2. 再跑一次安装脚本（覆盖同目录二进制）
3. `room --version` 确认

## 卸载

**macOS**

```bash
rm -rf ~/.local/share/room
rm -f ~/.local/bin/room ~/.local/bin/paper-derived
```

**Windows**：删除 `%LOCALAPPDATA%\Programs\Room`，并从用户 PATH 中去掉该 `bin` 路径。

---

## 开发者请注意

本包是**产品安装**。若你在改 Room 源码，请走仓库内开发安装：

```bash
./scripts/install-global.sh
```

不要用产品 zip 当开发环境。详见仓库 `PACKAGING.md` / `README.md`。
