# perf_harness 统一 metric 模型

> 状态：**已实现**（greenfield 重构后收敛为 `metric/` 包：`family` + `store` + `reduce`）。本文是 perf 的**唯一 metric 概念**，也是整个 harness 的**脊柱**：加压 / Probe / Workload 只是 metric 的**生产者**，report / SLO / capacity 只是**消费者**，中间收腰在 `MetricFamily`（族）+ typed `MetricSummary`（值，带 caveats）+ `MetricStore`（读面）上——让 per-request 延迟、资源 gauge、server counter、派生标量走**同一条读取面**，消费方都只认 `<family>{labels}.<stat>`。
> 取向参考 Prometheus 的 *typed metric* + *family/series + label* 思路（类型决定合法操作、label 即维度）。服务端遥测由内嵌 Prombed 按 Prometheus 语义抓取、存储和查询；查询结果再进入本模型（见 §5）。

---

## 1. 理念：metric 是脊柱，组件产 metric、分析读 metric

perf 的中心不是"加压"，是 **metric**。它是个**收腰（沙漏）**：生产侧形态各异，全部 fan-in 收敛成统一 metric；消费侧再从这一个模型 fan-out。两边互不知道（也不需要知道）对方怎么实现。

```
生产者（怎么来的·多样）             [收腰]               消费者（怎么看·多样）
 Workload.fire → 请求侧分布      ┐                      ┌→ report（汇总 / 按 facet / 按 service）
 Probe.sample  → 资源侧序列      ┼→  MetricFamily       ┼→ SLO gate（CI 退出码）
 reduce 聚合 → request.*                    ┘ + MetricSummary └→ capacity（满足 SLO 的最高档）
                                  + MetricStore
                                 `<family>{labels}.<stat>`
```

- **生产者只管产**：几种来路（每请求标量 / 周期采样序列 / 聚合与派生）在 `reduce` 处铸成同一种货币 `MetricSummary`，**不强行统一写侧形状**（标量 vs 序列本质不同），只在出口收敛。
- **消费者只管读**：SLO / capacity 通过 `MetricStore.query` 在指定 `Window` 内按 `<family>{labels}.<stat>` 寻址；service/facet 是实体维度，时间不伪装成 label。report 渲染请求侧热字段直读 `RequestStats`（Window 的存储底，非另一套计算），资源侧 pivot 也走 store。
- **加压不是 metric，是 x 轴**：harness 测响应面 `metric = f(Arm, Window)`；Arm 给出资源与负载配置，Window 给出实际观测边界，metric 是因变量。

而统一**不是**"一个大类塞所有字段"。它体现在三处，且都不抹平类型：

- **family**：每个 metric 自带 `value_kind`，类型决定它有哪些合法 stat（不是 `MetricStat(value=None, p95=None, peak=None…)` 这种 nullable 矩阵——消费时还得猜哪个字段有值）。用 **typed summary union** 保住类型语义。
- **series**：族 + labels 唯一确定一条 series（`series_id`），元数据只在 family 上存一份。
- **store**：任何 Window 内的 facet / service slice 都用同一个 `<family>{labels}.<stat>` 读，消费方不关心它来自 Outcome、Probe 还是派生计算。

---

## 2. 数据模型

