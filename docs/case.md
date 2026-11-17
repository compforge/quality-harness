# Case：测试意图与资产

## 资产与运行边界

spec-case 的 `Case` / `CaseSet` 是 e2e、eval、perf 共用的唯一输入资产。Case 保存稳定、可分享的测试意图：一次 stimulus、facets、source 依赖、各 face 的判定数据，以及可选的代码契约 binding。环境、凭据、负载参数和运行过程不进入 Case。

quality-harness 持有执行侧：Runner、CaseRun、Judge、运行证据和 Verdict。

```text
CaseSet + environment + variant
              ↓
CaseRun: prepare → execute → judge → cleanup
              ↓
Outcome Observation → Unit（Case + Outcome）→ Dataset
              ↓
assertion / judge / metric → EvaluationRun / Worksheet
              ↓
Verdict + Report
```

判定即数据适用于确定性 API 契约：`judge.e2e.assert` 直接描述 `{path, op, value}`，engine 可跨 case 复用。同一 case 需要前置资源、多步操作、异步收敛或昂贵收尾时，过程代码进入 CaseRun。

Experiment 等执行 overlay 只负责 Case 选择、variant、负载和环境，不覆盖 canonical Case 的 input、facets、sources 或
judgment。

## 编写与执行流程

Case 有两个编写前端：

```text
手写 CaseSet YAML ─────────────────────────────┐
                                               ├─→ canonical CaseSet
代码旁 NL marker → casegen / reviewed output ─┘
                                                      ↓
                                           engine / custom CaseRun
                                                      ↓
                                                   Verdict
```

- 数据优先：直接维护 CaseSet，适合共享 corpus、跨项目 conformance 和 benchmark。
- 代码优先：`@case/@spec` 或 `+case/+spec` 与被断言 symbol 共置，适合随契约演进。
- Python casegen 将 NL marker 编译为结构化 CaseSet，并以 intent hash 检查漂移。
- Go 的复杂 e2e 保留类型化过程代码；casegen 静态核对 marker 与 `caserun.Ref(caseset,id)`，不生成测试体。

两条路径共享 CaseSet + case id 身份。文件名、测试函数名、handler 名和 marker group 都不是运行身份。

Case 的内容和身份固定测试意图；variant、阶段预算、prepare / cleanup 和长时间等待属于
[CaseRun 执行](execution.md#caserun)。

## Spec binding

一个代码 symbol 可以拥有多份命名 spec。Case 的 `binding.symbol_id` 先定位 symbol，`binding.spec_id` 再连接该 symbol 的 `specs[].id`；单 spec 时 `spec_id` 可省略。casegen 复用 spec-case 的 marker grammar，不在 harness 定义第二套 plural spec 规则。

## Casegen

casegen 连接共置 marker 与黑盒可执行覆盖。marker grammar、Case/CaseSet 和 symbol/spec binding 归 spec-case；casegen 只负责 harness 侧编译或对账，不把 LLM 放进测试运行链路。

### Python：marker 编译为 CaseSet

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

### Go：marker 与 CaseRun 静态对账

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

### 完整性边界

casegen 证明的是“声明的 marker 已进入可执行资产或 CaseRun”，不是业务行为正确。运行正确性仍由 CaseSet assertions、CaseRun judge、cleanup evidence 和最终 Verdict 证明。`skipped` / `error` 不能被发布门禁解释为 pass。

### 命令

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

## References

- 资产与 binding 真源：[spec-case](https://github.com/compforge/spec-case)
- Python e2e 代码地图：[`../sdks/python/e2e_harness/AGENTS.md`](../sdks/python/e2e_harness/AGENTS.md)
- Go CaseRun 与 coverage gate：[`../sdks/go/AGENTS.md`](../sdks/go/AGENTS.md)
- 统一 Verdict：[`../spec/verdict-schema.yaml`](../spec/verdict-schema.yaml)
