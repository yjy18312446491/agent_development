# 项目开发与 GitHub 协作流程

当前协作契约以仓库根目录 [AGENTS.md](../AGENTS.md) 为准：Codex 连续完成常规工作、自查修复小问题并运行低风险离线回归；重要测试、重大问题处理和需授权的操作，先交用户与 Cursor 审核通过再执行。Cursor 审查任务为只读，过程记录进入 PR，不生成临时交接稿。下文历史工具分工、示例和合入节奏如与该契约冲突，遵从 AGENTS.md 及用户当前明确要求；自查或审核不替代操作授权。

本手册规定简历筛选 Agent 的日常开发方式。每次在功能分支完成代码、测试和审核。全部计划功能完成并通过整体测试后，再统一合并到 `main`。

## 工具分工

### Codex

Codex 负责理解需求、拆分任务、修改仓库代码、运行测试、检查 Git diff、提交和推送。涉及多个文件或需要保持架构一致的功能，优先由 Codex 完成。

### Cursor

Cursor 负责交互式编辑、快速定位代码、局部重构和补充测试。使用 Cursor 前先在终端切到当前功能分支。Cursor 工作期间不要让其他工具同时修改同一批文件。

### Grok

Grok 负责第二视角审查、错误假设检查、边界案例分析和方案比较。把 `git diff`、报错日志或脱敏后的设计片段交给 Grok 做只读审查，再由主开发者决定是否修改。

### VS Code 终端

VS Code 终端是运行测试和执行 Git 命令的地方。Codex 终端和 VS Code 终端可以任选一个作为当前终端，避免两个终端同时执行 `commit`、`push` 或分支切换。

## 每个功能的完整流程

### 1. 同步主分支

在 `agent开发` 目录执行：

```powershell
cd "E:\workbuddy\agent项目一\agent开发"
git status
git switch main
git pull --ff-only origin main
```

`git status` 有未处理修改时，先提交、暂存或明确处理这些修改，再开始新功能。

### 2. 创建功能分支

```powershell
git switch -c feat/pdf-extraction
```

常用前缀包括 `feat/` 新功能、`fix/` 修复、`test/` 测试、`docs/` 文档和 `refactor/` 重构。

### 3. 明确修改范围

开发前先写清楚输入、输出、失败状态和验收标准。默认目录如下：

- `app/` 应用代码
- `tests/` 测试
- `eval/` 评测脚本和报告
- `docs/` 设计和复盘
- `data/synthetic/` 合成数据

### 4. 选择一个主开发工具

选择 Codex 或 Cursor 作为主开发者。另一个工具只做只读审查。Grok 先看方案和 diff，等主开发者确认后再修改文件。

给 Cursor 或 Codex 的任务描述应包含目标、允许修改的路径、约束和验收标准。例如：

```text
目标：增加 PDF 文本提取模块
允许修改：app/extract.py、tests/test_extract.py、docs/ARCHITECTURE.md
约束：失败返回结构化状态，不抛出未处理异常，不使用绝对路径
验收：空文件、损坏文件和普通 PDF 都有测试
完成后：运行测试并报告修改文件、测试命令和结果
```

### 5. 运行本地检查

```powershell
python -m compileall app tests
python -m pytest
git diff --check
git diff --stat
git status
```

每个功能或修复验收后，按 [版本历史表](VERSION_HISTORY.md) 的八列格式记录问题、改法、回归测试和未解决项。同一个提交解决多个独立问题时分别补行，共用提交日期和短 SHA。版本记录随功能改动进入同一个 PR。

真实 LLM 调用需要单独记录模型、版本、温度、成本和失败情况。

### 6. 做第二轮审查

将以下提示交给 Grok 或 Cursor 的审查模式：

```text
请只审查，不直接修改文件。
目标功能：<一句话>
请检查：边界输入、异常处理、状态一致性、隐私数据、测试遗漏、是否违反 Agent.md。
请按严重程度列出问题，并引用文件路径和行号。
```

审查结果需要回到主开发工具中逐条核对。

### 7. 提交本地版本

确认测试通过后，只提交本次功能相关文件：

```powershell
git add app tests docs eval data/synthetic
git diff --cached --check
git commit -m "feat: add PDF text extraction"
```

一次提交保持一个主题。

### 8. 推送功能分支

```powershell
git push -u origin feat/pdf-extraction
```

推送前确认没有 `.env`、API Key、真实简历、个人联系方式、SQLite 数据库和临时日志。

### 9. 创建和审查 Pull Request

Pull Request 描述包含修改内容、验证命令和已知限制。审查重点是功能是否符合验收标准、错误状态是否可追溯、测试是否覆盖失败路径、是否引入敏感数据和是否破坏现有调用方。

### 10. 合并到 main

全部计划功能完成、各 PR 审核通过且整体测试通过后，按依赖顺序合并 Pull Request。合并完成后本地同步：

```powershell
git switch main
git pull --ff-only origin main
git branch -d feat/pdf-extraction
```

达到可演示节点时创建标签：

```powershell
git tag v0.1-pdf-extraction
git push origin v0.1-pdf-extraction
```

## 日常保存频率

Git 不需要每保存一次文件就提交。完成一个可解释的功能、修复一个问题、补完一组测试或完成一轮文档更新时提交一次。`git commit` 保存本地版本，`git push` 保存到 GitHub。

## 发生冲突时

先执行：

```powershell
git status
git diff
```

不要直接使用 `git reset --hard`。先保留当前修改，再决定是否暂存、提交、变基或合并。

## 本项目的默认合作方式

用户提出功能后，Codex 先读取 `Agent.md` 和相关代码，完成实现和测试。Cursor 适合局部编辑，Grok 适合第二轮审查，VS Code 终端负责验证 Git 和测试状态。功能完成后，Codex 负责提交、推送和更新文档。
