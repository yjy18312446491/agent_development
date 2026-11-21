# 干净安装、真实浏览器与目标容器验收方案

状态：**待 Cursor 审方案，未获执行授权，未运行以下验收。** 本文是长期交付验收规范；Draft PR 仅改文档，不新增脚本、依赖、工作流、镜像配置或自动触发器。审查通过后由用户明确选择执行 A/B/C 的范围；方案通过、PR 合入均不等于已授权执行或已验收。

## 1. 固定对象与现有证据

本轮候选源码固定为 main `300581980ef2f1da4e9a7d96ecc62aea79bd7b9c`，其中 #39 Squash 为 `1c91381e00db2a62647f9b6537176e8110a57a5b`。#39 已审 head `7e489313ea2e71fe776924dafc8a0e38647a7536`，合并树 `adb447d217403d43efd94e888190ab897f207170` 与已审/已测相同，后续 `3005819` 仅文档记录。

已有证据是现有虚拟环境中的全量 **731 passed**（5 条既有 SWIG 警告）、Mock PDF **13/13**，开发及 Cursor 的专项模拟 DOM **6 passed**。这些结果不能替代新环境安装、运行中服务、真实浏览器或新镜像验收。当前 Python 3.9 兼容线和基础镜像不在本轮升级；新安装成功也不表示依赖已全部锁定或达到生产部署标准。

执行前重新核对 main、方案审查 head 和候选 SHA；方案 PR 相对候选只能有文档差异。应用、tests、requirements、Dockerfile、compose.yaml 或数据一旦变化，停止沿用本方案结果，重新冻结对象和补审。不能在运行期间跟随 latest/main 漂移。

| 阶段 | 目标与环境 | 通过口径 | 当前情况 |
| --- | --- | --- | --- |
| A | Windows x64、Python 3.9、新源码导出与新 venv | 安装、pip check、全量离线与 Mock | 仅发现 py/python 命令；未创建新环境或安装 |
| B | A 的隔离环境、真实本机 Edge/Chrome、回环 HTTP | 默认与显式假向量两种启动模式的页面/网络/回归 | 未启动服务或浏览器；浏览器工具可用性待执行前核对 |
| C | Linux 容器、Docker Engine + Compose v2 | 当前默认镜像构建、禁用辅助服务、Mock 上传回查、保留卷重建 | 本机 PATH 未发现 docker CLI；不据此声称系统未安装 Docker |

三阶段分别记 PASS / FAIL / BLOCKED / NOT_RUN。C 的 Linux/Compose 成功不能写成 Windows Docker Desktop 通过；B 的宿主机假向量演示也不能写成容器已启用假向量服务。

## 2. 共用准备与零付费边界

以下准备动作也须等用户授权。每次使用新的 `out/acceptance/<run_id>/`，目录已存在即拒绝复用。先记录原工作区 git status，并对原 `eval/reports/` 全部既有文件建立相对路径/SHA 清单（目前 33 份），以及原 `out/` 中已有报告和索引的清单；仅哈希，不读取/输出报告正文。每阶段结束及最终收尾逐一复核旧文件未删除或变化。新的结果、截图、日志、SQLite、索引只写本轮目录，绝不覆盖原报告、数据库或锁。

拟执行的源码准备（PowerShell，**现在不运行**）：

```powershell
$sourceSha = '300581980ef2f1da4e9a7d96ecc62aea79bd7b9c'
$runId = 'accept-' + [guid]::NewGuid().ToString('N')
$runRoot = Join-Path (Get-Location) ('out/acceptance/' + $runId)
if (Test-Path -LiteralPath $runRoot) { throw 'Refusing existing acceptance directory' }
New-Item -ItemType Directory -Path $runRoot | Out-Null
git archive --format=zip --output "$runRoot/source.zip" $sourceSha
if ($LASTEXITCODE -ne 0) { throw 'Source export failed' }
Expand-Archive -LiteralPath "$runRoot/source.zip" -DestinationPath "$runRoot/src-host"
Expand-Archive -LiteralPath "$runRoot/source.zip" -DestinationPath "$runRoot/src-container"
```

只导出 Git 跟踪内容，不复制原工作区 `.env`、venv、tmp、out 或 eval/reports。核对两份导出无 `.env`、运行数据库、报告或锁，记录 archive SHA 和原始导出文件的 SHA 清单。保留 `.env.example` 即可，不把它改成真实配置。后续命令的工作目录必须明确指向对应导出，不能误用原仓库。

