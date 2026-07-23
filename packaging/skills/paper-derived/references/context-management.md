# 上下文预算管理与主动清理协议

> **这是 paper-derived 可运行性的核心协议。** 它解决编排者上下文随 Section 数线性增长最终超限的问题。

## 问题本质

子代理委托解决了「单次大 prompt 撑爆上下文」的问题，但**编排者自身的上下文仍随步骤线性累积**：

```
每 Section 编排开销 ≈ 6–10 个命令回合（next / prompt --out / spawn / --parse / summarize --out / spawn / --parse）
每回合 ≈ 200–500 tokens（命令输出 + 状态 JSON + 对话）
单 Section ≈ 1500–4000 tokens 编排开销
48 Section ≈ 72K–192K tokens 编排开销
+ 初始上下文（SKILL.md + workflow.md + setup）≈ 15K–25K
+ 错误重试 / 用户交互
──────────────────────────────
总计轻松突破 150K+ tokens
```

**编排者自己就是瓶颈。** 即使每个子代理干净利落，编排者对话历史里的命令输出、状态报告、决策日志线性堆积，auto-compact 还会摘丢跨节精确状态。

## 核心思路：最大化并行 × 按需清理 × 无感续传

**`/clear` 是 Claude Code 平台约束，无法由 Agent 自动执行。** 编排者必须由用户手动清理上下文。因此核心策略是**最大化 `parallel_batch` 以减少总回合数，提高清理触发阈值，降低用户干预频率**。

`session next` 返回 `parallel_batch` 时，N 个独立 Section 仅需 ~7 个命令回合（vs 单节 × N 的 N×7 回合）。在 128K 上下文中，这足以支撑 **40–60 个 Section 而不需清理**。

清理仅在以下情况触发：
- 连续大量依赖链 Section（无法并行，每个依赖链 Section 增加 ~8 回合）
- 频繁错误重试（每次 +8 回合）
- 用户与编排者的长对话交互

```
触发条件（默认阈值 15 Section，可通过 --cleanup-interval 调整）：
  1. 累计完成 cleanup_interval 个 Section
  2. 且 session next 未返回 assemble（即非最后一批）
  3. 写 RESUME_STATE.json → 告知用户 /clear → 用户发任意消息 → 自动续传
```

清理成本 ≈ 2–3 秒（清空 + 重读 SKILL.md + 恢复状态）。

## RESUME_STATE.json

位置：工作目录根目录（`/clear` 后仍然存在）。

### Session 模式

```json
{
  "workflow": "session",
  "session_id": "sess_abc123",
  "template_id": "srs",
  "phase": "generating",
  "completed_count": 12,
  "total_sections": 48,
  "batch_size": 6,
  "cleanup_count": 2,
  "format": "md",
  "user_prefs": {
    "output_format": "md",
    "budget": 120000,
    "style_notes": "正式风格，避免口语化"
  },
  "created_at": "2026-07-12T10:30:00"
}
```

`user_prefs` 保存跨清理需要记住的用户偏好（输出格式、风格要求等）。**编排者绝对不应在恢复后重新询问用户已保存在这里的偏好。**

**恢复方式**：`session_id` 是唯一真相源——恢复后跑 `session status` + `session next` 即可拿到精确当前状态。`completed_count` 仅用于向用户展示进度，不作为循环计数器（循环由 `session next` 驱动）。

### 分批生成模式（generate.md 大模板分批路径）

```json
{
  "workflow": "generate-batch",
  "template_id": "srs",
  "input_assets": ["input-1.json", "input-2.json"],
  "extract_result": "extract-result.json",
  "output_file": "doc.json",
  "batches_completed": [1, 2, 3],
  "next_batch": 4,
  "format": "md",
  "user_prefs": {
    "output_format": "md",
    "style_notes": null
  },
  "cleanup_count": 1,
  "created_at": "2026-07-12T10:30:00"
}
```

### 模板注册模式

```json
{
  "workflow": "register",
  "sample_file": "/path/to/sample.pdf",
  "template_name": "my-template",
  "description": "...",
  "phase": "analyzing_structure",
  "user_prefs": {},
  "cleanup_count": 0,
  "created_at": "2026-07-12T10:30:00"
}
```

## 清理决策

### 触发条件（满足任一即触发）

| 条件 | 阈值 | 说明 |
|------|------|------|
| Section 批次数 | 每 6 个 Section 完成 | 默认，session init 的 `--cleanup-interval` 可调 |
| 显式请求 | 用户说「清理上下文」「继续」「压缩」 | 立即触发 |
| 错误恢复 | 连续 3 个 Section 失败 | 清理可能的状态污染后重试 |

### 不触发条件

- 最后一批 Section 已完成（即将 assemble）→ 不清理，直接组装交付
- `cleanup_count` ≥ 10 → 告知用户，不再自动清理（异常循环保护）

