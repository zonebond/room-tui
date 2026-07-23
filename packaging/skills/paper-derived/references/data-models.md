# paper-derived 数据模型参考

## Template

| 字段 | 类型 | 说明 |
|------|------|------|
| id | str | 模板唯一标识 |
| name | str | 模板显示名称 |
| description | str | 模板描述 |
| extraction_prompt | str | 抽取模块 prompt |
| structure_prompt | str | 结构模块 prompt |
| style_prompt | str | 风格模块 prompt |
| validation_prompt | str | 校验模块 prompt |
| section_ids | list[str] | Section ID 列表 |
| section_dependencies | dict | Section 依赖关系 |
| section_tree | list[dict] | 带层级的 Section 树 |

## InputAsset

| 字段 | 类型 | 说明 |
|------|------|------|
| id | str | 资产唯一标识 |
| name | str | 资产名称 |
| type | str | 资产类型 |
| raw_content | str | 原始内容（--slim 时为空） |
| summary | str | AI 生成的摘要 |
| entities | list[Entity] | 提取的实体列表 |

## Entity

| 字段 | 类型 | 说明 |
|------|------|------|
| kind | str | 实体类型 |
| name | str | 实体名称 |
| description | str | 实体描述 |
| location | str | 在源文件中的位置 |

## DocumentTree

| 字段 | 类型 | 说明 |
|------|------|------|
| document_id | str | 文档 ID |
| template_id | str | 模板 ID |
| title | str | 文档标题 |
| sections | list[Section] | 递归 Section 列表 |
| metadata | DocumentMeta | 文档元数据 |

## Section

| 字段 | 类型 | 说明 |
|------|------|------|
| id | str | Section ID |
| title | str | Section 标题 |
| content | str | Markdown 内容 |
| children | list[Section] | 子 Section |
| level | int | 层级深度 |
| template_ref | str | 对应模板 Section ID |
| status | str | generated / placeholder / empty |
| lineage | list[LineageRef] | 内容来源追溯 |
| hints | list[str] | 生成提示 |

## LineageRef

| 字段 | 类型 | 说明 |
|------|------|------|
| input_id | str | 来源输入资产 ID |
| fragment_ref | str | 来源位置 |
| confidence | float | 置信度 |

## PreflightReport

| 字段 | 类型 | 说明 |
|------|------|------|
| ok | bool | 是否全部通过 |
| sections | list[SectionPreflight] | 各 Section 检查结果 |
| summary | str | 总结 |

## ValidationReport

| 字段 | 类型 | 说明 |
|------|------|------|
| passed | bool | 是否通过校验 |
| total_checkpoints | int | 总检查点数 |
| passed_count | int | 通过数 |
| failed_count | int | 失败数 |
| checkpoints | list[ValidationCheckpoint] | 检查点列表 |

## ValidationCheckpoint

| 字段 | 类型 | 说明 |
|------|------|------|
| rule | str | 校验规则描述 |
| severity | str | CRITICAL / WARNING |
| rule_type | str | fixable / input_dependent |
| passed | bool | 是否通过 |
| message | str | 详细信息 |

## GenerationSession

| 字段 | 类型 | 说明 |
|------|------|------|
| session_id | str | 会话唯一 ID (sess_xxx) |
| template_id | str | 模板 ID |
| phase | str | init \| feeding \| generating \| assembling \| complete |
| input_asset_ids | list[str] | 已注册的输入资产 ID |
| section_progress | dict[str, SectionProgress] | Section 生成进度 |
| token_budget | int | per-section token 预算（默认 60000） |
| checkpoint_version | int | Checkpoint 版本号 |

## SectionProgress

| 字段 | 类型 | 说明 |
|------|------|------|
| section_id | str | Section ID |
| status | str | pending \| ready \| generating \| done \| failed |
| depends_on | list[str] | 依赖的 Section ID |
| attempt_count | int | 生成尝试次数 |

## ContextStore（内部，Agent 不可见）

| 字段 | 类型 | 说明 |
|------|------|------|
| glossary | dict[str, str] | 术语表 |
| style_rules | list[str] | 风格规则 |
| validation_rules | list[str] | 校验规则 |
| entity_index | dict[str, ContextEntity] | 实体索引 (kind:name → entity) |
| extraction_map | dict[str, SectionExtraction] | Section → 实体映射 |
| raw_fragments | dict[str, str] | 实体 → 原文片段 |
| section_summaries | dict[str, SectionSummary] | Section → 摘要 |