安装、测试和服务均通过新子进程执行：使用 `ProcessStartInfo` 清空子进程环境，仅传必要的 SystemRoot/WINDIR/SystemDrive/ComSpec/PATHEXT/PATH/USERPROFILE 系统项；TEMP/TMP 指向本轮新建的临时目录，再显式设 `PYTHONNOUSERSITE=1`、关闭 LangSmith/LangChain tracing，并指定本轮独立 `SCREEN_DB_PATH`。不继承 LLM/EMBEDDING/DASHSCOPE/OPENAI/LANGCHAIN/LANGSMITH 等凭证或配置，不输出父进程环境值。USERPROFILE 仅供系统工具使用，pip 使用下述 isolated/公共 index；不调用 config list 等可能暴露配置的命令。

网络分阶段说明：

- A 允许安装所需的公共 PyPI/文件下载；C 允许固定基础镜像及公开依赖下载。不是完全离线安装，不把基础设施流量计为模型请求。
- 测试和服务禁止构造付费提供器、读取真实 Key、调用模型；没有 `--live`、没有 `project_scorer=deepseek`。浏览器只访问隔离服务的 `127.0.0.1`，无外链资源。
- 模型/Embedding 新尝试数和付费预算均为 **0**，不得借用过去 ¥0.10 预算、剩余次数或删除锁。未知网络或费用不能写成 0；发现疑似提供器调用立即停止并报告。
- 不进行用户全局 Python/pip 配置改写、系统包升级、安装浏览器/Docker Desktop、启用 WSL、切换 Docker context 或修改防火墙。环境不足标 BLOCKED，先取得变更授权。

每条外部命令检查退出码后才继续；安装/构建最长各 15 分钟，pytest 最长 10 分钟，服务健康就绪最多 60 秒，单条 HTTP 10 秒，单条 UI 用例最多 60 秒。首次失败保留证据并停止该阶段；不得静默重试安装、更换依赖/镜像、清库或变更预期以凑通过。A 失败不进入 B；C 独立记录环境阻塞，不能用历史 CI 成功补算。

## 3. A：干净安装与离线回归

在净化后的子进程、`src-host` 工作目录执行；执行器记录每条命令、解释器完整路径/版本/位数、pip 版本、依赖实装清单和退出码：

```powershell
py -3.9 -m venv .venv
.\.venv\Scripts\python.exe -m pip --isolated install --no-cache-dir --index-url https://pypi.org/simple -r requirements.txt
.\.venv\Scripts\python.exe -m pip --isolated check
.\.venv\Scripts\python.exe -m pip --isolated freeze
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m eval.run_eval --output out/validation/acceptance-mock.json
```

每次都用全新导出，该 Mock 路径先断言不存在；输出最终归入本轮证据目录，禁止覆盖。venv 不使用 system-site-packages，不复用旧 wheel/cache/venv，不升级系统 pip；requirements 中的范围依赖照原文件解析，不修改为“今天能装”的另一组版本。需要的公共下载不可达则 FAIL/BLOCKED，不偷用原 `.venv` 冒充成功。

验收断言：pip check 无依赖冲突；全量应与该源码的 731 项一致且无失败/跳过（数量变动须先解释）；Mock 为 13/13、10 PDF，SCORED=6、REVIEW=7。既有 5 条 SWIG 警告单列，新增告警逐条判断。记录实装版本与固定 LangGraph 配套版本核对，不能仅凭 requirements 文本声称已安装。

额外以新进程导入 `app.api:app`，确认默认无配置读取、无 `out/vector-index` 和数据库创建；不复用 pytest 已导入的模块掩盖启动问题。A 的 PASS 只说明此 Windows/Python 3.9 新环境，不代表 Linux 容器或其它 Python 版本通过。

## 4. B：真实浏览器与实际 HTTP 服务

使用 A 的解释器，在 `src-host` 中顺序运行下列两种模式；每种模式设不同的本轮 SQLite 路径。启动前确认 8000 端口空闲；冲突即停止，不杀未知进程或自动换端口。服务必须由执行器记录 PID/启动时间，并以隐藏窗口启动。模式切换仅停止本轮拥有的进程、确认端口释放后启动下一模式。

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.api:app --host 127.0.0.1 --port 8000
# 完成默认模式并停止其拥有的 PID 后，才切换显式假向量模式：
.\.venv\Scripts\python.exe -m app.advisory_demo
```

打开独立浏览器会话 `http://127.0.0.1:8000/`；记录浏览器名称/版本、Windows 版本、桌面视口 1366×900 与窄视口 390×844。使用真实页面交互与同源请求，不把 Node 模拟 DOM 或截图下载冒充浏览器运行。浏览器工具不可用则 BLOCKED，不能跳过后写 PASS。

