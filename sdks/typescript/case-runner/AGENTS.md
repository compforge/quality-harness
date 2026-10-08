# TypeScript Case 执行器

## 定位与边界

本包将 spec-case 的稳定 Case 与运行时目标组合为 PreparedCase，通过调用方注入的执行通道取得
观测，再按共享协议判据求值。它不发现 Service/Pod、不管理权限、不写 Doctor 产物，也不调度跨 Case 工作。

## 代码地图

- `src/http.ts`：HTTP 目标校验、请求构造、单次执行与响应判定。
- `tests/`：目标/资产分离、注入通道、预算与判定契约。

## 约定

Case 类型和 HTTP 输入/判据归 spec-case；本包仅拥有运行态契约。common 不依赖本包，toolbox 不认识 Case。
请求执行通道必须尊重 signal、timeoutMs 与 maxResponseBytes；实际流采集、平台与凭据生命周期由调用方持有。
使用 make lint/test/build；发布前先发布所依赖的 spec-case 版本。
