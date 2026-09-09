# Quality Harness：上层实现设计

> Quality Harness 在 Coding Harness 生成代码之后接过质量责任：它先理解被测项目，再发现并执行
> 已有质量能力，最终给出有证据的质量结论；没有可执行能力时，明确暂停并报告能力缺口。

本文描述 `quality-harness` 仓库长期计划中的项目级 Agent Harness，尚未实现。
仓库当前交付 e2e、eval、perf、trace、trajectory 领域 SDK 与共享工具箱。
下文的 Quality Harness 指上层执行与决策主体；领域 SDK 是它可以调用的能力。

## 1. 理念

只把数据集交给一组预先编写的 Evaluator，适合检查已知问题，却很难覆盖轨迹、项目和运行环境中
没有被事先枚举的异常。HarnessEval-W 的启发在于：可以让一个 harness 主动理解评估对象、寻找证据，
再调用具体评估能力，而不把质量判断完全限制在固定 Evaluator 的覆盖范围内。

Quality Harness 将这一理念扩展到项目级质量评估。它不是一个新的 test runner，也不是把 e2e、eval、
perf、trace 和 trajectory 混成一个分数；它扮演独立质量工程师，负责理解目标、提出当前需要回答的
质量问题、选择已有工具、解释执行证据，并诚实呈现未覆盖范围。

```text
被测目标
    → 理解项目
    → 发现质量能力
    → 执行验证
    → 解释证据
    → 评估结果
```

这里的被测目标不要求一定提供 diff。它可以是一个项目工作区或 revision、已经部署的服务或 Chat API，
也可以是 Coding Harness 的执行轨迹。README、AGENTS.md、代码、配置、版本信息和已有测试资产都是可选
证据，而不是强制输入契约。

## 2. 双速质量体系

开发过程需要及时纠错，也需要不受当前编码会话时间限制的全面审视，两者由不同闭环承担。

| 闭环 | 关注点 | 时效 | 典型动作 |
|---|---|---|---|
| devloop | 当前改动是否存在明显问题 | 同步、快速 | test、lint、build |
| Quality Harness | 当前项目整体质量、风险与盲区 | 异步、全面 | e2e、eval、perf、trace、trajectory |

Quality Harness 不替代 test、lint 和 build，也不要求进入每次 Coding Harness 的同步关键路径。devloop
可以在交付代码后异步触发一次定向评估；外部调度器也可以周期性触发全面评估，让耗时更长的执行、
证据分析和跨视角判断在后台完成。

Quality Harness 只负责评估，不修改被测代码。是否阻止合并、回滚或发布，属于消费评估结果的
交付策略，而不是 Quality Harness 自己的职责。

## 3. 质量能力

Quality Harness 不拥有一套包办所有测试的工具箱。它连接各领域的通用执行机制、随业务演进的真实
测试资产，以及 Agent 操作这些能力所需的过程知识。本仓库现有的领域 SDK 提供其中可跨项目复用的基础能力，
但项目是否真正可测，仍取决于业务仓内是否存在可执行的测试代码、环境适配和判定标准。

以 e2e 为例，能力由三部分共同组成：

1. quality-harness 或其它框架提供 Runner、生命周期、断言、证据和 Verdict 等基础机制；
2. 被测项目拥有跟随业务演进的 e2e 代码、Case、fixture、适配器和验收标准；
3. e2e 目录附近的 AGENTS.md、README、runbook、配置和历史问题，沉淀该项目如何准备环境、执行、
   清理和解释证据的过程知识。

缺少第二部分时，安装了测试框架也不表示项目拥有 e2e 能力。Quality Harness 通过 skill 将三部分
连接起来：先理解并发现项目自己的执行入口，再调用底层机制，最后按项目语义解释结果。

不同质量视角分别回答不同领域的问题：

