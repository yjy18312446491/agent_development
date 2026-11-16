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
| 2026-11-15 | v0.2-score | `0c5e032` | score | 路由指标只能选岗位，不能作为五维分数 | 按技能 40、项目 30、年限 15、学历 10、加分 5 计算；项目默认 Mock 零分，支持注入；必需技能不重复计入加分项 | `test_doctorate_with_full_jd1_match_and_injected_project_score_gets_100`、`test_bonus_skills_are_optional_deduplicated_and_capped` | 真实模型项目分、技能弱掌握和实战加权待评估 |
| 2026-11-15 | v0.2-score | `0c5e032` | score | 有效简历无匹配岗位时容易与人工复核混为一类 | `UNMATCHED` 返回 `SCORED`，总分只保留学历分并跳过项目打分；本科 7，未知 0 | `test_unmatched_keeps_only_education_score` | 历史评测的本科 9 分与当前 7 分口径不同，后续按新评测集验证 |
| 2026-11-15 | v0.2-score | `0c5e032` | score | 上游拦截或非法打分结果可能出现部分分数 | 对 Guard、画像、命中岗位和项目分逐级校验；失败返回 `REVIEW`、`-1` 和原因码 | `test_guard_blocks_before_profile_and_preserves_original_reason`、`test_invalid_job_configuration_skips_project_scoring`、`test_invalid_project_scores_and_exception_trigger_review` | 统一筛选入口和结果校验模块待实现 |
| 2026-11-15 | v0.2-score | `351cb29` | docs / test | README 和状态页仍将评分列为待实现，S001 只验证分项求和 | 同步离线 Mock 项目分的实际状态与评测口径，固定 S001 的五项分数和总分 52 | `test_normal_chinese_resume_from_extract_through_score`，全量 59 项通过 | 结果校验、统一筛选入口和真实模型项目评分待实现 |
| 2026-11-15 | v0.2-verify | `a3c0ce9` | verify | 评分结果缺少独立的结构、一致性和岗位校验 | 校验五项整数范围、总分、岗位 ID，以及 `UNMATCHED` 只保留学历分的规则 | `test_s001_passes_with_jd2_and_total_52`、`test_validation_checks_structure_then_route_then_unmatched` | 统一筛选入口和真实模型项目分待实现 |
| 2026-11-15 | v0.2-verify | `a3c0ce9` | verify | 上游已给出的复核原因和岗位 ID 容易被校验错误覆盖 | 合法 `REVIEW` 原样返回。校验发现错误时生成新的 `REVIEW`、总分 `-1` 和空岗位 ID | `test_job_not_found_review_keeps_the_original_job_and_reason`、`test_invalid_upstream_review_gets_a_new_review`，全量 71 项通过 | - |
| 2026-11-15 | v0.2-screen-one | `bb257ee` | screen | 单份 PDF 缺少调用全部筛选步骤的入口 | 串联提取、闸门、岗位配置、画像、路由、评分和校验。英文 S001 PDF 默认得到 `JD1 / SCORED / 60` | `test_normal_pdf_uses_default_jobs_and_scores_60`、`test_entry_uses_verified_score_and_keeps_real_route` | 批量筛选、结果整形和真实模型项目分待实现 |
| 2026-11-15 | v0.2-screen-one | `bb257ee` | screen | Guard 拦截后继续读取岗位会掩盖原始原因，底层 PDF 异常可能带本地路径 | 先处理 Guard 再加载岗位，读取错误使用固定复核提示。空岗位序列仍按 `UNMATCHED` 计学历分 | `test_real_guard_rejections_skip_all_later_stages`、`test_extract_error_does_not_expose_local_path_or_error_details`、空岗位回归，全量 81 项通过 | 离线评测集待建立 |
| 2026-11-15 | v0.2-mock-eval | `839582a` | eval | 单份 PDF 链路缺少固定标注和可复跑的评测报告 | 用真实筛选入口跑 10 份合成 PDF 的 13 个场景，逐字段比对；评测偏差退出 1，输入或写入错误退出 2 | `test_real_pdf_baseline_matches_all_labels`、`test_wrong_label_returns_one_and_reports_the_field`，全量 87 项通过 | 真实模型项目分待接入 |
| 2026-11-15 | v0.2-mock-eval | `839582a` | eval | 中文文本的岗位和分数不能直接作为英文 PDF 的标注 | 独立维护 PDF 评测清单，英文 S001 固定为 `JD1 / 60`；空岗位 0 和预期复核 -1 均按各自标注比对 | `test_real_pdf_baseline_matches_all_labels` | 英文学历和否定语句抽取仍受现有规则限制 |
| 2026-11-16 | v0.2-project-scorer | `2d36eec` | score | 项目分回包缺少结构和原文引证校验 | 校验三个评分维度和非零分引证，空片段跳过客户端；回包异常沿用人工复核并保留岗位 ID | `test_empty_or_whitespace_evidence_cannot_support_nonzero_points`、`test_screen_one_preserves_jd1_on_project_adapter_failure` | 真实模型客户端和费用配置待定 |
| 2026-11-16 | v0.2-project-scorer | `2d36eec` | eval | 项目分缺少可复用的人工标注 | 固定 12 条合成项目片段标注，用假客户端验证求和；`team_metric` 补齐岗位任务依据 | `test_frozen_labels_match_reviewed_scores_using_a_fake_client`，全量 97 项和 Mock 评测 13/13 通过 | 模型对项目证据和片段内指令的表现待联调 |
| 2026-11-16 | v0.2-deepseek-pilot | `d672519` | model | 本地配置和假客户端无法验证真实的项目分回包 | 新增显式开启的 DeepSeek 客户端和三条合成样本入口，限制请求次数并保留本地报告 | 真实请求 3 次，总分为 30、3、3，三条均与人工标注一致 | 尚未跑完整标注集，生产筛选继续使用 Mock |
| 2026-11-16 | v0.2-deepseek-pilot | `d672519` | test | 服务端错误可能导致重复请求或丢失已完成结果 | 限制重试次数，认证或余额错误停止试跑，报告已完成及未运行的样本 | 全量 105 项通过，Mock 评测 13/13 | 报告费用是基于可计量 token 的估算 |
| 2026-11-16 | v0.2-deepseek-eval | `35f9366` | eval | 连续重试可能耗尽后续样本的首发额度 | 完整轮为 11 条非空样本保留首发，整轮最多 2 次重试和 13 次请求 | `test_full_cli_reserves_all_first_attempts_after_two_retries` | 完整轮真实执行待审核 |
| 2026-11-16 | v0.2-deepseek-eval | `35f9366` | eval | 空片段没有模型回包，单条分差和全局中止需要区分 | 单独报告本地跳过、合法响应、单条失败和未运行样本，并比较成对指令样本 | 全量 110 项通过，Mock 评测 13/13，完整模式使用假 HTTP 验收 | 真实结果与泛化表现待验证 |
| 2026-11-16 | v0.2-deepseek-live | `99ac018` | eval | 完整评测入口缺少真实盲测结果 | 运行 12 条合成标注，记录三项分数、耗时、token 和两组成对样本差异 | 本地报告 `deepseek-all.json`，11/11 合法响应，三项全匹配 8/11，项目总分 MAE 1.0 | 真实招聘场景尚未验证 |
| 2026-11-16 | v0.2-deepseek-live | `99ac018` | eval | 本地空片段与模型响应混计会影响评测，退出码也容易被读成全匹配 | 空片段单独记 0，模型指标分母为 11，退出 0 表示完成整轮 | 空片段跳过 1 条，3 条分差已记录；110 项测试通过，Mock 13/13 | 个人贡献和结果证据的分差待复审 |
