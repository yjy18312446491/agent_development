# 独立相关性标注与检索基线评测

本规范记录独立合成查询、已复审相关性标注、默认关闭的假向量评测及受控真实检索基线。`eval.run_retrieval_eval` 仍仅支持离线假向量；另一个独立入口 `eval.run_retrieval_baseline` 已按授权完成一轮真实基线，结果见文末。检索仍未接入筛选或 HTTP 服务。

## 数据与标注来源

数据位于 `data/synthetic/retrieval/relevance_v1.json`，版本 `retrieval-relevance-v1`。共 12 条新编写的中文合成查询，每条分别标注 JD1、JD2、JD3，共 36 个相关性标签及 36 条依据。覆盖组用于检查样本分布，不是路由结果，也不作为向量输入。

标注由 Codex 根据现有岗位要求起草，已在 PR #33 获得 [Cursor 更正补审 APPROVED](https://github.com/yjy18312446491/agent_development/pull/33#issuecomment-7056976801)，审查 head 为 `1fd133b1f1f64cc01239baf02c6f962b52c12e01`。这是工具的独立标签复审，不是模型质量或真实招聘效果验收。没有使用规则路由、项目评分标签或先前 7 条真实排序生成标签；标签编写与 #33 复审时尚未请求这 12 条查询的真实向量。真实基线在标签冻结、入口审核和用户另行授权之后执行，原标签未变。

原标签 JSON 及其 SHA 保持不变，`annotation_status=draft_pending_cursor_review` 和作者说明保留为初次编写时的历史元数据。当前复审状态以新增的 `data/synthetic/retrieval/relevance_v1.review.json` 为准，记录已审核的数据 SHA、head、更正评论及被替代的旧评论。报告分别给出原始 `annotation_status` 和 `annotation_review_status=APPROVED`，不通过重写已审核内容改状态。

| 等级 | 判断依据 |
| --- | --- |
| 0 | 没有对应职责或技能的实际证据，不相关 |
| 1 | 有可迁移的部分技能，但核心技术、职责或经验存在明确缺口 |
| 2 | 原文提供符合岗位核心技术、职责及经验条件的具体证据 |

主指标固定以 **等级 2** 为相关；等级 1 保留为部分相关解释，不计入主指标的相关岗位集合。此值是标注等级界线，不是向量相似度阈值。两种后端岗位有 Python、MySQL 等重叠，等级 1 的理由须独立核对；团队经历和工具清单的相关性判断不等于项目贡献打分。

岗位证据使用既有配置及原文：JD1 为三年以上 Python/FastAPI/MySQL/Redis 中级后端；JD2 为 Python/Flask/MySQL 初级后端，配置经验范围 0～2.5 年；JD3 为一年以上 JavaScript/TypeScript/Vue/React 页面开发。相关性依据写在 JSON 各条 `rationale` 中，不能由 fake 排名反推。

| 样本 | 场景 | JD1 / JD2 / JD3 初稿等级 |
| --- | --- | --- |
| RV001 | 四年订单后端、接口限流与缓存 | 2 / 1 / 0 |
| RV002 | 三年半团队异步任务平台、个人负责重试与索引 | 2 / 1 / 0 |
| RV003 | 五年仓储后端、数据库与热点缓存 | 2 / 1 / 0 |
| RV004 | 应届生 Flask 图书借阅项目 | 1 / 2 / 0 |
| RV005 | 一年半团队报表服务、查询与分页 | 1 / 2 / 0 |
| RV006 | 半年实习、工具清单及具体 Flask/MySQL 任务 | 1 / 2 / 0 |
| RV007 | 两年前端、表单与组件交互 | 0 / 0 / 2 |
| RV008 | 一年半团队页面开发与状态管理 | 0 / 0 / 2 |
| RV009 | 三年浏览器端开发、渲染与键盘交互 | 0 / 0 / 2 |
| RV010 | 活动运营，无开发经历 | 0 / 0 / 0 |
| RV011 | 财务表格，无程序或页面开发 | 0 / 0 / 0 |
| RV012 | 仅行业兴趣与培训意向，无技术实践 | 0 / 0 / 0 |

本初稿有 9 条等级 2 相关查询、3 条无相关岗位查询，三个主要岗位组各 3 条。它只覆盖小规模中文合成场景，尚未覆盖英文、真实招聘分布、跨岗位复杂歧义或实际语义泛化。