| 目标或证据 | 主要视角 | 回答的问题 |
|---|---|---|
| 项目或服务 | e2e | 对外契约或验收条件是否成立 |
| 项目或服务 | perf | 压力下的延迟、吞吐与资源表现如何 |
| Chat API / agent | eval | 输出或行为质量是否达标 |
| 运行链路 | trace | 链路内部哪一层先反常 |
| Coding / agent Harness | trajectory | 决策、工具调用和行动过程是否合理 |

这些视角是可以组合的判断能力，不是 Quality Harness 必须固定执行的步骤。具体项目拥有什么资产、环境
和执行入口，决定当前能回答哪些问题。每个视角使用独立 skill 沉淀发现、执行和解释方式；业务特有的
规则与经验继续留在业务仓，通用 Harness 只负责装载 skill、提供工具和执行 agent loop。

以 trajectory 为例，trajectory_harness 负责将轨迹问题量化、细化到证据位置，并把 Dataset、Run、趋势与
可复用分析能力持续积累下来；quality plugin 的 trajectory skill 再结合项目上下文解释这些证据，判断下一步
应优先修改 prompt、tool、loop、compact 还是编排，并组织可对照的验证。前者沉淀“问题证据”，后者推进
“改进动作”，边界不应混在某个 Detector 或 Evaluator 里。

Quality Harness 可以使用阅读、搜索、shell、浏览器或 HTTP 等通用观察工具理解项目和解释结果，但这些
工具不能代替缺失的质量能力。仅凭阅读代码形成的印象可以成为风险提示，不能伪装成经过 e2e、perf 或
eval 验证的质量结论。

## 4. 工作流程

### 4.1 理解项目

Quality Harness 首先像刚接手项目的质量工程师一样建立最低必要认知：

1. 按项目约定查找并阅读 AGENTS.md；
2. 阅读 README，理解产品定位、使用方式和公开边界；
3. 选择性浏览关键代码入口、配置、构建文件和目录结构；
4. 形成项目做什么、核心链路是什么、可能从哪些边界验证的初步理解。

这一阶段的目标是为能力发现和结果解释提供上下文，不是先对全部源码做穷尽式审查，也不要求预先生成
一份固定 Plan。

### 4.2 发现能力

Quality Harness 从已有事实中发现可以执行的质量能力，包括项目接入的 quality-harness SDK、版本化 Case
或实验资产、已有命令和运行配置，以及能够访问的目标环境。声明文件可以提高发现的确定性，但不是使用
Quality Harness 的前置条件；即使项目没有新增专用声明，也应先从文档、代码和约定中尝试识别能力。

发现结果必须区分：

- 已找到且当前可执行的能力；
- 已找到但因环境、凭据或依赖不足而不可执行的能力；
- 当前没有发现对应质量能力的领域。

### 4.3 能力门禁

只有发现可执行能力后，Quality Harness 才开始对应测试。若本次评估意图没有任何可执行能力，则以
`No Quality Capability` 暂停，说明检查过哪些位置、缺少什么以及哪些问题仍无法回答。

暂停不是通过，也不能被汇总成绿色的 skipped。Quality Harness 不为了继续流程而临时生成测试程序、
探针或新的测试 Case；这些资产应由项目通过正常开发和 review 持有。

### 4.4 执行与解释

Quality Harness 驱动已有能力执行，消费各 harness 的原生 Run、Verdict、日志和证据。它需要结合项目
上下文解释结果，并明确区分：

- 质量失败：能力成功执行，并发现不符合判定标准的事实；
- 执行异常：测试过程、环境或工具本身失败；
- 证据不足：已有结果不足以支持通过或失败结论；
- 未覆盖：当前没有能力回答某个重要质量问题。

对同一次执行能够复用输出、trace 或 trajectory 时，应尽量从同一 Run 形成多个观察视角，不为了形式
上的完整而重复请求或发压。

底层 Harness 按 Kernel 的 `Case → Observation → Unit → Dataset → EvaluationRun / Worksheet`
语义保存结果。Quality Harness 可以为已有 Dataset 选择另一组 Detector、Evaluator、Measurer 与 Policy，复用
Observation 生成新的 EvaluationRun、Worksheet 与 Report；它不复制各 Harness 的 Unit 模型，也不另建一张抹平 e2e、eval、perf、
trace 和 trajectory 语义的总 Worksheet。

