# 辅助岗位证据 API / Demo

状态：Cursor 已对 #38 `b629dfc` 方案 APPROVED（评论 7059210783），用户明确授权 Ready + Squash 合入 `bd1795f`，合入记录 `3332550` 已推送 main。独立分支 `feat/offline-advisory-api` 已实施下述本机假向量接口与面板，专项离线验证通过，待代码审核。设计通过及假向量测试不表示真实语义质量通过。

## 运行方式

普通入口 `uvicorn app.api:app --host 127.0.0.1 --port 8000` 仍默认禁用辅助服务。显式启动假向量演示：

```powershell
.\.venv\Scripts\python.exe -m app.advisory_demo
```

启动器仅绑定 `127.0.0.1:8000`；在浏览器打开同源 `/`，使用单独的“合成样例证据演示”面板。它在启动阶段加载合成 jobs.json，并以固定 document 向量构建内容寻址索引；重复启动校验同一代，不覆盖已有文件。索引损坏则启动失败，不自动修复。运行目录仍为忽略的 `out/vector-index/`，无新增依赖、模型配置或外部请求。该启动命令为使用说明，本轮测试只模拟 uvicorn 绑定，未实际启动浏览器服务。

默认 status 为 `{"enabled":false,"mode":"disabled"}`。显式启用后可通过同源 API 查询：

```json
{"case_id":"demo_frontend","top_k":3}
```

提交到 `POST /advisory/evidence`，Content-Type 为 `application/json`。四个 case_id 见下文；不接受 URL 查询参数。没有用户提供的文本、上传文件或模型参数。

## 范围与输入

新增独立的辅助证据面板和接口，不改变 screen_one / screen_many、BatchRecord、POST /batches、GET /batches/{id}、SQLite 表结构、规则 jd_id、五维评分与默认 Mock。第一阶段只接受服务端白名单中的四个固定合成样例：demo_backend、demo_junior、demo_frontend、demo_other。样例、固定 query/document 向量与 profile 放应用侧的独立演示数据，不读取评测标签或导入 eval 包。

客户端不提交自由文本、PDF、向量、索引路径、模型名或凭证；新增辅助路径不创建 Embedding/DeepSeek 客户端。既有显式 DeepSeek 项目评分参数及预算/停止行为保持原状，本方案不允许辅助查询触发它。真实查询、PDF 辅助检索和在线模型不在此方案范围。固定向量只验证调用、索引和展示流程，不计算或声称模型准确率。

## 启用与存储

默认 app.api:app 和 Docker 入口保持辅助服务未安装。本提案不改变镜像、卷或默认容器命令；启用容器演示需后续单独审查可写索引路径与运行方案。显式的本机离线演示启动器才能注入固定向量服务，监听 127.0.0.1，不由网页按钮或请求参数改变服务端模式。默认导入、健康检查及状态查询不读取索引、配置、报告或锁。

启动时固定注入单一已解析的 `jobs-*.json` 路径、profile 和岗位快照，禁止扫描目录选择最新代；启用态只由注入对象决定，既有环境变量也不能打开。Content-Type 按现有 batches 方式忽略 charset 后缀后校验。

启用前的显式离线准备步骤使用合成岗位、固定 document 向量及 `IndexProfile("fake-controls", 3, "advisory-demo-v1")` 发布索引；只在 `out/vector-index/` 存储，重复代走既有载入校验，不覆盖。请求处理只加载/查询指定代，不创建、更新或删除索引。当前模型/revision/岗位失效时返回不可用，不自动生成新向量。旧真实报告没有原始向量，不从相似度反推或伪造真实索引。

## HTTP 契约

GET /advisory/status：
- 未启用返回 200、enabled=false、mode=disabled；不访问文件或模型。
- 显式启用的演示返回 enabled=true、mode=fixed-fake-vectors 和合成样例 ID/标题/示例文本；不在状态查询时加载索引。

