# 默认关闭的本地岗位向量索引

本分支新增 `app/vector_index.py`，使用标准库保存可重用的岗位向量快照。它只接收调用方显式提供的向量，不读取配置、不构造模型客户端、不生成向量、不回退发网。初版只用假向量做离线验证；真实基线报告不包含原始向量，不能从该报告重建真实索引。真实向量来源及产品接线另行审核和授权。

## 接口与默认行为

- `IndexProfile(model, dimension, revision)`：显式标明编码模型、维数与调用方维护的版本/命名空间；model/revision 是受限标签，不能是 URL 或带换行的配置值。
- `build_job_index(jobs, vectors, profile=...)`：复制岗位原文与数值向量，校验并归一化，生成不可变快照。只处理岗位，不保存查询或简历向量。
- `save_job_index(index, path=None)`：显式发布一代索引到 Git 忽略的 `out/vector-index/jobs-*.json`，默认文件名包含内容 SHA；不覆盖已有文件。
- `load_job_index(path, jobs=..., profile=...)`：要求调用方提供当前岗位与预期模型/版本，不能由文件里的声明自行决定兼容性。
- `search_job_index(...)` / `search_saved_jobs(...)`：默认 `enabled=False`，立即返回 `DISABLED`，不检查路径、读取索引或验证输入。仅显式 `True` 启用，返回现有 `RetrievalResult`，不产生路由决定。

最小假向量示例（首次保存一代索引）：

```python
from app.route import load_jobs
from app.vector_index import (
    IndexProfile, build_job_index, save_job_index, search_saved_jobs,
)

jobs = load_jobs()
profile = IndexProfile("fake-controls", 3, "synthetic-v1")
index = build_job_index(
    jobs, ((1, 0, 0), (0, 1, 0), (0, 0, 1)), profile=profile,
)
path = save_job_index(index)
result = search_saved_jobs(
    path, (0, 0, 1), jobs=jobs, profile=profile, enabled=True,
)
assert result.status == "OK"
assert result.hits[0].reference_jd_id == "JD3"
```

重复保存同名文件会得到 `INDEX_EXISTS`，读取已有代应使用 `load_job_index`。示例的 JD3 第一名仅是人为控制结果，不是语义验证。没有索引时返回复核，不创建目录或向量，不调用模型。

## 更新、失效与排序

快照包含 schema、模型/维数/revision、完整岗位配置 SHA、岗位 ID/标题/实际证据原文、归一化岗位向量及内容 SHA。源岗位 SHA 覆盖 `JobPosting` 的全部字段及嵌套技能列表；岗位顺序、原文、标题、年限或技能改变，都使旧代相对当前岗位失效。模型、维数或版本变化也拒绝复用。

岗位更新后，使用经过审核的相应向量建立新代并另存，旧文件不覆盖、不自动删除。新代可加载，旧代返回 `INDEX_STALE`；该模块不自动选择代、刷新模型版本或重新生成向量。查询向量须来自相同模型/版本的 query 编码；模块能检查维数及声明的 profile，无法仅凭数值证明查询编码来源。调用方声明的模型/revision 不能证明提供器实际权重版本，内容 SHA 是损坏/误改检测，不是数字签名或模型质量证明。

加载与查询前校验内容和当前岗位；查询后再次核对，避免把已变岗位当作原快照。索引文档/向量使用元组与不可变记录，构建后修改调用方原列表不影响索引。只读查询不改索引文件；查询向量仅用于本次余弦排序，不持久化。排序复用既有检索函数，按相似度降序、同分按参考岗位 ID 升序，保留岗位原文，`top_k` 口径不变。

使用线性余弦排序，适用于当前小规模岗位。首版上限为 128 岗位、4096 维、每份岗位证据 64 KiB、索引文件 8 MiB。空/零/非有限/非法数值或维数不符的向量均拒绝；对极大/极小有限向量沿用既有缩放归一化算法。

## 本地存储与失败边界

只接受受限目录下的 `jobs-*.json`，拒绝越界路径、符号链接和其它文件名；路径与错误体不进入公开异常。文件使用 UTF-8 规范 JSON，不使用 pickle，拒绝重复字段、NaN/Infinity、未知字段及 schema 错误，读取限制为 8 MiB 加一字节再判超限。

发布时先在同目录写临时文件并 flush/fsync，再以硬链接原子发布完整内容；目标存在时拒绝，竞争者也不能被覆盖。文件系统必须支持硬链接；不支持或写入失败则返回 `INDEX_WRITE_FAILED`，不降级为覆盖写。失败或清理异常可能留下未发布临时文件，它不会被当成 `jobs-*.json` 索引加载。该机制不声称对断电后的目录元数据提供持久性保证。

构建、保存和加载失败使用固定 `VectorIndexError` 原因码；查询失败返回 `REVIEW` 和空证据，缺失/损坏/过期索引不返回部分结果、不回退提供器。索引包含岗位原文和岗位向量，只应使用合成或已脱敏岗位；`out/` 运行文件、真实向量和配置均不提交 Git。模块不接触既有真实报告、请求锁或数据库。

## 验证与当前交付范围

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_vector_index.py tests/test_retrieval.py tests/test_retrieval_eval.py -q
.\.venv\Scripts\python.exe -m compileall -q app/vector_index.py tests/test_vector_index.py eval/run_retrieval_eval.py
```

本轮常规离线回归为 **193 passed**（索引新增 75 项、既有检索 34 项、标注评测 84 项，5 条既有 SWIG 警告）。另在当前工作区 `out/vector-index/` 用固定假向量实际保存/加载/查询通过，文件被 Git 忽略，零模型请求。工作区硬链接发布需原生文件系统权限：默认沙箱返回写入失败后，已用原生权限完成同一离线示例；未改变发布机制或降级覆盖。测试保护禁止读取仓库 `.env`、创建客户端和访问网络。

覆盖正常往返、并列排序、模型/维数/版本兼容、岗位更新与嵌套列表变化、不可变输入、非法向量、超限/损坏/重复字段、路径保护、fsync/发布失败及竞争不覆盖。即使索引故意把 JD3 排第一，默认 PDF 筛选仍为 `JD1 / 60`，无匹配样本仍为 `UNMATCHED / 0`。

本阶段没有 API/Demo/图节点接线、真实向量存储、新增请求、阈值拟合或评分改变。另修正一条已知旧评测提示，使其与“标签已有独立复审记录”的结构化状态一致；不改标签 SHA 或指标算法。新 head 尚未运行全项目整体验收，历史 588 不代表包含本模块；重要集成、容器验收与后续接线先交用户和 Cursor 审核方案。
