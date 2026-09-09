# Canonical Case 与 CaseRun

## 理念

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

判定即数据适用于确定性 API 契约：`judge.e2e.assert` 直接描述 `{path, op, value}`，engine 可跨 case 复用。同一 case 需要前置资源、多步操作、异步收敛或昂贵收尾时，过程代码进入 CaseRun，而不是回到每-case 继承基类。

E2E 通常在 CaseRun 内立即应用 assertion 并写 Verdict，因此 Dataset build 与 EvaluationRun 可以落在
同一 Run 目录；语义上仍要保留原始 Outcome、Case/variant identity 和判断结果的边界。已有 Outcome Unit
应能使用另一版 assertion engine 或 soft metric 离线复判，而不重新调用被测系统。

Unit identity 可以在 Outcome 产生前由 `case_id + variant` 确定。prepare、execute 或 judge 失败时，
对应 Unit 仍应保留失败状态和阶段证据，不能为了得到“干净 Dataset”而静默丢弃。Experiment 等执行
overlay 只负责 Case 选择、variant、负载和环境，不覆盖 canonical Case 的 input、facets、sources 或
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

## Case 与 CaseRun 的边界

| owner | 持有 | 不持有 |
|---|---|---|
| spec-case Case | input、facets、requires、`judge.<face>`、binding | base URL、凭据、weight/concurrency、prepare/cleanup 代码 |
| CaseRun | phase 顺序、typed state、variant、deadline、cleanup、phase evidence | canonical asset schema、服务专用协议字段 |
| Runner | 请求协议适配、Outcome | 业务判定与资源生命周期 |
| Judge | Outcome/观测事实到 pass/fail | 远端资源操作 |
| Verdict | 机器可消费的结论、对齐键、最小 metrics/artifact 指针 | 完整日志和富产物副本 |

cleanup 总会执行，并拥有不继承 execute 取消状态的独立预算。cleanup 失败表示执行不可信，结论是 `error`，不能作为 best-effort warning 被隐藏。业务上的“等待 GC/同步完成”属于 execute/judge 的 temporal assertion；测试卫生层面的删除资源属于 cleanup。

## Variant 与长时间 case

variant matrix 是同一 case 的运行展开，不复制 CaseRef。variant 的稳定 id 进入 `arm_id`，键值同时进入 facets，便于 Verdict 对齐和报告 pivot。

长时间 case 使用阶段 deadline 感知的 poll/retry/consistently：

- poll：等待条件最终成立；
- retry：只重试调用方明确分类的瞬态错误；
- consistently：要求条件在观察窗内持续成立；
- 不用固定 sleep 表达系统收敛。

Python 同步 step 通过 `PhaseContext.remaining_s` 协作传递 deadline，并在返回后检查超时；Go step 直接接收 `context.Context`。语义和 Verdict 一致，API 保持语言习惯。

## Spec binding

一个代码 symbol 可以拥有多份命名 spec。Case 的 `binding.symbol_id` 先定位 symbol，`binding.spec_id` 再连接该 symbol 的 `specs[].id`；单 spec 时 `spec_id` 可省略。casegen 复用 spec-case 的 marker grammar，不在 harness 定义第二套 plural spec 规则。

## References

- 资产与 binding 真源：[spec-case](https://github.com/compforge/spec-case)
- Python e2e 代码地图：[`../sdks/python/e2e_harness/AGENTS.md`](../sdks/python/e2e_harness/AGENTS.md)
- Go CaseRun 与 coverage gate：[`../sdks/go/AGENTS.md`](../sdks/go/AGENTS.md)
- 统一 Verdict：[`../spec/verdict-schema.yaml`](../spec/verdict-schema.yaml)
