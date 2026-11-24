# 干净安装、真实浏览器与目标容器验收方案

状态（2026-11-24 最新）：A、B01 表断言及 B02–B09 相应分项沿用已接受结论，不重做。B10 首轮一次损坏/启动被管理器进程守卫阻断，原 BLOCKED、损坏件、备份和终止账本全部保留。[Cursor 7121802341](https://github.com/yjy18312446491/agent_development/pull/40#issuecomment-7121802341)结果复审为 REQUEST_CHANGES：要求校验前保存完整观察，区分启动器/实际 Python/辅助后代，支持单对象及引号命令行并覆盖真实观察分支。当前在新忽略目录修复和离线验证（12.2），不改变原轮、不损坏新待执行副本、不启动；新候选待补审，真实预期失败仍未验证。当前无监听，整段 B/B10/C、Ready、合入未通过，冻结应用和所有旧现场不变。

## 1. 固定对象与现有证据

本轮候选源码固定为 main `300581980ef2f1da4e9a7d96ecc62aea79bd7b9c`，其中 #39 Squash 为 `1c91381e00db2a62647f9b6537176e8110a57a5b`。#39 已审 head `7e489313ea2e71fe776924dafc8a0e38647a7536`，合并树 `adb447d217403d43efd94e888190ab897f207170` 与已审/已测相同，后续 `3005819` 仅文档记录。

已有证据是现有虚拟环境中的全量 **731 passed**（5 条既有 SWIG 警告）、Mock PDF **13/13**，开发及 Cursor 的专项模拟 DOM **6 passed**。这些结果不能替代新环境安装、运行中服务、真实浏览器或新镜像验收。当前 Python 3.9 兼容线和基础镜像不在本轮升级；新安装成功也不表示依赖已全部锁定或达到生产部署标准。

执行前重新核对 main、方案审查 head 和候选 SHA；方案 PR 相对候选只能有文档差异。应用、tests、requirements、Dockerfile、compose.yaml 或数据一旦变化，停止沿用本方案结果，重新冻结对象和补审。不能在运行期间跟随 latest/main 漂移。

| 阶段 | 目标与环境 | 通过口径 | 当前情况 |
| --- | --- | --- | --- |
| A | Windows x64、Python 3.9、新源码导出与新 venv | 安装、pip check、全量离线与 Mock | 初轮 FAIL 保留；新一轮 PASS：安装一次/零重试、check、731 passed、Mock 13/13、默认导入通过，结果已复审 |
| B | A 的隔离环境、真实本机 Edge/Chrome、回环 HTTP | 默认与显式假向量两种启动模式的页面/网络/回归 | B01 表断言及 B02–B09 分项沿用；B10 首轮一次故障/启动后被执行器守卫阻断，BLOCKED，缺应用自然退出证据；整段 B 未通过 |
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

以下保留原基础流程及授权前规则；初轮失败后，已按第 8 节获审/授权完成一次新 A 并通过。**当前不再执行 A、不续装或重建 venv**，B 使用该通过环境；未来新增安装仍须单独冻结、审查与授权。

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

打开独立浏览器会话 `http://127.0.0.1:8000/`；记录浏览器名称/版本、Windows 版本、实际桌面视口 1366×848 与窄视口 390×844。桌面原目标 1366×900 已按用户限制及 [Cursor 复核](https://github.com/yjy18312446491/agent_development/pull/40#issuecomment-7102101101)替换，以 `innerWidth/innerHeight` 为准；旧截图的工具栏标注保留为历史。使用真实页面交互与同源请求，不把 Node 模拟 DOM 或截图下载冒充浏览器运行。浏览器工具不可用则 BLOCKED，不能跳过后写 PASS。

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

本矩阵是完整 B 的通过标准，不是失败后的自动续跑授权。B01 保留第 10 节限定结论，B02–B08 已接受分项见 10.5–10.6；剩余 B09/B10 须按具体新方案及适用授权执行。不能用既往 APPROVED 恢复已结束轮次，也不能把分项通过写成整段 B 或控制台零错误通过。

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

## 8. pip 代理 TLS 失败：只读归因与待审最小重试

### 8.1 原失败现场与当前允许范围

原轮次 `accept-cd1791c4db6647bfbc3ede9db0cc5c60` 保持 A=FAIL、B=NOT_RUN、C=NOT_AUTHORIZED，不重开该轮的状态或使用其 venv 续装。`py -3.9 -m venv` 成功，安装命令启用 isolated/no-cache/零重试并使用公共 PyPI，退出码 2；错误为 `ValueError: check_hostname requires server_hostname`。原 stderr SHA 为 `f7ef0e84e3915f18f6faed1653ac6f575d331750f2d7f5732b9d024a809db93b`，原 stdout 为空。原日志、result.json、归档、脚本、venv 与导出都保留，不改写初轮证据。

本次只读核对版本、代码、配置来源和代理元数据，Python 使用 `-B` 防止生成字节码；本地解析探针把 socket connect/create_connection 与 URL 打开入口设为拒绝。没有启动 pip CLI、握手、DNS/端口测试、下载、安装、服务或浏览器。只输出协议/回环/认证有无/端点一致等布尔或分类，不输出代理地址、端口、凭证、私有配置或 .env。

### 8.2 已确认的失败机制与仍未知事项

1. 现场为 **pip 21.2.4 / Requests 2.26.0 / urllib3 1.26.6**，依据新 venv 自带源码/元数据，并未升级包。驱动清空环境后没有任何代理环境键，pip isolated 配置中也没有生效的 proxy 项。
2. Requests 的 `Session.merge_environment_settings()` 在 `trust_env` 下调用 `get_environ_proxies()`；它最终进入 Python 3.9 `urllib.request.getproxies()`。该函数在环境代理为空时回退 Windows 注册表。`--isolated` 忽略 pip 配置来源，不会把 Requests 的 `trust_env` 关闭；清空环境不能阻断注册表代理。
3. 同一白名单环境的只读解析表明，PyPI 的实际选中项与注册表 HTTPS 项相同：scheme 为 `https`、主机为回环 IP、无内嵌认证。注册表另有 `http` 项：scheme 为 `http`，同样为回环 IP、无认证，host/port 与 HTTPS 项相同。
4. 现场 `urllib3.connection.HTTPSConnection._connect_tls_proxy()` 将代理 SSL context 的 `check_hostname` 设为 True；`urllib3.util.ssl_.ssl_wrap_socket()` 在主机为 IP 时省略传给底层的 `server_hostname`。Python 3.9 `ssl.SSLSocket._create()` 恰在 `check_hostname=True` 且 `server_hostname` 为空时抛出上述 ValueError，与失败调用栈一致。
5. 本地无 socket 的分支捕获使用虚构 IP/域名验证：该版本 IP 分支确实省略 hostname，域名分支传递 hostname。另一个纯解析探针表明，安装子进程显式给 HTTP/HTTPS 目的地址都配置现有 HTTP 项，可选中 `http` scheme 且端点不变；这不是网络或安装验证。

已确认的是“注册表回退 + IP 形式 HTTPS 代理 + 旧 TLS 包分支”造成当前本地参数错误。**未确认**：本地代理端口是否实际支持 HTTP CONNECT、是否能够访问公共 PyPI、包能否下载、干净安装能否成功。不能把注册表 scheme 当作真实服务协议探测，也不能据此判定 requirements 冲突或模型 Key 问题。

代理发现依据见 [Python getproxies](https://docs.python.org/3.9/library/urllib.request.html#urllib.request.getproxies)；HTTP 代理用于 HTTPS 目的地址时，经 CONNECT 隧道再与目的服务器做 TLS，见 [urllib3 1.26.6 代理说明](https://urllib3.readthedocs.io/en/1.26.6/advanced-usage.html#http-and-https-proxies)。这里引用协议语义，不声称当前本机端口已支持或已试连成功。

### 8.3 最小修复提案：只在安装子进程使用已配置的 HTTP 代理项

这是**待审且尚未实施**的进程级代理映射修订，不改 requirements、Python/pip/urllib3、应用、原驱动或系统代理配置。先由用户确认，或由获准的本地配置只读证据确认：该既有 HTTP 项对应 HTTP CONNECT 服务；不以一次端口/网络试探代替前置确认。当前只读证据尚不能满足真实服务协议确认，未满足时保持 BLOCKED，不运行安装。

取得修订方案审核和用户重试授权后，执行器仅在内存读取现有 HTTP 注册表项，验证它仍是 `http`、回环 IP、有合法端口、无 username/password、path/query/fragment，且与原 HTTPS 项的 host/port 相同；任一不符即停止，不猜协议、不换主机/端口、不接受带认证代理。

只对**新一轮安装子进程**在原白名单上追加两个小写键（示意，不是当前执行代码）：

```powershell
# $verifiedHttpProxy 来自上述已确认协议、通过本地断言的现有 HTTP 项。
# 不记录该变量值，不放在 CLI 参数、文件、PR 或父进程环境里。
$installInfo.Environment['http_proxy'] = $verifiedHttpProxy
$installInfo.Environment['https_proxy'] = $verifiedHttpProxy
```

`https_proxy` 的值也为现有 `http://…` 代理项：键表示目的地址类型，值的 scheme 表示到代理的传输。保留目标 `https://pypi.org/simple` 及目标 TLS 证书/主机名验证；不使用 trusted-host、关闭证书验证、改 CA、no_proxy=*、直连回退、SOCKS 切换或私有 index。`--isolated` 保持开启：已在本地源码/纯解析中确认 Requests 仍会读取这些标准代理键；不能改用会被 isolated 忽略的 PIP_PROXY。

安装结束即销毁该子进程环境；这两个键不传给 venv 创建、pytest/Mock、应用或 B，不修改用户全局环境、注册表、pip.ini 或 .env。LOCALAPPDATA/HOME 仍不加入白名单，也不恢复完整环境继承。

### 8.4 单轮重试、通过口径与停止点

新 head 获 Cursor 补审、协议确认及用户明确允许重试后，才：

1. 再次冻结源码 **3005819 完整 SHA**；为所有旧报告/锁及**整个失败现场**建立新 SHA 清单。创建全新 run_id / 双导出 / venv / 日志 / 数据库路径，不复用失败轮、清缓存或删除证据。
2. 用原白名单执行一次 venv 创建，核对自带 pip/Requests/urllib3 版本仍与本方案一致；版本变化、环境缺失或预检失败立即停止，不自行升级/安装修补包。
3. 在尚未发网时验证生效代理来自上述显式 HTTP 项、端点不变且无认证，仅保存脱敏元数据；不调用 pip config list 或输出参数值。日志/异常中出现本地代理地址必须先脱敏后对外汇报，原始日志只留本轮忽略目录。
4. 仅调用**一次**安装命令，使用 `--isolated --retries 0 install --no-cache-dir --index-url https://pypi.org/simple -r requirements.txt`，最长 15 分钟。保留退出码与 stdout/stderr；失败立即停止，不能自动改为直连、升级 pip、换镜像/代理或再次调用安装。
5. 仅安装成功后运行原 A 的 pip check/freeze、全量 731 项、Mock 13/13 和默认新进程导入检查；各步骤最多执行一次，失败停止。所有预期仍对应冻结源码，不调整业务断言。
6. A 全通过后仍停在报告结果，等待用户明确允许继续 B；**本修订申请不自动恢复初轮已中止的 B 授权**。B/C、#40 Ready/合入、发布、系统环境变更和真实模型调用不在单轮 A 重试授权中。

模型/Embedding 新尝试数与预算保持 **0**。公共依赖安装的 HTTP 数不能当作模型请求数，也不承诺它固定为某个数；只限制安装命令一次、网络重试为 0。若根因/协议未确认或修订未审，保持当前失败状态，不以“修复方案已写好”表示安装已恢复。

## 9. B01 访问权限异常：只读诊断与待审单轮重试

### 9.1 现场和本次未执行的操作

原 B 目录为 `out/acceptance/browser-b-a393c1d38c864d25902a26eb592d51e7/`，[结果评论](https://github.com/yjy18312446491/agent_development/pull/40#issuecomment-7062855513)记录 B01=BLOCKED、B02–B10=NOT_RUN。冻结源码仍为 `300581980ef2f1da4e9a7d96ecc62aea79bd7b9c`，执行时方案 head 为 `a617ad7`；原 `result.json` SHA 为 `117a20147859c9ef9299f28096e0d10d1c9e809636154399dadc2ac2cebacad1`。不改写原驱动、结果、截图或空日志。

本次仅阅读上述现场、执行工具权限声明、实装 Python/Uvicorn 源码和公开官方文档，并只读查询原时段 Windows Security 的 5152/5157 事件；查询无匹配事件，**不能据此排除过滤或断言某条 WFP/防火墙规则是根因**。未重新创建 socket、连接/绑定端口、运行应用导入、启动服务/浏览器、安装依赖或执行测试；未读取真实配置。

现有 `out/` 与 `eval/reports/` 共 **6466 个文件**（包含 B 的 12 个现场文件）建立只读聚合指纹；原 B 清单的 6454 个旧文件、B 自身证据及 A 的 286 个导出原始文件再次匹配。A 通过环境与最初失败轮全部保留。

### 9.2 已确认的近因、执行器缺口与未知项

1. 健康探针使用 `ProxyHandler({})`，访问字面回环 IP；原调用栈在 `socket.connect` 抛出 **WinError 10013 / WSAEACCES（访问权限拒绝）**，没有取得 HTTP 响应。这不是 FastAPI 的 4xx/5xx、模型鉴权、DNS、TLS 或第 8 节 pip 代理错误。错误语义见 [Microsoft Winsock](https://learn.microsoft.com/en-us/windows/win32/winsock/windows-sockets-error-codes-2)。
2. 服务与探针均以工具默认 `use_default` 启动；该执行上下文声明网络受限。就诊断方向而言，**工具沙箱的 socket 权限限制是优先假设**。但没有当时的策略命中日志或进程栈，不能把它写成已独立证明的唯一根因，也不能断言切换上下文必定成功。
3. Edge 显示 `ERR_CONNECTION_REFUSED`，服务未得到就绪证明；该浏览器结果与 Python 的权限错误是两个观察，不应混为同一错误码。PID 存在仅证明进程创建。原日志没有 Uvicorn 启动/应用异常记录，无法确定服务卡在解释器、事件循环、导入还是监听前的哪一步。
4. 本地 Uvicorn 0.32.1 在 `asyncio.run` 及配置加载后才输出 `Started server process`；Python 3.9 的事件循环初始化含本地 `socketpair`。因此空日志不能排除启动早期的本机 socket 受限；**这只是源码可达路径，不是本轮栈定位**，不据此替换事件循环、修改依赖或应用。
5. 原执行器在 `ReadToEndAsync()` 后等待进程退出，最后才写日志，缺少运行中的证据；其 `WaitForExit(3600000)` 也不能落实方案要求的 **60 秒就绪截止**。原启动到终止约 207 秒是收尾前的进程存活时间，不是 60 秒内就绪通过。两路读取已异步，不能无证据声称 stdout/stderr 管道死锁。流语义见 [Process.StandardOutput](https://learn.microsoft.com/en-us/dotnet/api/system.diagnostics.process.standardoutput?view=net-9.0)。
6. 默认受限上下文的进程/端口枚举曾不可见或报权限错误；`Get-NetTCPConnection -ErrorAction SilentlyContinue` 的空输出不足以证明端口空闲。收尾在获准的工具沙箱外只读确认归属后结束了本轮两层 Python，最终监听数为 0。另发现将自动解析的 JSON DateTime 再 `DateTime.Parse` 会丢失时区/精度；后续应使用原始 UTC 字符串或 ticks 比较，不能放宽 PID 归属检查来迁就解析错误。

结论：已确认健康探针的传输层权限拒绝，以及就绪、实时日志和观察权限不一致的执行控制缺口；服务未就绪的精确根因仍未知。当前没有依据修复业务代码、代理、Key 或 Windows 防火墙。

### 9.3 最小修复提案（尚未实施）

- **仅本轮工具执行上下文**：后续获审并授权后，为服务启动器、回环健康检查及所属进程/监听观察申请 `require_escalated`，由工具审批决定是否允许在工具沙箱外执行。这里不是要求应用以 Windows 管理员运行，不改系统安全策略、AppContainer loopback 豁免、代理、防火墙或全局环境；审批拒绝即停止，不自动换工具/地址绕过。操作仅限本机 `127.0.0.1:8000`，不宣称沙箱外工具具有按目标强制限流能力。
- **保留被验收对象**：仍使用 A 已通过的同一解释器及 `src-host`，不重装、不导入额外诊断包，不改应用/requirements/事件循环/绑定地址/端口。新执行器只放未来新轮忽略目录，不编辑原 A/B 驱动。Python 可加 `-B -u` 关闭字节码和输出缓冲；不改变服务业务路径。
- **证据先于等待**：在新轮以流式方式将 stdout/stderr 分别复制到仅新建日志并及时 flush，记录启动、实际运行 PID、UTC 启动时间、端口观察、HTTP 开始/结束和停止事件；不等退出才落盘。不抓内存/凭证，不输出完整环境或未知进程参数。
- **监督与归属**：启动器必须有独立 60 秒就绪监督；只读监听观察须成功且属于本轮已验证进程树，再做健康检查。未知进程占端口、观察被拒绝、子进程提前退出均立即停。UTC 用未自动转换的原文或统一 ticks 比较；运行中父子关系、路径、时间与命令同时核对，不能仅按端口或进程名杀进程。
- **HTTP 不借代理**：探针保持显式无代理，只访问固定回环 health/status，拒绝重定向；单请求至多 10 秒且不越过 60 秒就绪总期限。等待监听是有界只读状态轮询，不是反复启动或重复发 HTTP；health/status 各一次，权限拒绝、超时、非预期状态/正文即停，不重试。

不通过 `no_proxy=*`、关闭证书验证、改 Windows 配置、换端口、提升应用权限、重装包或复用原失败轮来“修好”检查。上下文切换及新执行器也只是待验证的最小提案，不写成已恢复。

### 9.4 待授权的单轮范围：仅 B01

新 head 获 Cursor 补审，用户再明确授权上述工具上下文及**仅 B01 的一轮**之后，才执行：

1. 重核候选 `3005819` 完整 SHA、方案新 head、A 的通过结果/实装环境及 286 个原始文件；确认 PR 相对候选仍仅文档。为全部现有输出、原失败轮、A 和 B 建立新清单。创建新的、拒绝复用的 run_id / TEMP / 默认模式 SQLite 目标 / 日志 / 结果；旧文件无一覆写，A 不再运行。
2. 只读完成归属/端口检查；按 9.3 在获工具批准的上下文隐藏启动**一次默认服务**。登记默认服务一次性调用标记；进程失败、脚本再次调用或中断均不得重开。没有 fake 启动、B09 重启或 B10 故障注入。
3. 60 秒内取得所属监听和 health/status 的预期 200/disabled，并确认此时没有 SQLite/索引。若失败，立即按核对后的 PID 树停止并保留逐步日志；禁止重复探针、另开进程或自动回退权限重跑。
4. 只有就绪通过才用本机已装 Edge/Chrome 的独立会话访问页面一次，确认默认辅助控件禁用、批次上传控件可用、页面不发 evidence POST；记录浏览器版本、实际视口、同源请求元数据与只含合成演示的截图。视口/工具能力不足按 BLOCKED，不用模拟 DOM 顶替；浏览器首个异常即停，不刷新碰运气。**不上传 PDF、不创建批次、不执行 B02–B10**。
5. 正常或异常结束都仅停止本轮拥有的进程和窗口，确认端口释放；保留新证据，逐一比较全部旧文件与原始源码。记录 B01 PASS/FAIL/BLOCKED；通过也只表示本次 B01，不表示 B 或整体验收完成。结果统一在 #40 交 Cursor 复核。

单轮 B01 的批准不恢复剩余 B、C、Ready/合入、发布、付费调用或系统变更。以上保留 `95107fb` 时点的诊断与待审提案，后续获审/授权的实际执行见第 10 节；B02–B10 仍为 NOT_RUN，所有模型/Embedding 新尝试和预算均为 **0**。

## 10. 人工 B01 三时钟、最新证据与后续停止点

### 10.1 已审人工协议

[人工修订方案](https://github.com/yjy18312446491/agent_development/pull/40#issuecomment-7078145195)已获 [Cursor APPROVED](https://github.com/yjy18312446491/agent_development/pull/40#issuecomment-7078293447)。仅在用户明确授权的新 run_id 使用；自动化工具 U0 不是人工方案前置。用户提前准备专用 Edge InPrivate/about:blank、Network/Console 和 1366×900，Codex 不通过整桌面截图检查配置。

| 时段 | 起点及上限 | 结束与失败处理 |
| --- | --- | --- |
| 服务就绪 | 唯一一次默认服务启动起 60 秒 | 所属进程树/监听验证后，显式无代理且拒绝重定向的 health/status 各一次通过；失败立即收尾，不重开 |
| 人工等待 | 写 READY 后向当前会话发“现在在准备好的 Edge 地址栏访问一次”信号，信号起最多 10 分钟 | 保持本轮管理进程，收到用户已观察/失败证据或超时即收尾；信号无法确认送达时按 READY 起最多 10 分钟记 BLOCKED |
| 页面观察 | 用户实际导航起最多 60 秒 | 只观察默认页面及原请求，不上传/查询；异常即停止，随后可以整理既有证据，但不得产生新的应用请求 |

工具调用返回、暂无输出不结束服务，也不触发第二次启动；就绪成功后解除就绪守护，不能将 60/120 秒套到人工等待。服务由本轮管理进程保持，超时、退出或中断均不重开。只停止经 PID/创建时间/命令与解释器白名单核对的本轮进程，不关闭用户浏览器。Windows venv launcher 及 pyvenv.cfg 指向的基础 python.exe 可构成合法父子链，不能仅按路径字符串误杀。

### 10.2 2026-11-22 已执行结果（不自动升格为 PASS）

冻结源码仍 `300581980ef2f1da4e9a7d96ecc62aea79bd7b9c`；执行时方案 head `95107fb5e7d5e270a8c863e2afbf5388f13a8024`。本轮目录 `out/acceptance/manual-b01-edge-691f021355ee48f1bfccad4297967eba/`，A 原环境未重装。默认 Mock 启动一次、6.328 秒就绪，health/status 探针各一次 200、status 为 disabled，无 SQLite/索引。

打开信号 20:02:07、人工截止 20:12:07；用户证据 20:08:03 收到，20:08:10 服务收尾（均北京时间）。所属 launcher 已停止、8000 无监听；8453 个旧输出文件与 286 个原始文件 SHA 不变。用户后续只清除既有 Network 筛选、展开时间范围并提供原记录，没有重开服务。三条 Network 为主页 document 200/4 ms、status fetch 200/4 ms、favicon.ico 404/4 ms；原服务日志确认主路径 GET，无 POST，另有 DevTools 探测路径 404。

证据索引与 [Network 补充](https://github.com/yjy18312446491/agent_development/pull/40#issuecomment-7078983054)已获评论 7079165948 只读复核。原 result 为 PENDING_INDEPENDENT_REVIEW，不改写。后续在 `user-screenshots/` 新增三张已有附件及 SHA 清单，已获评论 7079563852 独立目视补核，确认 B01 第 4 节表内页面断言；不将此扩大为整段 B 通过，不把截图或原日志提交 Git。

Cursor 确认 favicon/DevTools 两条 404 不阻塞 B01 表断言，但 Console 不能写“零错误”，整段 B 的该条件尚未满足。工具栏 1366×900、用户截图可见控件不等于独立测量 CSS 视口/InPrivate 标记/60 秒观察时长；这些限制保持。B01/整段 B 不自动记 PASS，不为这两条 404 改冻结应用或再开服务。

### 10.3 下一步范围

本小节保留 `394e67e` 启动前的后续范围时点；后续实际执行与当前停止点见 10.4，不将历史措辞当作恢复已结束轮次的授权。

本轮 `user-screenshots/page-console.png`、`disabled-advisory.png`、`network-all.png` 及 `manifest.json` 已获 Cursor 评论 7079563852 核对 SHA/字节并独立目视；B01 表断言已确认。保留 CSS 视口/InPrivate/观察时长与 Console 404 限制，不改原 result、不重开服务或重拍。

B02–B10 仍以第 4 节矩阵为后续候选范围：包含合成 PDF 上传/落库/回查、默认与显式假向量两种模式、视口/键盘、假索引重启与仅本轮新索引的故障演练。用户需另行明确授权所选范围及启动/重启/故障操作，再按新 run_id 执行；本次文档更新不是执行授权，不把 B01 的一次启动额度搬到下一轮。C 单独选择目标环境并授权，不安装 Docker/WSL 或复用旧 CI 结果。

没有新的模型调用、真实 Key 读取或历史预算复用。现阶段不执行 B02–B10/C、不 Ready/合入；若未来修复控制台 404，应另分支审查并重新冻结受影响验收，不移植本轮结果。

### 10.4 后续 M1 及 B03-only 的实际执行时点

[Cursor 评论 7080928381](https://github.com/yjy18312446491/agent_development/pull/40#issuecomment-7080928381)复核后续 M1：B02 上传结果与 SQL 一致，B04 默认脚本 7/7、日志 2×422+5×503、库未新增；B03 只有查询而无上传后新 document，仍 NOT_VERIFIED。用户脚本在截止前完成，但阶段完成信号未在时限内登记，原 result 保持 BLOCKED，不改写为 PASS；不重做已取得证据的上传/负例。

随后用户授权只补 B03。新轮 `manual-b03-0280e894ef1f49d9840c529bdea5326d` 原样复制已停止 M1 的数据库，原库/副本 SHA 一致；只启动一次。执行器阶段允许 B03，但实际分发仍判断 M1，误落到固定假向量入口。health=200、status 为 enabled/fixed-fake-vectors，disabled 断言拦截后立即停止；没有 READY 信号、用户页面访问、批次请求或 B03 浏览器操作。这是执行器错误，不能归因冻结应用或记录为真实模型调用。

23:07:16（北京时间）已完成收尾，所属进程停止、8000 收尾无监听；原库及副本 SHA 不变，8817 个旧文件、A 的 286 个原始文件、新运行源码 143 个文件未变化。误生成的 904 字节假索引仅在本轮新 `src-runtime/out/vector-index/`，与失败执行器和结果一起保留，不删除或用于后续阶段。原 B03 result 保持 BLOCKED/B03 NOT_RUN，未执行 M2/C/合入。

修复候选仅位于忽略目录 `out/acceptance-plans/b03-default-only-0ac6e30e410846119fda6ce9c90d0f1d/`：B03 专用默认入口无假模式回退，父执行器核对入口模块/目标及无索引；新轮绑定最终获审 head/review，不沿用过时文档 head。13 项离线 mock 检查覆盖默认入口、错误阶段和路径/标记/预算拒绝，拦截 runpy、网络与子进程；实际服务/网络/应用导入均为 0。它不等于 B03 运行验收通过。修复候选、失败结果与下一次单轮重试仍待 Cursor 核对及适用用户授权，当前不自动重开。

### 10.5 后续 B03–B07 分项复核（2026-11-23）

B03 后续新批次上传、刷新及同 ID 回查已获 [7082049974](https://github.com/yjy18312446491/agent_development/pull/40#issuecomment-7082049974)接受；它与原定只读旧 ID 的操作协议存在偏差，原失败及 BLOCKED 结果保留。B02/B04 默认部分沿用 [7080928381](https://github.com/yjy18312446491/agent_development/pull/40#issuecomment-7080928381)，不重复请求。

B05、B06 四样例及 B07 已获 [7091996576](https://github.com/yjy18312446491/agent_development/pull/40#issuecomment-7091996576)复核，额外三次查询保留；辅助参考排序不改变 S001 的 JD1/B/60/project 0/mock。B04 启用态五次固定 422、B06 前端 JSON 及切样例清空，已获 [7099280563](https://github.com/yjy18312446491/agent_development/pull/40#issuecomment-7099280563)确认。原 M2S 超时结果不改写为 PASS。

### 10.6 B08 功能矩阵收口与证据限制（2026-11-23）

窄屏实际 390×844、表宽 325/610、横向滚动最大约 285.33；加载时两控件 disabled=true，完成后恢复 false，沿用 7099280563。宽屏实际 1366×848、Tab/Enter、精确阻止一次 evidence 后的失败隔离及规则清除，沿用 [7102101101](https://github.com/yjy18312446491/agent_development/pull/40#issuecomment-7102101101)。阻止证据来自 DevTools 提示、规则受影响 1 次及服务端无该 POST；Network 截图的状态列不能独立读作非 200。规则列表为空只证明已移除，不代表移除后已重试成功。

方向键原始导出在截断前保存三组完整 trusted ArrowDown keydown→change→keyup：backend→junior→frontend→other；repeat=false。用户确认重复按键次数未控制且额外查询是主动试用，已接受协议偏差。补充视频显示关闭的原生选择框双向切换及说明更新；它是窗口结束后的演示，不能回填原始键事件。[Cursor 7103665591](https://github.com/yjy18312446491/agent_development/pull/40#issuecomment-7103665591)确认方向键功能项收口，B08 不再缺此项，不重开已通过浏览器项目。

保留限制：原始报告 60 states、truncated=true、丢弃 12 个键事件；保留的 ArrowUp keydown=0，12 个 keyup 都为 ArrowDown。不能把后续 change 倒推成 ArrowUp。finish=other 与后截图 junior/三条结果不一致；服务端实际一次 POST evidence=200，不能沿用原计划零查询作为实际数。没有 POST batches/GET batches。原 Down/Down/Up+零查询协议未精确执行，但用户及 Cursor 接受功能结论，无需重测。原超时、BLOCKED 和 PENDING_INDEPENDENT_REVIEW 文件均不改写；原始报告、偏差审计、用户说明、视频及 SHA 在 PR #40 保留。B08 收口不代表整段 B、B09/B10/C 或合入通过。

## 11. B09 同一假索引重启核对方案（首轮 11.1，修复新轮 11.2）

只复用 A 已验收解释器和冻结源码 `3005819`，在全新忽略目录中原样复制已停止 M2 的合成数据库及索引；原件不启动、不改写。复制的索引为 `jobs-bf2db11ce9156920cbb44255fc8bedb586a29a1ad901a466dec6bb45a9c9e6d7.json`，SHA-256 `d9fd41b9f21772a67bf2c8c715820e27d3f70231a6d2f034591e44b58296fc02`。两阶段始终使用同一副本、路径、文件标识及 mtime，不在中间重新复制或生成索引；数据库只读校验两条合成记录不变。

执行额度明确为首次启动加一次计划内重启，最多两次进程启动，执行器只调用一轮；任一失败即停止，不补次数、不借旧锁、不自动恢复。启动前绑定本轮具体执行器 SHA、新文档 head、Cursor 新范围复审及用户明确开启服务的指令。当前仅准备不写启动授权、不占用端口。每次只允许 127.0.0.1:8000，60 秒就绪上限，拥有的进程身份/模块来源/解释器及原件完整性都须核对。

第一次就绪通知送达后开始单一 600 秒人工窗口，包含计划重启耗时，不因第二次 READY 重置。用户在独立新页执行准备好的首次探针，同源 POST `/advisory/evidence` 一次，固定 `demo_frontend/top_k=3`；看到 OK 后只回文字“B09首次查询OK”。执行端核对一次 200 和索引未变，停止所属进程，再按已批准计划启动一次同路径服务。通知第二次就绪后，用户在同页（不刷新）执行第二次探针，再 POST 同一请求一次，对比两次实际响应体 SHA 和结构，回“B09完成”；执行端先停止服务，再收一次合并导出，避免上传占用窗口。

每阶段应用就绪探针 health/status 各一次；浏览器 evidence 共两次、零重试，不上传 PDF、不查询批次、不重测 B01–B08、不设置阻止规则。对比必须同时证明索引路径/代/SHA/文件标识/mtime 不变、查询内容相同、原数据库/所有旧现场/A/冻结源码完整。单靠两次 READY 或用户完成文字不能写 PASS；原始结果保留待独立复审。B10 的损坏索引测试、C、真实模型、Ready 和合入均不在本轮范围。

### 11.1 首次就绪后管理器异常停止（2026-11-23 执行，11-24 核对）

[Cursor 7106087706](https://github.com/yjy18312446491/agent_development/pull/40#issuecomment-7106087706)允许在用户明确开启后按候选运行。用户随后授权；`manual-b09-3dcc9ee5d2d34685b9717d5264b394f5/B09` 于北京时间 23:54:38 启动、23:54:42 就绪，health/status 各一次 200、fixed-fake-vectors。23:55:16 管理器读到 open-signal 后，把包含 utc 的完整信号展开传给自身已含 utc 参数的事件构造器，触发 TypeError；23:55:16.783 停止所属服务，23:55:26.922 完成收尾。原定 11-24 00:05:16 截止并未触发，不能归因用户超时或冻结应用。

本轮只有上述两条就绪 GET，没有浏览器主页/evidence/批次请求；首次查询与计划重启未执行。原 result 保持 BLOCKED/TypeError、actual_starts=1，账本 invocation=1、start_count=1、terminal_stop=true；保留 before=STARTING 的原始状态及实际 READY 文件，不改写旧结果或借剩余次数重开。8000 无监听，原件/副本 DB 与索引不变，11177 旧文件、A286 源码/A15 证据、新源码143 的收尾 SHA 均未变化。

修复只在新忽略目录准备：开放信号以独立字段记录，不再展开覆盖事件时间；补测真实 wait_user→信号记录→两阶段完成路径，而非仅模拟协调器。通知用户操作前，还须确认开放信号已被记录且管理器仍在运行。旧失败现场不改，修复候选及新一轮须补审和适用用户启动授权；当前不自动再开、不执行 B10/C/真实模型/合入。

### 11.2 B09 修复新轮实际结果（2026-11-24，已独立复审）

执行时方案 head 为 `ba82454c6beafa35c1a3b0658c79fa31639ba59a`，冻结应用仍 `3005819`；按 Cursor 7106983987 与用户已记录并再次确认的授权，在全新 `manual-b09-repair-5de57889769b40b7850f1361534132ff/B09` 执行。首次 00:24:12.367 启动、00:24:16.892 就绪；00:24:44.637 开放信号、00:24:44.742 确认处理，00:25:27 核对所属进程/监听及管理器仍存活后才通知操作。人工窗口仅此一个 600 秒，截止 00:34:44.637。

首次查询 00:28:03.246–.253 返回 200，收到确认后 00:28:58.459 停止第一进程，00:28:58.653 按计划启动第二进程，00:29:03.001 就绪；通知前再次核对存活。第二次查询 00:30:24.853–.860 返回 200，00:31:08.624 收到完成确认，00:31:09.294 停服务、00:31:18.237 收尾。完成回执距开放 383.988 秒，收尾 393.600 秒；两次启动共一轮，没有延长窗口或失败重试。

两段各恰好一次 POST `/advisory/evidence` 200，均固定 `demo_frontend/top_k=3`；响应原文 SHA 均为 `06bf5f8e4c667277f82f8d07ea9b8f462815d8bcfde1c00ce10172a5cbbfa7d9`，重新计算相符，原文与结构完全相同。顺序 JD3/JD2/JD1，advisory=true、fixed-fake-vectors、NOT_ESTABLISHED；这是假向量一致性，不是语义质量通过。只有每进程 health/status 就绪探针及首次页面/status，另一次 DevTools 自动路径 404 单独保留；无上传或批次请求，不称控制台零错误。

同一副本索引的路径、文件 ID `281474978039483`、mtime_ns `1795408681214819700`、904 字节及 SHA `d9fd41b9f21772a67bf2c8c715820e27d3f70231a6d2f034591e44b58296fc02` 在七个检查点全相同，中间未重新复制或生成索引。原件/副本 DB SHA `e6ca9af908984cfc71c4a42cd701b89ca9b246da0610efdebb388255a44b104a`、两条合成记录不变。收尾及导出后复核 11360 旧文件、A286 源码/A15 证据、143 新源码均零变化，15 个获审执行器文件也未改，8000 已释放。

导出来源限制单列：保存收到的聊天文本 UTF-8 原文，不称原剪贴板字节。它在嵌入 JSON 字符串中有多余转义，直接 JSON 解析在位置 103 失败；仅在派生副本中对 138 个双反斜杠引号及 6 个三反斜杠字母 n 去除一层，不插入字段或值，两个响应 SHA 均重算吻合。原文 SHA `45fb7cc5c1c5ab76d5079ddaa2b57540ad931dd13cde9b93b8d8f607209c4576`，派生 SHA `b638b26dff9de2136811a6b9cfb19e988f3df17dd8660883008e271c6c604589`。Python 3.9 原校验器又因浏览器 Z 时间戳报 ValueError；用独立离线兼容层仅在解析时将 Z 视作等价 +00:00，3 项兼容断言通过，报告值和获审文件未改。规范化、来源说明和审计均在本轮忽略目录，不追加服务或请求。

运行原 result 仍为 `PENDING_BROWSER_EXPORT_AND_INDEPENDENT_REVIEW`，结束时的 AWAITING_COMBINED_BROWSER_REPORT 字段保留为历史，不表示当前仍缺导出；后收导出单独存审计，不自动改写 PASS。[Cursor 7107685412](https://github.com/yjy18312446491/agent_development/pull/40#issuecomment-7107685412)已核对实际证据、原文转义派生、两次摘要及七个索引检查点，确认 B09 可以收口，无需补测。复审同时指出 PR 评论把 mtime_ns 末两位写成 9600，而文件与本节为 9700；以原文件整数 `1795408681214819700` 为准，后续跨 JavaScript 传递大整数用十进制字符串，不为展示精度差异重测。原首轮 BLOCKED/终止账本及全部旧锁不改；整段 B/B10/C、Ready、合入不随 B09 通过。

## 12. B10 损坏索引拒绝启动方案（首轮实际结果见 12.1）

这是一次预期失败的启动验证，不是打开可用网页。以下保留执行前方案，后续实际授权及首轮结果见 12.1。用户需明确授权本项“备份并损坏新副本＋仅一次启动尝试”，具体候选须经 Cursor 补审。准备时创建全新忽略目录、导出冻结源码 `3005819`、复用 A 已验收解释器，并从已停止 M2 的合成原件复制数据库及唯一假索引；准备独立有效备份，原件、B09 及其他旧轮次不启动、不损坏、不改写。准备阶段保留新副本有效，不做故障注入。

获审和用户明确要求后，一轮执行器调用只允许一次新索引损坏和一次启动尝试。先核对全体旧文件/A证据/冻结源码、解释器、目录及无监听，再保留新备份，将本轮 `src-runtime/out/vector-index/` 中唯一指定文件原地写入固定非法 JSON 字节；仅此新文件可变。新账本与独占标记先预留额度，错误或超时均终止，不重开、不恢复旧账本、不重新生成索引、不先启动正常服务。

子进程只运行本轮导出源码的显式 `app.advisory_demo`，使用清空后白名单环境及本轮 SCREEN_DB_PATH；核对 app/向量模块来源、解释器和父子进程归属。预期在创建固定假向量应用时读取损坏索引失败，由冻结入口自身退出：退出码 1、固定提示 `Fixed fake-vector demo unavailable`，并有真实入口抛出 SystemExit 的来源记录，不能把执行器预检查失败、导入失败或任意非零退出算作通过。

启动尝试最多 60 秒，观察所属进程和 8000 端口，但不发送 health/status/evidence 或任何 HTTP；子进程从导入应用前记录 Python 网络审计事件，结合端口采样、固定入口退出来源及日志核对没有绑定/连接/请求。任何监听、网络事件、成功启动、自动修复、额外代或非预期退出都停止并记录 BLOCKED，只清理本轮拥有的存活进程，不结束外来进程。观察次数和覆盖限制写入实际报告，不把端口采样单独当作全程证明。

自然退出后核对损坏文件路径/文件 ID/mtime/大小/SHA 仍等于注入后值、目录无新代；有效备份、原件、数据库、所有旧输出/A/冻结源码不变。损坏件和有效备份都保留，不自动还原。只有这些条件一起成立才记录“预期失败证据待独立复审”，不自行写整段 B PASS。没有浏览器步骤或人工 600 秒窗口；用户不访问页面、不运行控制台代码，执行端自动收尾再把结果交 Cursor。该准备时点 B10 尚未执行，后续见 12.1；C/Ready/合入及真实模型请求仍未执行，启动权限不由先前 B09 授权继承。

### 12.1 B10 首轮进程归属守卫阻断（2026-11-24，待独立复审）

执行方案 head `2c706b3e6e82c87f9e08c55e28fd568c516a0980`、冻结应用 `3005819`，9 个获审候选文件 SHA 未变；以 Cursor 最新预审 7121557954 和用户已记录并再次确认的授权，在 `manual-b10-4c0259661e5846a78076c50b0da4aa01/B10` 执行。北京时间 11:59:52.673 记录新副本损坏、11:59:52.919 启动；11:59:55.332 管理器以 `Owned image/command mismatch` 阻断，11:59:55.971 清理所属进程，12:00:07.524 收尾。实际 invocation/damage/start 都是 1/1，terminal_stop=true，无重试或额度复活。

应用来源记录已写入，受管 Python 子进程指向本轮 app/向量模块及 A 解释器，父 PID 与启动器绑定；网络/凭证审计文件存在且为空，stdout/stderr 均空。但没有 `entry-exit.json`，没有应用自然退出码和固定提示。进程快照函数在保存 `owned-processes.json` 前就对所见树的映像/命令进行复合断言，失败快照未保留，无法确定具体 PID、映像或哪一条件不符，不能推断是外来进程、某个系统工具、冻结应用错误或已证明的误报。两条端口采样均空，另有收尾无监听；没有完整预期退出观察链，不能用这些有限信息补成 B10 PASS。

原 result 保持 BLOCKED，stop_reason 保持原文、exit_code 未取得；管理器执行清理，不称应用自行拒绝启动。启动器及已记录应用 PID 后续只读复查均已不存在，8000 无监听；没有 HTTP、真实模型或 C/合入操作。原损坏件保留 20 字节，SHA `9dc527e81bb458c161ab29cb19e4be39e3643ca83e117ac6365326a7a2f97d6c`，文件 ID `281474978040073` 与准备时一致，mtime_ns `1795492792633361700` 在注入后和收尾一致；独立有效备份仍 904 字节/SHA `d9fd41b9f21772a67bf2c8c715820e27d3f70231a6d2f034591e44b58296fc02`，原件/副本 DB 两行不变，未自动还原或生成新代。

收尾实际核对 11575 旧文件、A286 源码/A15 证据、143 新冻结源码零变化。失败结果、事件、空日志、原件/备份/损坏件及终止账本全部保留；不在原候选改脚本、放宽守卫或重新执行。先由 Cursor 核对实际阻断与证据缺口，再确定修复或补测范围；如需后续修复，应先保存失败前观察快照、区分具体失败条件并覆盖真实观察分支，不能用模拟总流程代替进程观察回归。当前 B10、整段 B、C、Ready、合入均未通过。

### 12.2 B10 进程观察修复候选（仅准备，未执行）

Cursor 7121802341 确认原管理器对整棵树要求同一命令片段且在断言后才保存快照，提出五项修复。原失败行未留下，不能事后补出其真实映像/命令；新修复针对已确定的校验设计和证据保存问题，不声称定位了原行的具体内容。只在全新忽略目录导出同一冻结源码、复制有效原件/独立备份，原 B10 的 20B 损坏件、904B 备份、BLOCKED/锁/账本均不动。

新候选在解析或断言前写独占编号原始 CIM 快照，再另存校验结果和具体失败 PID/字段/原因。CIM list、单进程 object 及退出后的空观察分别处理；空 ExecutablePath 显式拒绝用于解释器认证，不把空字符串解析为工作目录。启动器绑定存活 Popen 及其创建时间，实际运行解释器绑定本轮 app-origin 的 PID/父 PID、白名单映像、包装器 SHA 和初始 argv；其他后代如 conhost 只保存为未用于解释器认证的观察，不要求它们带 Python 脚本命令。

实际解释器命令使用 Windows CommandLineToArgvW 解析引号与路径，允许 argv0 是已允许的 venv 或 Python39 解释器，核对目标脚本和 B10 阶段，不再匹配未经引号处理的整段文本。纯辅助进程不参与脚本目标认证；未知/错误 Python 映像、错误目标、缺少实际解释器字段、父子关系或创建时间变化仍阻断，并保留原始快照。极快自然退出时明确记录 CIM 未捕获的限制，必须用独占本轮包装器来源、初始 argv/解释器/父子绑定及实际冻结入口 SystemExit 证据交叉核对，不能只凭缺进程或非零退出通过。

新增离线回归直接调用真实 Attempt.snapshot/纯观察校验及 Windows 参数解析，覆盖多进程 list、单对象、引号/空格路径、Python39 argv0、辅助后代、空映像、错脚本/阶段、创建时间、断言前文件已保存等；不再只靠替换 Attempt 的总流程测试。所有输入是明确标注的合成观察，不回填原轮丢失快照，不启动服务或读取真实进程命令。原9个获审文件不改；新候选完整自查后在原PR补审，用户继续推进的指令保留，补审前没有新轮执行。

未来新轮仍只允许一次新副本损坏、一次显式假入口尝试、60秒上限、无HTTP/真实模型/C/合入，异常即停；固定应用自然 SystemExit/1/固定提示、空网络凭证审计、无监听、无修复/新代、备份/原件/旧文件完整的原通过条件保持。当前 B10 仍 BLOCKED，修复候选仅准备，不能当作实际故障路径已通过。
