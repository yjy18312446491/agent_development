# 合成数据集

这里的 PDF、岗位文本和标注全部使用虚构内容，供抽取层和文本质量闸门回归测试。

PDF 夹具使用英文正文，保证 PyMuPDF 在干净环境中不依赖本机中文字体也能稳定提取文字。中文姓名、学历、年限和技能画像使用同目录的 `.txt` 源文件回归，manifest 中的 `profile_text_file` 指向对应源文件。PDF 测试只验收 `extract_status`、`guard_status` 和 `reason_code`。

## 目录

- `resumes/` 包含正常、空文件、图片型、损坏、过短、教育经历、项目经历和混合经历样本。
- `resumes/project_snippets/` 包含中英文切段文本、5 份短英文 PDF 和 1 份长英文 PDF。PDF 使用 PyMuPDF 和内置 Helvetica 11 号字，标题独立成行。短 PDF 验证切段边界，`pdf_screen_jd1.pdf` 用于 `tests/test_screen_snippet_pdf.py`，验证通过闸门后路由至 JD1 和注入项目评分。这些夹具独立于 Mock PDF 的 13 个评测场景。
- `jobs/` 包含三个合成岗位文本和带稳定 `jd_id`、年限范围、技能列表的 `jobs.json`，供 route 和后续 score 阶段使用。
- `manifest.jsonl` 保存每个样本的预期抽取状态、guard 状态和画像字段。

## 规则

样本不包含真实候选人姓名、联系方式、公司、账号或平台导出数据。测试使用相对仓库路径读取样本。
