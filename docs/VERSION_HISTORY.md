# 版本历史

功能改动按问题分行。同一 commit 可以有多行。阶段列记录开发里程碑，短 SHA 用于 `git show` 查看历史。Git 发布标签在正式发布时建立。

| 日期 | 阶段 | commit | 模块 | 问题 | 改法 | 回归 | 未解决 |
|---|---|---|---|---|---|---|---|
| 2026-11-14 | v0.2-extract | `5833647` | extract | 没有可测试的 PDF 文本抽取 | 增加 `extract_pdf`、结构化状态和文本清洗 | `tests/test_extract.py` | - |
| 2026-11-14 | v0.2-extract | `2897ecb` | extract | PDF 入口类型注解在 Python 3.9 下不兼容 | 改用 `Union[str, Path]` | `compileall`、`tests.test_extract` | - |
| 2026-11-14 | v0.2-extract | `d14307f` | test | 未安装 PyMuPDF 时 PDF 集成测试会失败 | 缺少可选依赖时跳过真实 PDF 用例 | `tests/test_extract.py` | - |
| 2026-11-14 | v0.2-extract | `8ec4943` | extract | 还不能抽姓名、年限、学历和技能 | 增加候选人画像和规则抽取 | `tests/test_extract.py` | - |
| 2026-11-14 | v0.2-extract | `d2046b9` | extract | `Go` 会误命中 `Google`、`good` 等英文子串 | 增加英文技能边界匹配 | `test_skill_matching_does_not_match_substrings` | - |
| 2026-11-14 | v0.2-extract | `586be41` | extract | 教育日期被算进工龄 | 工作日期只在工作或实习语境中计算 | `test_education_dates_are_excluded_from_years` | B01、B05 的年限与父目录文件名标注不一致，待评测集整理 |
| 2026-11-14 | v0.2-extract | `586be41` | extract | 多段连续或重叠工作只取最长一段 | 按月份计算工作区间并集 | `test_contiguous_work_ranges_are_unioned` | - |
| 2026-11-14 | v0.2-extract | `586be41` | extract | 年制本科和出生年份被算成工作年限 | 排除 `年制`、`年生` 并过滤教育语境 | `test_education_years_and_work_years_are_distinguished`、`test_birth_year_is_not_work_experience` | - |
| 2026-11-14 | v0.2-extract | `586be41` | extract | 标题被当成姓名，缺少姓名时兜底不稳定 | 排除标题词，支持正文前几行和文件名兜底 | `test_titles_are_not_names`、`test_profile_uses_filename_for_missing_name` | - |
| 2026-11-14 | v0.2-extract | `586be41` | extract | 否定句中的 Python、Docker、Nginx 仍进入技能列表 | 增加句内否定判断 | `test_negated_skills_are_not_extracted` | - |
| 2026-11-14 | v0.2-extract | `586be41` | extract | 未知学历的默认分值偏高 | 将未知学历分和画像默认学历分设为 0 | `test_empty_profile_returns_stable_defaults` | - |
| 2026-11-14 | v0.2-guard | `a69ac5d` | guard | 空 PDF 会进入后续评分路径 | 空内容返回 `EMPTY` 和 `REVIEW` | `test_empty_extract_is_manual_review` | - |
| 2026-11-14 | v0.2-guard | `a69ac5d` | guard | 扫描碎片和损坏乱码会被误判为普通短文本 | 使用有效字符比例区分 `SUSPECT_SCAN_OR_CORRUPT` | `test_low_information_fragment_is_suspect_scan_or_corrupt` | OCR 尚未实现 |
| 2026-11-14 | v0.2-guard | `a69ac5d` | guard | 过短但可读的简历缺少明确复核原因 | 返回 `TOO_SHORT`，原因中保留实际字数和阈值 | `test_short_realistic_text_is_too_short` | - |
| 2026-11-14 | v0.2-guard | `a69ac5d` | guard | 解析失败和未评估状态容易与零分混用 | `REVIEW` 使用 `score=-1`，通过闸门保留 `score=None` | `test_extract_error_is_manual_review`、`test_missing_dependency_is_manual_review` | - |
| 2026-11-15 | v0.2-route | `7f23911` | route | 没有稳定 `jd_id` 的确定性岗位路由 | 增加 JSON 岗位配置和稳定岗位 ID | `test_load_jobs_uses_stable_relative_config`、`test_route_id_is_from_config_or_unmatched` | score 尚未实现 |
| 2026-11-15 | v0.2-route | `7f23911` | route | 岗位选择不能同时考虑技能和年限 | 使用 `0.4 * skill_ratio + 0.6 * years_fit` | `test_four_year_backend_routes_to_intermediate_job`、`test_entry_level_backend_routes_to_junior_job` | - |
| 2026-11-15 | v0.2-route | `82358d6` | route | 四年候选人技能贴初级岗时仍可能进入 JD2 | 年限超过岗位上限时排除该岗位 | `test_four_year_flask_profile_stays_out_of_junior_job` | - |
| 2026-11-15 | v0.2-route | `82358d6` | route | 测试、Kubernetes、监控等词过宽，可能误伤后端简历 | 收窄测试和运维方向词，补充支持词回归 | `test_backend_support_terms_do_not_create_direction_conflict` | 更复杂的跨方向简历待后续评测集验证 |
| 2026-11-15 | v0.2-route | `82358d6` | route | 重复岗位 ID 会造成路由配置不稳定 | 加载时检测重复 `jd_id` 并抛出 `ValueError` | `test_duplicate_job_ids_fail_to_load` | 缺字段和非法字段校验留到配置校验阶段 |
| 2026-11-15 | v0.2-route | `82358d6` | route | `UNMATCHED` 的路由指标容易被下游读成零分 | 保留最佳路由指标，并明确 `combined` 不是五维总分 | `test_unmatched_keeps_best_route_metrics` | score 模块尚未接入 |
| 2026-11-15 | v0.2-guard | `263da98` | test | 英文 PDF 状态和中文画像共用一个测试，容易误读覆盖范围 | 拆分 PDF 状态测试与中文 `.txt` 画像测试，在合成数据说明中标明各自的验证范围 | `test_synthetic_pdf_manifest_matches_guard`、`test_synthetic_profile_sources_match_manifest` | 中文 PDF 到画像的端到端回归待补 |
| 2026-11-15 | v0.2-guard | `263da98` | guard | 只有少量可读中文提示的扫描件，当前判定缺少回归 | 记录可读提示返回 `TOO_SHORT`、无文字层返回 `EMPTY` 的行为 | `test_chinese_scan_prompt_is_currently_too_short`、`test_synthetic_pdf_manifest_matches_guard` | OCR 和扫描件识别待实现 |
| 2026-11-15 | v0.2-guard | `263da98` | guard | 类型签名漏写已支持的 `None` 输入 | 将 `guard_text` 的参数标注为 `Optional[ExtractResult]` | `test_empty_object_is_structured_review` | - |
