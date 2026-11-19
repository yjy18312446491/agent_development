# LangGraph 与辅助检索合入后的容器复验

2026-11-19 在 GitHub 托管 Ubuntu 运行器完成 Linux Docker Compose 复验。应用源码固定为 main 的 `6ad54cb009a64ecb5b51281a4e5ff0e803379332`，其中包含 PR #26 的图编排和 PR #27 的辅助检索。当前文档提交不改变应用、依赖或容器配置。

## 可追溯证据

| 项目 | 实际值 |
| --- | --- |
| PR #27 合并提交 | `a23d552f1603216d1c057b88139b2561fad8fd98` |
| 合并双亲 | `2e69677`、`a4b1ca1`；合并树与 `a4b1ca1` 相同，保留 `c0a103b` |
| 应用验证源码 | `6ad54cb009a64ecb5b51281a4e5ff0e803379332` |
| 独立 CI 分支 | `ci/langgraph-runtime-verification` |
| CI 提交 | `6367e2fa9caa62e54ba2ffa3421e264884684989` |
| 工作流运行 | [44532474624，success](https://github.com/yjy18312446491/agent_development/actions/runs/44532474624) |
| Docker / Compose | Engine `28.0.4` / Compose `v2.38.2` |
| 合成批次 ID | `18f269e2e05a40c0aa6a17f3fad292b1` |
| 重建前容器 | `00ec4ff0c91e0865f33c6c2de63b551a7dee513dcaf094096ce3d8874b0e951f` |
| 重建后容器 | `927dd7ccd9dcb38f06dd06810563eae512ded7b3837f37e640b783ef49aa7089` |

运行使用公有仓库的标准 `ubuntu-24.04` 运行器，不使用大型运行器，不上传镜像、缓存或构建 artifacts。标准公有仓库运行器按 [GitHub 计费说明](https://docs.github.com/en/actions/concepts/billing-and-usage) 免费。本轮真实 DeepSeek 与 Embedding 请求均为 0。

## 已验证

- 原有 Dockerfile 与 Compose 配置成功构建并健康启动；无宿主机端口发布，服务继续监听容器内回环地址。
- 镜像内 `pip check` 通过，用户 UID 为 1000，`/data` 可写。健康检查返回 200 且没有提前创建数据库。
- 镜像包含合成岗位配置及 S001/S002 两份 PDF，不含 `.env`、`.git`、本机虚拟环境、测试目录或额外 PDF。pytest 未安装。
- LangGraph 配套版本与项目固定值一致：`langgraph 0.6.11`、`langchain-core 0.3.76`、`langsmith 0.4.37`、`langgraph-checkpoint 2.1.2`、`langgraph-prebuilt 0.6.5`、`langgraph-sdk 0.2.9`。HTTPX 因 SDK 的运行时依赖进入镜像，旧验证中“无 HTTPX”的条件不再适用。
- 容器内 `screen_one` 实际运行图编排，S001 保持 `JD1 / 60`，项目分为 0。
- 辅助检索缺省调用返回 `DISABLED`；显式注入固定假向量取得 `JD1/JD2/JD3` 参考证据，无真实 Embedding 调用。
- 默认批次 POST 上传 S001/S002：S001 为 `SCORED / JD1 / B / 60 / mock`，项目分 0；S002 为 `REVIEW / EMPTY / -1`，档位与五项分数为空。GET 回查与 POST 的记录内容、顺序相同。
- 非法文件名上传返回 422，SQLite 中仍只有原来一个批次和两条记录。
- `down` 后保留命名卷，重新 `up` 得到不同容器 ID；同批次 GET 回查内容与顺序保持一致。
- 同期宿主机全量 344 项通过、Mock PDF 13/13、冻结 8 份样本预览零请求。与容器联调的检查口径分别记录，未宣称镜像内运行了全量 pytest。

## 复现与边界

在有 Docker Engine 的机器检出应用源码 `6ad54cb`，按 README 的 Docker 命令构建、上传和重建回查。完整自动验证脚本保留在独立 CI 分支的 [.github/workflows/langgraph-runtime-verification.yml](https://github.com/yjy18312446491/agent_development/blob/6367e2fa9caa62e54ba2ffa3421e264884684989/.github/workflows/langgraph-runtime-verification.yml)，它按完整 SHA 拉取应用源码。该 CI 分支不合入本次文档 PR。

这是 Linux/Compose 的运行证据。本机未发现可用 Docker 命令，Windows Docker Desktop 仍待可选复验。真实 Embedding 客户端、语义召回效果和检索生产接线尚未完成，当前证据仅验证默认关闭与假向量接口。冻结 PDF、人工金标、v2 prompt、确定性 `jd_id`、评分与默认 Mock 路径保持原样。
