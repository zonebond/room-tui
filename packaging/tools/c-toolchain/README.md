# Suite-bundled C toolchain (oob-divzero ASan)

Room 完整套件可附带 **可移植 C/C++ 工具链**（clang + runtime），仅供 **oob-divzero** 的 ASan/UBSan 验证层使用，不要求用户本机另装 Xcode / Visual Studio / MinGW。

## 布局（安装后）

```
{ROOM_HOME}/
  bin/room[.exe]
  bin/oob-divzero[.exe]
  tools/c-toolchain/
    bin/clang[.exe]       ← 探测目标
    bin/…                 ← 链接器 / 运行时
    lib/…                 ← compiler-rt（ASan 需要）
    ROOM-NOTICE.txt
```

## 运行时探测顺序

1. `OOB_CC` / `ROOM_CC` / `CC`
2. `{install}/tools/c-toolchain/bin/clang`
3. PATH 上的 `clang` / `gcc` / `cc`（开发机回退）

`room doctor` 显示：

- `asan toolchain: bundled  …` → 产品路径
- `asan toolchain: system …` → 开发可用，发版应改为 bundled

## 发布者如何塞进套件

### Windows

```powershell
# 1) 下载可移植 clang（~200–400MB，不进 git）
.\scripts\fetch-c-toolchain-windows.ps1

# 2) 打完整套件（含 oob + 工具链）
.\scripts\package-suite.ps1 `
  -PaperDerived C:\pd.exe `
  -OobDivzero C:\oob-divzero.exe `
  -RequireCToolchain
```

默认源：`vendor\c-toolchain-windows`（`bin\clang.exe`）。

### macOS

```bash
./scripts/fetch-c-toolchain-macos.sh
./scripts/package-suite.sh \
  --paper-derived ../paper-derived/dist/... \
  --oob-divzero "$(command -v oob-divzero)" \
  --pi ... \
  --require-c-toolchain
```

默认源：`vendor/c-toolchain-macos-<arch>/`。

## 许可

工具链组件（LLVM / MinGW 等）遵循各自上游许可证。再分发时保留树内 LICENSE 与 `ROOM-NOTICE.txt`。Room 不主张对这些组件的版权。

## 体积

完整 `tools/c-toolchain` 通常 **150MB～400MB+**。这是「傻瓜式 oob ASan」的产品成本；轻量包可省略并依赖本机 clang（不推荐作为发布默认）。