```python
# metric/family.py —— 纯数据 + 纯函数，只依赖 stdlib（不 import model.py → 无环）
MetricSide      = Literal["request", "resource"]
MetricValueKind = Literal["counter", "gauge", "distribution", "scalar"]
Caveat = Literal["co_biased", "high_drop", "few_samples",
                 "stale", "counter_reset", "probe_error"]   # 可信度，随值走

@dataclass(frozen=True)
class MetricFamily:                # 族：元数据声明一次，NO label（label 在 series 上）
    name: str                      # ttft_ms / top.mem_mi / request.duration_ms（族名，不含 label）
    unit: str                      # ms / MiB / count
    side: MetricSide               # request=按 facet 切片 | resource=按 service 切片（§3.2）
    value_kind: MetricValueKind    # 决定合法 stat
    source: str = "client"         # client / http / k8s / server —— 瓶颈归因分组（纯元数据）
    description: str = ""          # 人话含义；报告 tooltip 用

def series_id(name, labels) -> str:    # 具体 series = 族 + labels（Prometheus 记法）
    # 无 label → 'top.cpu_m'；有 → 'top.cpu_m{service="example"}'（label 按 key 排序）
    ...

@dataclass(frozen=True)            # 每个 summary 自带 caveats（CO-bias 的 p99 不会被当干净值读）
class CounterSummary:       total: float; rate: float|None=None; increase: float|None=None; caveats=frozenset()
@dataclass(frozen=True)
class GaugeSummary:         last: float;  mean: float|None=None; peak: float|None=None;     caveats=frozenset()
@dataclass(frozen=True)
class DistributionSummary:  n: int; mean: float; p50: float; p95: float; p99: float;        caveats=frozenset()
@dataclass(frozen=True)
class ScalarSummary:        value: float;                                                   caveats=frozenset()

MetricSummary = CounterSummary | GaugeSummary | DistributionSummary | ScalarSummary

@dataclass(frozen=True)
class Missing:                      # query 的"无数据"是个值，不是裸 None
    reason: Literal["no_slice", "no_data", "too_few_samples", "probe_error"]
Read = float | Missing
```

**合法 stat 由 `value_kind` 决定**（resolver 据此校验）：

| value_kind | 合法 stat |
|------------|-----------|
| `counter` | `total` / `rate` / `increase` |
| `gauge` | `last` / `mean` / `peak` |
| `distribution` | `n` / `mean` / `p50` / `p95` / `p99` |
| `scalar` | `value` |

**读面 = `MetricStore`**（`metric/store.py`，架在 trials 上，是消费方唯一入口）：

```python
class MetricStore:
    def query(self, trial, ref, window=None) -> Read  # 默认 measurement Window
    def pivot(self, trial, family, by, window=None) -> dict
    def rows(self) -> list[TrialRecord]               # 扫响应面（找 knee / capacity）
```

`query` 先确定 Window（省略时为 measurement），再把 `service` label 路由到资源 metric、facet label 路由到请求 slice、无 label 路由到该 Window 的整体请求统计。底层 `resolve(summaries, ref)` 纯函数从 `{series_id: MetricSummary}` 里取 `getattr(summary, stat)`，缺则返回 `None`（store 转成 `Missing`）。`RequestStats` 仍是请求 slice 的**存储底**，store 是其上的**寻址层**（perf 版 TSDB vs PromQL）。

寻址示例（一套语法，类型不抹平；service/facet 是 label，Window 单独传入）：

```text
ttft_ms.p95                          # request  / distribution
request.duration_ms{difficulty="complex"}.p99   # request / distribution（facet 切片）
request.error_rate.value             # request  / scalar
client.inflight.peak                 # resource / gauge
top.mem_mi{service="worker"}.peak    # resource / gauge（资源·某服务）
prometheus.request_rate{service="example"}.mean  # resource / gauge（PromQL 结果）
```

---

## 3. 关键设计

### 3.1 typed union 而非 nullable 矩阵

`value_kind` + 四个独立 summary 保住了类型语义：`gauge` 没有 `p99`、`scalar` 没有 `peak`，这些在**解析期就是错误**，而不是运行时返回 `None` 静默跳过。这是 Prometheus "类型决定合法操作" 的忠实借用（见 §5）。

### 3.2 label 边界：`side` 一位说清谁能切什么

实体切片即 label（service / facet，统一 `<name>{labels}.<stat>`），
而**哪些 label 合法由 family 的 `side` 决定**——一位数据，不是按 metric 名特判的散文规则：

- `side="request"`（Outcome 聚合而来：内置 `request.*`、ttft_ms、动态 `first_<event>_ms`）
  → 可带已声明的 facet label（请求侧能归因到具体请求）。
