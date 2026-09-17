# Python 平台工具箱

## 项目定位与边界

独立分发包 `harness-toolbox` 提供异步基础设施客户端。测试环境操作与诊断采集共享机制；
环境台账、凭据来源、业务查询、动作授权和结果判定由消费方拥有；环境内的网络地址解析由 toolbox 拥有。

## 代码地图与核心模块

```text
harness_toolbox/
├── client.py       # common 生命周期类型的兼容导出
├── environment.py  # KubernetesEnvironment → KubernetesClientFactory
├── address.py      # IP/DNS/环境内 Service 地址候选与解析失败
├── errors.py       # ToolboxError 契约与协议子类；适配器负责转换原生异常
├── diagnostics.py  # 客户端连接路径快照，与异常独立
├── transport.py    # 连接解析、直连、port-forward、Pod Python 路径
├── data_loader.py   # 单次读取范围内共享结果、错误与在途任务
├── prometheus.py   # Prometheus DataSource、有界抓取与 Prombed 查询历史
├── kube_portforward.py # 执行期 Service/Pod IP 隧道复用与资源身份校验
├── socks.py        # 外部客户端接入 Transport 的 loopback SOCKS5 适配
├── process.py      # 有界子进程 I/O、退出结果与取消清理
├── kube/           # 本地/Host 原生资源后端、worker 通道、状态等待、Event、exec 与隧道
├── opensearch.py   # HTTP 池、有界响应与 scroll
├── mysql.py        # SQLAlchemy async 池与 Pod Python 查询
├── pod_log.py      # 按物理实例/窗口共享采集，依赖 Kubernetes 客户端
└── tests/          # 协议 stub 和本机 HTTP/进程测试
```

## 关键约定

- 根调用方通过 `async with ClientManager()` 管理执行期，子调用方只接收 ClientProvider。
- ClientFactory 创建不做外部 I/O；DataSource 是其中的数据访问语义。initialize 完成依赖获取后才发布成功，dispose 必须幂等。
- Service 名称必须用对应 Environment 的 kubeconfig/context 解析，不回退本机短名 DNS；仅初始化阶段切换地址，业务请求不重放。
- 同 key 的配置、凭据和容量必须一致；失败资源清理完成才允许重试。禁止日志输出凭据和查询内容。
- 协议依赖仅通过 extras 引入；通用生命周期归 harness-common，只使用标准库。Skill 必须显式安装所用协议 extra。
- Pod 删除通过 UID precondition 保证实例身份；exec/log API 没有原子 UID 条件，只能前后核验。
- ClientManager 释放连接、临时采集文件和子进程，不隐式删除消费方创建的远端 Pod。
- Client 保持执行期身份；Transport 负责失效通道的退役与后续重建，已发送的业务操作不重放，最终关闭后不重开。

## References

- [使用指南](../README.md)
- [生命周期与访问边界](../docs/lifecycle.md)
- [跨语言工具箱](../../../../../docs/toolbox.md)
