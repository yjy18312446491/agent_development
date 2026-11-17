# 简历岗位智能匹配 Agent

这是一个面向招聘初筛的 AI 应用项目。目标是从 PDF 简历提取候选人信息，匹配岗位 JD，提供可解释评分和异常复核。

## 当前状态

- 当前 Python 功能分支已实现单份 PDF 筛选入口，串起文本抽取、质量闸门、岗位路由、五维评分和结果校验，均有回归测试。
- 当前总分包含离线 Mock 项目分，默认项目分为 0。真实模型接入后的评分效果将用评测集单独验证。
- 已有 10 份合成 PDF、13 个场景的离线 Mock 回归基线，覆盖正常评分、人工复核和打分回调异常。详见 [评测说明](eval/README.md)。
- DeepSeek 项目分完成首轮 12 条合成片段评测。空片段本地跳过，11 条模型响应均合法，8 条三项分值与人工标注完全一致。默认单份筛选仍采用 Mock。详见 [评测说明](eval/README.md#deepseek-完整评测)。
- DeepSeek v2 合成片段真实对照在完整重跑中取得 14/14 条有效评分，三项评分均与标注一致。原样本全匹配 11/11，项目总分 MAE 0.0。详见 [v2 对照结果](eval/README.md#真实-v2-对照结果)。
- 独立的项目原文提取函数按中英文独立标题识别项目段，已用英文合成 PDF 跑通从文件到项目原文的离线路径。现有 PDF 筛选仍采用 Mock 项目分。
- 项目评分适配器可以显式注入筛选流程，已用假客户端验证项目原文传递、空段跳过和异常回包复核。
- 英文合成长 PDF 已用于离线端到端测试，覆盖读取文件、通过闸门、路由至 JD1、传递项目原文和将项目分计入总分。
- 固定合成 PDF 已完成一次显式 DeepSeek v2 联调，项目分 14、总分 74。日常筛选继续使用 Mock。详见 [端到端联调说明](eval/README.md#固定合成-pdf-的-deepseek-端到端联调)。
- 单份筛选已提供可序列化的结果记录，包含档位、分项和复核原因。
- 批量筛选已支持逐份处理 PDF，并返回可序列化结果。
- 批量结果可按批次保存到本地 SQLite，并导出 CSV。
- 真实项目分的生产接入，以及 LangGraph 编排和 FastAPI 接口待实现。
- Coze 原型和旧 Python 版的离线评测属于历史资料。当前仓库的验证范围见 [项目状态](docs/STATUS.md)。
- 历次问题、修复及回归用例见 [版本历史](docs/VERSION_HISTORY.md)。

## 目标架构

```text
PDF 简历
  -> 文本解析与质量闸门
  -> 候选人画像抽取
  -> JD 向量检索与岗位路由
  -> 规则评分 + LLM 项目评分
  -> 结果校验与人工复核
  -> SQLite/PostgreSQL + CSV/API
```

## 目录

```text
agent开发/
├── app/                 # 应用代码
├── tests/               # 单元测试和回归测试
├── eval/                # 评测脚本、标注说明和报告
├── data/synthetic/      # 脱敏或合成 JD、简历和标注
├── docs/                # 架构、决策、复盘和运行说明
├── .env.example         # 环境变量示例
├── .gitignore
└── README.md
```

## 开源约定

仓库只使用合成或脱敏数据，不提交 API Key、个人简历、手机号、邮箱、平台内部链接和运行数据库。对外发布前需要完成测试、评测口径说明和干净环境运行验证。

## 运行单份筛选

在仓库根目录用项目环境运行以下示例。英文合成 PDF 的默认结果为 `JD1 / SCORED / 60`，项目分使用离线 Mock。

```python
from app.screen import screen_one

result = screen_one("data/synthetic/resumes/S001_normal_backend.pdf")
print(result.score.jd_id, result.score.status, result.score.total)
record = result.finalized.as_dict()
print(record["tier"])  # B
```

通过校验的评分按总分分档：80–100 为 A，55–79 为 B，0–54 为 C。`UNMATCHED` 按保留的学历分归入 C。`REVIEW` 保留原因码和总分 `-1`，档位和分项为空。

## 运行批量筛选

```python
import json
from pathlib import Path

from app.screen import screen_many

paths = sorted(Path("data/synthetic/resumes").glob("S*.pdf"))
records = screen_many(paths)
print(json.dumps([record.as_dict() for record in records], ensure_ascii=False))
```

批量结果与输入逐项对应。每个对象包含文件名 `source_name` 和单份筛选的 `finalized` 结果。转成字典时，文件名与状态、总分和档位处于同一级。缺失或损坏的文件各自产生复核记录，后续文件继续处理。

## 保存批次与导出 CSV

在上面的批量示例之后，可用现有的 `records` 写入本地数据库。示例输出目录 `out/` 已被 Git 忽略。

```python
from contextlib import closing
from uuid import uuid4

from app.store import connect, export_csv, fetch_batch, save_batch

output = Path("out")
output.mkdir(exist_ok=True)
batch_id = uuid4().hex
with closing(connect(output / "screen-results.sqlite")) as conn:
    save_batch(conn, batch_id, records)
    export_csv(fetch_batch(conn, batch_id), output / f"screen-{batch_id}.csv")
```

同一批次内文件名必须唯一。重复写入会抛出 `sqlite3.IntegrityError`，该次写入全部回滚。回读按写入顺序返回。CSV 使用 UTF-8 BOM，复核记录的档位和五项分数留空。

## 下一步

1. 审核批次持久化和 CSV 导出。
2. 逐步补齐 FastAPI、Docker 和 Demo。