- `side="resource"`（Probe 序列，包括 PromQL 查询结果）→ 只能裸或带**资源侧 label**：
  `{service="…"}`；`observe:` 开 `per_pod` 时该服务的 series 变成 `{pod="…",service="…"}`
  （按 replica 拆，pod 只是又一个资源 label）。

可切性的真轴是"**能否归因到某请求**"：request 侧能（一条延迟属于某个 case），resource 侧
不能（一个 t=5s 的 cpu 采样不属于"复杂请求"）。SLO 据此**双向**校验：facet label 配
resource family → 报错；`service` label 配 request family → 也报错（否则会静默 missing 或崩
resolver）。SLO 的寻址只到 `{service=…}` 这一层（pod 名是易变的随机后缀，不适合做 gate）——
所以 per_pod 服务上的 service 级 SLO 在解析期直接报错，见 §3.5。

### 3.3 builtin `request.*` 虚拟 metric：resolver 当门面，typed 字段保留

`RequestStats` 的 `n/p50_ms/p95_ms/p99_ms/error_rate/throughput_rps/drop_rate` **保留为具体字段**（汇总表/CSV 的热路径直接读，不绕 resolver）。同时框架注册一组内置 descriptor，让它们也能被 resolver/SLO 统一寻址：

| 虚拟 metric（side=request） | value_kind | 委托字段 |
|-------------|-----------|----------|
| `request.duration_ms` | distribution | `p50_ms/p95_ms/p99_ms`（mean/n 同源） |
| `request.error_rate` | scalar | `error_rate` |
| `request.throughput_rps` | scalar | `throughput_rps` |
| `request.drop_rate` | scalar | `drop_rate` |

resolver 是**统一读面（façade）**，不是替换具体字段。

### 3.4 facet schema 与 SLO gate：declared 才能进门，runtime 只进 report

借 Prometheus label 思路、但更严：**进 gate 的 facet 必须有静态可审查的 schema**。

```text
declared_facets = config.facets  ∪  cases[].facets  ∪  Workload.describe_facets()
```

- `cases[].facets`：静态 mix 维度，天然可 gate。
- `facets:`（config，带允许值）：显式声明维度，可用于 runtime facet 的 gate。
- `Workload.describe_facets() -> list[FacetDescriptor]`：业务 adapter 声明它会盖哪些 runtime facet（如 `heavy`）。
- `Workload.fire()` 实际盖的**未声明** facet：**只进 report**（探索用），**不允许被 SLO scope 引用**。

这修正了 !56 的局限（当时只验 `cases[].facets`，会误杀合法 runtime facet）。

### 3.5 解析期 fail-fast —— gate 不能"配错了还假绿"

SLO 配置在**解析期**统一校验，任一不过即 `ValueError`（绝不留到运行时静默 skip）：

1. **未知 metric**：`metric` 的 family 不在 registry。
2. **非法 stat**：`<name>.<stat>` 的 stat 不在该 `value_kind` 的合法集合（§2 表）。
3. **label 越界**：资源(time_sampled)metric 配 facet label；或 `service` 配请求侧 metric（§3.2 双向规则）。
4. **未知 label 值**：facet 值不在 declared schema（§3.4）；`service` 值不在 `observe:` 观测集。
5. **producer 契约**：`Workload.describe()` 只能声明 request 侧 distribution、`Probe.describe()` 只能 resource 侧，谁都不许 shadow builtin `request.*`，同名 family metadata 冲突也报错。Probe 的元数据是单一声明表 `families: dict[name, FamilySpec(unit, value_kind, description)]`——describe/summarize/Engine 共读一份（借 otel-collector mdatagen 的'metric 元数据是一张表'）。
6. **per_pod 与 service 级 gate 冲突**：某服务开了 `per_pod` 后只有 `{pod,service}` series、没有 service 级聚合，`{service="…"}` 的 SLO 每轮都会落 skip（而 `strict_slo` 默认 false，等于 CI 门静默失效）→ 解析期报错，让用户在"按 pod 拆"与"service 级 gate"之间显式二选一。