POST /advisory/evidence：
- 请求类型只接受 `application/json`，JSON 只允许 case_id 和可选 top_k，top_k 缺省 3，必须为严格整数 1–3（拒绝 bool、浮点及字符串）；case_id 必须在白名单。
- 未启用时先返回固定 503，再考虑解析请求体，不读取索引；启用时请求体用流式计数限 4 KiB，拒绝超限、非法 JSON/UTF-8、未知字段、重复键与非法值，返回固定 422，不反射原始输入。
- 查找服务端固定 query 向量，再由本地索引查询；不读 .env、不回退 HTTP、不复用或重建任何付费客户端。
- 有效返回 200：advisory=true、mode=fixed-fake-vectors、quality_conclusion=NOT_ESTABLISHED、case_id、status、reason_code、hits。hits 仅含 reference_jd_id、title、similarity、evidence_excerpt（最多 1000 字符）。
- 索引缺失、损坏、版本不符或过期返回固定 503、status=REVIEW、空 hits 和白名单原因码；异常不带本机路径、向量、配置、原始错误体，不返回部分证据。

接口保持同源调用，不放宽 CORS 或将服务绑定到公开地址。响应不包含选定岗位字段、总分、分项、档位或 batch_id；advisory 结果不写入既有批次记录或数据库。相似度是参考排序，不表示通过、录用、岗位决定或拒绝匹配阈值。无相关合成样例仍可能返回参考岗位，不伪称正确拒绝。

## Demo 行为

页面增加单独的“合成样例证据演示”区域，先读状态；未启用时显示简短不可用信息并禁用按钮。启用时由用户选择固定合成样例并点击查询，始终显示“固定向量演示”标识；显示参考岗位、原文片段和相似度，不将 top-1 当成规则判岗。

该区域与现有上传/批次回查/评分表分开，不能把固定样例结果显示成本次上传 PDF 的语义结果。所有服务端文本使用 textContent 等安全文本节点，不把岗位标题或证据拼接到 HTML。页面不展示路径、向量坐标、Key 或内部配置。

## 验证结果与后续验收

本次常规开发测试全部使用临时假索引/固定向量，未启动真实 HTTP 服务或浏览器：
1. 默认 status/health/Demo 不读索引、配置、锁、真实报告或网络；未启用 evidence 不读文件。
2. 4 KiB 限制、JSON/UTF-8/重复键/未知字段/白名单/top_k 边界；输出脱敏及文本渲染。
3. 索引/profile/岗位变更、损坏与读失败都空证据，不回退向量提供器；查询不落盘、不变更索引。
4. 即使参考 JD3 排第一，既有 S001 仍 JD1/60、UNMATCHED 仍 0，默认及显式项目评分路径不变。
5. 四份已冻结的真实锁/报告 SHA 前后不变；任意模型客户端构造、配置读取或网络触发即失败。

实际命令与结果：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_advisory_api.py tests/test_api.py tests/test_demo.py tests/test_vector_index.py -q
node --test tests/js/test_advisory_demo.cjs
.\.venv\Scripts\python.exe -m compileall -q app/advisory.py app/advisory_demo.py app/api.py tests/test_advisory_api.py
```

Python **191 passed**，其中新增接口/启动器 **68 项**，其余为既有 API/Demo/索引相关回归；5 条既有 SWIG 弃用警告。Node **6 passed**，使用内置 vm 与模拟 DOM 执行页面实际脚本，验证默认禁用、显式查询、文本安全、错误/重复点击与批次 UI 隔离；它不是浏览器兼容性或视觉验收。compileall 和 git diff --check 通过。四份真实锁/报告 SHA 前后不变。

独立 Draft PR 待代码审查；最终 head 的全项目 pytest、Mock CLI 回归、干净安装、图/API 整体集成及目标容器/真实浏览器验收尚未执行，须按重要测试边界先经用户与 Cursor 审核/授权。不能把索引核心的 663 项历史验收当成新接口验收。

## 审核与停止条件

本契约的固定演示范围已获方案审核与用户实施授权；实施在独立功能分支进行，API+Demo 同一完整子功能统一送审代码。#38 方案合入只改文档，本分支新增接口和页面；未改 Docker、不发送真实请求。推送 Draft PR 后停止该分支修改，代码审核及全项目与容器等重要验收仍按上述边界执行。

后续真实向量持久化或自由文本/PDF 查询必须另提输入、来源、模型/维数/版本、停止逻辑、预算和隐私方案，取得适用授权后再做；不利用已结束轮次的余额或请求次数。
