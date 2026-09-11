"""Evidence-based failure stage, preserving uncertainty about remote internals."""
import json,re


def _stable_failure(metrics):
    """Describe measured contract errors; never infer a shortfall by default."""
    errors = metrics.get('stable_task_errors', [])
    if 'page_identity_mismatch' in errors or any(e.startswith('missing_stable_anchor:') for e in errors):
        label = '缺少关键信息'
    elif 'too_many_records' in errors:
        label = '有效条目过多'
    elif 'duplicate_records' in errors:
        label = '条目重复'
    elif any(e in errors for e in ('incomplete_records', 'invalid_link_output')):
        label = '字段格式或记录不完整'
    elif 'too_few_complete_records' in errors:
        label = '有效条目不足'
    else:
        label = '任务规则未满足'
    counts = f"完整且去重的记录 {metrics.get('stable_unique_record_count','未提供')} 条；至少要求 {metrics.get('stable_minimum_records','未提供')} 条"
    maximum = metrics.get('stable_maximum_records')
    if maximum is not None:
        counts += f"，最多允许 {maximum} 条"
    return label, '内容核验', counts + '。规则：' + (', '.join(errors) or '未提供具体错误')


def diagnose(r,c,raw,source=None):
    from .metrics import task_measured
    if not task_measured(r['metrics'], c.get('task_kind')):
        return None
    try:payload=json.loads(raw)
    except (ValueError,TypeError):payload={}
    if not isinstance(payload,dict):payload={}
    nested=payload.get('error') or {}
    if not isinstance(nested,dict):nested={}
    meta=r.get('provider_metadata',{});m=r['metrics']
    fragments=[str(r.get('error_type') or ''),str(r.get('error_message') or '')]
    if not r['ok']:fragments.extend([raw[:3000],json.dumps(meta.get('errors') or [],ensure_ascii=False)[:1000]])
    message=' '.join(fragments);low=message.lower()
    upstream=nested.get('upstream_status_code') or meta.get('upstream_status')
    category=None;stage='';proof='';certainty='直接返回依据'
    def set_result(label,where,evidence):return label,where,evidence
    if m.get('deadline_overrun'):
        category,stage,proof=set_result('超过端到端预算','调用截止时间','实际耗时超过该调用登记的预算；保留原始内容及计时，但不计为按时交付。')
    elif meta.get('empty_content'):
        category,stage,proof=set_result('正文提取为空','内容提取','接口成功，但返回 result 为空；响应类型：'+str(meta.get('content_type') or '未提供'))
    elif not r['ok']:
        if any(x in low for x in ['unsupported_content_type','unexpected_content_type','unsupported_file','file type that firecrawl cannot process']):
            category,stage,proof=set_result('不支持输入类型','方法能力',message[:650])
        elif 'do not support this site' in low:
            category,stage,proof=set_result('方法不支持该站点','方法策略',message[:650])
        elif 'web_redirect_blocked' in low:
            category,stage,proof=set_result('方法禁止跨站跳转','方法安全策略',message[:650])
        elif any(x in low for x in ['web_blocked_url','private/internal','ssrfblocked','non-public ip']):
            category,stage,proof=set_result('方法安全限制','方法安全策略',message[:650])
        elif 'datadome captcha' in low:
            category,stage,proof=set_result('源站要求验证码','源站访问',message[:650])
        elif 'blocked by anti-bot protection: structural:' in low:
            category,stage,proof=set_result('方法空页规则拦截','方法内容检查',str(r.get('error_message'))[:650]+'；这是方法的结构启发式判断，不能据此确认源站反爬。')
        elif 'download is starting' in low:
            category,stage,proof=set_result('下载未被方法接管','浏览器下载',message[:650])
        elif 'crawl_empty_content' in low:
            category,stage,proof=set_result('正文提取为空','内容提取',message[:650])
        elif 'source_not_available' in low:
            category,stage,proof=set_result('服务拒绝该来源','服务取页',message[:650])
            certainty='服务返回 SOURCE_NOT_AVAILABLE；不据此推断站点对所有访问者不可用'
        elif r.get('error_type')=='authentication':
            category,stage,proof=set_result('接口鉴权失败','服务接口',message[:650])
        elif r.get('status_code')==429 or 'rate_limit' in low:
            category,stage,proof=set_result('限流 / 配额','服务接口',message[:650])
        elif nested.get('code')=='browser_fetch_failed' and 'deadline exceeded' in low:
            category,stage,proof=set_result('服务内浏览器超时','服务取页',str(nested.get('message')))
        elif 'execution context was destroyed' in low:
            category,stage,proof=set_result('浏览器执行上下文中断','浏览器执行',message[:650])
        elif re.search(r'timed out|(?<![a-z])timeout(?![a-z])|deadline exceeded',low):
            category,stage,proof=set_result('加载 / 提取超时','取页或提取',message[:650])
        elif upstream in {401,403} or re.search(r'(?:http[_ :]*|failed \(|status code[: ]*|status_not_ok.{0,80})(?:401|403)',low) or re.search(r'(401|403) client error',low):
            category,stage,proof=set_result('源站拒绝该次访问','源站访问',str(nested.get('message') or r.get('error_message') or message)[:650])
        elif any(x in low for x in ['crawl_not_found','404 page not found','failed (404)']):
            category,stage,proof=set_result('取页返回 404','源站访问',message[:650])
        elif 'err_aborted' in low or 'network connection aborted' in low:
            category,stage,proof=set_result('浏览器导航中止','浏览器导航',message[:650])
        elif any(x in low for x in ['name resolution','err_name_not_resolved']) or r.get('error_type')=='dns':
            category,stage,proof=set_result('域名解析失败','网络连接',message[:650])
        elif 'extraction failed' in low and 'no content' in low:
            category,stage,proof=set_result('正文提取为空','内容提取',message[:650])
        elif any(x in low for x in ['failed to connect','connection','err_']) or r.get('error_type')=='network':
            category,stage,proof=set_result('取页连接失败','网络连接',message[:650])
        elif r.get('error_type') in {'provider_exception','response_validation'}:
            category,stage,proof=set_result('本地适配器执行失败','本地执行',message[:650])
        else:
            category,stage,proof=set_result('服务取页失败','服务取页',message[:650])
            certainty='服务未披露更细原因；不能仅凭该错误归责源站或服务内部'
    elif m.get('text_encoding_corruption'):
        category,stage,proof=set_result(
            '输出文本解码损坏','内容提取',
            f"保存输出含 {m.get('replacement_character_count',0)} 个 Unicode 替换字符 "
            f"（占 {m.get('replacement_character_ratio',0)*100:.1f}%）；原文字节已在方法输出前被替换，无法从该 Markdown 还原。")
    elif c.get('task_kind', 'body') == 'body' and m.get('declared_truncation_hits'):
        category,stage,proof=set_result(
            '正文范围不完整','内容核验',
            '供应商明确声明返回内容已截断：'+', '.join(m['declared_truncation_hits']))
    elif m.get('forbidden_hits'):
        category,stage,proof=set_result('返回非目标内容','内容核验','命中错误页 / 排除标记：'+', '.join(m['forbidden_hits']))
    elif (m.get('blocked_page') or m.get('stable_blocked_page')
          or 'blocked_page' in m.get('stable_task_errors', [])):
        hits = m.get('blocked_page_hits') or []
        detail = ('：' + ', '.join(hits)) if hits else ''
        category,stage,proof=set_result('返回非目标内容','内容核验','内容检查命中 blocked_page'+detail+'；记录、链接或资源数量足够也不代表取得目标内容。')
    elif 'forbidden_content' in m.get('stable_task_errors', []):
        category,stage,proof=set_result('返回非目标内容','内容核验','稳定内容检查命中 forbidden_content：返回正文含登记的排除标记。')
    elif 'stable_task_pass' in m:
        if not m.get('stable_task_pass'):
            category,stage,proof=_stable_failure(m)
    elif c.get('task_kind')=='structure':
        if m.get('named_entry_count',0) < m.get('minimum_entries',3):
            category,stage,proof=set_result('有效条目不足','内容核验',f"检出 {m.get('named_entry_count',0)} 条具名详情入口；至少需要 {m.get('minimum_entries',3)} 条")
        elif m.get('list_field_coverage',0)<1:
            category,stage,proof=set_result('字段格式或记录不完整','内容核验','具名详情入口数量足够，但列表字段未全部满足要求。')
        elif not m.get('structure_pass'):
            category,stage,proof=set_result('内容结构不完整','内容核验','具名详情入口数量足够，但结构检查未通过；不能据此认定条目不足。')
        elif not m.get('list_task_pass'):
            restricted=[x for x in m.get('list_checks',[]) if x.get('access_restricted')]
            if restricted:
                category,stage,proof=set_result('详情访问受限','独立内容核验',f"{len(restricted)} 条返回链接的详情访问受限：{restricted[0].get('access_note','')}。名称与详情身份未获证实；不据此认定笔记失效或内容错误。")
            else:
                category,stage,proof=set_result('条目内容未获证实','独立内容核验',f"前三条中 {m.get('confirmed_entries',0)} 条得到独立名称—链接证据；未确认不等于错误")
    elif c.get('task_kind')=='file' and not m.get('raw_file_pass'):
        category,stage,proof=set_result('未取得原文件','交付核验','没有可验证的原文件字节；文字或链接不能替代文件')
    elif c.get('task_kind')=='resource' and not m.get('resource_pass'):
        missing=[x.get('id','') for x in m.get('resource_results',[]) if not x['pass']]
        unexpected=m.get('unexpected_resource_urls',[])
        if missing:
            proof='未命中资源：'+', '.join(missing)
            if unexpected:proof+='；同时返回非目标资源：'+', '.join(unexpected)
            category,stage,proof=set_result('缺少目标资源','资源核验',proof)
        elif unexpected:
            category,stage,proof=set_result(
                '返回非目标资源','资源核验',
                '目标资源已命中，但答案字段还返回了非目标资源：'+', '.join(unexpected))
        else:category,stage,proof=set_result('缺少目标资源','资源核验','未命中登记的目标资源。')
    elif c.get('task_kind','body')=='body' and not m.get('content_pass'):
        if m.get('body_coverage_pass') is False:
            category,stage,proof=set_result('正文范围不完整','内容核验','未满足分区正文覆盖要求：'+', '.join(x['id'] for x in m.get('body_region_results',[]) if not x['pass']))
        elif not m.get('length_pass'):
            category,stage,proof=set_result('正文不足','内容核验','提取正文没有达到本例最低长度要求')
        elif any(not f['pass'] for f in m.get('fact_results',[])):
            if any(not f['pass'] and f.get('match_scope')=='entry' for f in m['fact_results']):
                category,stage,proof=set_result('条目未完整匹配','内容核验','没有匹配到条目的完整字段组合；这不表示每个字段都不存在。见逐项判定。')
            else:
                category,stage,proof=set_result('缺少关键信息','内容核验','未命中核对点：'+', '.join(f['id'] for f in m['fact_results'] if not f['pass']))
        elif m.get('legacy_marker_pass') is False:
            category,stage,proof=set_result('缺少关键信息','内容核验','未命中要求的正文标记：'+', '.join(m.get('expected_markers',[])))
        elif any(not p['pass'] and p['count'] < p['minimum'] for p in m.get('pattern_results',[])):
            category,stage,proof=set_result('有效条目不足','内容核验','未满足数量核对点：'+', '.join(
                f"{p['id']} 检出 {p['count']}，至少需要 {p['minimum']}" for p in m['pattern_results'] if not p['pass']))
        else:category,stage,proof=set_result('内容结构不完整','内容核验','未满足本例字段或数量规则，见逐项判定')
    if not category:return None
    return {'category':category,'stage':stage,'evidence':proof,'certainty':certainty,
            'independent_source_check':source,'source_check_boundary':'独立复查与调用的时间、网络、会话不同；一次拒绝不能证明网站失效。'}
