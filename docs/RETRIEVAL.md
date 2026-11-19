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

这些测试验证接口契约和隔离性，不能作为真实语义召回质量的指标。真实 embedder、模型版本、索引更新、阈值、检索评测和生产接线均待后续阶段。
