# Python 平台工具箱

## 项目定位与边界

`harness_toolbox` 提供可被多个 Python Harness 与消费仓复用的环境操作和观测能力。它不拥有 Case、
负载模型、故障场景或 Verdict；业务项目负责选择目标、决定动作时机并判断结果。

## 代码地图与核心模块

```text
harness_toolbox/
├── kube/
│   ├── model.py   # 稳定的 Options / PodRef / Pod / Event 投影
│   └── client.py  # async Kubernetes 配置、控制、等待与 Event 采集
└── tests/         # 使用协议 stub 的单元测试，不依赖真实集群
```

## 关键约定

- Kubernetes client 通过可选依赖 `quality-harness[kube]` 提供，不给其它 Harness 强制增加运行时依赖。
- Python 与 Go 共享 namespace scope、Pod UID fencing、确定性快照和等待语义，但保持各自语言惯用 API。
- Python API 使用 async I/O；显式设置请求超时和连接池容量，调用方对 client 生命周期负责。
- 只向消费方暴露稳定投影，不泄漏 `kubernetes_asyncio` 的生成模型。

## References

- 平台工具箱边界与共享语义：[`../../../docs/toolbox.md`](../../../docs/toolbox.md)
- Go 对等实现：[`../../go/toolbox/kube`](../../go/toolbox/kube)
