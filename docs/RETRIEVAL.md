# Embedding 辅助检索接口（阶段 C）

本阶段交付独立的内存检索接口和固定假向量测试。实现位于 `app/retrieval.py`，验证位于 `tests/test_retrieval.py`，只使用 Python 标准库与现有岗位数据结构，无新增依赖。

## 使用边界

`retrieve_job_evidence(text, jobs, *, enabled=False, embedder=None, top_k=3)` 返回 `RetrievalResult`。缺省关闭时立即返回 `DISABLED`，不会加载岗位文件、读取环境、创建客户端或调用提供器。调用方负责显式提供已经抽取的合成简历文本和岗位序列。

启用时必须传入 `enabled=True` 和可调用的 embedder。字符串 `"true"` 等值不会启用。embedder 接收不可变的文本元组：第一个是查询，后续依次是岗位证据文本；返回相同顺序、相同数量的数值向量序列。当前接受 Python 序列，不接受迭代器或隐式转换字符串向量。

岗位有非空 `raw_text` 时，以去除首尾空白的原文作为证据；否则从标题、必需技能、加分技能和年限生成文本。岗位 ID 必须非空且不重复。传给提供器的只有字符串快照，不含可修改的岗位、路由或评分对象。

每次开启调用只执行一次批量 embedder 调用，无内置重试、网络客户端、配置读取、向量持久化或缓存。当前实现与测试未发送真实 Embedding 请求。以后接入真实客户端需另行明确超时、请求预算与授权。

## 排序与结果

- `JobEvidence` 包含 `reference_jd_id`、`title`、实际送入 embedder 的 `text` 和 `similarity`，结果使用不可变数据类和元组。
- 使用余弦相似度，范围限制为 `[-1, 1]`。先按最大绝对分量缩放，再归一化，避免有限的大数或极小数导致范数溢出或下溢。
- 相似度降序，并列按参考岗位 ID 升序；`top_k` 必须是正整数，超过岗位数则返回全部岗位。当前无相似度阈值，负相似度也可能出现在前 k 项中。
- `reference_jd_id` 仅引用原有岗位。检索结果不表示规则已经命中岗位，相似度不是五维分数或模型准确率。
- 检索函数不接收路由结果或评分结果，不调用 `route_profile`、`score_candidate` 或 `screen_one`。现有筛选图、批量 API、Demo 和 SQLite 结构没有接入此接口。
- 即使辅助排序第一名与规则不同，仍由原确定性路由决定 `jd_id`；`UNMATCHED` 和五维评分保持原规则。辅助接口的 `REVIEW` 不覆盖既有筛选状态或分数。

## 状态

| 状态 | 原因码 | 行为 |
| --- | --- | --- |
| `DISABLED` | `RETRIEVAL_DISABLED` | 默认关闭，零提供器调用 |
| `OK` | `RETRIEVAL_OK` | 返回排序证据 |
| `REVIEW` | `RETRIEVAL_INPUT_ERROR` | 空查询、空岗位、非法引用或参数，零提供器调用 |
| `REVIEW` | `EMBEDDER_MISSING` | 启用但缺少提供器，零提供器调用 |
| `REVIEW` | `EMBEDDING_ERROR` | 提供器异常，无重试，固定提示，不包含底层细节 |
| `REVIEW` | `EMBEDDING_INVALID` | 数量或维数不一致、空/零向量、非有限数值或非数字，返回空证据 |

校验失败不会返回部分排序，也不会伪造零相似度。该结果没有总分或选定岗位字段，不进入筛选结果落库。模块不记录简历文本、向量或提供器异常日志。

