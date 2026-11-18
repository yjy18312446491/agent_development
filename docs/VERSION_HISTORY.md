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
| 2026-11-16 | v0.2-deepseek-live | `d138e14` | eval | `team_we` 的个人贡献实测 3、标注 0 | 复审确认「我们完成了」仅能确认团队行动，保留个人贡献标注 0 | 核对首轮报告、冻结标注及评分标准。全量 110 项通过，Mock 13/13 | 个人贡献边界留待 v2 对照 |
| 2026-11-16 | v0.2-deepseek-live | `d138e14` | eval | `team_metric` 和 `flask_jd2` 的结果证据实测 3、标注 7 | 复审确认具体团队指标和接口测试交付符合结果证据 7，保留两条标注及 v1 基线 | 11 条合法响应，三项全匹配 8/11，项目总分 MAE 1.0，两组指令对分差为 0 | 结果证据边界留待 v2 对照 |
| 2026-11-16 | v0.2-project-score-v2 | `2237df5` | score / eval | v1 在团队主语、团队指标和接口测试上出现三条分差 | v2 保留 v1 提示词正文并追加三条口径，原 12 条标注加 3 条对照 | v1 与 v2 预览分别列出 12 和 15 条。原始标注哈希未变，全量 115 项通过 | 真实对照待审核，需检查原样本新分差 |
| 2026-11-16 | v0.2-project-score-v2 | `2237df5` | eval | 新旧轮需要独立报告，重试可能挤占后续首发 | v2 报告单独输出，拆出原样本指标与六条对照实测，保留 14 次首发，整轮最多 16 次请求 | 假 HTTP 覆盖请求上限、原报告隔离和中止状态。Mock PDF 13/13 | 默认筛选继续使用 Mock，PDF 项目片段提取待后续 |
| 2026-11-17 | v0.2-project-score-v2-live | `ceb9d5e` | eval | 首轮 v2 的 `full_30` 和 `team_claimed_faster` 遇到网络错误 | 保留首轮报告，在相同提示词和标注下重跑完整清单 | 首轮 12/14 有效、16 次请求。重跑 14/14 有效、14 次请求，无重试或失败。两轮空片段均跳过 | 两份原始报告保存在本地 |
| 2026-11-17 | v0.2-project-score-v2-live | `ceb9d5e` | score / eval | v1 的三条分差和 v2 新对照需要真实结果 | 按同一批 11 条原样本对照，记录三条原分差、新样本和指令对 | v1 8/11、MAE 1.0。v2 11/11、MAE 0.0。新对照 3/3，两组指令对分差均为 0。全量 115 项通过，Mock PDF 13/13 | 合成片段实验，默认筛选继续用 Mock |
| 2026-11-17 | v0.2-project-snippet | `c5ceb29` | extract | 评分适配层需要外部提供项目片段，整份简历尚无可独立验证的切段入口 | 按中英文独立标题切出项目段，保留正文原文，按顺序拼接；无正文时返回空串 | 新增 39 项，全量 154 项通过，Mock PDF 13/13。S007、S008 的同行标签按规则返回空串 | 同行标题暂不识别，现有 PDF 集成用例只覆盖空返回，默认筛选仍用 Mock |
| 2026-11-17 | v0.2-project-snippet-pdf | `97b8e96` | test | 现有 PDF 项目切段集成只覆盖空返回 | 新增 5 份英文合成 PDF 和对应源文本，比对 PDF 抽出的项目原文，覆盖单项目、同节多项目、多个项目段和空段 | 新增 6 项，全量 160 项通过。S001、S007、S008 切段为空，Mock PDF 13/13，S001 总分 60、项目分 0 | 默认筛选采用 Mock，显式 DeepSeek 评分接入待审核后另做 |
| 2026-11-17 | v0.2-snippet-scorer | `42bdb29` | score | 切段器和项目评分尚缺可显式注入的连接 | 新增 `make_injected_scorer`，在回调中把项目原文交给现有评分校验，沿用空段跳过和非法回包复核 | 新增 8 项、全量 168 项通过，Mock PDF 13/13；空段未调用假客户端，异常保留 JD1 和 `PROJECT_SCORER_ERROR` | 真实 HTTP 0 次，默认筛选使用 Mock；含项目段的长简历端到端路径待测试 |
| 2026-11-17 | v0.2-screen-snippet-pdf | `3ff46d2` | test | 已有短 PDF 无法通过闸门，S001 通过闸门但无独立项目标题 | 新增 1 份含独立项目段的英文长 PDF，以 `screen_one` 回归读文件、闸门通过、JD1 路由和假客户端接收的原文 | 新增 2 项、全量 170 项通过。Mock PDF 13/13。长 PDF 默认 60 分，注入项目分 21 后总分 81 | 真实评分 HTTP 0 次，显式 DeepSeek 接入待另做 |
| 2026-11-17 | v0.2-screen-deepseek | `cd254b6` | eval | PDF 筛选链路只有假客户端回包，真实模型只测过已切好的片段 | 固定合成 PDF 增加显式 `--live` 联调入口，检查原文传递、分数叠加及复核 | 真实请求 1 次，DeepSeek v2 项目分 14、总分 74，Mock 总分 60。新增 5 项，全量 175 项通过，Mock PDF 13/13 | 结果来自单份合成 PDF，默认筛选仍用 Mock |
| 2026-11-17 | v0.2-finalize | `a32a67e` | finalize | 单份筛选只有校验后的内部评分结构，缺少可序列化档位记录 | 新增 `screen_one(...).finalized.as_dict()`，沿用 A 起点 80、B 起点 55；保留复核状态与原因，档位为空 | 新增 11 项、全量 186 项通过；Mock PDF 13/13。S001 为 B / 60，合法 0 分为 C、复核 -1 无档位 | 批量筛选、持久化与导出待后续 |
| 2026-11-17 | v0.2-screen-many | `3a55a24` | screen | 单份筛选无法一次返回多份 PDF 的有序结果 | 新增 `screen_many` 和 `BatchRecord`，复用单份筛选与档位，保留文件名和既有复核原因，隔离意外异常 | 新增 6 项，全量 192 项通过；10 份合成 PDF 返回 10 条记录，Mock PDF 13/13，S001 为 B / 60 | 默认使用 Mock，存储与导出待后续 |
| 2026-11-18 | v0.2-store | `b294efe` | store | 批量筛选结果尚无法持久化和导出 | 新增 SQLite 参数化写入和有序回读，按批次和文件名判重，整次写入原子回滚，CSV 保留固定表头及复核空单元格 | 新增 9 项、全量 201 项通过，Mock PDF 13/13；10 份合成 PDF 写入和导出均为 10 条 | 数据库及 CSV 由调用方指定保存位置，测试用临时目录 |
| 2026-11-18 | v0.2-fastapi | `550936f` | api | 批次筛选与 SQLite 写入缺少本地 HTTP 入口 | 增加健康检查、批次上传及批次查询，限制上传大小和文件名，清理原件，复用每请求独立 SQLite 连接 | 新增 21 项、全量 222 项通过，Mock PDF 13/13。S001 B/60、S002 EMPTY/-1，POST 与 GET 结果一致 | 以 127.0.0.1 运行，使用 Mock。Docker 与 Demo 待后续 |
| 2026-11-18 | v0.2-docker-compose | `9c064ba` | packaging | 本地 API 缺少容器配置；交接稿的 3.9.9-slim-bookworm 标签在公开仓库不存在 | 固定 3.9.25-slim-bookworm 摘要，非 root 运行并保留容器内回环监听，使用命名卷保留 SQLite，白名单包含岗位配置和两份合成夹具 | Docker Hub 标签与摘要已核对，配置静态检查通过；宿主机全量 222 项通过，Mock PDF 13/13 | 本机未实跑，待 Docker Engine 验证构建、容器内 HTTP 和重建回读 |
| 2026-11-18 | v0.2-docker-compose-live | `ecdc68d` | packaging | Docker 实际构建、命名卷权限与重建回读待验收 | 用独立 GitHub 托管 Linux 运行器验收配置提交 `c908730` | Engine 28.0.4、Compose v2.38.2；批次 `31baf778f8b64289981848835df00e5a` 重建回读一致；宿主机 222 项和 Mock 13/13 通过 | Windows Docker Desktop 待复验。 [运行日志](https://github.com/yjy18312446491/agent_development/actions/runs/44243560755) |
