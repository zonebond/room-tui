# Session 状态机详解

## 生命周期

```
init ──→ feeding ──→ generating ──→ assembling ──→ complete
```

| Phase | 触发条件 | Agent 职责 |
|-------|----------|-----------|
| init | `session init` | 记住 session_id |
| feeding | `session feed --parse` | 查看 data_gaps，决定继续或补充输入 |
| generating | `session prompt --parse` | 循环 next → prompt → parse，可选 summarize |
| assembling | `session next` 返回 assemble | 调用 `session assemble` |
| complete | assemble 完成 | 质检 + 交付 |

## Section 状态转换

```
pending ──→ ready ──→ generating ──→ done
                         │
                         └──→ failed ──→ generating (重试)
                                    └──→ 告知用户 (attempt ≥ 3)
```

| 状态 | 含义 | 进入条件 | 退出条件 |
|------|------|----------|----------|
| pending | 未获得输入数据 | session init | session feed 为该 Section 提供实体 |
| ready | 数据就绪，可生成 | feed 匹配到实体 / 依赖完成 | session prompt 被调用 |
| generating | 正在生成 | session prompt 被调用 | --parse 提交结果 / 中断 |
| done | 生成完成 | --parse 成功 | — |
| failed | 生成失败 | --parse 返回异常 | 重试 / 用户干预 |

**中断恢复**：`generating` 状态的 Section 在 `session next` 时自动重置为 `ready`。

## session next 决策树

```
session next
  │
  ├── all_done? → action: "assemble"
  │
  ├── 有 ready 的 section?
  │     ├── 是 →
  │     │     1 个 → action: "generate", section_id: "..."
  │     │     多个 → action: "generate", parallel_batch: [...]
  │     │     (最多 6 个并行)
  │     │
  │     └── 否 →
  │           ├── 有 pending? → action: "feed_more", pending_sections: [...]
  │           └── 有 generating? → action: "wait", in_progress: [...]
  │
  └── (不应出现: 既无 ready 又无 pending 又无 generating)
```

## 错误恢复策略

### Section 生成失败

| 场景 | 判断 | 行为 |
|------|------|------|
| 首次失败 (attempt=1) | 临时问题 | 直接重试：`session prompt --section <id>` |
| 第 2 次失败 (attempt=2) | 可能是上下文不足 | 重试，并建议 `session summarize` 补充摘要 |
| 第 3 次失败 (attempt≥3) | 持续性问题 | **告知用户**，提供选项：补充输入 / 跳过该 Section / 降低预算重试 |

### Feed 失败

| 场景 | 行为 |
|------|------|
| LLM 响应 JSON 解析失败 | 重试 feed，检查响应格式 |
| data_gaps 严重（>50% Section 缺数据） | 告知用户输入严重不足，建议补充后再继续 |
| 部分实体提取失败 | 可继续，缺失 Section 会生成 placeholder |

### 中断恢复

1. 调用 `session status` 查看进度
2. 调用 `session next` — 自动跳过 done 的 Section
3. `generating` 的 Section 自动重置为 `ready`，重新生成
4. 继续正常循环

## Token 预算调优

prompt 由子代理在独立上下文窗口（约 200K）执行，budget 的约束对象是**子代理**，不是主编排上下文。默认 60,000。

| 场景 | 建议 token_budget | 说明 |
|------|------------------|------|
| 多数模板（Section 间弱依赖） | 30,000 - 60,000 | ContextStore 只挑相关上下文，默认档够用 |
| 强依赖模板 / 单节需要大量前文 | 60,000 - 100,000 | 子代理负载加重，速度和成本上升 |
| 上限 | ≤ 120,000 | 再大子代理读 prompt + 生成正文可能顶到其窗口上限 |

**原则**：budget 是 per-section 的输入 token 上限，不包括输出。budget 越大，每个 Section prompt 包含的上下文越丰富，但每个子代理越慢越贵；且需给子代理留足输出与工具开销的余量（引擎已按 70% 输入 / 30% 输出切分）。
