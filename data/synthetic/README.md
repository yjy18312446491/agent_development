# 合成数据集

这里的 PDF、岗位文本和标注全部使用虚构内容，供抽取层和文本质量闸门回归测试。

PDF 夹具使用英文正文，保证 PyMuPDF 在干净环境中不依赖本机中文字体也能稳定提取文字。中文姓名、学历、年限和技能画像使用同目录的 `.txt` 源文件回归，manifest 中的 `profile_text_file` 指向对应源文件。PDF 测试只验收 `extract_status`、`guard_status` 和 `reason_code`。

## 目录

- `resumes/` 包含正常、空文件、图片型、损坏、过短、教育经历、项目经历和混合经历样本。
- `resumes/project_snippets/` 包含中英文项目段提取专用的虚构文本。期望原文由 `tests/test_project_snippet.py` 维护，这些夹具独立于 Mock PDF 的 13 个评测场景。
- `jobs/` 包含三个合成岗位文本和带稳定 `jd_id`、年限范围、技能列表的 `jobs.json`，供 route 和后续 score 阶段使用。
- `manifest.jsonl` 保存每个样本的预期抽取状态、guard 状态和画像字段。

## 规则

样本不包含真实候选人姓名、联系方式、公司、账号或平台导出数据。测试使用相对仓库路径读取样本。
