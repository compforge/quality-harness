# PullRequest

PullRequest 是 Repository 内的一次代码合入提案，统一表示 GitHub PR 与 GitLab MR。common 保存身份及平台事实；Forge 访问、发现分页、评论线程和合并就绪判断属于仓库工具包，观察水位、变化比较和 Input 接纳属于消费方。

## 身份与事实

`repository` 是编号所属的**目标仓库**，复用现有 Repository；`number` 是仓内正整数编号（GitLab `iid`），不是平台全局数据库 ID。`id()` / `ID()` 为 `Repository.id() + "#" + number`；TypeScript 用 `pullRequestId` 从 Repository 身份字段推导。平台适配负责校验编号及必需身份字段。

同一个 PR 追加提交、force-push、关闭/重开、合并后仍是同一身份。源仓、分支、目标仓 fork 来源均不参与比较；相同编号但 Forge 或仓库不同的 PR 不相等。Python 的相等和哈希按身份比较，Go 用 `SameIdentity`，TypeScript 比较 `pullRequestId`。对象相等不能用于判断快照是否发生变化，消费方应明确比较需要观察的事实字段。

可选事实包括 title、url、state、source_repository、source_branch、target_branch、head_sha、base_sha、updated_at、merged_at。TypeScript 使用 camelCase；Go 用惯用字段名，JSON/YAML 使用 snake_case。源仓也是 Repository；源仓缺失时不推断为目标仓。`head_sha` 是提案源分支 tip；`base_sha` 是平台报告的比较基线，不承诺是目标分支当前 tip。两者都不作为身份。

`state` 只有 `open`、`merged`、`closed`，其中 closed 表示未合并而关闭。未获取的 state 在 Python 为 None、TypeScript 为 undefined、Go 为零值；不能当作 closed。`updated_at` / `merged_at` 是平台 ISO 8601 时间，缺失不证明没有更新或合并；合并终态读取 state。采集时间与观测完整性留在调用方的观测记录中。

TypeScript 的 `RepositoryKey` / `PullRequestKey` 是仅需身份字段的结构化引用；`RepositorySnapshot` / `PullRequestSnapshot` 从同一对象模型派生出可序列化的事实形状，去掉 `id()`，包括 PR 内嵌的目标和来源 Repository。它们不是另一套对象身份，`repositoryId` / `pullRequestId` 对引用、快照和进程内对象给出同一结果。读取客户端可直接返回快照，消费方无需构造占位方法或自行维护字段投影。Python 数据类与 Go struct 已自然分离序列化字段和方法，共享身份及 JSON 事实语义不变。

## 消费边界

common 不持有 token、HTTP 客户端、查询游标或轮询策略，也不解释业务授权、通知或合并决策。它不提供平台响应解析器；访问实现先校验远端响应，再构造公共对象。合并可行性和评审线程属于按需读取的观察事实，不能把共享身份对象当成持续有效的合并许可。

三个语言实现共同消费 `conformance/common/pull-requests.json`，验证目标仓归属、fork 与 revision 不改变身份、merged/closed/未知状态的区别，以及事实序列化往返。

## 合并事实的独立维度

`state` 是 open / closed / merged 生命周期，只有平台已确认合并才是 merged。
`conflictState`（Python/Go wire 为 `conflict_state`）只描述代码冲突：none / conflicted / unknown。
`mergeReadiness`（wire 为 `merge_readiness`）描述平台当时报告的合并条件：ready、conflict、
discussions_unresolved、ci_blocked、needs_approval、draft、behind、blocked 或 unknown。
无冲突不能推出 ready，ready 不能推出 merged，也不代表调用者有合并权限。遗漏字段仍是未知。
这些事实不参与身份；平台读取、分页、游标和状态映射由 repocli 负责，common 不实现网络或扫描算法。
