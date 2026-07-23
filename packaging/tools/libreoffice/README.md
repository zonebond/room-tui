# Suite-bundled LibreOffice (headless .doc converter)

Room 在 Windows 一键安装包中可附带 **LibreOffice 程序树**，仅供引擎无界面转换旧版 `.doc`，不安装开始菜单图标、不要求用户打开 Writer。

## 布局（安装后）

```
%LOCALAPPDATA%\Programs\Room\
  bin\room.exe
  bin\paper-derived.exe
  tools\libreoffice\
    program\soffice.exe     ← 探测目标
    program\…               ← 过滤器 / DLL
    share\…                 ← 资源（转换需要，勿只拷 soffice.exe）
    ROOM-NOTICE.txt
```

## 发布者如何塞进套件

在 **Windows 构建机**上（需要网络下载 ~300MB MSI 一次）：

```powershell
# 1) 下载并解压到 vendor\（不进 git）
.\scripts\fetch-libreoffice-windows.ps1
# 可选: -Version 24.8.4  -Force

# 2) 打完整套件（自动发现 vendor\libreoffice-windows）
.\scripts\build-windows-suite.ps1 `
  -PaperDerivedRepo ..\paper-derived `
  -RequireLibreOffice

# 或:
.\scripts\package-suite.ps1 -PaperDerived C:\pd.exe -LibreOffice vendor\libreoffice-windows
```

产物：

- `dist\suite\room-suite-*-windows-*\tools\libreoffice\…`
- Inno payload 同样带上 `tools\`（`room-setup.iss` 已支持 `skipifsourcedoesntexist`）

## 运行时探测顺序

1. 环境变量 `ROOM_LIBREOFFICE` / `PAPER_DERIVED_LIBREOFFICE`
2. `{install}/tools/libreoffice/program/soffice.exe`
3. PATH / 系统已装 LibreOffice
4. Word/WPS COM（paper-derived）

`room doctor` 会显示 `doc converter: LibreOffice …`。

## 许可

LibreOffice 为自由软件（MPL / LGPL 等）。再分发时保留本树中的许可证文件与 `ROOM-NOTICE.txt`。  
详见 https://www.libreoffice.org/about-us/licenses/

## 体积

完整 `tools/libreoffice` 树通常 **300MB～500MB+**。对「傻瓜式读 .doc」这是可接受成本；若打轻量包可省略 `tools\`，用户仍可本机装 Word 或另存 `.docx`。
