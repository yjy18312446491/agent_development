# Changelog

## [Unreleased]

- 新增请求级 trace_id 贯穿：FastAPI 中间件为每个请求绑定 trace_id（接受入站 `X-Trace-Id` 回显，否则生成 hex uuid），写回 `X-Trace-Id` 响应头并输出 `trace=… method path status duration_ms` 日志；全部 SSE 事件载荷附带 `trace_id`；JSON 响应体刻意不带 trace_id 以保持「POST 与 GET 回查逐字段相等」契约，数据级关联仍由 `batch_id` 承担；新增 `app/tracing.py`（contextvar，线程池工作可见）。
- MCP stdio 服务扩为 5 工具（版本 0.2.0）：新增只读 `get_batch`（按批次读结果）、`get_batch_tasks`（逐文件任务进度）与 `export_batch_csv`（导出 UTF-8 BOM CSV，仅允许写已存在目录下的新文件、不覆盖）；HTTP 数据访问由既有 FastAPI 端点承担，MCP streamable-HTTP 传输未实现（stdlib-only 服务的既定边界）。
- 新增有界并发批量筛选与 429 指数退避：`screen_many` 增加 `max_workers`（1–16，默认 1），线程池并发保持输入顺序与失败隔离，同步 API 在 Mock 批量用 4 workers（注入真实评分器保持串行以保预算计数正确）；DeepSeek 客户端 429 重试改为指数退避（1s 起步、每次翻倍、8s 封顶），其他瞬时错误保持 0.5s 固定延迟，重试次数与请求预算契约不变。
- 新增批次任务状态持久化与断点恢复：`batch_tasks` 表逐文件记录 pending/done，同步端点改为逐份增量落库（结果与任务状态同事务先后写入）；新增 `GET /batches/{id}/tasks` 进度查询与 `POST /batches/{id}/resume` 断点恢复端点——只重筛未完成文件、复用已完成结果并返回 `resumed/reused` 清单，响应结构与 GET/回查一致。
- 新增 FastAPI SSE 流式端点 `POST /batches/stream`：每份 PDF 筛完即推 `progress` 事件，结束推 `done`（含持久化批次，可回查），失败推固定 `error` 码（上传写入/保存/流式失败），不回传私有细节；默认同步 `POST /batches` 行为不变，multipart 校验抽为共享入口。
- 新增风险感知编排入口 `app/orchestration.py`（默认链路不变）：`risk_critique` 确定性风险审查（闸门/复核/无匹配/注入特征）、`human_review` LangGraph interrupt 人工复核（approve/reject，reject 降级 REVIEW）、`export_report` Markdown 评估报告（仅派生字段）；`screen_one_risk`/`screen_many_risk` 可选启用，auto 模式行为与基线逐字段一致。
- 新增项目分人工标注流程（盲评工作表 + 一致性校验，纯离线零请求）：`eval.run_project_score_annotation` 从 15 条冻结片段生成无金标泄漏的盲评 CSV，`--check` 校验片段完整性、档位合法性与标注者列，并输出逐维精确匹配、MAE 与二次加权 Cohen's κ；分歧作为人工仲裁输入，不改金标、不设通过线。
- 新增检索质量 v4 资历特征重排（纯离线零请求）：查询年限与岗位 min/max 窗口比对后确定性重排；控制向量下 7 处年限型错排修复（RV008/HV008 技能域错排维持），真实 live 排序回放 RV005/HV005/QV004 全部修复且零破坏（v1 8/9→9/9、held-out 8/9→9/9、v2 11/12→12/12）；不接筛选，质量结论仍为 NOT_ESTABLISHED。
- 项目片段抽取识别中文短标题「项目」：cn30 覆盖 3/30→4/30（R09），批量评测清单扩至 17 例；授权预算内 live 一轮 4 请求/0 重试，4/4 有效、守恒全过、决策零漂移，R07 升 A、R11/R16 由 C 升 B。
- 判岗错排修复评估收口：R13（JD2 `max_years` 边界）实测改配置会引爆全仓 133 项冻结快照测试（`jobs.json` 被检索/embedding/基线哈希锚定），回退并维持边界归因；R18 与 R10 特征同构、R27 当前行为比旧标注更合理，三例均维持已知限制并沉淀防呆到 PITFALLS。
- 新增中文合成集整份简历 DeepSeek 批量评测入口（16 例 SHA 冻结子集）：仅项目片段非空的 R07/R11/R16 各发 1 次请求（首试 3、重试上限 3、总上限 6），2026-11-25 实测 3/3 有效、三维守恒全过、判岗与闸门决策零漂移；无项目分金标，不评准确率。
- 修复本地岗位向量索引的原子发布错误分类：文件系统对已存在目标抛裸 `OSError`（WinError 1）时，`target.exists()` 成立仍映射 `INDEX_EXISTS`，竞态保护不再被误报为 `INDEX_WRITE_FAILED`；新增 2 条回归单测，全量 789 通过。
- 新增中文合成集判岗质量评测（30 份 R01–R30）：当前代码实测判岗 19/22（86.4%）、异常拦截 4/4、闸门拦截 4 例，报告含混淆矩阵、分组正确率与错排归因；口径为确定性路由 + Mock 项目分，与历史 96.7% 不可比。
- 新增与 v1 不相交的 12 条 held-out 检索查询；授权 live 一轮 15 向量/3 HTTP/0 重试，Recall@1=8/9，HV005 错排，质量未过关。
- FastAPI 增加显式 `advisory=embedding` 旁路：默认关闭，不改规则岗位与分数，不落库；超长查询单条跳过。Demo 对 `/favicon.ico` 与 Chrome DevTools 探测路径返回 204。Windows Docker Desktop 仍未做。
- 用户授权复核 HV001–HV012 共 36 条标签，全部维持原等级；数据 SHA 与 `PENDING_INDEPENDENT_REVIEW` 不变。检索质量 8/9 记为交付限制，不改金标或阈值。
- 新增检索质量 v2：评测专用 JD4–JD6 与 18 条查询；授权 live 24 向量/3 HTTP/0 重试，Recall@1=11/12，QV004 错排，质量未过关。不改筛选岗位。
- 新增检索质量 v3 离线混合实验（BM25+RRF，零请求）：假向量控制下融合提升 Recall@1（v2 9/12→11/12）；真实 live 排序回放显示三处错排同型且词法与语义一致地错，词法融合对真实错排中性，修复需 Rerank 或资历特征。
- 新增 MCP stdio 服务 `python -m app.mcp_server`（纯标准库，零新依赖）：暴露 `screen_resume`（默认 Mock 批量筛选）与 `list_jobs` 只读工具；无网络、无凭据读取，不改筛选契约。

