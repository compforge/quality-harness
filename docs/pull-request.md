# PullRequest

PullRequest 是 Repository 内的一次代码合入提案，统一表示 GitHub PR 与 GitLab MR。common 保存身份及平台事实；Forge 访问、发现分页、评论线程和合并就绪判断属于仓库工具包，观察水位、变化比较和 Input 接纳属于消费方。

## 身份与事实

`repository` 是编号所属的**目标仓库**，复用现有 Repository；`number` 是仓内正整数编号（GitLab `iid`），不是平台全局数据库 ID。`id()` / `ID()` 为 `Repository.id() + "#" + number`；TypeScript 用 `pullRequestId` 从 Repository 身份字段推导。平台适配负责校验编号及必需身份字段。

同一个 PR 追加提交、force-push、关闭/重开、合并后仍是同一身份。源仓、分支、目标仓 fork 来源均不参与比较；相同编号但 Forge 或仓库不同的 PR 不相等。Python 的相等和哈希按身份比较，Go 用 `SameIdentity`，TypeScript 比较 `pullRequestId`。对象相等不能用于判断快照是否发生变化，消费方应明确比较需要观察的事实字段。

可选事实包括 title、url、state、source_repository、source_branch、target_branch、head_sha、base_sha、updated_at、merged_at。TypeScript 使用 camelCase；Go 用惯用字段名，JSON/YAML 使用 snake_case。源仓也是 Repository；源仓缺失时不推断为目标仓。`head_sha` 是提案源分支 tip；`base_sha` 是平台报告的比较基线，不承诺是目标分支当前 tip。两者都不作为身份。

`state` 只有 `open`、`merged`、`closed`，其中 closed 表示未合并而关闭。未获取的 state 在 Python 为 None、TypeScript 为 undefined、Go 为零值；不能当作 closed。`updated_at` / `merged_at` 是平台 ISO 8601 时间，缺失不证明没有更新或合并；合并终态读取 state。采集时间与观测完整性留在调用方的观测记录中。

## 消费边界

common 不持有 token、HTTP 客户端、查询游标或轮询策略，也不解释业务授权、通知或合并决策。它不提供平台响应解析器；访问实现先校验远端响应，再构造公共对象。合并可行性和评审线程属于按需读取的观察事实，不能把共享身份对象当成持续有效的合并许可。

三个语言实现共同消费 `conformance/common/pull-requests.json`，验证目标仓归属、fork 与 revision 不改变身份、merged/closed/未知状态的区别，以及事实序列化往返。