| 编号 | 实际操作 | 必须满足的结果 |
| --- | --- | --- |
| B01 | 默认模式打开页面、GET health/status | health=200；status disabled；辅助按钮/样例选择禁用，评分上传仍可用；未生成索引/数据库 |
| B02 | 页面上传 S001/S002 两份合成 PDF | S001=JD1/SCORED/B/60/project 0/mock；S002=REVIEW/EMPTY/-1，分项/档位为空；批次 ID 自动填入 |
| B03 | 点查询批次并刷新后回查 | GET 与 POST 相同且顺序不变；按数据库只读回查本批次也相同；页面没有路径/内部错误 |
| B04 | 用同源浏览器 fetch 发非法文件名、重复文件名、未知 case、重复 JSON 键、top_k=true/4、4097 字节 body | 在启用态分别为固定 422；默认 evidence 无论 body 均先固定 503；不增加批次记录或改写索引；没有反射原始输入 |
| B05 | 停止默认服务后以新数据库启动显式假向量模式，打开新页 | status 为 fixed-fake-vectors 与四个白名单样例；页面始终明确“固定向量演示/语义质量未验证”，仅在点击时 POST evidence |
| B06 | 依次查询四个样例，再切换样例 | 每次 3 个参考 hits，含 ID/标题/相似度/≤1000 字符原文；NOT_ESTABLISHED；无关样例也可能排序，不宣称拒绝阈值；切换时清空旧证据 |
| B07 | 查询 demo_frontend，再上传/回查 S001 | 参考 JD3 可第一，但原批次表、jd_id、分数不被覆盖；S001 仍 JD1/60/mock；辅助请求前后 SQLite 记录数不变 |
| B08 | 窄/宽视口、Tab 键、禁用/加载/失败提示 | 表格可横向滚动，控件/文本不溢出；键盘可选样例并查询；错误只出现在对应面板，不影响已加载批次表 |
| B09 | 保存新假索引 SHA 后，查询并重启同一假模式 | 查询不改索引；重启使用同一代且 SHA 不变，不覆盖已有代 |
| B10 | 在隔离导出中停止服务、备份本轮假索引，然后只损坏该新索引并尝试启动 | 固定失败且没有监听端口；不自动修复、不发 HTTP；保持损坏件及备份供复核，不触碰原仓库索引 |

B04 的边界请求通过真实浏览器同源 fetch 执行：默认模式只检查非法上传和 evidence 的提前 503；B05 启用后再补测样例/JSON/top_k/大小的 422。不使用真实 PDF/个人信息，也不提交任何模型参数。B10 仅是已授权范围内对本轮新假数据的故障演练，放在正常路径全部成功之后。它不能替代运行中索引故障的单元测试证据。

网络证据只记录本任务页面发出的请求元数据：方法、同源路径、状态、耗时；不导出整个用户浏览器的 Cookie/HAR/本地存储。页面不应发跨源请求或任何 DeepSeek/百炼请求；没有控制台错误。保存默认禁用、默认评分、假向量证据、窄屏四类截图（只含合成数据）；不截终端环境变量或配置编辑器。

## 5. C：目标 Linux/Compose 默认镜像验收

执行前选定并记录实际 Docker host。优先在用户已经配置好的 Windows Docker Desktop 的 **Linux containers** 模式验证该 Linux 镜像；当前只读检查没有 docker CLI，此路径暂为 BLOCKED。若用户改选现有 Linux Engine/Compose 机器，则只报告该 Linux/Compose 环境，不声称验证 Windows Desktop。不得自行安装 Docker/WSL 或改绑服务；若需要 GitHub Linux CI，先单独提交按完整源码 SHA 检出的手动工作流 Draft PR，经审查及执行授权后运行，不复用旧固定 SHA 工作流制造新结果。

采用现有 Dockerfile/compose.yaml 和新的 `src-container` 导出，不加宿主机端口映射、host network、源码/凭证 bind mount 或容器辅助索引卷。服务仍监听容器内 127.0.0.1，辅助接口默认关闭。**不在容器里启动 app.advisory_demo**：当前非 root 用户对 `/srv/out/vector-index` 的可写性未定义；容器假向量启用/挂载方案不属于本轮。

在净化环境的 `src-container` 中执行（`$composeProject` 必须为本轮唯一、小写且尚未存在的 project 名，执行前核对不会碰其它项目；以下所有命令均显式带 `-p`/`-f`）：

```powershell
$composeProject = 'accept-' + [guid]::NewGuid().ToString('N')
docker version
docker compose version
docker info --format '{{.OSType}}'
docker compose -p $composeProject -f compose.yaml config --quiet
docker compose -p $composeProject -f compose.yaml build --pull --no-cache
docker compose -p $composeProject -f compose.yaml up -d --wait --wait-timeout 60
docker compose -p $composeProject -f compose.yaml exec -T api python -m pip check
docker compose -p $composeProject -f compose.yaml ps -q api
```

