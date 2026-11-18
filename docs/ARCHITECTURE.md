# 架构说明

当前目标是把已有 Coze 原型中的业务链路迁移成可测试的 Python 应用。

```text
extract -> guard -> route -> score -> verify -> finalize
```

- `extract` 负责 PDF 文本提取和基础字段抽取。
- `guard` 接收 `ExtractResult`，负责文本质量和异常状态。空文件、读取失败、疑似图片或损坏、过短文本分别进入人工复核，复核结果使用 `score=-1`。通过闸门的结果保留 `score=None`，交给后续评分模块。当前文字质量规则无法区分“图片上只有少量中文提示语”的扫描件和真实过短文本，这类输入会按 `TOO_SHORT` 处理。没有文字层的图片 PDF 仍返回 `EMPTY`。
- `route` 读取带稳定 `jd_id` 的岗位配置，按技能命中率和年限契合度计算确定性结果。方向信号冲突或最佳技能命中率低于 0.30 时返回 `UNMATCHED`，不调用模型，也不负责评分。
- `score` 按技能、项目、年限、学历和加分项计总分。默认筛选使用 Mock 项目分，DeepSeek 在显式评测入口注入。有效简历的路由结果为 `UNMATCHED` 时，仅保留学历分并返回 `SCORED`。Guard 拦截、岗位配置异常或评分失败时返回 `REVIEW` 和 `-1`。
- `verify` 校验结果结构、分项范围、总分和岗位 ID，并检查 `UNMATCHED` 是否只保留学历分。合法的评分和复核结果原样通过，发现错误时返回新的 `REVIEW`、`-1` 和校验原因码。
- `screen_one` 从单份 PDF 调用抽取、闸门、画像、路由、评分和校验。闸门拦截时直接返回经过校验的复核结果。通过时可使用默认岗位或调用方提供的岗位序列。
- `extract_project_snippet` 可独立接收已清洗的简历文本。它按独立成行的中英文项目标题定位正文，在下一节已知标题前切断，并按文档顺序保留各段原文。正文缺失时返回空串。当前可用作离线提取工具。
- PDF 集成测试使用 `extract_pdf(path).text` 调用 `extract_project_snippet`，直接比对合成 PDF 中的项目原文，覆盖单段、多段和空段。
- 离线评测逐个调用 `screen_one`，将合成 PDF 的实际路由、评分和复核结果与固定标注比对，并保存逐场景报告。
- `project_scorer` 接收显式提供的项目片段和岗位要求，校验注入客户端返回的三项分值和原文引证，再加总为项目分。DeepSeek 在冒烟和完整评测中显式注入 HTTP 客户端。评测默认使用 v1 提示词，完整评测可显式选择 v2，生成独立的对照报告。默认单份筛选采用离线 Mock。
- `make_injected_scorer(client)` 生成可供 `screen_one` 或 `score_candidate` 显式使用的打分回调。回调从已抽取的简历正文切出项目段，由 `score_project` 将项目原文和岗位要求交给客户端。空段沿用零分和跳过调用的规则，非法回包沿用 `PROJECT_SCORER_ERROR` 复核。
- 合成长 PDF 回归使用实际抽取、闸门和路由，分别从 `screen_one` 验证默认 Mock 与显式假客户端，检查项目分计入总分且其他分项一致。
- `eval.run_screen_deepseek` 对固定合成 PDF 运行同一筛选路径。显式传 `--live` 时，通过现有适配器注入 DeepSeek v2，并记录 Mock 对照与模型实测。
- `finalize` 从已校验的评分生成展示和存储记录，可通过 `screen_one(...).finalized.as_dict()` 取得。记录保留状态、岗位、总分、分项、评分来源和复核原因。`SCORED` 按 80 和 55 两个阈值分档，`REVIEW` 的档位为空。
- `screen_many` 按输入顺序逐份调用 `screen_one`，每份返回一个 `BatchRecord`。记录序列化时在已校验字段上加 `source_name`。既有复核原因原样保留，未预料的单份异常使用固定原因 `BATCH_ITEM_ERROR`。
- `store` 将 `BatchRecord.as_dict()` 的五个分项展开，按批次保存到调用方指定的 SQLite 数据库。`screen_results` 以 `(batch_id, source_name)` 保证批次内唯一。按批次回读时用参数绑定并保留写入顺序，CSV 沿用公开字段，复核分项的数据库空值导出为空单元格。
- `app.api` 提供本地 HTTP 健康检查、上传批次和批次查询。上传经校验后写入请求的临时目录，筛选沿用 `screen_many` 的默认 Mock。原件随目录清理，写入和回读使用请求独立数据库连接，接口返回 `fetch_batch` 的扁平记录。
- 本地 Docker Compose 配置复用同一 API，镜像使用非 root 用户，服务监听容器内回环地址，SQLite 数据使用命名卷。宿主机回归通过项目虚拟环境执行。
