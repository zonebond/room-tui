# Room 打包与部署（产品分发）

> **目标用户**：小团队同事、非开发者、试用者  
> **目标体验**：拷贝安装包 → 一键安装 → `room doctor` 绿 → `room` 能用  
> **分发方式**：手动发包；**macOS / Windows 分别**提供一键安装器  
> **开发 vs 生产**：**严格分离**

| 项 | 值 |
|---|---|
| 状态 | Active — 产品分发规格 |
| 分发形态 | **L3 单文件二进制套件**（Room + paper-derived + pi） |
| paper-derived 分支 | **`claude0`（唯一产品线）** — 勿用 `master` 打 Room 套件 |
| 渠道 | 手动 Release 包（zip），不依赖 PyPI / 源码仓库路径 |
| LLM Worker | 套件内 `pi` 单文件（源码 `build:binary`）；密钥仍在用户本机 |

---

## 1. 产品决策（已锁定）

| 决策 | 选择 | 原因 |
|------|------|------|
| 用户安装形态 | **L3 单文件二进制** + 一键安装脚本 | 不要求目标机有 Python / 不碰 venv |
| 套件内容 | **`room` + 能力包 + `pi`** 同包同装 | 平台 + paper-derived + oob-divzero + Worker |
| 能力包 | **paper-derived**（文档）+ **oob-divzero**（C 越界/除零） | skill 文档 + CLI 二进制并列 `bin/` / `skills/` |
| oob ASan | **捆绑 `tools/c-toolchain`（clang）** | 不依赖本机 VS/Xcode；`fetch-c-toolchain-*.ps1|sh` |
| `pi` | **捆绑 bun 编译单文件**（有源码） | 锁定版本；API Key 仍在 `~/.pi` |
| 开发安装 | `./scripts/install-global.sh` / editable | 仅开发者；**不**作为产品分发路径 |
| 渠道 | 手动打 zip 发同事 | 小团队、可控、可离线 |
| 平台 | **macOS**（arm64 / x86_64 分包）+ **Windows**（amd64） | 首要桌面环境 |

### 非目标（本阶段不做）

- 公开 PyPI / Homebrew / winget
- 把 Node 全家桶塞进 Room 进程（pi 仍为旁路单文件）
- Docker 作为主安装路径（仅未来 CI 可选）
- 强制用户从源码 `pip install -e`

---

## 2. 两种安装轨道（必须分清）

```
┌─────────────────────────────────────────────────────────────┐
│  产品轨道（给同事 / 试用）                                     │
│  Release zip → install.sh / install.ps1 → PATH 上的二进制    │
│  产物目录：~/.local/share/room/  (mac) / %LOCALAPPDATA%\Room │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  开发轨道（仅本仓库贡献者）                                     │
│  git clone → ./scripts/install-global.sh → editable .venv    │
│  改代码即生效；与产品安装路径互不覆盖（见安装器前缀）            │
└─────────────────────────────────────────────────────────────┘
```

| | 产品 | 开发 |
|--|------|------|
| 命令来源 | 冻结二进制 | `.venv` + shim |
| 是否要仓库 | 否 | 是 |
| 是否要 Python | 否 | 是 |
| 升级方式 | 装新版 zip | `git pull` + reinstall |
| 文档入口 | `installs/README.md` | 根 `README.md` 开发节 |

---

## 3. 套件内容（每个平台一个 zip）

```
room-suite-<version>-<os>-<arch>.zip
└── room-suite/
    ├── README.md                 # 给最终用户（中文）
    ├── install.sh                # macOS / Linux
    ├── install.ps1               # Windows
    ├── install.bat               # Windows 双击入口
    ├── bin/
    │   ├── room[.exe]            # Room 单文件二进制
    │   ├── paper-derived[.exe]   # 能力 1：文档引擎
    │   ├── oob-divzero[.exe]     # 能力 2：C OOB/div-zero CLI
    │   └── pi[.exe]              # LLM Worker（pi coding-agent bun compile）
    ├── skills/
    │   ├── paper-derived/SKILL.md
    │   └── oob-divzero/SKILL.md
    ├── tools/
    │   ├── libreoffice/          # 可选：读旧 .doc
    │   │   └── program/soffice.exe
    │   └── c-toolchain/          # 产品默认：oob ASan 用 clang
    │       └── bin/clang[.exe]
    ├── config.example.toml       # 可选默认配置样例
    └── THIRD_PARTY.txt           # 许可证摘要（可选）
```

### Windows：捆绑 LibreOffice（.doc 傻瓜式）

旧版 `.doc` 无需本机 Word。发布者在 **Windows 构建机**执行：

```powershell
.\scripts\fetch-libreoffice-windows.ps1          # → vendor\libreoffice-windows\
.\scripts\build-windows-suite.ps1 `
  -PaperDerivedRepo ..\paper-derived `
  -RequireLibreOffice
```

