# 简历岗位智能匹配 Agent

这是一个面向招聘初筛的 AI 应用项目。系统接收 PDF 简历，解析候选人信息，匹配岗位 JD，进行可解释评分，并将异常结果交给人工复核。

## 当前状态

- Coze 工作流原型已跑通。
- Python 版已有离线规则引擎、LangGraph 编排、Mock Scorer、SQLite/CSV 输出和规则测试。
- 真实 LLM、FastAPI、向量数据库、Docker 和公开 Demo 仍在建设中。
- 当前评测指标来自离线 Mock Scorer，只作为基线。

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

## 下一步

1. 完成 Python 版真实模型链路。
2. 接入向量检索和评测记录。
3. 用 FastAPI 提供单份与批量接口。
4. 增加 Docker Compose 和最小 Demo。
