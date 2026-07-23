# 在 Windows 虚拟机里打 Room 套件

给 Windows 同事用的 **一键 zip** 必须在 **Windows 上**构建。  
本机（Mac）上的 `room-suite-*-macos-*.zip` **不能**给 Win 用。

---

## 虚拟机准备（一次性）

| 依赖 | 说明 |
|------|------|
| Windows 10/11 **x64** | 与同事机器架构一致 |
| **Python 3.10+** | [python.org](https://www.python.org/downloads/) 安装时勾选 **Add python.exe to PATH** |
| Git（可选） | 或从 Mac 共享文件夹拷贝源码 |
| 终端 | **PowerShell**（管理员非必须） |

验证：

```powershell
python --version    # >= 3.10
```

---

## 源码怎么进虚拟机

任选：

1. **共享文件夹**（Parallels / VMware / VirtualBox）：把 `room-tui` 与 `paper-derived` 挂进 VM  
2. **git clone** 内网 Gitea  
3. **zip 拷贝** 两个仓库

建议目录并列：

```text
C:\src\room-tui
C:\src\paper-derived
```

---

## 一条龙打包（推荐）

在 **room-tui** 目录打开 PowerShell：

### 完整套件（room + paper-derived + pi）— 推荐

```powershell
cd C:\src\room-tui

# 已有两个 exe：
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows-suite.ps1 `
  -PaperDerived C:\path\to\paper-derived.exe `
  -Pi C:\path\to\pi.exe

# 或从源码构建引擎 + Room 专用 pi（third_party\pi submodule；需 bun）
# 先: git submodule update --init --recursive
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows-suite.ps1 `
  -PaperDerivedRepo C:\src\paper-derived `
  -PiRepo C:\src\room-tui\third_party\pi
```

### 仅引擎（不推荐，会 `pi not found`）

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows-suite.ps1 `
  -PaperDerived C:\path\to\paper-derived.exe `
  -AllowNoPi
```

### 单独打 Room-branded pi（submodule）

```powershell
cd C:\src\room-tui
git submodule update --init --recursive
.\scripts\build-room-pi.ps1
# → dist\bin\pi.exe + theme\  （勿用 code.research\pi）
dir dist\bin\theme
```

> **重要**：`bun compile` 的 pi 会从 **exe 同目录** 读 `theme\dark.json`。  
> 套件脚本会自动拷贝 `theme/` 等资源；若手拷只拷 `pi.exe` 会 ENOENT 崩溃。

产物：

```text
C:\src\room-tui\dist\suite\room-suite-0.1.0-windows-x86_64.zip
C:\src\room-tui\dist\installer\Room-Setup-0.1.0-windows-x86_64.exe   # 若已装 Inno Setup
C:\src\room-tui\dist\installer-payload\   # Inno 源：必须含 paper-derived.exe + skills\
```

把 **zip** 或 **Setup.exe** 发给同事即可。

**硬门禁（缺则打包/安装失败）**：

| 项 | 路径 |
|----|------|
| 引擎 | `bin\paper-derived.exe` |
| 必装 skill | `skills\paper-derived\SKILL.md` |

旧版 Inno 脚本曾用 `skipifsourcedoesntexist` 跳过引擎、且不装 skill —— 会导致 `room doctor` 报 paper-derived 失败。当前脚本已修复。

---

## 分步（调试用）

```powershell
cd C:\src\room-tui

# 1) 只打 room.exe
powershell -ExecutionPolicy Bypass -File .\scripts\build-binary.ps1

# 2) 冒烟
.\dist\bin\room.exe --version

# 3) 组装（paper-derived.exe 自己准备好）
powershell -ExecutionPolicy Bypass -File .\scripts\package-suite.ps1 `
  -PaperDerived C:\src\paper-derived\build\paper-derived.exe
```

---

## 同事怎么装

```powershell
# 解压 zip 后
cd room-suite-0.1.0-windows-x86_64
powershell -ExecutionPolicy Bypass -File .\install.ps1

# 新开终端
room --version
room doctor
# 期望：engine OK、skills: ok required=paper-derived
```

完整套件应已含 **`pi.exe` + theme/**。若 `doctor` 仍缺 pi，说明打包装了 `-AllowNoPi` 或旧包。

若 SmartScreen 拦截：属性 → 解除锁定，或「仍要运行」。

---

## 常见问题

| 现象 | 处理 |
|------|------|
| `python` 不是内部命令 | 重装 Python 并勾选 PATH；或用 `py -3` |
| 执行策略禁止脚本 | `-ExecutionPolicy Bypass` 已写在命令里 |
| `paper-derived.exe` 版本太旧 | 需带 `version` 子命令的 **≥ 0.2.0** 引擎 |
| 杀毒误报 PyInstaller 单文件 | 加白名单或换机构建签名（本阶段不做签名） |
| 虚拟机很慢 | 给 VM ≥ 4GB RAM；首次 pip/pyinstaller 较久 |

---

## 与 Mac 包的关系

| 包 | 给谁 |
|----|------|
| `room-suite-*-macos-*.zip` | Mac 同事 |
| `room-suite-*-windows-*.zip` | **Windows 同事**（本 VM 产出） |

开发机仍用 `scripts\install-global.sh`（Mac）或 editable 安装，**不要**把产品 zip 当开发环境。