## 离线验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_retrieval.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m eval.run_eval --output tmp/embedding-mock.json
.\.venv\Scripts\python.exe -m eval.run_live_pdf_eval
```

最后一条命令仅预览，不带 `--live`。专项测试使用固定向量验证排序、并列、负相似度、top-k、证据映射、默认关闭、单次提供器调用、参数与异常回包、数值边界。真实 PDF 对照故意将 JD3 排第一，检查正常样本仍为 `JD1 / 60`，无匹配样本仍为 `UNMATCHED / 0`，项目分仍为 0。

这些测试验证接口契约和隔离性，不能作为真实语义召回质量的指标。真实向量冒烟、索引更新、阈值、检索质量评测和生产接线均待后续阶段。

## 百炼客户端：离线接入阶段

`app/embedding_client.py` 已提供独立的 urllib 客户端，以假 HTTP 验证原生 DashScope 协议；`app/embedding_cache.py` 提供预取与缓存。它们未进入筛选、图节点、FastAPI、Demo 或 Docker 默认路径。本阶段没有真实模型请求，现有 `retrieve_job_evidence` 行为不变。

配置仅由显式调用 `EmbeddingConfig.from_env_file(Path(...))` 读取，默认预览不读 `.env`。只读取 `EMBEDDING_PROVIDER`、`EMBEDDING_API_KEY`、`EMBEDDING_MODEL`、`EMBEDDING_BASE_URL`、`EMBEDDING_DIMENSION` 和 `EMBEDDING_REGION`，不回退到 `LLM_API_KEY`。固定百炼北京、`text-embedding-v4`、dense、1024 维。域名只允许北京经典域名或单层业务空间的 `cn-beijing.maas.aliyuncs.com` 地址；拒绝重定向、凭据 URL、端口、查询参数和其它地域。配置 repr、异常与报告不包含 Key 或工作空间地址。

客户端每批最多 10 条，本轮还采用**每条最多 2048 UTF-8 字节**的保守短文本限制，超限本地拒绝，不截断。它不是精确的 Qwen Token 计数器，也没有声称完整支持 8192 Token 长输入；现有 3 份 JD 与 7 份查询都满足此限制。未来放宽需另外接入并验证对应 tokenizer。[官方 API 规格](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api/)

传输层每次调用只尝试一次，拒绝第 4 次 HTTP。全轮缓存层共用一次瞬时重试机会；HTTP 400–404、HTTP 200 的提供器错误码、非法向量或未知 usage 立即停止。返回值包含冻结的模型、角色、文本 SHA、维数、向量、HTTP 状态、延迟和用量；原生响应的 `text_index` / `index` 用于恢复顺序，缺失、重复、冲突或越界均拒绝。失败回包能校验出有效 usage 时保留其值，未知用量和费用用 `null`，不写成 0。

缓存以 `(document/query, 文本 SHA)` 为键，角色不同不能互相复用。只有 `embed_documents` / `embed_queries` 能预取；`as_retriever_embedder()` 只查缓存，缺失时抛固定错误，不能补发 HTTP。先 3 条 document、再 7 条 query，之后 7 次检索均在本地完成；同一角色的重复 SHA 不重复请求。客户端与缓存用于**单轮顺序执行**，不跨轮复用或并发共享。

全轮除 3 次请求上限外，还按每次输入条数 × 8192 Token × 北京同步单价预留最坏费用，预留超过 ¥0.10 就在发请求前拒绝。超时的潜在费用仍占预留额。实际估算按提供器的有效 usage 和 ¥0.0005/千 Token 计算；任何尝试用量未知时，总用量与总费用记未知。该值是公开单价估算，不是实付账单。[官方模型价格](https://help.aliyun.com/zh/model-studio/text-embedding-v4)

## 冻结数据预览与报告

```powershell
.\.venv\Scripts\python.exe -m eval.run_embedding_eval
```

默认只校验清单 SHA 与各 PDF SHA、本地抽取及闸门，预览 3 个岗位与 7 个查询向量计划；`short_gate` 本地跳过。无需 Key，不创建客户端。报告只写到被 Git 忽略的 `eval/reports/`。此版本即使带 `--live` 也直接失败退出，待 Cursor 复审及用户另行确认本机配置后再做真实冒烟。

清单在本机 CRLF 工作区的冻结 SHA 是 `647d4327...`，同一 Git 提交的 LF 文件 SHA 是 `2b179f2e...`。入口只接受这两个已核对的精确值；其它字节改动均拒绝。报告同时写出实际 `manifest_sha256` 与 CRLF 冻结标识 `frozen_manifest_sha256`，不把换行转译说成样本变更。PDF 继续逐文件核对原 SHA。

`evaluate_injected` 仅供显式注入的传输层做离线集成测试：假 HTTP 测试完成两批预取和 7 组本地排序。报告把传输方式标为 `injected-transport`，不猜测外部注入对象是否真的发网，因此 `real_http_requests` 为 `null`；默认预览明确为 0。排序仅作为观察，不以已有规则路由或项目分标签计算语义准确率。报告包含 SHA、每次状态、usage、延迟、估算费用和参考 ID 排序，不包含原始向量、密钥或完整 URL。

配置和假回包客户端已实现，真实 10 向量冒烟尚未运行。后续范围仍受 2 次首发、整轮最多 1 次重试、总 HTTP 不超过 3 次、¥0.10 预算约束。
