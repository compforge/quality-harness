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

## E2E 主流程

项目可以按环境组织配置目录，同时让同一用例函数接收环境参数：目录负责环境选择，
函数复用行为契约。二者不需要各复制一套用例。

项目 fixture 准备目标、收集实际环境与组件版本；E2E 执行入口检查必需条件，再运行
execute / judge，最终执行 cleanup。准备失败、必需条件未知或不满足、清理失败均为
error；产品行为不满足断言为 fail。缺失权限可以是被测条件，但不能自动算作跳过或通过。

Go 使用 `e2e/environment.Run`，Python 使用 `run_in_environment`，复用 CaseRun
已有的阶段预算和清理机制。Environment / profile 写入 variant，运行快照独立记录
runner、可选 Host 与 target 的实测值、来源和采集时间。快照跟随 CaseRun 输出到
`environments.json`，不包含 SSH 地址、kubeconfig 内容或连接凭据。

Profile 是项目拥有的条件组合，例如限制 ptrace、使用某个 AppArmor 配置。
profile 名和部署声明不能代替真实探测；要求拒绝 ptrace 的测试必须证明拒绝发生在
目标执行上下文，再验证应用降级行为。

## 能力归属

- common 拥有 Environment、可选 Host 与中立事实模型，不执行 I/O。
- toolbox 提供主机命令、事实采集及 Kubernetes 操作，使用调用方的超时和资源边界。
- E2E Harness 拥有 CaseRun 执行、环境前提校验及证据出口。
- 项目拥有环境配置、部署 fixture、权限 profiles、行为断言和清理。

toolbox 的 Host 命令支持 local / SSH。Python 的原生 KubernetesDataSource 在当前
进程访问 API，因此显式拒绝远端 Host，避免误读同名本机 kubeconfig；调用方可在
Host 上运行客户端，或通过 Host 命令执行 kubectl。不会静默切换访问位置。