复审记录的规范化 SHA-256 为 `9adc330ac2bfb43ecb27e887738be46b048c998e9a278e9a2965eb7ea5ebc825`。加载器同时固定数据与复审记录，缺失、改写审批结论、head、数据 SHA 或换回旧评论均拒绝。它是可追溯的本地审批记录，不是数字签名；运行时不访问 GitHub，审查者仍应对照链接核验原评论。

加载后还保存输入指纹。评测前重新核对岗位、查询、标签、理由与复审信息，防止调用方在内存改动后仍沿用旧审批信息；不一致时在构造假向量提供器前拒绝。

## 冻结与校验

入口 `eval/run_retrieval_eval.py` 只接受该版本的 12 条固定顺序样本，检查唯一 ID、唯一非空查询、2048 UTF-8 字节上限、完整岗位标签与理由、整数等级 0/1/2（布尔值不接受）、覆盖分布及标注状态。拒绝重复 JSON 键、非有限 JSON 数值和未知字段，包括误加的路由或评分字段。

数据按 UTF-8 JSON、键排序和固定分隔符计算规范化 SHA-256：

`704ff394272ce4b9b3b4cf25b59440d82ea44ffec3d738d965a918377cdfc04b`

报告同时记录规范化数据 SHA 与实际文件字节 SHA；CRLF/LF、JSON 排版变化可保持语义 SHA，不会伪装成内容变更。标签或理由变化会改变规范化 SHA，必须作为显式版本修改复审，不能为了对齐模型排名悄悄更新预期值。

岗位配置只接受既有 jobs.json 的已核对 CRLF/LF 字节 SHA，且加载后固定比对 JD1/JD2/JD3 顺序和岗位原文 SHA，并复查文件未在加载中变化。本阶段不修改岗位数据、现有 PDF、评分金标或 prompt。

## 默认关闭与显式假向量入口

```powershell
.\.venv\Scripts\python.exe -m eval.run_retrieval_eval
.\.venv\Scripts\python.exe -m eval.run_retrieval_eval --fake
```

第一条输出 `disabled-preview`：加载校验数据，但不构造 fake 提供器，检索结果均为 DISABLED、Recall 为 null。第二条输出 `fixed-fake-vectors`：只构造本地固定 3 维控制向量，调用既有 `retrieve_job_evidence` 做 12 次排序。12 次 fake embedder 调用不是 HTTP；两种模式的真实请求均为 0。

该模块不导入真实 Embedding 客户端、缓存或 live 入口，不读取 `.env`，不提供可注入真实传输的命令行参数，也没有 `--live` 开关。未知开关直接拒绝。提供器只查固定映射，缺失即失败，不补发请求。查询及文档向量按 ID 固定，代码不读取相关性标签来生成向量；改标签不会改变 fake 排名。

控制向量故意让 RV002、RV005、RV008 排错第一名，还包含并列和负相似度。主指标在这套人工控制数据上为 Recall@1 = 6/9、Recall@3 = 9/9；这些值只验证计算程序，不评价模型、标注质量或招聘匹配效果，也不是通过阈值。

## 指标与失败口径

- 对一条存在相关岗位的查询，Recall@k = 前 k 个唯一参考岗位中等级 2 的数量 / 该查询全部等级 2 岗位数量。
- 宏平均 Recall@1/3 的分母是全部 9 条存在相关岗位的查询，不是成功返回的查询数。
- 检索失败保留 REVIEW、原因码和空排名，单条 Recall 为 null；在宏平均中，该相关查询按 0 计入，不通过排除失败来抬高指标。报告同时给出失败数，CLI 返回非零。
- 3 条无相关岗位查询不进入 Recall 分母，单条 Recall 为 null；它们仍显示排名和相似度观察，不把 top-1 当成功匹配，不计算拒绝匹配准确率。
- 仅有 3 个岗位时，完整排名的 Recall@3 天然饱和，不能作为质量验收门槛。将来增加独立复审的相关性数据后才能讨论真实效果指标及验证集，不在这些控制样本上调相似度阈值。

## 报告、回归与停止点

输出只允许 `eval/reports/retrieval-offline-*.json`，以独占模式创建，不覆盖已有报告，也不能写到 live JSON、请求锁、数据清单或目录外。报告不保存查询原文、理由原文或向量；包含输入 SHA、待审标注状态、fake 标志、排名、计数及分母。`quality_conclusion` 始终为 `NOT_ESTABLISHED`。

