"""Reader-facing interpretation; all benchmark numbers come from current evidence."""
import json
import statistics
from html import escape as e
from pathlib import Path

from .metrics import evaluate_content, task_passed
from .report_capabilities import SOURCES


TITLE = '网页抓取通过了，为什么内容仍可能不好用？'


def render_explainer(out, cases, records, *, table, cell, link):
    def passed(r):
        return task_passed(r['ok'], r['metrics'], cases[r['case_id']].get('task_kind', 'body'))

    def attempt(r, label):
        return link('../attempts/' + r['attempt_id'] + '.html', label)

    def pct(value):
        return f'{value * 100:.1f}%'

    def chapter(key, title, body):
        return f'<section id="{key}"><h2>{title}</h2>{body}</section>'

    names = {r['provider'] for r in records}
    web = [r for r in records if cases[r['case_id']].get('track', 'web') == 'web']
    cow = [r for r in web if r['provider'] == 'cowagent-built-in']
    reader = '<header class="essay-head"><p class="eyebrow">报告解读 · 面向软件工程师</p>'
    reader += f'<h1>{TITLE}</h1><p class="lead">HTTP 成功、任务通过和正文可用，是三个不同的问题。读懂它们，才能知道这张榜单该用来做什么决策。</p>'
    reader += f'<p class="source-note">数据范围：{len(cases)} 个任务 · {len(names)} 种方法 · {len(records):,} 条执行记录。文中数字随本报告数据重新计算；离线实验不计入榜单。</p></header>'
    sections = [('question', '先分清拿到了什么'), ('rules', '任务通过究竟怎么判'), ('example', '沿着一个真实案例核算'),
                ('quality', '通过率与全文质量为何分离'), ('cowagent', 'CowAgent 的优势与代价'),
                ('limits', '这组数据还不能证明什么'), ('decide', '怎样用报告做选型')]
    toc = '<aside class="essay-toc"><details open><summary>阅读导航</summary><ol>' + ''.join(
        f'<li>{link("#"+key, title)}</li>' for key, title in sections) + '</ol></details></aside>'
    body = chapter('question', '1. 你的服务收到了 200，但用户要的未必是这段文字', '''
<p>假设你正在做一个把网页送进搜索索引或问答系统的服务。调用一个 URL 后，接口返回了一长段文本，没有报错。接下来你需要知道：这是不是目标文章？关键操作步骤有没有漏掉？导航栏、推荐文章和登录提示占了多少？</p>
<p>本报告把“给定一个 URL，取回指定内容”的实现称为一个方法或 provider。它可能是普通 HTTP 客户端、会运行 JavaScript 的浏览器，也可能是托管抓取 API。这里没有让 Agent 自主搜索、规划或反复尝试，也没有用大模型给结果打分。</p>
<figure class="essay-flow"><ol><li><b>源页与任务</b><span>保存参考正文、字段或资源清单</span></li><li><b>参评方法</b><span>对给定 URL 发起调用，保存原始返回</span></li><li><b>机械校验</b><span>按本例规则判断，另算质量和耗时</span></li></ol><figcaption>参考由评测侧独立保存；不作为答案提供给参评方法。判断器读取返回值，不观察模型的解释。</figcaption></figure>
<p><strong>请求成功</strong>只说明适配器把调用判为正常返回。有些方法不暴露源站 HTTP 状态，所以它甚至不等于“源站返回了 200”。<strong>任务通过</strong>说明该例的规则门槛满足；<strong>正文质量</strong>则在回答取回多少、混入多少。这三层不应合成一个模糊的“效果好”。</p>''')
    kinds = [
        ('正文 / 文档 / 文字信息', '请求成功；长度、关键核对点、显著标记、反向错误标记及该例结构规则满足。', '没有 F1 最低分门槛；不验证每一句语义。'),
        ('图片 / 视频入口', '请求成功，返回文本或原生附加字段里找齐登记的资源地址（或允许地址规则）。', '输入 URL 本身不算发现；匹配链接不等于下载或理解媒体。'),
        ('动态列表', '至少 3 个去重后的具名详情入口，返回顺序中前三个名称与详情身份都有独立证据。', '不要求旧榜单的名单、名次或价格；未证实不等于捏造。'),
        ('原文件', '需要实际字节或受控附件的可核验证据，而不是下载地址。', '当前运行链路未写入原文件验收结果；本轮不能据此比较二进制交付能力。'),
    ]
    rules = '<p>先按案例的任务类型选判定器，不是用同一个关键词公式衡量所有任务。</p>'
    rules += table(['任务', '通过条件', '不能据此推出'], [('', cell(a)+cell(b)+cell(c)) for a,b,c in kinds], metrics=[])
    rules += '''<h3>正文任务更接近一组验收测试</h3>
<p>当前数据集的正文事实覆盖门槛均为 100%。每个“事实组”是一项核对点，组内多个表达命中一个即可，组与组之间则必须全部满足。比如一本书的名字、价格和库存是三个核对点，不是看到任意一个就算通过。</p>
<pre aria-label="正文判定伪代码">请求成功
AND 返回字符数 ≥ 本例最低字符数
AND 每个必需事实组至少命中一种允许表达
AND 显著标记门槛满足（未配置则跳过）
AND 没有命中非参考正文自身包含的错误页标记
AND 本例额外结构规则满足（未配置则跳过）</pre>
<p>这是便于阅读的伪代码。显著标记列表非空时要求至少命中一个；错误页标记若也出现在参考正文中，会得到豁免，避免把介绍 HTTP 403 的正常技术文档当成错误页。最低长度检查的是返回文本字符数，不是清洗后的正文字符数。</p>
<p>匹配前对两边做确定性规范化：统一 Unicode 和大小写，清除特定零宽排版字符，折叠空白，保留 Markdown 链接的可见文字，去掉部分行内格式。例如 <code>Settings &gt; AI</code> 与 <code>Settings&gt;AI</code> 等价。数字、正负号、否定词和英文词边界不能被随意抹掉；同义改写没有登记为允许表达时仍可能漏判。</p>
<p><strong>这里最容易误读的地方：任务通过不要求“全文完整度 ≥ 90%”，也不要求 F1 达标。</strong>这些指标是另一组诊断信息。只覆盖少量核对点的通过，不能当作全文正确性的证明。</p>'''
    body += chapter('rules', '2. “任务通过”是规则验收，不是全文认证', rules)
    sample = next((r for r in records if r['provider']=='cowagent-built-in' and r['case_id']=='ecommerce-list'), None)
    example = '<p>具体门槛属于案例，而不是 provider。打开“评测集”的一个案例，就能查看参考和核对点；再进入执行详情，查看该方法实际命中了哪一项。</p>'
    if sample:
        case = cases[sample['case_id']]
        example += f'<p>以 {link("../datasets/ecommerce-list.html", "Books to Scrape 书目页")} 为例，目标是取回当前页书目正文。参考是独立保存的书目区域；最低长度为 <strong>{case["min_content_chars"]} 字符</strong>，核对点如下。</p>'
        example += table(['核对点', '允许表达', '实际结果'], [('', cell(e(f['id']))+cell(e(' / '.join(f['alternatives'])))+cell('命中' if f['pass'] else '缺失')) for f in sample['metrics']['fact_results']], metrics=[])
        raw = (out / sample['artifact_path']).read_text()
        keys = ['required_facts','forbidden_any','min_content_chars','fact_coverage_threshold','ground_truth_main_content','required_patterns']
        kwargs = {key:case[key] for key in keys if key in case}
        altered = raw.replace('51.77','999.99')
        original_score = evaluate_content(raw, case['expected_any'], **kwargs)
        altered_score = evaluate_content(altered, case['expected_any'], **kwargs)
        example += f'<p>{attempt(sample,"CowAgent 本次返回")}保存了 {len(raw):,} 字符，事实组命中 {sample["metrics"]["fact_hits"]}/{sample["metrics"]["fact_total"]}，任务结果为{"通过" if passed(sample) else "未通过"}。你可以下载原始输出，逐项查找上述文字，而不必相信一句概括性的“抓取成功”。</p>'
        example += f'<aside class="essay-note"><h3>离线反事实实验：只改价格会怎样？</h3><p>在这份已保存输出的副本里，把所有 <code>51.77</code> 改成 <code>999.99</code>，然后调用同一个 <code>evaluate_content</code>。原文正文规则{"通过" if original_score["content_pass"] else "未通过"}；修改后的价格核对点{"命中" if next(f for f in altered_score["fact_results"] if f["id"]=="first-price")["pass"] else "缺失"}，正文规则{"通过" if altered_score["content_pass"] else "未通过"}。文本依旧很长，请求状态也没有变化，内容验收却失败了。</p><p class="source-note">实验只操作内存副本，不改原始记录，也不计入榜单；回归测试会重新执行这一判断。</p></aside>'
        example += '<p>反过来，如果返回值保留这几个核对点，却漏掉未被抽查的一本书，仍可能通过。这是规则采样的边界，不应把“命中几项事实”写成“整页都正确”。</p>'
    body += chapter('example', '3. 顺着书名、价格和库存，亲自核算一次', example)
    quality = '''<p>全文指标使用参考和输出的<strong>词频重合</strong>：英文按词、中文按字，重复词按出现次数计数。它不是语义模型，也不保留句子顺序。</p>
<pre aria-label="全文指标公式">重合数 = Σ min(参考中某词的次数, 输出中该词的次数)
完整度 = 重合数 / 参考词数
纯净度 = 重合数 / 输出词数
F1 = 2 × 完整度 × 纯净度 / (完整度 + 纯净度)</pre>
<p>全文指标只覆盖有完整参考的案例；先算每一例，再按方法取算术平均。请求失败计 0，请求成功但规则未通过时仍按实际文本计分。F1 不能先平均完整度和纯净度再重新套公式，这两个计算顺序一般不等价。</p>'''
    low = min((r for r in cow if passed(r) and r['metrics'].get('word_f1') is not None), key=lambda r:r['metrics']['word_f1'], default=None)
    if low:
        m = low['metrics']
        quality += f'<aside class="essay-note"><h3>真实反例：通过，却夹带大量非参考文字</h3><p>{attempt(low,e(cases[low["case_id"]]["name"]))}中，CowAgent 任务通过，完整度为 <strong>{pct(m["word_recall"])}</strong>，纯净度仅 <strong>{pct(m["word_precision"])}</strong>，F1 为 <strong>{pct(m["word_f1"])}</strong>。关键点没漏，不代表输出适合直接进入检索索引或有限的模型上下文。</p></aside>'
    quality += '<p>完整度高但纯净度低，通常需要检查导航、目录、推荐区或参考范围的差异；它并不自动证明返回文字全部无用。即使三项分数都高，也不保证数字与实体的对应关系正确。词袋指标可能忽略语序变化，不能替代业务字段校验。</p><p>这也解释了“完整度 100% 却未通过”并不矛盾：相同词语可能散落在不同位置，没形成要求的事实表达；格式规范化的缺陷也可能误判。应打开核对点及原文定位原因，而不是凭单列数字直接改成通过。</p>'
    body += chapter('quality', '4. 找齐关键点，和交付干净的正文，是两回事', quality)
    cowbody = '<p>从本轮适配器和所核对源码看，CowAgent 的网页路线没有调用浏览器或大模型：HTTP 取页后，移除 script、style 和 HTML 标签，再整理文字。它不是靠额外推理理解页面后才得到高分。</p>'
    if cow:
        avg = lambda k: statistics.mean(r['metrics'][k] for r in cow if r['metrics'].get(k) is not None)
        cowbody += f'<p>在当前 {len(cow)} 个网页任务中，它有 <strong>{sum(r["ok"] for r in cow)} 次请求成功、{sum(passed(r) for r in cow)} 次任务通过</strong>。平均完整度 {pct(avg("word_recall"))}、纯净度 {pct(avg("word_precision"))}、F1 {pct(avg("word_f1"))}。这些数字更支持“关键内容保留较多”，而不是“具备更强浏览器能力”。</p>'
        rows=[]
        for name in ['cowagent-built-in','langchain-webbase','cloudflare-markdown','exa','moli-webfetch']:
            selected=[r for r in web if r['provider']==name]
            full=[r['metrics']['word_f1'] for r in selected if r['metrics'].get('word_f1') is not None]
            if selected:
                rows.append(('',cell(link('../providers/'+name+'.html',name))+cell(pct(sum(passed(r) for r in selected)/len(selected)))+cell(pct(statistics.mean(full)) if full else '—')))
        cowbody += table(['网页方法', '任务通过率', '平均 F1'], rows, metrics=[])
    cowbody += '''<h3>机制小实验：多保留文字，同时丢掉链接属性</h3><p>用一段包含正文、导航、页脚、详情链接、图片和视频的合成 HTML，直接执行 CowAgent 的纯文本提取函数。没有联网，也没有经过其他 provider。</p>'''
    probe_path = Path(__file__).resolve().parents[2] / 'research/cowagent-text-probe.json'
    if probe_path.exists():
        probe=json.loads(probe_path.read_text())
        (out/'guide').mkdir(exist_ok=True)
        (out/'guide/cowagent-text-probe.json').write_text(json.dumps(probe,ensure_ascii=False,indent=2)+'\n')
        cowbody += f'<details><summary>展开实验输入与实际输出</summary><h4>输入 HTML</h4><pre>{e(probe["fixture"])}</pre><h4>实际输出</h4><pre>{e(probe["output"])}</pre><p>{link("cowagent-text-probe.json","下载实验、提取函数与源码校验值")}</p></details>'
    cowbody += '''<p>正文、导航、页脚和链接文字都留下来了；脚本内容、<code>href</code>、<code>img.src</code> 和 <code>video.src</code> 没有留下来。这个结果同时解释了它的优势和代价：粗粒度提取不易误删某段文字，也不会自动把资源地址和详情链接交付给下游。</p>
<p>如果正文已经在服务器返回的 HTML 中，普通 HTTP 就可能完成任务；运行 JavaScript 不必然带来收益。如果页面必须执行脚本后才生成正文，这条路线就会受限。数据集的分类标签不是渲染需求的逐页证据，不能把所有文档站都当作已验证的静态站。</p>'''
    if cow:
        rows=[]
        for track,label in [('web','网页'),('document','文档'),('image','图片'),('video','视频'),('challenge','真实网站')]:
            selected=[r for r in records if r['provider']=='cowagent-built-in' and cases[r['case_id']].get('track','web')==track]
            if selected:rows.append(('',cell(label)+cell(str(sum(passed(r) for r in selected)))+cell(str(len(selected)))))
        cowbody += table(['CowAgent 任务范围','通过任务','总任务'],rows,metrics=[])
        failures=[r for r in cow if not passed(r)]
        cowbody += '<p>网页失败样本也值得先看：'+ '、'.join(attempt(r,e(cases[r['case_id']]['name'])) for r in failures)+ '。同一实现，在不同任务上的表现并不一致。</p>'
    cowbody += f'<p>实现依据：{link(SOURCES["cow"][1],"CowAgent WebFetch 源码")}；{link("https://github.com/lexmount/browser-harness-eval/blob/ba8d574093c2d8653ce79766972581610ef75ce2/harness/web_fetch/providers/cowagent.py","本轮适配器")}。历史 CowAgent 记录没有保存逐次源码指纹；这里的小实验验证的是核对时源码机制，不把它冒充为对历史每次调用的完整因果证明。</p>'
    cowbody += '<p><strong>可以解释，但尚未证明因果：</strong>较宽的文字保留范围、当前以文档与开发者页面为主的样本，以及只检查部分关键点的门槛，与它的高通过率相符。没有做“同一响应字节、替换不同提取算法”的全面对照实验，不能量化哪个因素贡献了多少，更不能据此宣称它胜过所有托管服务。</p>'
    body += chapter('cowagent','5. CowAgent 的高通过率，来自什么，又不代表什么？',cowbody)
    file_records=[r for r in records if cases[r['case_id']].get('task_kind')=='file']
    missing_file=sum('raw_file_pass' not in r['metrics'] for r in file_records)
    limits = '''<p>这是一组固定网址与参考上的单轮记录，不是全网抽样，也不是每个方法完整产品能力的竞赛。以下边界会直接影响结论。</p>
<ul><li><strong>任务权重：</strong>网页有 100 例，其他每组 10 例。把它们直接混成总分，会让网页决定大部分排名。先按业务选任务组，再比较方法。</li>
<li><strong>采样与参考：</strong>关键点有限，长度规则可被杂文满足；完整参考依赖正文范围选取。固定阈值是验收约定，不是经过统计证明的正确性概率。</li>
<li><strong>动态页面与时间：</strong>不同方法并非全部同时运行，Moli 是补跑。相同 URL 可能返回不同时间、地区、会话或个性化内容；保存参考不等于所有方法抓到同一页面版本。</li>
<li><strong>独立证据的覆盖：</strong>动态列表返回旧参考之外的新条目时，需要独立证据才能确认。缺证据记为未通过会压低这一类分数，但不能把它写成 provider 返回错误。</li>
<li><strong>耗时有条件：</strong>均值、P50、P95 只看通过的任务，不包含失败等待，也不代表每种方法面对完全相同的一组成功样本。缺少重复试验，几个百分点不能被当成显著差异。</li>
<li><strong>接口和配置：</strong>参评的是明确配置下的某个接口，不是品牌的所有 API。没有成本、生产并发和长期稳定性测试；官方能力应另看“方法介绍”。</li></ul>'''
    if missing_file:
        limits += f'<aside class="essay-note"><h3>原文件一列要特别谨慎</h3><p>本轮 {len(file_records)} 条原文件任务记录中，{missing_file} 条未写入 <code>raw_file_pass</code>。当前运行器没有接通统一的原文件字节验收链路，缺少验收标记按未通过处理。因而表格里的 0 只能表示“没有已验证通过记录”，<strong>不能解释为这些产品都不支持下载文件</strong>。有效比较还需要接通附件返回、MIME、字节数和 SHA-256 校验。</p></aside>'
    body += chapter('limits','6. 在这些地方，榜单还不能替你下结论',limits)
    body += chapter('decide','7. 从自己的一个失败案例开始选型', '''<p>如果你的需求是给知识库取技术文档，先看网页任务中的失败案例和正文噪声，而不是先追求一个总榜冠军。CowAgent 可以作为低复杂度的候选；当目标正文必须依靠脚本生成，或需要保留资源入口时，再检查浏览器型或托管方法是否满足接口契约。</p>
<p>建立一次选型判断，可以按下面的顺序走：</p><ol><li>写清下游需要的是正文、结构化字段、资源链接还是文件字节。</li><li>从自己的目标网站挑一组固定任务，先保存参考和可执行验收规则。</li><li>在相同时间窗重复运行候选方法，保留每次失败，不只挑成功样本。</li><li>并排查看原文、核对点和输出，再比较可用内容量、成本与成功任务耗时。</li></ol>
<p>在本报告中，先从 <a href="../datasets/index.html">评测集</a> 找一个与你业务相近的任务，进入详情对照两个方法。需要核查接口与官方能力时，跳到 <a href="../adoption/index.html">方法介绍</a>；准备自己复跑时，查看 <a href="../reproduce/index.html">复现说明</a>。一次能解释清楚的失败，比一个无法解释的高通过率更能帮助接入决策。</p>''')
    body += '<section id="reading"><h2>进一步核对</h2><p>'+link('../methodology/index.html','全部评测规则')+' · '+link('../environment/index.html','执行条件与时间')+' · '+link('../results.jsonl','完整执行记录')+'</p><p class="source-note">写作结构参考 Simon Willison 的 '+link('https://simonwillison.net/2025/Jul/17/vibe-scraping/','Open Sauce 日程抓取实践')+'：从具体问题切入，展示输入、结果与可追查证据，再讨论失败与限制。本文的指标、实验与结论均来自本项目，不沿用该文的工具能力结论。</p></section>'
    return '<div class="wrap essay">'+reader+'<div class="essay-layout">'+toc+'<article class="essay-body">'+body+'</article></div></div>'
