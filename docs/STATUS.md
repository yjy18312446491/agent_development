# 项目状态

## 当前功能分支已实现

- PDF 文本和候选人字段抽取
- 文本质量闸门
- 基于合成 JD 的确定性岗位路由
- 独立的 Embedding 辅助检索接口已通过 [PR #27](https://github.com/yjy18312446491/agent_development/pull/27) 合入 main，合并提交 `a23d552`，保留 `c0a103b`、`a4b1ca1`。默认关闭，显式注入假向量提供器验证余弦排序、证据引用、非法回包复核，参考 ID 不回写规则路由或评分。新增 34 项专项测试通过，全量 344 项、Mock PDF 13/13、零请求预览通过。PR #27 交付时尚未接入真实 Embedding 服务，后续独立传输冒烟见下文；接口仍未接入筛选或 HTTP 默认路径。[接口说明](RETRIEVAL.md)
- 五维规则评分，项目分使用离线 Mock，默认为 0
- 评分结果校验，检查分项、总分与岗位 ID，保留合法的上游复核结果
- 单份 PDF 筛选入口，从 PDF 返回经校验的评分或人工复核结果
- 合成 PDF 文件状态、中文简历画像和抽取至评分校验的回归测试
- 离线 Mock 评测基线，使用 10 份合成 PDF 验证 13 个筛选场景
- 项目分适配层，使用假客户端验证三项分数和原文证据，并记录 12 条合成标注
- DeepSeek 项目分独立冒烟入口，真实跑通 3 条合成片段
- DeepSeek 12 条合成标注的首轮真实评测，1 条本地跳过，11 条有效，三项全匹配 8/11，项目总分 MAE 1.0
- DeepSeek v1 的三条分差已复审并记录，保留现有标注与首轮结果。默认筛选继续使用 Mock。
- 项目评分 v2 首轮有两条网络失败。完整重跑后，14 条非空样本均取得有效评分且三项全匹配。原样本 11/11，MAE 0.0，三条 v1 分差均对齐标注。默认筛选仍使用 Mock。
- 已实现独立的项目段离线提取，并用英文合成 PDF 跑通从文件抽取到项目原文。默认筛选继续用 Mock，DeepSeek 仍从独立评测入口运行。
- 项目段适配器已用假客户端验证。空段项目分为 0，非法回包保留岗位 ID 并进入复核。
- 合成长 PDF 已通过 `screen_one` 验证闸门、JD1 路由和假客户端注入。假客户端只收到项目原文及岗位要求，项目分 21 计入总分。
- 已在固定合成长 PDF 上显式运行真实 DeepSeek v2。一次请求得到 JD1、项目分 14、总分 74。真实联调由评测入口开启，默认筛选使用 Mock。
- 单份筛选结果可经 `finalize` 转成 JSON 友好的记录。已评分结果有 A/B/C 档，人工复核保留原因与 `-1`。
- 批量筛选按输入顺序逐份调用 `screen_one`，生成带文件名的结果。意外异常单独记为 `BATCH_ITEM_ERROR`，后续文件继续处理。
- 批量结果可写入调用方指定的 SQLite 数据库，按批次回读并导出 CSV。重复写入会回滚，数据库与运行输出保留在本地。
- FastAPI 本地入口已支持健康检查、批量上传和按批次查询，使用 Mock 及每请求独立 SQLite 连接。
- FastAPI 的显式 `project_scorer=deepseek` 项目评分已通过 PR #24 合入 main，按批次保留请求预算和停止状态。2026-11-19 已用一份冻结合成 PDF 完成本机真实 HTTP 联调，1 次模型请求、0 重试，项目分 24、总分 84、A 档，POST、GET 与 SQLite 一致。缺省筛选与 Demo 使用 Mock。[HTTP 联调记录](../eval/README.md#2026-11-19-本机-http-联调)
- 本地 Demo 可上传合成 PDF、展示档位和复核原因码，并按批次 ID 回查。评分沿用 Mock。
- `screen_one` 的本地 LangGraph 编排已通过 [PR #26](https://github.com/yjy18312446491/agent_development/pull/26) 合入 main，合并提交 `badbe18`，保留功能提交 `d890563` 和版本记录 `89364ed`。图复用抽取、闸门、岗位加载、画像、路由、评分和校验，沿用 `ScreenResult` 与 `BatchRecord`。图内异常进入人工复核，每次调用独立保存状态并关闭外部追踪。合入后复跑全量 310 项通过，Mock PDF 13/13、预览零请求；默认使用 Mock，本次未发送真实模型请求。
- v0.3 已完成 8 份英文合成 PDF 的真实测评。5 次请求均取得合法评分，3 份按预期跳过，三项全匹配 3/5，项目分 MAE 1.4。复审记录两处分差，保留原标注。默认筛选继续使用 Mock。[实测记录](../eval/README.md#2026-11-19-实测复审)
- 多 PDF 评测的 HTTP 400–404 及 `REQUEST_LIMIT` 停止逻辑已通过 PR #23 合入 main。后续应调用样本保留停止原因并记为未运行，跳过场景仍本地核对。全量 244 项测试和 Mock PDF 13/13 通过，预览零请求。
- Docker Compose 历史配置 `c908730` 已在 GitHub 托管 Linux 运行器实跑。Docker Engine 28.0.4、Compose v2.38.2，构建及镜像内 `pip check` 通过。UID 1000 可写 `/data`，健康检查未建库，镜像内确认岗位配置和两份合成 PDF。
- 合成批次 `31baf778f8b64289981848835df00e5a` 的 POST 与 GET 返回一致，S001 为 `JD1 / B / 60 / mock`，S002 为 `REVIEW / EMPTY / -1`。非法上传返回 422，库中仍只有原批次。保留命名卷重建后，新容器 `43587a789d38` 代替原容器 `bc06079640b8`，用同一批次 ID 读回的内容与顺序一致。 [实测日志](https://github.com/yjy18312446491/agent_development/actions/runs/44243560755)

- 2026-11-19 已对合入 LangGraph 与辅助检索的 main 源码 `6ad54cb` 完成 Linux/Compose 复验，CI 提交 `6367e2f`。验证文档已通过 [PR #28](https://github.com/yjy18312446491/agent_development/pull/28) 合入 main，合并提交 `ef26cb6`，保留 `9652a61`、`84d9186`；CI 分支继续独立保留。固定 LangGraph 依赖、图编排默认 Mock、辅助检索默认关闭和假向量调用均通过；新批次 `18f269e2e05a40c0aa6a17f3fad292b1` 的上传、回查、非法上传 422 及保留卷重建通过。宿主机全量 344 项、Mock PDF 13/13、预览零请求通过。[最新日志](https://github.com/yjy18312446491/agent_development/actions/runs/44532474624) · [验证边界](CONTAINER_VALIDATION.md)

- 百炼 Embedding 离线客户端与 SHA 缓存已通过 [PR #29](https://github.com/yjy18312446491/agent_development/pull/29) 合入 main，合并提交 `2793072`，保留 `0018fe7`、`5265a6e`。该次合入后全量 421 项、Mock PDF 13/13、Embedding 预览零 HTTP 通过；默认关闭、未接线，真实请求 0，合入当时 `--live` 仍强制禁用。[客户端边界](RETRIEVAL.md#百炼客户端离线接入阶段)

- 受控 Embedding live 入口已通过 [PR #30](https://github.com/yjy18312446491/agent_development/pull/30) 合入 main，合并提交 `a1b5d02`，双亲 `b2cb9c6`、`3b0d3cc`，保留功能提交 `50c530b` 和版本记录 `3b0d3cc`；合并树与审核版本一致。合入后在 main 复跑全量 448 项、Mock PDF 13/13、默认 Embedding 预览零 HTTP 通过。固定 SHA、整轮最多 3 次 HTTP / 1 次重试 / ¥0.10 预留及请求锁保留机制不变；默认预览，检索未接线，本次真实请求 0。

- 2026-11-20 已在 `514d336` 完成北京 `text-embedding-v4` / 1024 维真实传输冒烟：3 document + 7 query，`short_gate` 本地跳过；2 次 HTTP、0 重试，返回用量 1140 token（78 + 1062），估算 ¥0.00057、预留 ¥0.04096，低于授权预算 ¥0.10，最终账单未核验。原报告与请求锁保留，run_id 一致。冒烟后 Mock PDF 13/13，S001 仍为 `JD1 / 60`。检索默认关闭、未接线，7 组辅助排序只作观察，不代表语义质量通过。[实测记录](../eval/README.md#2026-11-20-真实-embedding-冒烟)

## 待完成

- 12 条独立中文合成查询及 36 条相关性标签已获得 [Cursor 更正补审 APPROVED](https://github.com/yjy18312446491/agent_development/pull/33#issuecomment-7056976801)，假向量离线评测已通过 PR #33 合入 `d00a31e`。独立复审记录与评测前输入指纹已获 [#34 补审通过](https://github.com/yjy18312446491/agent_development/pull/34#issuecomment-7058205770)，经用户授权以 merge commit `cac1216` 合入 main，双亲 `d00a31e` / `18695bb`，树与审查 head 相同，保留原提交关系。原标签 JSON/SHA 和历史编写状态不变；APPROVED 只指标签复审，模型质量仍待验证，原锁保留。[评测规范](RETRIEVAL_EVAL.md)

- `eval.run_retrieval_baseline` 已获 [#35 最终补审通过](https://github.com/yjy18312446491/agent_development/pull/35#issuecomment-7058206435)，在 #34 merge commit `cac1216` 合入后，经用户授权转向 main 并 Squash 为 `f870f48`，树与已审/已测 `b1ba9e7` 相同；历史入口验收为全量 588 项、专项 56 项、Mock 13/13。2026-11-21 用户另行授权 15 向量/最多 3 HTTP/零重试/¥0.10；在 `9bc16b6` 实际运行一轮 SUCCESS：3 HTTP、0 重试、624 token，估算 ¥0.000312（账单未核验），预留 ¥0.06144。Recall@1=8/9、Recall@3=9/9（分母仅 9 条有相关岗位查询，另 3 条无相关查询为 null），RV005 错排。结果已获 [Cursor 归档复审 APPROVED](https://github.com/yjy18312446491/agent_development/pull/36#issuecomment-7058528222)，经用户授权 Ready + Squash 合入 #36 `ea6e9c6`，树与已审 head 相同；不代表质量通过；新旧锁和原报告保留，默认 Mock、检索关闭，jd_id/评分/标签不变，不追加请求。[实测及边界](RETRIEVAL_EVAL.md#2026-11-21-真实-15-向量基线已复审)

- Windows 宿主机 Docker Desktop 复验（可选，不阻塞检索质量主线）
- 最新验收（2026-11-24）：A、B01 表断言及 B02–B09 已接受分项沿用，不重做。B10 首轮 BLOCKED/已消耗账本、20B损坏件和904B备份保持；[Cursor 7121802341](https://github.com/yjy18312446491/agent_development/pull/40#issuecomment-7121802341)为 REQUEST_CHANGES，确认缺应用自然退出证据和失败前快照，要求在新目录修复观察/解释器认证并覆盖真实 CIM 形状。[修复准备范围](RELEASE_ACCEPTANCE.md#122-b10-进程观察修复候选仅准备未执行)：先落盘快照、区分启动器/实际 Python/辅助后代、Windows引号参数解析、空映像显式处理，离线直接调用实际观察方法；不回填原失败行、不改原候选，不启动新服务或损坏待执行副本。新候选自查后补审，用户继续推进的指令保留，补审前不执行。当前无监听，整段B/B10/C、Ready、合入未通过，所有旧现场保留，不发真实模型请求或修改冻结应用。以下两条为历史时点。
- B01 时点（后续实际状态见下一条）：[交付验收方案](RELEASE_ACCEPTANCE.md) 继续冻结源码 `3005819`。A 单轮重试安装一次/零重试、pip check、731 passed、Mock 13/13、默认导入已通过并复审。2026-11-22 获用户新授权的人工 B01 仅启动一次默认 Mock，6.328 秒就绪，health/status 均 200/disabled，主页和 status 的用户 Network 截图均为 200/4 ms；20:08:10 已收尾，8000 释放、无数据库/索引，8453 个旧输出文件与 286 个原始文件 SHA 不变。[Cursor 最新复核](https://github.com/yjy18312446491/agent_development/pull/40#issuecomment-7079165948) APPROVED 指记录、后端和主路径网络成立，**不宣布 B01 或整段 B PASS**。两条图标/DevTools 404 不阻塞 B01 表，但不能声称 Console 零错误；三张原有脱敏 PNG 已获 [Cursor 独立目视补核](https://github.com/yjy18312446491/agent_development/pull/40#issuecomment-7079563852)，B01 第 4 节表断言已独立确认；整段 B 仍未通过，原 result 不改写。本文只归档结果、人工三时钟与踩坑；B02–B10/C 未执行，后续重要验收及合入另行授权；不追加真实请求，全部旧现场/锁/报告保留。Docker CLI 仍仅有此前 PATH 未发现的证据，未再探测/安装。
- 历史 B03-only（2026-11-22）：[Cursor 7080928381](https://github.com/yjy18312446491/agent_development/pull/40#issuecomment-7080928381)确认后续 M1 的 B02、B04 默认证据成立，B03 仍缺刷新。用户授权的新一轮在默认就绪检查发现 fixed-fake-vectors 后立即停止：执行器分发条件漏改，未给用户发送 READY、没有页面/批次操作或真实模型请求；原库/副本 SHA 不变，8817 旧文件、286 原始源码及 143 新源码匹配。本轮新假索引/执行器/失败结果保留。默认专用入口修复候选及父执行器入口核对已准备，13 项离线 mock 检查通过；没有再次启动。B03/B04 启用/B05–B10/C 仍未完成，修复候选和新重试须复审/适用授权，不能复活旧轮或声称整段 B 通过。
- 辅助证据 API/Demo [契约](RETRIEVAL_API.md) 的 #38 方案已审合入 `bd1795f`；实现 #39 `7e48931` 已获 [Cursor 代码 APPROVED](https://github.com/yjy18312446491/agent_development/pull/39#issuecomment-7059799414)。按用户明确授权，在该不变 head 上全量 **731 passed**、Mock **13/13** 后 Ready + Squash 合入 main `1c91381`，唯一父提交 `3332550`，树与已审/已测 head 相同，本机同步。默认关闭、本机四样例假向量、只读固定索引与独立面板已具备；原专项 191 项及模拟 DOM 6 项通过，S001 仍 JD1/60。无新依赖、真实请求、jd_id/评分或 Docker 变化；`eval/reports` 全部 33 份旧文件 SHA 不变，Mock 使用新忽略文件。运行中服务/真实浏览器、干净安装和目标容器交付验收待单独方案及授权；假向量演示不等于语义质量通过。
- 默认关闭的 `vector_index` 已获 [Cursor 对 #37 的 APPROVED](https://github.com/yjy18312446491/agent_development/pull/37#issuecomment-7058812760)。按用户明确授权，在不改审查 head `aaa8508` 的前提下全量 663 项、Mock 13/13 通过，再 Ready + Squash 合入 `71b09ec`；唯一父提交 `6cf0993`，树与已审/已测 head 相同，本机 main 已同步。索引有模型/维数/revision、岗位/内容快照校验与不覆盖的原子发布；仅假向量验证，真实向量来源、API/Demo 接线及目标容器验收未做。不改 jd_id、评分或真实锁，后续接线先审具体方案。[范围与边界](VECTOR_INDEX.md)
- 按可演示完整交付仍有 3 个收尾里程碑：① 本轮质量复审和独立样本验证；② 可重用本地向量索引及默认关闭的辅助证据接线；③ 最终版本的干净安装/集成/容器/Demo 验收与发布材料。当前独立基线只给小样本描述性结果，不改变规则路由；新增真实实验另行审范围与预算，不重跑已结束轮次。[剩余完成标准](RETRIEVAL_EVAL.md#距离可演示完整交付的剩余里程碑)
- 复审 v2 完整对照，补充更多合成项目样本的评测

## 指标口径

当前离线评测用英文合成 PDF 验证提取、闸门、路由和评分，项目分默认使用离线 Mock。当前结果用于规则回归。历史样本未纳入此基线，真实模型项目分需用人工标注的项目证据单独验证。
