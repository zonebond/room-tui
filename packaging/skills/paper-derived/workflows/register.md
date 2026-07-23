# 工作流二：注册模板

用户提供样例文档，你学习其结构、风格、校验规则后注册为四模块模板。

## Step 0.5: 检查模板重复

在执行 `$PAPER_DERIVED_BIN template register` 之前，先运行：

```bash
$PAPER_DERIVED_BIN template list --json
```

检查是否有**内容相似**的已有模板。比对方法：

1. **Section 重叠度**：比较已有模板的 `section_ids` 与即将注册的文档结构。如果两个模板共享超过 50% 的 section，很可能用途相同。
2. **描述语义**：对比 description 是否指向同类型文档（如"API 接口设计" vs "RESTful API 文档"）。
3. **名称检查**：完全同名的注册会被 CLI 自动拦截，但你也要注意语义相同但表述不同的名称。

如果发现内容高度相似的模板，告知用户：「已存在相似的模板：[列表，含 section 重叠信息]。你是想：」
   - **覆盖旧模板** — 先 `$PAPER_DERIVED_BIN template delete <旧id>`，再注册新模板
   - **两个都保留** — 用不同的 name 注册
   - **取消注册** — 直接使用现有模板，跳到工作流一

对于无法仅凭 section_ids 判断的情况，可进一步用 `$PAPER_DERIVED_BIN template show <id>` 查看完整 prompt 内容做深度比对。

注意：CLI 有两层自动拦截——同名（name 精确匹配）和同 id（LLM 生成的 id 冲突）。但名称不同、id 不同但内容高度相似的模板仍可能被重复注册，这是你需要通过比对来防止的。

## Step 1: 构造并执行（走子代理）

> **上下文纪律同样适用**：注册 prompt 内嵌样例文档全文，体积可能很大。**不要在主上下文执行**，套用子代理协议（见 SKILL.md）：

```bash
mkdir -p prompts responses
# ① 落盘 prompt（文本格式，stdout 只回摘要）
$PAPER_DERIVED_BIN template register <sample-file> -n <template-name> -d "描述" --out prompts/reg-tpl.md
# ② 子代理读 prompts/reg-tpl.md（==== SYSTEM ==== / ==== USER ==== 两段）执行
#    → 写 responses/reg-tpl.json → 只回 DONE
```

子代理的任务是分析样例文档的结构、风格、内容模式、隐含校验规则。

## Step 2: 解析并确认

```bash
$PAPER_DERIVED_BIN template register <sample-file> -n <template-name> --parse responses/reg-tpl.json
# → {"status":"template_registered","template_id":"...","sections":5,"section_ids":[...]}
```

解析后模板存入 `~/.paper-derived/templates/<name>/profile.json`；stdout 只回注册摘要，完整定义用 `template show <id>` 查看（如需比对，派子代理去读）。

**向用户展示**：「模板已注册，包含 5 个 Section、7 条校验规则。你可以看看要不要调整？」用户可以直接编辑 `profile.json` 里的四个 prompt 模块。