- 体积约 **+300MB～500MB**（你方接受则默认打满包）
- 运行时：`ROOM_LIBREOFFICE` + `tools\libreoffice\program\soffice.exe`
- `room doctor` 显示 `doc converter: LibreOffice …`
- 细节：`packaging/tools/libreoffice/README.md`

安装后布局（产品）：

**macOS**

```
~/.local/share/room/
  bin/room
  bin/paper-derived
  VERSION
~/.local/bin/room              → 指向 share 内二进制（或复制）
~/.local/bin/paper-derived
~/.config/room-tui/config.toml # 用户配置（首次可选生成）
```

**Windows**

```
%LOCALAPPDATA%\Programs\Room\
  bin\room.exe
  bin\paper-derived.exe
  VERSION
%USERPROFILE%\.local\bin\      # 或直接把 Programs\Room\bin 加进用户 PATH
```

### 套件内相对路径约定（关键）

安装后 **`room` 与 `paper-derived` 必须在同一 `bin/` 目录**。  
冻结的 Room 会优先调用**同目录**的 `paper-derived`，无需用户设 `PAPER_DERIVED_BIN`。

---

## 4. 构建流水线（发布者本机）

```
[开发机 / 发布机]
  1. scripts/build-binary.sh          # 本平台打 room 单文件
  2. 同平台 paper-derived 单文件     # paper-derived/scripts/build-cli.sh
  3. 同平台 pi 单文件                # pi monorepo: packages/coding-agent && npm run build:binary
  4. scripts/package-suite.sh --paper-derived … --pi …
  5. 手动发给同事（注意 os/arch 与对方一致）
```

| 脚本 | 作用 |
|------|------|
| `scripts/build-binary.sh` / `.ps1` | PyInstaller 打 `room` onefile → `dist/bin/room[.exe]` |
| `scripts/package-suite.sh` | 收集 `room` + `paper-derived` + **`pi`** + 安装器 → zip |
| `scripts/build-windows-suite.ps1` | Windows 一键（含可选 `-PiRepo`） |

`build-binary` 的 `--clean` / `-Clean` **只清 room 产物**（`build/pyinstaller`、`dist/bin/room*`），  
**不会**整目录删掉 `dist/bin/`，以免顺带清掉已放好的 `paper-derived` / `pi` / `theme/`。

### 脚本编码（macOS + Windows 双平台）

中文 Windows 的 PowerShell 5.x 默认不是 UTF-8；macOS bash 则要求 shebang 前**无 BOM**。统一约定：

| 类型 | 编码 | 正文要求 |
|------|------|----------|
| `*.ps1` | **UTF-8 with BOM** | **仅 ASCII**（禁止 `…` `—` `→` `✓` 等） |
| `*.bat` / `*.cmd` | ASCII / UTF-8 | 仅 ASCII |
| `scripts/**/*.sh`、`installs/**/*.sh` | **UTF-8 without BOM** | 禁止 smart 标点；CJK 可选（推荐打包脚本也用 ASCII） |

检查 / 自动修复：

```bash
python3 scripts/check-script-encoding.py
python3 scripts/check-script-encoding.py --fix
```

`package-suite.sh` / `package-suite.ps1` 打包前会跑此检查。

### Agent 工具输出编码（macOS + Win10 + Win11）

Agent 跑 `bash` / 嵌套 `powershell` / `curl` 时，输出必须能被正确解码，否则模型读到乱码。

| 平台 | 主要风险 | Room 策略 |
|------|----------|-----------|
| **macOS** | 极少；默认 UTF-8 | `LANG/LC_*=en_US.UTF-8`；解码固定 UTF-8 |
| **Windows 10** | PowerShell 5.1 管道常为系统 ACP（中文 CP936 等） | 环境变量 + `shellCommandPrefix` + 嵌套 PS 注入 UTF-8 OutputEncoding + 多代码页回退解码 |
| **Windows 11** | 可能开「系统 UTF-8」beta，也可能仍是 ACP | 同上（UTF-8 干净则直接用；否则回退） |
| **Linux** | 容器缺 locale | `LANG/LC_*=C.UTF-8` |

实现位置：

| 层 | 路径 |
|----|------|
| Room 进程 env | `src/room_tui/pi_env.py` → `pi_agent_environ` / `preferred_utf8_locale` |
| settings 前缀 | `ensure_utf8_shell_settings` → `settings.json` `shellCommandPrefix` |
| Room-branded pi | `scripts/patches/room-pi-utf8-shell.patch`（`build-room-pi` 时打入） |

**必须重编 pi** 后 patch 才进二进制：`./scripts/build-room-pi.sh` 或 `.ps1`。

### 避免装到旧 room.exe（Windows）

