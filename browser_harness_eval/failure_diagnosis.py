"""Evidence-based failure stage, preserving uncertainty about remote internals."""
import json,re


def diagnose(r,c,raw,source=None):
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
    if meta.get('empty_content'):
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
    elif m.get('forbidden_hits'):
        category,stage,proof=set_result('返回非目标内容','内容核验','命中错误页 / 排除标记：'+', '.join(m['forbidden_hits']))
    elif c.get('task_kind')=='structure':
        if not m.get('structure_pass') or m.get('list_field_coverage',0)<1:
            category,stage,proof=set_result('列表字段不足','内容核验',f"检出 {m.get('named_entry_count',0)} 条具名详情入口；至少需要 3 条")
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
        category,stage,proof=set_result('缺少目标资源','资源核验','未命中资源：'+', '.join(missing))
    elif c.get('task_kind','body')=='body' and not m.get('content_pass'):
        if not m.get('length_pass'):
            category,stage,proof=set_result('正文不足','内容核验','提取正文没有达到本例最低长度要求')
        elif any(not f['pass'] for f in m.get('fact_results',[])):
            category,stage,proof=set_result('缺少关键信息','内容核验','未命中核对点：'+', '.join(f['id'] for f in m['fact_results'] if not f['pass']))
        else:category,stage,proof=set_result('内容结构不完整','内容核验','未满足本例字段或数量规则，见逐项判定')
    if not category:return None
    return {'category':category,'stage':stage,'evidence':proof,'certainty':certainty,
            'independent_source_check':source,'source_check_boundary':'独立复查与调用的时间、网络、会话不同；一次拒绝不能证明网站失效。'}


def normalize_empty_response(r,raw):
    """Correct an API-success/empty-extraction conflation, retaining raw evidence."""
    if r['provider']!='cloudflare-markdown' or r['ok']:return
    try:p=json.loads(raw)
    except ValueError:return
    if isinstance(p,dict) and p.get('success') is True and p.get('result')=='' and r.get('status_code')==200:
        r['provider_metadata'].update(empty_content=True,content_type=(p.get('meta',{}).get('headers') or {}).get('content-type'),request_normalization='HTTP 200 + success=true：请求成功；result 为空：任务失败。原始响应保留。')
        r.update(ok=True,error_type=None,error_message=None)