预检要求 Docker host 为 linux、Compose 支持 wait/timeout。构建保持仓库固定 Python 3.9.25 基础镜像 digest，保存最终镜像 ID、基础摘要、Docker/Compose/Python 与实装包版本；不推送镜像或缓存。构建下载失败/镜像不可用就停止，不能换基础镜像或静默复用旧镜像。

容器内 HTTP 只用 `docker compose ... exec -T api python -` 的标准库 urllib 访问 `http://127.0.0.1:8000`，无宿主机 curl 或浏览器直连容器的要求。上传使用镜像自带 S001/S002；multipart 构造及读回比对按 README 的已知流程，但每次都保留本轮唯一 project 参数，不在原项目执行 README 中的无 project 命令。

| 编号 | 检查 | 必须满足的结果 |
| --- | --- | --- |
| C01 | 构建、健康、权限 | 新镜像运行、healthy、UID=1000、/data 可写；首次 health/status 不建库；pip check 通过 |
| C02 | 文件/配置隔离 | 镜像有 app/static/demo.html、新 advisory 模块、合成 jobs 和仅 S001/S002 PDF；无真实 .env/.git/venv/tests/eval/reports；pytest 不必安装，HTTPX 可被 SDK 间接依赖 |
| C03 | 默认禁用新接口 | health/status=200 且 disabled，evidence=503 空 hits；不生成索引或模型配置，页面包含独立面板但不能开启服务 |
| C04 | Mock 上传与 GET/SQLite 一致 | S001=JD1/B/60/mock/project 0；S002=EMPTY/-1；两条记录顺序一致；非法上传 422，不增加记录 |
| C05 | 保留卷重建 | 记录本轮 container/image/volume ID 和原批次结果，down（无 -v）后 up --no-build --wait，容器 ID 改变、镜像 ID 及命名卷不变，同批次 GET/SQLite 仍相同 |
| C06 | 默认隔离约束 | 当前 CMD/回环监听保持原值，无发布端口/特权/host 网络/额外挂载；无提供器凭证、请求或真实向量 |

重建与停止命令（只操作本轮 project）：

```powershell
docker compose -p $composeProject -f compose.yaml down
docker compose -p $composeProject -f compose.yaml up -d --no-build --wait --wait-timeout 60
# 核对 C05 后停止，不删除卷或镜像：
docker compose -p $composeProject -f compose.yaml down
```

不使用 down -v、volume rm、system prune 或清理其它容器；保留本轮卷、镜像与证据便于复审。健康失败最多收集本轮容器的必要脱敏日志，不打印 docker inspect 全量 Environment 或宿主机凭证。运行时提供器调用数应为 0；不把公网依赖下载或健康探测混算成真实模型请求。

## 6. 证据、停止与完成标准

每阶段在本轮目录新增独立结果，列出候选源码 SHA、方案审查 head、用户授权范围、命令/操作、开始结束时间、退出码、环境版本、每个编号的实际结果、失败/阻塞原因、脱敏证据路径及 SHA。尚未执行一律 NOT_RUN；缺环境为 BLOCKED，不能写 0 失败即通过。日志/截图/报告/数据库/索引不上 Git，仅将脱敏结论写对应 PR 及长期状态文档。

结束时停止本轮拥有的服务与浏览器会话；PID/启动时间不能确认归属则不杀进程，报告待人工处理。新沙箱、虚拟环境、索引、SQLite、卷和报告均保留，不递归删除。比较原工作区及导出原始文件清单，除了本轮专属再生输出不应有源文件变化；原报告/锁任一 SHA 或数量异常立即停止，保留现场，不自动恢复掩盖问题。

方案只读审核 → 用户选择 A/B/C 并授权执行 → 已审方案/冻结源码下实际验收 → 结果送 Cursor 复审。发现代码问题应另分支修复、补测/审查并重新冻结受影响阶段；小修复也不能把旧 SHA 的结果移植到新代码。发布/tag/合 main、新增真实实验及升级 Python/依赖均不在执行授权中。

A/B/C 全 PASS 且结果复审通过，才能称“该候选在所列环境完成交付验收”。C BLOCKED 时可报告 A/B 通过和明确的容器缺口，不能称全部完成。即使全部通过，也只交付默认 Mock + 固定假向量辅助演示，不宣称真实检索语义质量或生产可用性已经通过。

## 7. 命令依据

命令语义核对使用 [Python 3.9 venv](https://docs.python.org/3.9/library/venv.html)、[pip 命令选项](https://pip.pypa.io/en/stable/cli/pip/) / [pip check](https://pip.pypa.io/en/stable/cli/pip_check/)、[Compose up](https://docs.docker.com/reference/cli/docker/compose/up/) / [down](https://docs.docker.com/reference/cli/docker/compose/down/) 官方文档。执行时仍须记录实际工具版本及选项支持情况，不能把网站最新版本号当成本机安装版本。
