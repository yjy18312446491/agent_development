# 项目状态

## 当前功能分支已实现

- PDF 文本和候选人字段抽取
- 文本质量闸门
- 基于合成 JD 的确定性岗位路由
- 合成 PDF 文件状态与中文画像文本的回归测试

## 待完成

- 规则与 Mock 项目评分、结果校验、统一筛选流程
- 当前 Python 版的端到端评测、LangGraph 编排及 SQLite/CSV 输出
- Embedding 与向量检索
- FastAPI API
- Docker Compose
- 真实模型评测
- Demo 页面

## 指标口径

旧 Python 版的 30 份评测使用离线 Mock Scorer。当前功能分支的验证范围是抽取、闸门和路由的回归测试。端到端评测从统一筛选流程实现后开始。