## [v0.6-delivery-acceptance]

- 同步已复审的干净安装 A、B01 表断言及 B02–B08 分项证据；B08 功能矩阵收口，宽屏按实际 1366×848，保留原始失败、截断和用户接受的操作偏差。
- B09 已独立收口；B10 第三轮取得预期失败证据（自然 SystemExit/1/固定文案，8000 无监听）。前两轮 BLOCKED 现场保留。B 分项收口，控制台零错误仍不成立。C 已在 GitHub Linux 对冻结 `3005819` 通过 C01–C06，本机未安装 Docker。
- 实施默认关闭的假向量辅助证据 API 与独立合成样例面板；显式本机启动固定索引，查询只读、输入限界，保持批次岗位与评分不变，无新增真实请求。
- 新增默认关闭的本地岗位向量索引，支持模型/岗位版本校验、过期拒绝与不覆盖的原子发布；仅假向量离线验证，并修正已复审标签的旧报告提示。
- 记录授权的一轮真实 15 向量基线：3 HTTP、零重试，相关查询 Recall@1=8/9，估算 ¥0.000312；结果待审，新旧锁保留，不改路由、评分或标签。
- 新增独立相关性真实基线的受控入口：默认零请求，三批最多 3 次 HTTP、零重试和独立持久锁；源码检查先于凭证读取，已通过离线全量 588 项，不改变路由或评分。
- 新增 12 条独立合成检索查询及 36 条待复审相关性标注，提供默认关闭、显式假向量的离线评测；保持原路由、评分与请求锁，真实请求为 0。
- 固化 PR #33 更正补审证据，保持原标签与 SHA，评测前拒绝内存快照变化；常规工作自主实现和自查，重要变更保留测试/审查及操作授权。
