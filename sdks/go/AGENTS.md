# Go SDK

## 项目定位与边界

Python 侧形状的 Go 等价实现，**预先按 e2e/eval/perf 三分**，并为多个 Harness 提供中立的平台工具箱。当前 e2e 落地确定性契约测试、完整 CaseRun 生命周期，以及「case 贴着 handler」的静态覆盖闸门。Python/Go 对齐执行语义与 Verdict，不要求 API 语法一致。

## 代码地图与核心模块

```
sdks/go/
├── e2e/                # 确定性契约测试（= sdks/python/e2e_harness）
│   ├── caserun/        # prepare/execute/judge/cleanup + phase budgets/evidence + Recorder
│   ├── testrun/        # go test 的 Run 聚合、TestMain 集成与统一 Verdict 出口
│   ├── matrix/         # variant Cartesian product；arm_id/facets 投影
│   ├── core/           # Config + runtime identities / profile+capability / context-aware Poll/Retry/Consistently
│   ├── runner/         # 纯 Runner(ctx) + Outcome / JSONRunner / RawRequest
│   ├── judge/          # 不依赖 testing.T 的 Assertion
│   ├── burst/          # burst.Run[T] 泛型并发发射
│   └── contract/       # +case marker 与 caserun.Ref(caseset,id) 的静态 coverage gate
├── toolbox/
│   └── kube/           # e2e / perf 共用的 namespace-scoped Kubernetes 操作与观测
├── eval/  perf/        # 占位骨架（短期不实现，仅固定包边界与 import 路径）
├── report/             # Go Verdict wire projection
├── cmd/casegen/        # CLI：list / check
└── go.mod              # module: github.com/compforge/quality-harness/sdks/go
```

## 关键约定

- **case 贴着 handler**：marker grammar 与 plural `specs[]` / `binding.spec_id` 由 spec-case 持有；casegen 用 Go AST 纯静态扫描，不 import、不运行被测服务。
- **执行身份**：测试以字面量 `caserun.Ref("<canonical-caseset>", "<case-id>")` 绑定资产。CaseSet 内 case id 唯一；variant 在同一 CaseRun 内展开，不重复声明 Ref。
- **Kernel 对齐**：Runner `Outcome` 是 Observation，一次 CaseRun（Case + variant）是 Unit；`testrun.Run` 收集的可复判 Unit facts 对齐 Dataset，本次选择的 judge/assertion 直接定义评估侧重点，Verdict 和后续报告从对应 Worksheet 投影。Go 与 Python 共享这些语义，不要求公开 API 同形；详见 [`../../docs/kernel.md`](../../docs/kernel.md#dataset-与反复评估)。
- **coverage gate 不生成测试体**：`casegen check` 要求每个 marker 恰有一个 Ref，并拒绝 missing/orphan/duplicate/dynamic ref。测试过程代码归消费方，框架不维护 scaffold/meta 区域。
- **生命周期失败语义**：judge mismatch 用 `caserun.Fail` → fail；请求、环境、timeout、cleanup 异常 → error；cleanup 独立 context 且总执行。
- **Run 是运行出口**：消费仓用 `testrun.Run.Assert` 记录每个 CaseRun，并在 `TestMain` 调 `testrun.Run.Main`；统一写入 `runs/<scope>/<run-id>/verdict.json`，未执行任何 Case 时不制造空 run。`testrun` 只是 Go testing adapter，稳定模型仍是 CaseRun → Run → Verdict。
- **跨语言行为由 fixture 约束**：`../../conformance/e2e/caserun.yaml` 同时被 Python/Go 测试消费，固定阶段顺序、状态、cleanup、variant/facets 与 Verdict rollup 语义。
- **平台工具箱保持中立**：`toolbox/kube` 只执行 namespace-scoped 的 Kubernetes 操作、稳定等待和证据采集，不解释业务 Worker、Case、故障场景或 Verdict；目标 selector、注入时机与通过条件由消费方拥有。详见 [`../../docs/toolbox.md`](../../docs/toolbox.md)。
- **单行 marker 天然躲开 gofmt**：每个 `+case` 是一行，没有续行就没有 Go 1.19+ doc-comment 把续行改 tab 缩进的问题。
- **import 路径**：四层在 `e2e/` 下，如 `github.com/compforge/quality-harness/sdks/go/e2e/core`。

## 开发与测试

```bash
cd sdks/go && go test ./...
cd sdks/go && go vet ./... && gofmt -l .         # gofmt -l 输出为空才算干净
cd sdks/go && go build ./...

# 在被测服务仓库里驱动 casegen（discovery 不依赖被测服务编译）
go run github.com/compforge/quality-harness/sdks/go/cmd/casegen list  --source ./internal/api
go run github.com/compforge/quality-harness/sdks/go/cmd/casegen check --source ./internal/api --test ./tests/e2e --caseset sandbox-runtime
```

## References

- 跨 Harness 环境操作与观测工具箱：[`../../docs/toolbox.md`](../../docs/toolbox.md)
- 跨语言约定（case/config schema）：[`../../spec/conventions.md`](../../spec/conventions.md)
- `+case` 注解 fixture：[`e2e/contract/testdata/src/sandbox.go`](e2e/contract/testdata/src/sandbox.go)
