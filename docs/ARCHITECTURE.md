# 架构说明

当前目标是把已有 Coze 原型中的业务链路迁移成可测试的 Python 应用。

```text
extract -> guard -> route -> score -> verify -> finalize
```

- `extract` 负责 PDF 文本提取和基础字段抽取。
- `guard` 接收 `ExtractResult`，负责文本质量和异常状态。空文件、读取失败、疑似图片或损坏、过短文本分别进入人工复核，复核结果使用 `score=-1`。通过闸门的结果保留 `score=None`，交给后续评分模块。
- `route` 负责岗位方向与年限契合度。
- `score` 使用规则计算可枚举维度，并调用 LLM 判断项目语义。
- `verify` 校验子分范围、总分一致性和结构化输出。
- `finalize` 输出可落库、可评测的结果。