| 环节 | 行为 |
|------|------|
| `build-binary.ps1` | 每次 `--force-reinstall` 可编辑包 + PyInstaller `--clean`；写出 `dist/bin/room.BUILD.txt`（sha256 / 时间） |
| `package-suite.ps1` | **默认先重建 room**；`-SkipRoomBuild` 才复用已有 `dist\bin\room.exe`；源码比二进制新则报错 |
| `build-windows-suite.ps1` | 始终 `-Clean` 打 room，再打包（`-SkipRoomBuild`） |
| `install.ps1` | 打印 suite VERSION + sha；结束运行中的 room；拷贝后 **校验 sha 一致** 才成功 |

装完后请**新开终端**执行 `room doctor`，并对照安装日志里的 `sha256` 是否与打包输出一致。

### 必装 Skills（傻瓜式套件）

| 项 | 说明 |
|----|------|
| 清单 | `packaging/required-skills.txt`（当前仅 **paper-derived**） |
| 打包 | `package-suite` 把 skill **文档**装进 `suite/skills/<name>/`（不含第二份引擎二进制） |
| 安装 | `install.ps1` / `install.sh` / **Inno Setup** 写入 **Room 隔离** `~/.config/room-tui/agent/skills` 与产品 `skills/`（不写入系统 `~/.pi`） |
| Inno Setup | 必须从 `dist/installer-payload`（= 完整 suite）编译；**强制**含 `bin/paper-derived.exe` + `skills/paper-derived/` |
| 发现 | Room 扫描产品目录 + **Room agent** skill；`room doctor` 缺必装 skill 报 FAIL |

skill 源优先：`packaging/skills/`（仓库内置）→ `PAPER_DERIVED_SKILL` → `../paper-derived/skill`。

### Room agent 与系统 Pi Agent 隔离（方案 B：Room-branded pi）

| 产品 | 配置目录 | 环境变量 / 默认 |
|------|----------|-----------------|
| 系统 **Pi Agent** | `~/.pi/agent` | （默认，`code.research/pi` 等） |
| **Room** 内置 worker | `~/.config/room-tui/agent` | `ROOM_CODING_AGENT_DIR`；piConfig `name=room` |

**pi 源码（单仓管理）**：

| 路径 | 用途 |
|------|------|
| **`third_party/pi`** | **唯一** Room 用 Pi 源码（git submodule） |
| `code.research/pi` | **禁止**用于 Room（其他产品） |
| `c-checkers/pi-room` | 已弃用并列 clone，请改用 submodule |

```bash
git clone --recurse-submodules <room-tui>
git submodule update --init --recursive
./scripts/build-room-pi.sh    # 应用 piConfig brand + 产出 dist/bin/pi
```

构建会跑 `scripts/apply-room-pi-brand.py`（`name=room`, `configDir=.config/room-tui`）。  
Room 启动仍 `apply_room_pi_isolation()`；安装器写 `ROOM_CODING_AGENT_DIR`。  
详见 `third_party/README.md`。

**硬门禁（`scripts/verify-room-pi.py`，失败即退出）**：

| 环节 | 检查 |
|------|------|
| `build-room-pi` | source 已 brand；写出并校验 `dist/bin/pi.ROOM.txt` |
| `package-suite` | 入包 pi 必须有 stamp；suite 含 `bin/pi.ROOM.txt` + theme；`VERSION` 写 `pi_brand=room` |
| `build-windows-suite` | 打包前 verify binary；installer-payload 再 verify suite |
| 缺 stamp / 来自 `code.research/pi` | **直接失败**，不会打出半残包 |

人不必对照清单；流水线不通过就停。

| `scripts/install-global.sh` | **仅开发** editable 安装 |

### 构建 pi 单文件（有源码）

pi monorepo（例：`code.research/pi`）官方已支持：

```bash
cd packages/coding-agent
npm run build:binary   # bun build --compile → dist/pi + copy-binary-assets
# dist/ 须含: pi[.exe], theme/dark.json, theme/light.json, …
```

依赖：Node + **bun**、monorepo `npm install`。  
产物与 `room` **同架构**（x86_64 / arm64 不可混用）。

**旁路资源（必带）**：编译后的 pi 用 `dirname(execPath)/theme/` 读主题；  
`package-suite` 会从 pi 同目录拷贝 `theme/`、`assets/`、`package.json` 等进 `bin/`。  
只拷 `pi.exe` 会导致 `ENOENT theme\dark.json` 并在 Room 里显示为 session Warning 失败。

### 跨平台说明

| 产物 | 在哪台机器打 |
|------|----------------|
| macOS arm64 | Apple Silicon Mac（原生 arm64 Python） |
| macOS x86_64 | Intel Mac / Rosetta 下的 x86_64 Python |
| Windows x86_64 | **Windows 虚拟机或真机**（见 `installs/WINDOWS_VM.md`） |