测试覆盖手算多相关岗位 Recall、等级界线、无相关查询、失败分母、标注与岗位快照变化、非法向量和脱敏、默认关闭、CLI 输出保护、禁止凭证/网络路径，以及默认筛选 `JD1 / 60` 和 `UNMATCHED / 0` 不变。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_retrieval_eval.py -q
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m eval.run_eval
```

本阶段不安装新依赖，不持久化真实向量，不做真实故障演练，不改变 screen_one、FastAPI、Demo、jd_id 或五维评分。既有 `embedding-live.lock` 与原真实报告保留，不创建新轮 live 配额。标注、评测设计与代码完成后推 Draft PR，等待 Cursor 对标签依据、指标和零请求边界审查；真实效果实验、新预算和产品接线另行授权。

2026-11-21 本机验收：新增专项 69 passed，全量 517 passed（原 448 + 69，5 条既有 PyMuPDF SWIG 警告）；Mock PDF 13/13；默认关闭、显式 fake、既有 Embedding 预览均为零 HTTP，compileall 和 pip check 通过。原真实报告及请求锁字节 SHA 未变；本轮没有读取真实 Key 或发送真实模型请求。

复审证据收尾阶段在原测试基础上补充 15 项，最终专项 84 passed、全量 532 passed，仍只有 5 条既有 SWIG 警告；默认/fake 和 Mock 13/13 通过。新增覆盖复审记录丢失、篡改、错链、等价排版，以及内存中标签、查询、复审信息与嵌套列表改变后不得复用审批信息。

## 首轮真实基线的执行前方案

本节保留执行前提出并送审的 12 查询与 3 岗位基线范围；实际授权、执行结果与限制记录在下方“2026-11-21 真实 15 向量基线”。方案、传输成功和模型质量验收分别表述。

- 候选模型继续为北京 `text-embedding-v4`、1024 维。15 个唯一输入按 document 3 条、query 10 条、query 2 条分三批首发；拟定整轮最多 3 次 HTTP、0 重试、¥0.10 预算，任一失败即停止后续批次。
- 2026-11-21 核对的[官方同步价格](https://help.aliyun.com/zh/model-studio/text-embedding-v4)为 ¥0.5/百万输入 token。按每输入 8192 token 保守预留：`15 × 8192 × ¥0.0000005 = ¥0.06144`，低于拟定 ¥0.10；不依赖免费额度，最终账单以平台为准。价格或输入范围变化需重审。
- 原 `eval.run_embedding_eval` 固定旧 7 查询且已有锁，缓存层仍有共享重试逻辑，不能直接拿来执行这个零重试方案。新数据集另用下述独立入口，先以假 HTTP 验证，再审查真实执行方案。
- 旧真实报告和旧锁永久保留。新轮必须在用户明确批准新范围与预算之后，采用经过审核的独立轮次标识和持久锁，引用旧轮记录；不得删除、重命名旧锁或刷新旧轮配额。
- 实验只产出该 12 条样本上的真实基线，按已冻结的等级界线和失败分母计算，不为抬高指标改标签，不用测试结果调拒绝匹配阈值。保持默认 Mock、检索关闭及原 jd_id/评分。
- 原方案要求使用用户本人配置的独立百炼 Key。方案编写时未读取或重置 Key；实际执行由本轮授权覆盖，程序仅在本机读取配置，未输出或提交 Key。本轮结束后不自动授予下一轮调用权限。

## 独立基线入口（已合入，按轮授权）

`eval.run_retrieval_baseline` 已实现新一轮的受控路径。缺省只输出预览；预览不读 `.env`、不构造客户端、不占用锁，也没有任何模型 HTTP。原 `eval.run_retrieval_eval` 仍仅支持离线假向量。

```powershell
.\.venv\Scripts\python.exe -m eval.run_retrieval_baseline
```

真实模式是该模块的显式 `--live`，该命令的实现不构成运行授权。执行前必须由用户与 Cursor 审核实现、测试结果及新一轮范围/预算；确认后才可执行一次。不能将旧 10 向量冒烟的授权移用到这 15 向量。新入口不复用带重试的缓存：直接调用现有客户端，每批仅一次，3/10/2 三批，整轮最多 3 次 HTTP、0 重试、¥0.10。按 15 × 8192 token 预留 ¥0.06144；费用估算与平台账单分开。

入口再次校验已审核的数据及内存输入快照；每个后续批次前也复核。live 预检要求可识别且相关已跟踪源码没有未提交变化，并在读取配置前完成源码检查，脏源码或未知提交不会读取 Key。报告独占创建，独立的 `retrieval-relevance-v1-live.lock` 在第一次 HTTP 前独占写入并 fsync，保存 run_id、报告位置、输入 SHA 和边界。此锁仅用于经过新授权的该轮实验；旧 `embedding-live.lock` 与旧报告不改、不删、不迁移。

每次 HTTP 前先原子保存 IN_FLIGHT 意图及本次费用预留；中断或保存失败保留锁和最后可用证据，不自动重跑。HTTP 400–404、408、429、5xx、网络超时、未知 usage 和非法向量均停止剩余批次，不因可重试状态而重试。未知用量的整轮费用为 null，不能假称零费用。终态写入失败时原进行中报告仍可能 counts_final=false，需人工复核，不能以该文件中的零计数断言没有请求。

只有三批预取全部成功才以 SHA/角色查表做本地排序；排序不发 HTTP。任一预取失败时，保守地将 12 查询全部记为 NOT_RUN，9 条相关查询仍以 0 进入宏 Recall 分母，3 条无相关查询 Recall 为 null。不排除失败来抬高指标。真实成功也只给描述性基线，`quality_conclusion=NOT_ESTABLISHED`；三岗位 Recall@3 饱和，不设通过阈值，不改标签、不调拒绝阈值、不接入产品。

仅报告输入 SHA、ID、相似度、用量及固定原因码，不保存原文、Key、配置地址、错误体或真实向量。入口专项现为 56 项，使用临时假 Key、临时报告目录和假 HTTP。2026-11-21 按 Cursor #35 审核建议及用户明确授权，完成修复后的离线整体验收：全量 588 passed（5 条既有 SWIG 警告）、Mock PDF 13/13、默认预览零请求；旧报告和旧锁 SHA 未变，新轮锁不存在。此前 55 项/局部 207 项是初版记录。

2026-11-21 Cursor 已分别对 #34 `18695bb`、#35 `b1ba9e7` 补审 APPROVED。按用户授权，#34 以 merge commit `cac1216` 合入，#35 转向 main 后核对 8 文件补丁一致，再 Squash 合入 `f870f48`；最终树 `15e373995895c26ba341117279e055c3b56aebc0` 与已审/已测 head 相同，因此沿用上述全量结果，不重复全量测试。本机 main 已同步，合入后只做零请求预览、源码和锁/报告核对；没有运行真实新轮、修改旧锁或产品接线。后续合入记录只改长期文档。

## 2026-11-21 真实 15 向量基线（已复审）

用户明确授权一轮：北京 `text-embedding-v4` / 1024 维，3 岗位 + 12 查询，最多 3 次 HTTP、零重试、¥0.10，失败或中断不重跑。执行源码为 main `9bc16b6fbf567afb46fc85d229dc5901dc79edbb`；相关代码与 #35 已审版本相同。执行前核对源码干净、冻结数据、独立百炼配置合法与新轮锁不存在。实际只运行一次受控 live 命令，本轮已结束，没有追加请求。

| 批次 | 角色 / 数量 | HTTP | 返回 token | 延迟 ms | 估算费用 ¥ |
| --- | --- | --- | --- | --- | --- |
| 1 | document / 3 | 200 | 78 | 9327.864 | 0.000039 |
| 2 | query / 10 | 200 | 469 | 10567.153 | 0.0002345 |
| 3 | query / 2 | 200 | 77 | 6656.400 | 0.0000385 |
| 合计 | 15 向量 | 3 次尝试，0 重试 | 624 | 26551.417（请求延迟之和） | 0.000312 |

状态 `SUCCESS`、`counts_final=true`，12 查询全部排序成功，失败数 0。按 2026-11-21 核对的[北京同步官方单价](https://help.aliyun.com/zh/model-studio/text-embedding-v4) ¥0.5/百万输入 token 估算；最坏费用预留 ¥0.06144，授权预算 ¥0.10，账单未核验。不以 3 次请求推断稳定 P50/P95 或服务 SLA。

等级 2 计为相关。9 条有相关岗位查询构成宏 Recall 分母，3 条无相关查询单列，Recall 为 null。独立按冻结标签与报告排序复算：**Recall@1 = 8/9（88.89%），Recall@3 = 9/9（100%）**。这不是 12 条的总体准确率；仅有 3 岗位时 Recall@3 天然饱和，不能当质量通过门槛。报告仍为 `DESCRIPTIVE_BASELINE` / `quality_conclusion=NOT_ESTABLISHED`，结果归档已获 [Cursor APPROVED](https://github.com/yjy18312446491/agent_development/pull/36#issuecomment-7058528222)，不表示模型质量已验收。

| 查询 | 标注相关岗位 | 实测顺序 | top-1 相似度（四舍五入） | Recall@1 |
| --- | --- | --- | --- | --- |
| RV001 | JD1 | JD1 > JD2 > JD3 | 0.672021 | 1 |
| RV002 | JD1 | JD1 > JD2 > JD3 | 0.682823 | 1 |
| RV003 | JD1 | JD1 > JD2 > JD3 | 0.679834 | 1 |
| RV004 | JD2 | JD2 > JD1 > JD3 | 0.643257 | 1 |
| RV005 | JD2 | JD1 > JD2 > JD3 | 0.681510 | 0 |
| RV006 | JD2 | JD2 > JD1 > JD3 | 0.654348 | 1 |
| RV007 | JD3 | JD3 > JD1 > JD2 | 0.662057 | 1 |
| RV008 | JD3 | JD3 > JD1 > JD2 | 0.652007 | 1 |
| RV009 | JD3 | JD3 > JD1 > JD2 | 0.663925 | 1 |
| RV010 | 无 | JD2 > JD3 > JD1 | 0.477920 | null |
| RV011 | 无 | JD2 > JD3 > JD1 | 0.541625 | null |
| RV012 | 无 | JD2 > JD3 > JD1 | 0.576243 | null |

唯一有相关查询的 top-1 分差为 RV005：标注 JD1=1、JD2=2、JD3=0，模型将 JD1（0.681510）排在 JD2（0.631803）前。样本是一年半 Python/Flask/MySQL 后端经验，明确没有 FastAPI 和缓存实际经验；初级 JD2 的已审标注保持不变。排序可能受否定描述中的技能词及年限/职责权重影响，这是待核验的解释，不能仅由这次排序确认原因或据此修改标签。无相关查询虽然仍有 top-1，但没有拒绝匹配阈值，不能算正确接收或正确拒绝；不在这 12 条测试查询上拟合阈值。

本机证据保留在 Git 忽略的 `eval/reports/`，不提交 JSON、向量、Key 或锁：

- 报告 `retrieval-baseline-live-20261121-round01.json`，SHA-256 `16f0dc6f534fc7a320f7834df0e9296680b5c4f073520d211bf877712a195201`。
- 新锁 `retrieval-relevance-v1-live.lock`，SHA-256 `7340d61c0217f96022cce72e1e854a358e8a4a3fbddbc28e03f7f4646566382a`，run_id 与报告一致；锁不删除，配额不刷新。
- 标签规范化 SHA `704ff394272ce4b9b3b4cf25b59440d82ea44ffec3d738d965a918377cdfc04b`；评测输入 SHA `04c2e8a5412ea632d15bcc65c1bf458a95cd0f6cf9366b67f4038505fd71fffa`，与报告、新锁和当前已审快照一致。
- 旧 `embedding-live.lock` SHA `8fca5a119dec235eb053f7d56b74de6fb5b7d99ddc4f7f0a68a42911e9c9d955`；旧 `embedding-live-20261120-round01.json` SHA `b9a233810d1e5a2d87ca9bd15b78da08edff9c127e75e4fefba639f50e31386b`，均未改变。

本轮验证仅执行真实受控入口一次，以及只读的报告/锁/源码/费用公式核对和指标独立复算；没有重跑 live、全量测试或 Mock CLI。历史全量 588 / 专项 56 / Mock 13/13 属入口已审版本验收。本次归档只改文档，jd_id、评分、标签、prompt 与应用代码不变，默认仍 Mock，辅助检索关闭；原始向量不持久化。结果已获 Cursor 对 `6a810d8` 的只读归档复审 APPROVED，按用户明确授权将 PR #36 Ready 并 Squash 合入 `ea6e9c6`，唯一父提交 `9bc16b6`，合并树与已审 head 相同；本机 main 已同步。本次合入未重新发请求、改动锁或宣称质量合格。

## 距离可演示完整交付的剩余里程碑

PDF 抽取/闸门、确定性路由与五维评分、显式 DeepSeek、LangGraph、SQLite/CSV、FastAPI/Demo 与 Linux Compose 均已有各自实现或验证记录；检索现已从传输冒烟推进到独立标签上的真实描述性基线。按可演示完整交付，仍有 **3 个收尾里程碑**，不按提交数估算完成百分比：

| 里程碑 | 当前缺口 | 完成标准 |
| --- | --- | --- |
| 1. 检索质量复审与独立验证 | v1 与 held-out live 均为 Recall@1=8/9；HV005 与 RV005 同类错排；拒绝匹配未验证 | 保留原标签与 SHA；不得用这批调阈值；结果只是描述性，`quality_conclusion=NOT_ESTABLISHED` |
| 2. 本地索引与辅助证据接线 | 假向量演示已合入；真实 Embedding 仅显式 `advisory=embedding` 旁路，Docker 默认仍关闭 | 默认 Mock；旁路不改规则 `jd_id` 与五维评分、不写入 SQLite；镜像不读提供器配置 |
| 3. 最终整体验收和发布材料 | 冻结 `3005819` 的 A/B/C 已完成；接线后的新版本需重新验收 | 干净安装、真实浏览器分项、Linux Compose 与入口文档已对冻结应用归档；Windows Desktop 仍可选；版本标签按授权打在已验证 main |

## 独立验证样本与质量口径（held-out）

新增 `data/synthetic/retrieval/relevance_heldout_v1.json`（`dataset_id=retrieval-relevance-heldout-v1`），12 条中文合成查询 HV001–HV012，36 个标签，覆盖组仍为 JD1/JD2/JD3/none 各 3 条。查询与 v1 的 RV001–RV012 文本不相交，岗位 SHA 与 v1 相同。入口 `eval.run_retrieval_heldout` 默认预览、`--fake` 才构造 3 维控制向量；拒绝 `--live`，真实请求 0。报告前缀 `retrieval-heldout-`，不写查询原文。

标签由 Cursor 起草，`annotation_review_status=PENDING_INDEPENDENT_REVIEW`，不能冒充已审。v1 规范化 SHA 仍为 `704ff394…`；held-out 规范化 SHA 为 `e1d0b0be…`。加载时若 v1 SHA 变化或查询撞车，立即拒绝。

质量口径（尚未宣称通过）：

- 相关仍以等级 2 计；宏平均分母是 9 条有相关岗位的查询。
- 3 条无相关查询不进 Recall 分母，也不计算拒绝匹配准确率。
- 仅 3 个岗位时 Recall@3 饱和，不能当门槛。
- **不得**在 v1 或 held-out 上拟合相似度阈值。
- fake Recall@1=6/9、Recall@3=9/9 只验证计算程序（HV002/HV005/HV008 故意错排）。
- 筛选、FastAPI、Docker 默认路径仍不接入真实 Embedding。

### 2026-11-24 真实 held-out 15 向量

用户授权一轮：北京 `text-embedding-v4` / 1024 维，3 岗位 + 12 条 HV 查询，最多 3 次 HTTP、零重试、¥0.10。因岗位与查询角色分开且每批最多 10 条，实际需要 3/10/2 三批，不能压成 2 次 HTTP。执行源码 `5026ab8`；旧 `embedding-live.lock` 与 `retrieval-relevance-v1-live.lock` SHA 未改。本轮已结束，锁保留，不重跑。

| 批次 | 角色 / 数量 | HTTP | 返回 token | 延迟 ms | 估算费用 ¥ |
| --- | --- | --- | --- | --- | --- |
| 1 | document 3 | 200 | 78 | 863 | 0.000039 |
| 2 | query 10 | 200 | 461 | 535 | 0.000231 |
| 3 | query 2 | 200 | 69 | 315 | 0.000035 |

状态 `SUCCESS`、3 HTTP、0 重试，用量 608 token，估算 ¥0.000304，预留 ¥0.06144，授权预算 ¥0.10，账单未核验。Recall@1=8/9、Recall@3=9/9（分母 9 条有相关岗位查询）。唯一 top-1 分差为 HV005：标注 JD2 相关，模型将 JD1 排在第一。3 条无相关查询 Recall 为 null，其 top-1 不算拒绝成功。`quality_conclusion=NOT_ESTABLISHED`。v1 SHA `704ff394…` 与 held-out SHA `e1d0b0be…` 未改。报告 `retrieval-heldout-live-59dc7cfd….json` SHA `0ecfaffb…`，新锁 SHA `5a89f947…`，不提交。

Windows Docker Desktop 复验为可选项；Linux Compose 证据不等于真实 Embedding 接线版本的容器验收。更多独立合成项目样本与 DeepSeek 质量验证纳入上述第 1 项。当前预算仅覆盖已经结束的本轮，后续不自动发请求、开新轮或更改评分。
