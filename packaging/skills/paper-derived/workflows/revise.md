# 工作流三：修改文档

用户对已生成的文档提出修改要求。

> **上下文纪律同样适用**：`revise` 命令会把目标 Section（或整档）原文装进 prompt，体积可能不小；**执行 prompt 的是子代理，不是主 Agent**。主 Agent 只落盘 prompt、派子代理、`--parse` 看状态，不读文档正文进自己的上下文。准备目录：`mkdir -p prompts responses`。

## 局部修改（走子代理）

```bash
# ① 落盘 prompt（含该 Section 原文 + 修改指令）
$PAPER_DERIVED_BIN revise section <doc.json> <section-id> "修改指令" --out prompts/rev-<section-id>.md
# ② 子代理读 prompts/rev-<section-id>.md 执行 → 写 responses/rev-<section-id>.json → DONE
# ③ 解析并覆盖文档
$PAPER_DERIVED_BIN revise section <doc.json> <section-id> "修改指令" \
  --parse responses/rev-<section-id>.json -O <doc.json>
```

告知用户改了什么（用状态里的字段，不复述整段正文）。

## 全局修改（走子代理）

用于风格统一、术语替换、语气调整等全局变更。整档原文进 prompt，尤其要走子代理：

```bash
$PAPER_DERIVED_BIN revise global <doc.json> "修改指令" --out prompts/rev-global.md
# → 子代理执行 prompts/rev-global.md → responses/rev-global.json → DONE
$PAPER_DERIVED_BIN revise global <doc.json> "修改指令" --parse responses/rev-global.json -O <doc.json>
```

> 大文档的全局修改，若单个 prompt 仍过大，考虑改为按 Section 循环 `revise section`（每节一个子代理，可并行），避免整档一次性塞进单个子代理窗口。

## 质检修复循环（被 generate.md / session.md 调用）

`gen validate` 报出 `CRITICAL` + `fixable` 时的自动修复：对每条 fixable 项调用 `revise section` 修复，**全程走子代理**，最多 3 轮；仍不过则转 `input_dependent` 处理（告知用户补充）。主 Agent 只在轮次间看 `--parse` 状态，不把被修正的正文读进上下文。