### 3.6 运行时三态：skip ≠ pass

解析期过了，运行时仍可能某 slice 确实无数据（如某档随机权重恰好没发出 complex）。这时 `query` 返回 `Missing` → SLO 落 **`skipped`**（三态 `pass/fail/skipped` 之一）。skip **不是 pass**：

- 默认不翻 run 退出码，但报告/CLI **显式列出**（别误读为通过）；`strict_slo: true` 则算失败。cooldown 是恢复证明，missing 会直接失败关闭。
- skip **不计 capacity**（不确认的档不算容量，保守）。
- **诚实线**：typo 进不了 skip——结构非法在解析期就死（§3.5），只有"合法但本档空"才 skip。你没法 typo 出一个静默 skip。

### 3.7 caveat：可信度随值走

每个 Window 内的 `MetricSummary` 自带 `caveats`，`reduce` 铸币时盖：closed 模型 → `co_biased`（尾延迟偏乐观）、drop 超阈 → `high_drop`、样本太少 → `few_samples`；观测侧再补两个——counter 中途回退（pod 重启）→ `counter_reset`（increase/rate 已用正增量累积修正，但窗口被切开）、probe 有 tick 失败 → `probe_error`（series 有洞，平稳趋势可能是假象）。这样一个 CO-biased 的 p99 / 一个断档期间的 cpu 曲线**自带标记**随值流过 store，report 据此出标位——而不是只在报告里写句散文。

### 3.8 观测面与判定面：observational by default, gateable only by explicit SLO

服务暴露的 PromQL 查询结果默认只属于**观测面**：进报告、进响应曲线、进 analyze，但**不进 judge / 熔断 / capacity**。**判定面**只有三个成员——`Workload.judge`（单请求成败，输入签名只有 Outcome，probe 产物结构上到不了它）、错误率熔断（只读 judged outcomes）、run 级 SLO 门。观测数据影响成败的**唯一通道**是 config 里显式写的 SLO 引用（如 `prometheus.error_rate{service="example"}.peak < 0.01`）——opt-in，不是默认。

这个边界靠**类型签名**硬约束（不是目录约定）：`judge(outcome) -> Verdict`、`_breaker_snapshot(timed, …)`。`workload` 一个类骑跨两面（`fire` 观测、`judge` 判定）是刻意的两段式设计，不按面拆分。配套的观测可信原则：观测系统自身的故障必须可见（§3.7 的 `probe_error`、`Missing("probe_error")`、validity 红旗）——**宁可断线，不画假趋势**。

---

## 4. 模块归属与依赖 DAG（无环）

metric 收腰是一个**包** `metric/`，三个子模块按职责分层：**纯模型** `family.py`（纯数据 +
纯函数，只依赖 stdlib，**不 import `model.py`**）、**读面** `store.py`（架在 `TrialRecord`
上）、**铸币** `reduce.py`（塑形 + caveat，engine 只收原始）。包的 `__init__` 只重导出纯
family 层（store/reduce import model，进 init 会循环）。

关键无环点：`family.py` 的 `resolve` 作用于 `dict[series_id, MetricSummary]`，不直接依赖 `RequestStats` —— 于是 family 层纯净，上层反过来 import 它。

```
metric/family.py  MetricFamily(side/value_kind) / *Summary(+caveats) / Missing / series_id / parse_ref / resolve / LEGAL_STATS / FacetDescriptor
model.py          Window.{request,by_facet,probe_metrics}          TrialRecord.metrics: dict[str, MetricFamily]
metric/reduce.py  outcomes → RequestStats(+请求侧分布) + caveat 铸币（pct / unit_of）
observe/          families 表（FamilySpec）→ describe()/summarize()  ← 资源侧生产者
metric/store.py   MetricStore(query/pivot/rows) + builtin request.* + slice_summaries  ← 消费方唯一读面
slo.py            evaluate_slo 走 MetricStore.query → 三态；config 解析期 fail-fast（§3.5）
report/           汇总走 RequestStats（存储底·热路径），资源/SLO 走 store
```

