"""Chinese interface strings for the saved matrix report variant."""
ZH = {
    'target_url': '目标网址', 'default_title': '全量测试结果', 'passed': '通过', 'failed': '已调用·未通过',
    'adapter_rejected': '未调用·适配器提前拒绝', 'uncommitted': '已调用·未入库', 'not_called': '未调用',
    'adapter_rejected_note': '适配器在请求发出前拒绝，产品没有被调用；原始记录和旧分数保留，但不计为产品失败，也不计入通过率分母。',
    'totals': '计划 {planned} 组；保存 {saved} 条；已调用未入库 {uncommitted} 条；未调用 {not_called} 条；适配器提前拒绝 {rejected} 条。',
    'suite': '套别', 'provider': '方法', 'status': '状态', 'search': '搜索',
    'all_suites': '全部套别', 'all_providers': '全部方法', 'all_statuses': '全部状态',
    'search_hint': '案例编号、名称、网址或错误', 'plan': '计划', 'called_cases': '实际调用案例',
    'saved': '保存', 'normal': '接口正常', 'content_pass': '内容通过',
    'called_failed': '已调用未通过', 'adapter_reject': '适配器拒绝', 'close': '关闭详情',
    'saved_checks': '保存的验收检查', 'checks_note': '以下为已保存的检查结果；本报告不重新评分。',
    'original_evidence': '原始证据', 'banner': '诊断运行 · 非正式评测 · 保存结果预览 · 机器评分未经独立复核',
    'scope_note': '接口正常不等于内容通过；实际调用按案例计，重试不另计案例。适配器提前拒绝不是产品能力失败；未入库和未调用项没有分数。',
    'methods': '方法结果汇总', 'all_cases': '全部案例与证据', 'case': '案例', 'detail': '详情',
    'previous': '上一页', 'next': '下一页', 'limitations': '范围与限制', 'bindings': '源证据绑定',
    'unscored': '未评分', 'score_fail': '未通过', 'yes': '是', 'no': '否', 'unrecorded': '未记录',
    'score_label': '内容评分', 'called_label': '实际调用', 'time_label': '保存耗时',
    'retry_label': '保存重试次数', 'no_error': '无保存错误信息', 'case_link': '冻结案例与规则',
    'record_link': '原始结果记录', 'answer_link': '原始返回', 'log_link': '真实调用日志',
    'receipt_link': '证据凭据', 'no_files': '无可用文件；缺失项未补造。',
    'open_detail': '查看结果与证据', 'filtered': '当前筛选', 'groups': '组',
    'page': '页', 'per_page': '每页', 'items': '条', 'elapsed': '保存耗时（ms）',
    'sort_help': '点击汇总表的数值标题可排序；汇总始终显示全部方法。结果表可按保存耗时排序，缺失耗时排在最后。',
}

ZH.update({
    'configuration': '本轮配置', 'method_info': '方法资料', 'execution': '执行记录',
    'config_note': '以下汇总来自已保存的调用记录。请求参数表示调用时的设置，不证明服务端一定采用了该设置。',
    'observed_config': '保存的配置字段', 'config_cases': '记录数',
    'no_config': '记录未包含可展示的配置字段；可在执行记录中查看原始结果和调用日志。',
    'no_method': '没有可用于方法资料的已保存结果。', 'method_links': '查看方法资料与执行记录',
    'datasets_title': '评测集', 'dataset_note': '任务以套别和原案例编号共同区分；同名案例没有合并，规则没有修改。',
    'rules_link': '查看冻结任务', 'methodology_title': '评测方法',
    'methodology_note': '本报告只展示保存的机器评分及调用状态，不重新计算验收得分。接口正常、内容通过、适配器拒绝和未评分项分别统计。',
    'analysis_title': '结果分析', 'analysis_note': '全部方法和案例集中在首页结果表；可按套别、方法和状态筛选，并打开每条记录的验收检查及原始证据。',
    'open_matrix': '查看统一结果表', 'review_title': '核验记录',
    'review_note': '源记录、文件哈希、覆盖范围和报告链接由构建检查验证。机器评分没有经过逐条独立复核；未评分项没有补造分数。',
    'guide_title': '报告解读', 'adoption_title': '方法介绍',
    'adoption_note': '方法资料取自现有方法注册信息和本轮保存记录；本报告没有新增价格或市场采用情况调查。',
    'reproduce_title': '环境与复现', 'reproduce_note': '以下绑定用于追溯原始输入、配置和结果。此报告构建只读取已有证据，没有重新调用产品。',
    'changes_title': '变更记录', 'changes_note': '本次只改变报告展示：五套任务按原身份汇总，全部保存分数保持不变，缺失项单独标记。',
    'accounts_title': '账号与安装', 'accounts_note': '本报告没有账号余额或安装过程的独立证据。方法页展示已有请求配置；不展示凭据，也不据此推断账号状态。',
    'english_title': 'English edition', 'english_note': '本报告只生成中文版本。',
    'return_home': '返回统一报告', 'scope': '本页范围', 'saved_metadata': '保存的调用配置',
})

