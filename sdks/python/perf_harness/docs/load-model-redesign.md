# perf_harness 加压模型（load model）

perf_harness 怎么决定"压多大、什么时候压"。两个压测里绕不开的细节是本设计的出发点：

1. **不会一上来就压满**——目标强度是从 0 慢慢爬上去的（ramp）。
2. **"稳定 N qps" 有不同机制**——开环（固定到达率，与响应时间无关）vs 闭环（N 个并发用户，吞吐由响应时间决定）；闭环里还有"每个用户多久发一次"（think-time / pacing）。

---

## 1. 理念：三个正交轴

"一次加压"由三个**正交**维度描述，分别落在 `load.py` 的数据结构里。正交是关键：爬坡是 Shape 的事、开/闭环是 Model 的事、节奏是 Pacing 的事，互不耦合——任何 Shape 都能驱动任何 Model。

| 轴 | 类型 | 含义 |
|----|------|------|
| **Model** | `LoadProfile.model: "open" \| "closed"` | 到达率是否依赖响应时间 |
| **Schedule** | `LoadProfile.schedule: Schedule` | 强度随时间的分段线性函数 |
| **Pacing** | `LoadProfile.pacing: Pacing`（仅 closed） | 每个虚拟用户两次发射间的 think-time |

### Model：open vs closed（排队论词汇）

- **closed**：维持 N 个并发用户，每个 loop `fire → wait(pacing) → fire`。吞吐是**结果**（取决于 RT + think-time）。回答"N 个并发用户扛不扛得住"。
- **open**：外生到达率 λ(t)，与 RT 无关，`max_inflight` 背压。回答"稳定 N qps 扛不扛得住"。

> **"稳定 N qps" 天然属于 open 模型**——`level` 就是到达率。用 closed `level=10` 想要 10 qps 是错的：10 条流若 RT=50ms 会压出约 200 qps。closed 回答的是并发数，qps 是结果不是输入。

### Schedule：强度是时间的函数（ramp 是一等公民）

`Schedule` 是一串 `Stage`，强度连续——`ramp` 从上一段终点线性走到 `to_level`，`hold` 维持在 `to_level`：

```python
Schedule.intensity(t) -> float   # t 秒时的目标强度；单位由 Model 决定（open=rps / closed=并发数）
```

强度单位由 Model 决定（open→rps，closed→并发用户数）。Schedule 提供两个常用构造器：

- `Schedule.ramp_hold(level, ramp_s, hold_s)`：最常见的"爬坡→稳定"。`ramp_s=0` 退化成直接满载的单 hold。
- `Schedule.spike(base, peak, base_s, rise_s, peak_s)`：基线→突刺→回落，演示多阶段的价值。

因为 ramp 只是 `intensity(t)` 在爬升，**两个 Model 共用同一条 Schedule**，无需各写一份爬坡逻辑。

### Pacing：闭环 think-time（借 locust 分类）

| `Pacing.kind` | 行为 |
|---------------|------|
| `none`（默认） | 发完立刻发下一个（最大吞吐） |
| `constant` | 固定 `secs` think-time |
| `between` | `[secs, max_secs]` 均匀随机 |
| `constant_pacing` | 单次迭代总耗时 = `secs`（扣掉请求耗时）→ 每用户约 1/`secs` req/s |

`constant_pacing` 让"10 个用户各 1s 发一次"可表达。注意：闭环靠 pacing 逼近某 qps 仍受并发上限约束，它的价值是**模拟真实用户节奏**；"我就要稳定 N qps"还是走 open。

---

## 2. 流程：Engine 怎么消费 LoadProfile

`Engine._run_trial` 按 `model` 选 driver，两个 driver 都消费同一条 Schedule：

```
_run_trial(target, arm):
  client = AsyncClient(max_connections ~ peak_level)
  observer = _observe(probes...)                       # 周期采 Probe
  drive = _drive_open if load.model=="open" else _drive_closed
  await drive(workload, ctx, load, cases, weights, run_id, timed)
  _aggregate(...)                                      # 实际边界 → measurement/stage/cooldown Windows
```

### `_drive_open`（开环）

积分 λ(t) over **真实**流逝时间：每 tick 把 `λ(t)·dt` 累加进 accumulator，每攒满 1 个就发一次。