## 恢复协议（SKILL.md 启动时执行）

恢复设计目标：**编排者恢复后直接继续，不重新询问任何用户已回答过的问题。** `user_prefs` 承载跨清理的用户偏好。

```
SKILL.md 被加载
  │
  ├── 检查 $PWD/RESUME_STATE.json 是否存在
  │     ├── 不存在 → 正常流程，识别用户意图
  │     │
  │     └── 存在 →
  │           ├── created_at > 24 小时 → 告知用户发现过期进度，询问是否续传
  │           ├── workflow = "session" → 读 session_id 和 user_prefs
  │           │     → session status → session next
  │           │     → 直接继续循环，不重新询问格式/风格/budget
  │           │     → 告知用户当前进度后立即开始下一批 Section
  │           │
  │           ├── workflow = "generate-batch" → 读配置和 user_prefs
  │           │     → 从 next_batch 继续分批生成，不重新询问
  │           │
  │           ├── workflow = "register" → 读配置和 user_prefs
  │           │     → 从保存的 phase 继续注册
  │           │
  │           └── workflow 未知或 session_id 不匹配
  │                 → 告知用户状态异常，保留 RESUME_STATE.json，等待指示
  │
  └── 恢复成功 → 更新 RESUME_STATE.json（新 checkpoint）→ 继续执行
      恢复失败 → 保留 RESUME_STATE.json → 告知用户 → 等待指示
```

**关键行为约束**：
- 恢复后**绝不**重新询问用户已保存在 `user_prefs` 中的偏好
- 恢复后的第一条消息应该是进度摘要（"已恢复 Session X，进度 12/48，继续生成..."），然后直接开始下一批
- 不重复输出 SKILL.md 的角色说明或通用约束——直奔主题

## 各工作流集成点

### session.md

循环中每完成 `CLEANUP_INTERVAL` 个 Section（默认 6）：

1. 更新 `RESUME_STATE.json`（`completed_count`、`cleanup_count`、`user_prefs`）
2. 调用 `session next` 判断：
   - `action = "assemble"` → 跳过清理，直接组装交付
   - 其他 → 告知用户进度 + `「请 /clear 后发送任意消息，自动续传」`
3. `/clear` 后用户发送任意触发 paper-derived 的消息 → SKILL.md 检测 RESUME_STATE.json → 读 `session_id` → `session status` → `session next` → 直接继续，不重新询问

### generate.md 分批模式

每 2–3 批后写 RESUME_STATE.json（含 `next_batch`），提示清理后续传。详见 generate.md。

### register.md

大样例（> 30K）开始前写 RESUME_STATE.json 作为保护。成功即删。

### revise.md

单次操作无需清理。大文档逐 Section 全局修改同样适用批次清理。

## 批次大小选择

`--cleanup-interval` 控制编排者上下文清理的频率。**越大 = 越少中断，但对并行度敏感**。

| 模板 Section 数 | 建议 cleanup_interval | 预计清理次数 | 说明 |
|----------------|---------------------|-------------|------|
| ≤ 20 | 不清理 | 0 | `parallel_batch` + 常规路径足够 |
| 21–40 | 20（或默认 15） | 1–2 | 仅依赖链密集时需清理 |
| 41–60 | 15 | 2–3 | 保守阈值 |
| 60+ | 10–12 | 5+ | 建议拆分输入或改用更小模板 |

## 防抖与异常保护

1. **连续清理保护**：`cleanup_count` 记录已清理次数。若连续清理 ≥ 3 次但 `completed_count` 未增长 → 说明某 Section 反复失败 → 停止清理，告知用户排查。
2. **新旧状态冲突**：恢复发现 `RESUME_STATE.json` 与 `session status` 严重不一致（如 session 已完成但 RESUME 说还在 generating）→ 以 CLI 磁盘状态为准，废弃 RESUME。
3. **残留清理**：`session assemble` 或文档交付后，删除 RESUME_STATE.json。
4. **多会话隔离**：RESUME_STATE.json 存当前 session_id。若用户用同一目录开新 session，旧 RESUME 的 session_id 不匹配 → 提示用户确认。

## 与子代理执行协议的关系

上下文预算管理是**编排者层面**的协议，子代理执行协议是**单次 prompt 执行层面**的协议。两者互补：

| 层面 | 协议 | 解决的问题 |
|------|------|-----------|
| 编排者 | 上下文预算管理（本文档） | 编排者上下文随步骤线性增长 |
| 子代理 | 子代理执行协议（SKILL.md） | 单次 prompt 体积过大 |
| CLI | ContextStore + summarize | 下游 prompt 组装体积控制 |

三层防护 → 无论文档多大，每一层的上下文都在可控范围内。
