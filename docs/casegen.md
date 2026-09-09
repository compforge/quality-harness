# casegen

## 定位

casegen 连接共置 marker 与黑盒可执行覆盖。marker grammar、Case/CaseSet 和 symbol/spec binding 归 spec-case；casegen 只负责 harness 侧编译或对账，不把 LLM 放进测试运行链路。

## Python：marker 编译为 CaseSet

```text
@spec/@case
    → AST discover
    → Compiler(NL → structured input + judge.e2e.assert)
    → canonical CaseSet YAML
    → load_caseset + validate
```

编译产物提交进 git，运行时 engine 只读取结构化 Case，不调用 LLM。当前 `DraftCompiler` 生成可填写的确定性草稿；未填写 assertion 的 e2e face 是 `error`，不会假绿。

产物顶层 `compiled_from` 保存 `case_id → marker intent hash`。`casegen check` 重新扫描 marker 并对比 hash：

- marker 新增或意图变化：drifted；
- 产物中存在但 marker 已删除：orphaned；
- 未变化 case 原样复用，保留人工 review 后的结构化内容。

每条编译 Case 写入 `binding.symbol_id`、spec 文本和可选 `binding.spec_id`。`@spec(text, id=...)` 对齐 spec-case 的 plural `specs[]`；同一 symbol 多份 spec 必须用唯一 id，单 spec 可省略。

## Go：marker 与 CaseRun 静态对账

Go 的复杂 e2e 需要类型化 state 和过程代码，因此 casegen 不生成 Go test scaffold。测试用字面量声明资产引用：

```go
caserun.Ref("sandbox-runtime", "idle_gc")
```

```text
+case markers under --source ─┐
                              ├─ casegen check → exact coverage
caserun.Ref under --test ─────┘
```

`casegen check --source ... --test ... --caseset ...` 要求：

- CaseSet 内 marker case id 唯一；
- 每个 marker 恰有一个同 CaseSet Ref；
- 没有 orphan 或 duplicate Ref；
- Ref 的 CaseSet 与 case id 必须是非空字符串字面量，保证静态 gate 可判定。

variant matrix 在一个 CaseRun 内展开，不重复 Ref。handler/文件重命名不会改变 CaseSet + case id 的执行身份。

## 完整性边界

casegen 证明的是“声明的 marker 已进入可执行资产或 CaseRun”，不是业务行为正确。运行正确性仍由 CaseSet assertions、CaseRun judge、cleanup evidence 和最终 Verdict 证明。`skipped` / `error` 不能被发布门禁解释为 pass。

## 命令

```bash
# Python
casegen compile --source ./server --out ./cases.yaml --caseset service-api
casegen check   --source ./server --out ./cases.yaml

# Go
go run github.com/compforge/quality-harness/sdks/go/cmd/casegen list \
  --source ./internal/api
go run github.com/compforge/quality-harness/sdks/go/cmd/casegen check \
  --source ./internal/api --test ./tests/e2e --caseset service-api
```
