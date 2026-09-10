# Python 平台工具箱

## 项目定位与边界

独立分发包 `harness-toolbox` 提供异步基础设施客户端。测试环境操作与诊断采集共享机制；
环境解析、凭据来源、业务查询、动作授权和结果判定由消费方拥有。

## 代码地图与核心模块

```text
harness_toolbox/
├── client.py       # Client / DataSource / ClientProvider / ClientManager
├── transport.py    # 连接解析、直连、port-forward、Pod Python 路径
├── process.py      # 有界子进程 I/O、退出结果与取消清理
├── kube/           # Pod 增删、状态等待、Event、exec 与隧道
├── opensearch.py   # HTTP 池、有界响应与 scroll
├── mysql.py        # SQLAlchemy async 池与 Pod Python 查询
├── pod_log.py      # 按物理实例/窗口共享采集，依赖 Kubernetes 客户端
└── tests/          # 协议 stub 和本机 HTTP/进程测试
```

## 关键约定

- 根调用方通过 `async with ClientManager()` 管理执行期，子调用方只接收 ClientProvider。
- DataSource 创建不做外部 I/O；initialize 完成依赖获取后才发布成功，dispose 必须幂等。
- 同 key 的配置、凭据和容量必须一致；失败资源清理完成才允许重试。禁止日志输出凭据和查询内容。
- 协议依赖仅通过 extras 引入；基础生命周期包只使用标准库。Skill 必须显式安装所用协议 extra。
- Pod 删除通过 UID precondition 保证实例身份；exec/log API 没有原子 UID 条件，只能前后核验。
- ClientManager 释放连接、临时采集文件和子进程，不隐式删除消费方创建的远端 Pod。

## References

- [使用指南](../README.md)
- [生命周期与访问边界](../docs/lifecycle.md)
- [跨语言工具箱](../../../../../docs/toolbox.md)