**不能**在 macOS 上可靠产出 Windows `.exe`。

Windows 虚拟机一键：

```powershell
cd C:\src\room-tui
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows-suite.ps1 `
  -PaperDerivedRepo C:\src\paper-derived
# → dist\suite\room-suite-*-windows-x86_64.zip
```

### 版本锁定

- 套件 `VERSION` 文件：`room=<ver>` + `paper-derived=<ver>`
- `PACKAGING.md` / Release note 写兼容矩阵
- 引擎 caps：`room doctor` 继续守门

---

## 5. 一键安装器行为

### macOS — `install.sh`

1. 检测架构，确认包内二进制可执行  
2. 安装到 `~/.local/share/room/`  
3. 把 `room` / `paper-derived` 链到 `~/.local/bin/`  
4. 提示 `PATH`（若缺）  
5. 探测 `pi`：有则 OK；无则打印安装指引（**不阻断安装**，但提示 doctor 会红）  
6. 可选写入 `config.example.toml` → `~/.config/room-tui/config.toml`（若不存在）  
7. 运行 `room doctor`（若 PATH 已生效）

### Windows — `install.ps1` / `install.bat`

1. 安装到 `%LOCALAPPDATA%\Programs\Room\`  
2. 用户 PATH 追加 `bin`  
3. 同样探测 `pi`  
4. 提示**新开终端**后执行 `room doctor`

### 卸载（文档级）

- macOS：删 `~/.local/share/room` 与 `~/.local/bin/{room,paper-derived}`  
- Windows：删安装目录并从用户 PATH 移除  

---

## 6. 运行时解析顺序（产品二进制）

`paper-derived`：

1. CLI `--bin` / 环境变量 `PAPER_DERIVED_BIN`  
2. **与 `room` 可执行文件同目录的 `paper-derived[.exe]`**（套件默认）  
3. `PATH` 上的 `paper-derived`  
4. 配置文件 `engine.bin`

`pi`：

1. `--pi-bin` / `PI_BIN`  
2. **与 `room` 同目录的 `pi[.exe]`**（套件默认）  
3. `PATH` 上的 `pi`  

---

## 7. `pi` 与「傻瓜式」边界

| 能力 | 套件是否自带 |
|------|----------------|
| 打开 TUI、消息列表、配置 | ✅ `room` |
| 文档引擎 CLI | ✅ `paper-derived` |
| 调 LLM 可执行文件 | ✅ `pi` 单文件（完整套件） |
| API Key / provider 登录 | ❌ 用户 `~/.pi` / 环境变量 |

**用户首次清单：**

1. 解压 → 跑安装脚本  
2. 配置模型密钥（若尚未配置）  
3. `room doctor` 全绿  
4. `cd 项目目录 && room`

缺 `bin/pi` 的旧包：安装器警告；新发版应始终带 pi。

---

## 8. 验收标准（发布前）

在**干净机**（无本仓库、无开发 venv）上：

- [ ] 解压 zip，运行安装脚本成功  
- [ ] 新终端：`which room` / `where room` 有结果  
- [ ] `room --version` 正常  
- [ ] `paper-derived version`（或等价）正常  
- [ ] `room doctor`：引擎 OK；**pi OK**（同目录或 PATH）  
- [ ] `cd 某项目 && room` 能进 TUI 并发消息（需已配密钥）  
- [ ] 开发机上的 `install-global.sh` **不会**被产品安装覆盖坏（路径分离）

---

## 9. 目录与脚本索引

```
room-tui/
  PACKAGING.md                 # 本文
  packaging/
    room.spec                  # PyInstaller 规格
    config.example.toml
  scripts/
    build-binary.sh            # 打 room 单文件
    package-suite.sh           # 组装套件 zip
    install-global.sh          # 开发安装（非产品）
  installs/                    # 产品安装器模板（打进 zip）
    README.md                  # 最终用户说明
    macos/install.sh
    win/install.ps1
    win/install.bat
```

---

## 10. 发版检查清单（发布者）

1. 版本号：`pyproject.toml` / `__version__` 对齐  
2. 测试：`uv run pytest -q`  
3. 本机构建：`./scripts/build-binary.sh`  
4. 准备同平台 `paper-derived` 二进制  
5. `./scripts/package-suite.sh --paper-derived /path/to/paper-derived`  
6. 在干净目录试装一遍  
7. 上传 zip / 发同事  

---

## 11. 后续可选（未做）

- 代码签名 / 公证（macOS Gatekeeper、Windows SmartScreen）  
- 自动从 Gitea 拉最新套件的小更新器  
- 内嵌极简 OpenAI 兼容调用，弱依赖 `pi`（产品能力变更，另议）  
- Linux 套件（结构同 macOS）  