DAG：`metric.family ← model ← {metric.reduce, metric.store, engine}`；`store ← {slo, report}`；`engine ← {store, reduce, slo}`。

---

## 5. 对 Prometheus 的对应与差异

Prometheus 有 Counter / Gauge / **Histogram** / **Summary**；我们是 Counter / Gauge / **Distribution** / **Scalar**。

```text
Prometheus          →  perf_harness
Counter             →  counter        (total/rate/increase，照搬)
Gauge               →  gauge          (last/mean/peak，照搬)
Histogram + Summary →  distribution   (合并：单 trial 内自算分位)
(无)                 →  scalar         (trial 末派生值)
```

边界本质：**Prombed 负责 trial 内的 Prometheus scrape + 短期 TSDB + PromQL；perf metric 负责把查询结果与客户端请求、K8s 资源放进同一张可报告、可静态审查 SLO 的表。**

- **Histogram+Summary 合并成 `distribution`**：那俩的分裂是为了跨 scrape 目标聚合（Histogram 查询期 `histogram_quantile`、Summary 预算 φ 不可聚合）。perf 在单 trial 内持有 raw per-request 值自己算分位，**没有 fleet 要聚合**，分裂没意义。
  - **不借名字的真正原因**：借了反而坑懂 Prometheus 的人——`Histogram`/`Summary` 带着"可/不可聚合、分位在哪算"的预期，我们两个都违背；两个 value_kind 合法 stat 还一样 = 没挣到存在。用一张映射表教得更准。
- **新增 `scalar`**：Prometheus 用 recording rule / 查询表达式表达的派生单值（error_rate/throughput），我们建模成一等类型，让 resolver/SLO 统一对待。
- **`side` 是我们的轴**：Prometheus 全是时间序列 scrape，没有"每请求"概念（延迟被迫塞 Histogram）。我们保留每请求 raw 值→distribution，对压测更忠实（不丢尾）；request/resource 一位就说清了"谁能按什么切"（§3.2），不需要按来路分三种 kind。
- **temporality 词汇（对齐 OTLP）**：`CounterSummary` 同时携带两种 temporality 的读法——`total` 是 **cumulative**（窗口末累计值），`increase`/`rate` 是 **delta**（稳态窗口内的正增量累积，Prometheus `increase()` 语义，pod 重启回卷盖 `counter_reset`）。SLO 引用时选 stat 即选 temporality。
- **labels → declared facets，更严**：Prometheus alert rule 可引任意 label（typo 易静默失效）；我们要求进 gate 的 facet 必须 declared（§3.4）。
- **照搬的部分**：① **类型决定合法操作**（counter 不能 `rate` gauge、gauge 不能取 p99，且提前到解析期校验）；② **family/series + label 即维度**（`MetricFamily` 是族、`name{labels}` 是 series，report by_service/by_facet = 同一个 group-by-label）；③ **抓取健康是合成 series**——Prombed 每次 scrape 合成 `up`/`scrape_duration_seconds`，perf 每 tick 另合成 `<family>.up{service=…}` 表示整个 probe（抓取加所有查询）的可用性；④ **Prometheus 语义不再复制**——文本解析、staleness、counter reset、窗口外推、聚合与 `histogram_quantile` 均由 Prombed 实现。

Prometheus histogram 不进入 perf 的 `histogram` value_kind；consumer 用 PromQL
`histogram_quantile(...)` 将其投影成 gauge 序列。这样 Prometheus 原生类型留在 Prombed，perf
只接收报告真正需要的结果类型。

---

## 6. Window 与 metric 正交

Stage 是负载计划，Window 是执行后形成的观测边界。measurement、每个实际经过的 ramp/hold、cooldown 都是 Window；请求结果和资源采样在同一 Window 上分别规约，因而一条 SLO 不会用 hold 的请求延迟配上整轮 Trial 的 CPU。

