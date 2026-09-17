# Environment 与 Host

Environment 表达服务运行及验证所针对的环境。local、devbox、devbox-k8s 是具名环境；
OS、发行版、kernel 和权限是实测事实，不从这些名字推断。相同环境可以供 E2E、perf、
trace 及部署工具使用，环境模型不依赖具体 Harness。

## 可选的 Host

Host 是 Environment 的可选组成部分，描述主机身份及 local / SSH 访问方式。
Host 环境直接在这台机器运行目标；Kubernetes 环境通过这台机器操作集群。
Kubernetes 的 kubeconfig 路径属于这台 Host 的文件系统，Host 不代表 Pod 所在节点。
省略 Host 时，操作在当前执行主机进行；这不改变环境本身的身份。

例如，一台 devbox 可以直接运行 Hostel，也可以作为另一个 Kubernetes 环境的访问入口：

```yaml
# service.environment
name: devbox-k8s
kind: kubernetes
host:
  name: devbox
  transport: ssh
  address: test-builder
kubeconfig: /etc/rancher/k3s/k3s.yaml
context: default
```

Runner 是执行测试代码的角色，它可以在本机、Host 或 Pod 内运行。部署主机、Runner、
目标进程可能拥有不同的文件系统与权限；采集证据时必须保留来源。
即使部署主机和 Pod 共享 kernel，也不能把部署主机探测到的 ptrace 权限作为 Pod 的事实。

## EnvironmentContext：执行期的环境访问上下文

Environment 是配置与身份声明，不持有连接；EnvironmentContext 绑定本次选定的 Environment、
借用的 ClientProvider 与截止时间，供 Service、Workload 操作及部署、诊断、测试流程共同使用。
它不依赖 Fixture，不执行 I/O、不拥有客户端释放权，也不自动取消操作。调用方将剩余预算
传给具体操作，并在根执行退出前等待子任务结束。

Python 使用单调时钟秒数 deadline / remaining_s；TypeScript 使用 performance.now() 的
毫秒数 deadlineMs / remainingMs，不使用墙钟时间。TypeScript 的环境类型由消费方泛型提供。
ClientManager 接收中立的 ClientFactory：环境访问工厂与 DataSource 共用初始化、复用和释放机制。
不要求 Environment 提供 client()，也不引入只有 init/dispose 的 EnvironmentClient 基类。

Python 的 FixtureContext 在 EnvironmentContext 上增加 phase，供 prepare/cleanup 使用；
普通环境操作只依赖 EnvironmentContext，不需要知道编排阶段。

EnvironmentContext 不预设运行平台。对本地 Web 服务，HostEnvironment 配合 local Host 表达
运行位置；项目 Fixture 启动进程并等待就绪，领域执行回调运行 E2E，Fixture 最后停止自己启动的进程。
ClientManager 仅释放访问该服务的 HTTP 客户端等资源，不因连接释放而停止被测服务。
因此本地进程、远端主机和 Kubernetes 可以复用执行上下文与编排契约，不要求统一的平台 Client 基类。

## 环境与 Case 的两层生命周期

Python `harness_common.fixture` 提供 `EnvironmentFixture` 和 `run_environment`。
项目先分配自己的 state，再实现异步 `prepare(ctx, state)` 与 `cleanup(ctx, state)`；
state 保存资源句柄、已采集事实和借用的 Client。准备过程中取得的句柄立即放入 state，
让部分准备失败时的清理仍能找到资源。ctx 提供所选 Environment、ClientProvider 与阶段剩余时间。

一次执行的顺序为：

```text
environment prepare → 前提校验与证据 → Harness 执行（含 Case 清理）
                    → environment cleanup → ClientManager dispose
```

环境共享层只调用一个领域执行回调，不识别 Case。E2E 的 CasePlan / Go Definition、perf 的
Trial setup / deactivate / cleanup 继续决定用例的准备、清理和时机。回调必须等待自己的
任务与清理全部结束，才能归还环境；共享环境资源由 EnvironmentFixture 持有。

各阶段有独立预算。prepare 失败、前提不满足或未知会阻止领域执行，仍运行环境清理；
领域错误与环境清理错误分别保存在 `EnvironmentRun.phases`。已有领域结果保留在 `result`，
环境执行的 `healthy` 仅反映外层健康，最终运行结论还需要结合领域 Verdict。
取消会在领域退出、环境清理和客户端释放完成后继续向上传播。异步回调须配合取消；
ClientManager 的最终释放采用协作式预算，等待释放完成并将超时记为错误，避免遗留后台释放任务。

`observe(state)` 只读取项目已采集的事实；入口在准备结束时深拷贝快照，再检查可选的 target
必需条件。清理后的观察独立追加，不能覆盖准备证据。项目负责把外层阶段与观察连同领域产物
持久化，不把环境失败伪装成一个 Case。环境身份与访问凭据仍和证据分开。

下面展示接入结构，资源创建与领域执行由项目实现：

```python
from harness_common import EnvironmentBudgets, run_environment

# state 在调用前分配，fixture 实现 prepare / cleanup。
execution = await run_environment(
    environment, state, fixture, run_harness,
    budgets=EnvironmentBudgets(prepare_s=60, run_s=300, cleanup_s=60, dispose_s=15),
    observe=lambda state: state.snapshot,
    required={"ptrace": "denied"},
)
# 保存 execution.phases / observations，并结合 execution.result 的领域结论报告。
```

## E2E Case 主流程

项目可以按环境组织配置目录，同时让同一用例函数接收环境参数：目录负责环境选择，
函数复用行为契约。二者不需要各复制一套用例。

项目 fixture 准备目标、收集实际环境与组件版本；E2E 执行入口检查必需条件，再运行
execute / judge，最终执行 cleanup。准备失败、必需条件未知或不满足、清理失败均为
error；产品行为不满足断言为 fail。缺失权限可以是被测条件，但不能自动算作跳过或通过。

Go 使用 `e2e/environment.Run`，Python 使用 `run_in_environment`，复用 CaseRun
已有的阶段预算和清理机制。Environment / profile 写入 variant，运行快照独立记录
runner、可选 Host 与 target 的实测值、来源和采集时间。快照跟随 CaseRun 输出到
`environments.json`，不包含 SSH 地址、kubeconfig 内容或连接凭据。
其中 `environment` 固定为准备阶段结束时的快照，`cleanup_environment` 保存清理后的事实；
准备失败也保留已收集事实，后续 state 变更不能改写历史。

Profile 是项目拥有的条件组合，例如限制 ptrace、使用某个 AppArmor 配置。
profile 名和部署声明不能代替真实探测；要求拒绝 ptrace 的测试必须证明拒绝发生在
目标执行上下文，再验证应用降级行为。

## 能力归属

- common 拥有 Environment、可选 Host、中立事实及环境生命周期编排；具体 I/O 由项目回调与 toolbox 实现。
- toolbox 提供主机命令、事实采集及 Kubernetes 操作，使用调用方的超时和资源边界。
- 各 Harness 拥有自己的 Case / Trial 生命周期、领域判断与产物出口；E2E 另检查 Case 特有的环境前提。
- 项目拥有环境配置、部署 fixture、权限 profiles、行为断言和清理。

toolbox 的 Host 命令支持 local / SSH，Helm 等命令行工具使用这条路径。
`KubernetesResourcesClientFactory` 按 Environment 选择本地池或 Host-native worker，提供统一的
原生资源操作。低层 `KubernetesClientFactory` 仍只管理当前进程的 API 池，避免误读远端同名
kubeconfig。访问通道与恢复边界见 [工具箱](toolbox.md)。