- **drift-free**：dt 是实测的，不是假设的。
- **ramp 天然**：λ≈0 时不发、不会出现无限长首个间隔；λ 爬升则发射加速。
- 超过 `max_inflight` 的到达记为 `client_saturated` 丢弃，不发。

### `_drive_closed`（闭环）

一个 supervisor 每 tick 把并发用户数跟踪到 `round(intensity(t))`：少了 spawn、多了 retire（cancel）。每个 user loop `fire → wait(pacing)`。

- 同一条 Schedule 既能让闭环**爬上去**，也能（spike/阶梯回落时）**降下来**。
- retire 时 cancel：被 cancel 的 in-flight fire 仍走 `_fire` 的 `finally`，inflight 计数保持平衡，且不追加 outcome。

### warmup 与 ramp 解耦

`warmup_s` 是**统计丢弃边界**，独立于 Schedule 的 ramp。`_aggregate` 让 measurement 与各 stage Window 都从 `warmup_s` 之后开始，请求与 Probe 采样使用同一组 Window 边界。两者常重合，但概念分离——可以 ramp 30s 却丢弃 45s（ramp + 稳定窗）。

---

## 3. 配置（config.py）

`load:` 块两种写法，互斥：

```yaml
# 写法 A：显式 shape（一个 arm，可阶梯/突刺）—— 稳定 open 10 qps
load:
  model: open
  max_inflight: 200
  warmup_s: 30
  stages:
    - { ramp_to: 10, over_s: 30 }    # 0→10 线性爬坡
    - { hold: 10,   for_s: 120 }     # 稳定 10 qps

# 写法 B：level 扫描（每个 level 一个 ramp→hold arm）—— 容量画像常用
load:
  model: closed
  levels: [5, 10, 20, 40]
  ramp_s: 20
  steady_s: 120                       # warmup_s 默认 = ramp_s
  pacing: { kind: constant_pacing, secs: 1 }   # closed-only
```

- 写法 A：`stages` → 单个 `LoadProfile`，可在一个 trial 内编排阶梯/spike。
- 写法 B：`levels`/`level` + `ramp_s`/`steady_s` → 每个 level 一个 `Schedule.ramp_hold` arm，是 resources×load 网格里 load 这一维的便捷写法。

---

## 4. 关键设计点

### 为什么 Model 与 Schedule 正交

强度被建模成"时间的函数"（Schedule）而非标量，是 ramp 成为一等公民的前提。Model 只决定"到达率是否依赖 RT"，与"强度随时间怎么变"无关。两者一旦正交，ramp 就只在一处实现（driver 消费 Schedule），不必为 open/closed 各写一份。

### 为什么 open 用积分而非固定间隔

时变速率下，按"当前瞬时速率算下一个间隔"会在陡峭 ramp 段系统性滞后（λ≈0 时算出超长间隔）。按真实 dt 积分 λ 是正确做法：累加 `λ·dt`、攒满即发，既 drift-free 又对任意 Schedule 形状通用。

### 报告维度（facet + Window）

`Outcome` 带 `facets`，报告对每个 facet 做 marginal pivot（见 `report.py`）。混合流量按 `difficulty` 等 Case facet 拆分。

**Stage 规划，Window 观测**：Engine 按 Stage 的实际时间边界建立唯一 `window_id`，请求按发射时间归窗，Probe series 用同一边界归约。每个 hold Window 用自己的实际时长做吞吐分母；ramp 是过渡 Window。spike 中即使两段都叫 `hold@base`，也保持为两个 Window，不按展示名合并。capacity 只读 complete hold Window。详见 [`result-semantics.md`](result-semantics.md)。

---

## References

- 数据结构：[`../load.py`](../drive/load.py)（`LoadModel` / `Stage` / `Schedule` / `Pacing` / `LoadProfile`）
- 两个 driver 与聚合：[`../engine.py`](../engine.py)（`_drive_open` / `_drive_closed` / `_aggregate`）
- 配置解析：[`../config.py`](../config.py)（`_parse_loads` / `_parse_schedule` / `_parse_pacing`）
- 包总览：[`../README.md`](../README.md)