其中 trajectory_harness 的领域接口专门化为 Trajectory、派生 Measurements、Detector 与 Verifier；
成本/效果和硬/软规则分别是 Detector / Verifier 的两个正交维度。

### 4.5 输出结论

评估结果是对已有执行产物的项目级解释和汇总，至少回答：

- Quality Harness 对项目和本次评估目标的理解；
- 发现了哪些能力，实际执行了哪些能力；
- 各领域的结论、关键发现和证据位置；
- 哪些能力执行失败或证据不足；
- 哪些重要范围尚未覆盖，以及缺少什么能力。

评估结果引用 e2e、eval、perf、trace 和 trajectory 的原生产物，不抹平它们各自的判定语义，也
不依赖一个总分制造确定性。消费者应能从结论回溯到目标 revision、环境、执行记录和底层证据。

## 5. Skill 先于专用运行时

Quality Harness 按“先稳定过程知识，再替换执行宿主”的方向渐进成立：

```text
Codex / Claude + quality skill
            ↓
通用 Harness Framework + 同一 quality skill
            ↓
Baton Plugin ──prompt──▶ Harness + 同一 quality skill
```

第一步先让 quality skill 在 Codex 和 Claude Code 中经过真实项目校准。此时可以直接观察 Agent 能否
找到项目测试目录、遵循业务 runbook、正确执行入口，并诚实区分 pass、fail、error、blocked 和
no capability。过程中的通用经验进入 skill，项目特有经验继续留在项目测试目录。

当 skill 已能稳定驱动质量任务后，通用 Harness Framework 只需提供模型、工具、上下文和 skill
装载机制，就能复用同一套过程知识，不必重新实现一套 e2e、perf 或 trajectory 编排。只有 agent loop
本身出现稳定且无法由 skill 表达的需求时，才把它提升为专用 Quality Harness Runtime。

Baton Plugin 位于更外层。它通过 prompt 请求 Harness 执行某项质量任务，并负责 Resource、调度、
权限、异步 Lane、状态和结果回流；具体怎么发现和运行 e2e 仍由 Harness 中的 skill 决定。这样
Baton Plugin 不复制 Harness 过程知识，skill 也不承担长期调度和恢复状态。

这条路径让每一层都可以独立验证：skill 先证明任务能被 Agent 跑顺，Harness Framework 再证明执行
宿主可替换，Baton Plugin 最后证明任务能够被异步和周期性控制。

## 6. 触发方式

### 定向异步触发

devloop 或人可以给出明确评估意图，例如“看下这次版本的 e2e”“检查 perf 是否退化”或“评估这批
Coding Harness 轨迹”。Quality Harness 围绕指定问题发现并执行相关能力，把结果异步回流给触发方。

### 周期性全面触发

外部调度器可以按日或其它周期要求 Quality Harness “全面看下”。此时它重新理解目标的当前状态，盘点
所有可发现能力，执行当时允许执行的验证，并报告质量变化、风险、未知项和能力缺口。调度、运行窗口、
环境授权和通知渠道由 devloop、Baton、CI 或其它外部系统负责，不进入 Quality Harness 的稳定内核。

## 7. 边界

- 不假设一定存在 diff、专用声明或完整测试计划；
- 不凭空创造项目没有的测试能力，也不生成代码来绕过能力缺口；
- 不以 LLM 的主观判断替代 harness 执行证据；
- 不修改被测代码，不承担缺陷修复；
- 不取代项目自身的 test、lint、build 和版本化测试资产；
- 不决定合并、发布或回滚，只提供可追溯的质量事实、结论和未知项。

Quality Harness 的目标不是保证“系统完全没问题”，而是让当前版本已经验证什么、发现了什么、还不
知道什么都变得清楚，并让这些结论能够被后续开发和交付闭环持续消费。