Window 有唯一 `id`，展示名可以重复；例如 spike 前后的两个 `hold@base` 是两个 Window。SLO 的 `WindowSelector` 选择时间，metric label 只选择实体。长请求按 dispatch/start 时间归入 Window，避免 `sleep 30s` 因结束较晚被错误记到下一阶段。capacity 只消费完整 hold Window，提前熔断前已经完整跑完的较低 hold 仍可作为容量证据。

---

## 7. 已拍板的决策（不再开放）

1. ✅ scalar vs distribution → **typed `value_kind` union**，统一在 family/store 层，不在字段层。
2. ✅ Histogram+Summary → **合并 `distribution`**，不借这两个名字（借了反而误导）；新增 `scalar`。
3. ✅ 模块归属 → `metric/` 包：`family`（纯模型）+ `store`（读面）+ `reduce`（铸币）；`__init__` 只重导出纯 family 层；resolver 作用于 map 避免环。
4. ✅ label 边界 → 由 family 的 `side` 一位决定：resource 只裸或资源 label（`service`，per_pod 下再加 `pod`）；request 可切 facet；`service` 只属 resource series（双向，§3.2），Window 单独选择时间。
5. ✅ facet gate → **declared schema**（config.facets ∪ cases ∪ describe_facets）；runtime 未声明 facet 只进 report。
6. ✅ family/series 分离 → `MetricFamily` 无 label、元数据一份；series = 族 + labels（`series_id`）。
7. ✅ 唯一读面 → `MetricStore`（query/pivot/rows）；`RequestStats` 是存储底，store 是寻址层（热路径仍直读 RequestStats）。
8. ✅ SLO 三态 → `pass/fail/skipped`，skip 不是 pass、不计 capacity、`strict_slo` 可收紧；producer 契约解析期校验。
9. ✅ caveat 随值走 → 可信度是类型不是散文；distribution 内部可来自 raw 值，未来可换 HDR bucket，类型不变。

---

## 8. 对 otel-collector 的对应（参考，不照搬）

pdata/pmetric（OTLP）与本模型独立收敛到同一形状：`Metric`(name/unit/description) ≈
`MetricFamily`、`DataPoint.Attributes` ≈ series labels、OTLP type 决定合法操作 ≈
`value_kind` 门控 stat、`DataPointFlags.NoRecordedValue`（staleness 标记） ≈
`Missing` + `up` 序列 + `probe_error`。**不引入**它的完整类型动物园
（ExponentialHistogram / Summary、int/double 双值型）与 pdata 所有权机制——那是
"开放互联协议 + 多组件流水线"的复杂度预算，perf 是单进程闭环。

选择性借了三点：

1. **mdatagen 思想**：metric 元数据是一张声明表（Probe 的 `families: dict[name,
   FamilySpec]`），记录、注册表、文档共读，杜绝多份词汇漂移。
2. **default/optional 准则**（collector `docs/scraping-receivers.md`）：默认观测 =
   判断系统健康所必需；冗余表达 / 高基数 PromQL 输出 label / 高开销 /
   需特殊配置的 → 显式 opt-in。Prometheus query 的 `labels` 是静态输出契约，也是 cardinality
   边界；查询返回额外 label 时 probe 直接失败，不让自由维度悄悄进入报告。
3. **exemplar（未来 hook，对接 trace_harness）**：OTLP 数据点可挂 exemplar 关联
   trace。perf 的对应位是 `Outcome.meta` 记 trace_id——延迟分布可下钻到具体
   trace，是"一次执行、多面观测"里 perf↔trace 两面的天然桥。现在不做，留指针。

---

## References
- 加压模型：[`load-model-redesign.md`](load-model-redesign.md)
- 结果语义 / SLO：[`result-semantics.md`](result-semantics.md)
- 当前实现锚点：`metric/family.py`（MetricFamily/*Summary/Missing/resolve）· `metric/store.py`（MetricStore）· `metric/reduce.py`（铸币）· `observe/base.py`（Probe/Prombed）· `slo.py` + `config.py`（三态/校验）
