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
- FastAPI 提供本地批量上传和按批次回查的单页 Demo，默认使用 Mock 评分。显式 DeepSeek 接口已完成本机单样本真实联调，LangGraph 已编排现有筛选函数，Embedding 检索待实现。
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

## 运行本地 FastAPI

筛选入口使用本地 LangGraph 编排，项目依赖固定为支持 Python 3.9 的 `langgraph==0.6.11` 及已验证的配套版本。图复用现有规则函数，每次执行使用独立状态并关闭外部追踪。[包版本信息](https://pypi.org/project/langgraph/0.6.11/)

在仓库根目录使用项目虚拟环境安装依赖并启动。服务仅用于本机，监听 `127.0.0.1`，评分默认使用 Mock。

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn app.api:app --host 127.0.0.1 --port 8000
```

浏览器打开 [本地 Demo](http://127.0.0.1:8000/)，上传 `data/synthetic/resumes/` 下的 `S001_normal_backend.pdf` 和 `S002_empty.pdf`。示例结果分别为 `SCORED / B / 60` 和 `REVIEW / EMPTY / -1`。页面自动填入批次 ID，可直接点击「查询批次」验证回读。

另开 PowerShell，用合成 PDF 提交一次批次并查询结果。

```powershell
curl.exe http://127.0.0.1:8000/health
$batch = curl.exe -sS -F "files=@data/synthetic/resumes/S001_normal_backend.pdf" -F "files=@data/synthetic/resumes/S002_empty.pdf" http://127.0.0.1:8000/batches | ConvertFrom-Json
curl.exe "http://127.0.0.1:8000/batches/$($batch.batch_id)"
```

单次最多上传 10 份 PDF，每份最多 5 MiB，上传校验失败返回 `422`。同一批次文件名按大小写不敏感的方式查重。数据库默认保存在 `tmp/screen-results.sqlite`，可通过进程环境变量 `SCREEN_DB_PATH` 指定位置。上传原件在筛选结束后从临时目录清理。

本地 HTTP 仅在显式提交 `POST /batches?project_scorer=deepseek` 时读取模型配置并注入 DeepSeek v2 项目评分，配置失败返回固定的 `503 / 模型未配置`，其他评分参数值返回中文 `422`。每批最多「文件数 + 2」次请求、最多 2 次瞬时重试；HTTP 400–404 或 `REQUEST_LIMIT` 后，后续应调用模型的文件进入 `REVIEW / PROVIDER_STOPPED / -1`，空项目段和本地拦截继续按原规则处理。批次可按原接口回查。接口已通过假客户端验收和本机单样本真实联调，实测项目分 24、总分 84、A 档。Demo 与缺省 HTTP 请求使用 Mock。[HTTP 实测记录](eval/README.md#2026-11-19-本机-http-联调)

## Docker（可选）

需要 Docker Engine 和 Compose。服务 `api` 以非 root 用户运行，监听容器内的 `127.0.0.1:8000`，并使用 Mock。Compose 无宿主机端口映射。SQLite 保存在命名卷 `screen-data` 中，容器内路径为 `/data/screen-results.sqlite`。

基础镜像固定为 `python:3.9.25-slim-bookworm` 的摘要，保留 Python 3.9 兼容线。这个配置用于本地合成样本验收。

```powershell
docker version
docker compose version
docker compose config
docker compose up -d --build --wait
docker compose exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read().decode())"
```

用镜像自带的两份合成 PDF 验证批量上传和回读，记录返回的批次 ID。

```powershell
$batchId = @'
import json
from pathlib import Path
from urllib.request import Request, urlopen

base = "http://127.0.0.1:8000/batches"
boundary = "screening-smoke"
fixtures = Path("/srv/data/synthetic/resumes")
parts = []
for name in ("S001_normal_backend.pdf", "S002_empty.pdf"):
    header = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    )
    parts.extend((header.encode("ascii"), (fixtures / name).read_bytes(), b"\r\n"))
body = b"".join(parts) + f"--{boundary}--\r\n".encode("ascii")
request = Request(base, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
with urlopen(request, timeout=10) as response:
    created = json.load(response)
with urlopen(f"{base}/{created['batch_id']}", timeout=10) as response:
    assert json.load(response) == created
normal, empty = created["items"]
assert (normal["jd_id"], normal["tier"], normal["total"], normal["project_source"]) == ("JD1", "B", 60, "mock")
assert (empty["status"], empty["reason_code"], empty["total"], empty["tier"]) == ("REVIEW", "EMPTY", -1, None)
print(created["batch_id"])
'@ | docker compose exec -T api python -
```

停止并重建容器时保留命名卷，再查询同一批次。

```powershell
docker compose down
docker compose up -d --wait
docker compose exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/batches/$batchId', timeout=3).read().decode())"
```

2026-11-18 已在 GitHub 托管 Linux 运行器上验证 `c908730`，使用 Docker Engine 28.0.4 和 Compose v2.38.2。构建、健康检查、合成 PDF 上传、非法上传 422 和重建回读均通过。Windows 宿主机的 Docker Desktop 尚未验收。 [容器验收日志](https://github.com/yjy18312446491/agent_development/actions/runs/44243560755)