ZH.update({
 'completion_title': '完成情况与未完成项',
 'completion_note': '本表按原案例集区分同名案例。已有分数全部保留；未入库项没有分数。适配器提前拒绝属于未调用，原始记录中的旧分数保留，不计为产品失败，也不计入通过率分母。详情页中的任务编号带案例集前缀，仅用于避免报告链接冲突，原始任务和结果见证据文件。',
 'completion_headers': ['方法','计划','实际调用','已入库','正常返回','通过','适配器拒绝','已调用未入库','未调用'],
 'missing_headers': ['案例集','方法','原案例编号','状态','保存原因','证据'],
})

ZH.update({
 'suite_denominator_note': '按仓库案例集统计。通过率＝实际调用且已保存的通过数／实际调用且已有明确评分的结果数。未入库和未调用项不补成失败；适配器提前拒绝属于未调用，不计入通过率分母，原始记录和旧分数保留。',
 'suite_result_headers': ['方法','计划案例','已保存结果','未入库或未调用','正常返回','通过','通过率（明确分母）'],
})

ZH.update({
 'recovery_link': '离线恢复说明',
 'recovery_note': '原始证据已核验；耗时未知，未评分、未正式入库，无新增调用。',
 'recovery_incomplete': '重试未完成。',
 'recovery_completed': '已使用原定重试次数。',
})

ZH.update({
 'history_title': '修复前的记录',
 'history_note': '以下保留修复前的原始记录，不重复计入当前结果。当前结果采用预先声明范围内的补测结果，不按分数择优。',
 'history_scope': '补测范围与原记录绑定',
})

ZH.update({
 'continuation_title': '补齐的一次失败重试',
 'continuation_note': '首轮已调用一次，本次补齐原约定的一次重试，共两次调用。当前分数和耗时采用最后一次调用，不按结果择优；首轮证据保留。',
 'continuation_cost': '两次调用累计耗时：',
 'continuation_unknown_cost': '不可用：首轮精确耗时未保存。',
 'continuation_prior': '查看首轮调用证据',
})

ZH.update({
 'coverage_banner_title': '保存结果覆盖情况',
 'coverage_banner_counts': '计划 {planned} 项，已入库 {saved} 项；已调用未入库 {uncommitted} 项；未调用（含按要求跳过）{not_called} 项；适配器提前拒绝 {rejected} 项。',
 'coverage_banner_note': '未入库和未调用项没有补造分数；适配器提前拒绝不代表产品失败。本报告用于查看保存证据，不代表完整验收或正式发布。',
})

ZH.update({
 'final_call_timing_headers': ['最终调用耗时均值','最终调用耗时 P50','最终调用耗时 P95'],
 'final_call_timing_title': '最终调用耗时均值 / P50 / P95',
 'final_call_timing_help': '三项均采用各方法实际调用且已保存结果的最终一次调用耗时，包含通过与失败；未入库、未调用和适配器提前拒绝不计入，各方法样本数可不同。耗时不参与通过率排名，跨日期、跨批次记录不等同于同窗受控速度比较。',
 'final_call_timing_note': '每个逻辑结果只取最终一次调用耗时，不是包含此前重试和等待的累计耗时。补齐重试的结果在详情页另列已知的两次调用累计耗时；首轮耗时未保存时标为不可用。',
})
