# Product 与 Service 的离线知识

Service 与 Product 可用可选的 description 提供简短介绍，并声明离线业务知识。
TypeScript 通过 knowledge 统一组织结构化内容；Python 与 Go 的已有说明字段保持各自 API。
Service 聚焦自身职责与交接边界，Product 承载跨服务的端到端模型；内容由消费项目维护并随版本交付。
这些元数据不参与身份，也不构成可执行计划或现场观测。Python 的相等与哈希忽略它们；Go Product
通过 SameIdentity 比较业务身份，结构体相等仍会比较所有字段。未提供表示知识缺省，Markdown 正文
由消费方保留；公共层不执行其中的命令。

## 内容模型与输出

TypeScript common 的 Product 与 Service 通过可选的 `knowledge` 贡献 `Knowledge`：Concept 解释核心对象、关系与约束，
Workflow 用具名 Step 描述业务过程，Article 用 Section 展开专题，Dictionary 保存可逐条查询的类型化词条。
知识是消费方维护的离线声明，拥有可引用的内容身份，不改变产品或服务身份，也不证明目标环境的运行状态。
Step 的 parentId 只表达包含关系，同级顺序由数组决定；词典允许同码多定义，稳定引用由词条 ID 承担。
知识内容接口统一继承 Markdown，以 `markdown(): string` 提供无 I/O 的文本表示；KnowledgeSource 仅记录来源。
common 提供结构约束与确定性 Markdown 转换函数，声明方法可委托这些函数；父对象调用子对象的 `markdown()` 组合输出，领域词条的额外字段由 Entry 自身展示。
JSON 只保留内容字段，消费方加载后须为各层知识对象补齐方法，再作为可调用的知识声明暴露。
发行、内容加载、查询、搜索与索引由消费方负责，词典定义来源版本独立于其分发版本。
