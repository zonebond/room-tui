# 大文档策略对比

三种策略解决不同层面的大文档问题，按需选用或组合。

## 策略总览

| 策略 | 解决什么 | 命令 | 断点续传 | 上下文管理 | 适用场景 |
|------|----------|------|----------|-----------|----------|
| `--chunk-size` + `--slim` | 输入大文档注册 | `input register` | ❌ | Agent 管理 | 单份大输入，小模板 |
| `--sections` + `--extract` + `--into` | 输出分批生成 | `gen generate` | ❌ | Agent 管理 | 中等文档，手动分批 |
| Session 模式 | 全流程 | `session *` | ✅ | CLI 自动 | 大文档，多输入，需续传 |

## 何时用哪种

```
大文档？
  │
  ├── 只是输入大（1份 200K PDF + 小模板 5 Section）
  │     → 工作流一 + input register --chunk-size --slim
  │
  ├── 只是输出大（小输入 + 大模板 20+ Section）
  │     ├── 能一次生成 → 工作流一
  │     └── 超上下文 → 工作流一 + gen generate --sections 分批
  │
  ├── 输入输出都大（3份输入 + 30 Section）
  │     → 工作流四：Session 模式
  │
  └── 需要暂停/继续
        → 工作流四：Session 模式（唯一支持断点续传）
```

## 策略组合示例

### 输入大 + 输出小
```bash
# 大文档分块注册 + 精简
$PAPER_DERIVED_BIN input register big.pdf -n big --chunk-size 30000 --slim

# 正常工作流一生成
$PAPER_DERIVED_BIN gen generate -i big.json -t api-design -O output.json
```

### 输入小 + 输出大
```bash
# 正常注册
$PAPER_DERIVED_BIN input register spec.md -n spec

# 分批生成
$PAPER_DERIVED_BIN gen extract -i spec.json -t srs --parse /tmp/pd/extract.json
$PAPER_DERIVED_BIN gen generate -i spec.json -t srs \
  --sections scope,identification --extract extract.json --into doc.json -O doc.json
# ... 后续批次
```

### 输入大 + 输出大
```bash
# Session 模式一步到位
$PAPER_DERIVED_BIN session init -t srs
$PAPER_DERIVED_BIN input register big.pdf -n big --chunk-size 30000 --slim
$PAPER_DERIVED_BIN session feed -s <sid> -i big.json
# → 循环生成...
$PAPER_DERIVED_BIN session assemble -s <sid> -O output.md
```
