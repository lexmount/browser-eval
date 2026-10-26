"""Create an isomorphic English view of the generated Chinese report.

Measured material is intentionally left in its source language.  Only report chrome,
editorial copy, labels, and judgement vocabulary are localized.  Both editions are
therefore generated from the same rows rather than maintained as separate reports.
"""
from __future__ import annotations

import html
import json
import posixpath
import re
import shutil
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup, Comment

from harness.web_fetch.report_reading import archived_translations, render_archived_reading

NAV = [
    ("datasets/index.html", "Dataset"),
    ("methodology/index.html", "Methodology"),
    ("analysis/index.html", "Analysis"),
    ("review/index.html", "Review"),
    ("guide/index.html", "Interpretation"),
    ("adoption/index.html", "Methods"),
    ("reproduce/index.html", "Reproduction"),
    ("changes/index.html", "Changelog"),
    ("accounts/index.html", "Access & setup"),
]


# Exact editorial translations take priority.  Dynamic evidence (task names,
# references and provider output) is deliberately absent from this catalog.
_TRANSLATIONS = json.loads(Path(__file__).with_name("report-translations.json").read_text(encoding="utf-8"))

TEXT = dict(_TRANSLATIONS['TEXT'])


# These strings are presentation copy shared by the overview, methodology,
# provider, review, release-note, and reproduction pages.  Keeping them here
# (rather than maintaining a second set of report builders) is intentional:
# both languages still render the same measured rows and evidence objects.


# Remaining interface copy is report prose rather than measured
# provider output, so the English top-level pages localize it.

# Reader-facing task-family copy from report_components.py. These strings are
# rendered on the methodology page and therefore need full English editions;
# measured task names and prompts remain unchanged.


# Some generated blocks concatenate several fixed sentences into one text node,
# or split a sentence around an inline link/code element.  These replacements
# are deliberately narrow report phrases; they never include task names,
# reference answers, or provider output.
PHRASES = dict(_TRANSLATIONS['PHRASES'])


# The adoption page carries a large, evidence-bound capability and pricing
# inventory. Keep its exact-string translations in small data-only modules so
# the shared localization engine remains reviewable.


def _body_region_summary(match):
    regions = match[1]
    for source, target in (("开头", "Opening"), ("前部", "Early section"),
                           ("中部", "Middle"), ("后部", "Late section"),
                           ("结尾", "Ending"), ("位置", "Region")):
        regions = regions.replace(source + " ", target + " ")
    return ("Body coverage is incomplete. Regions below target: "
            + regions.replace("、", ", ")
            + ". The complete reference and actual output are shown side by side below.")


REGEX = [
    (re.compile(r"^新批次启用自身 DEBUG 并捕获网络库日志；旧 (\d+) 条不计入本报告。$"), r"The new batch enabled native DEBUG output and captured network-library logs; \1 earlier records are excluded from this report."),
    (re.compile(r"^每次物理调用预算为 ([\d.]+) 秒，重试后的累计耗时可能超过该预算。$"), r"Each physical call has a \1-second budget; accumulated latency after retries can exceed it."),
    (re.compile(r"^本报告包含 (\d+) 个任务、(\d+) 个逻辑结果，任务通过 (\d+)/(\d+)。结论仅适用于已登记的任务和规则。$"), r"This report contains \1 tasks and \2 logical results, with \3/\4 task passes. Conclusions apply only to the registered tasks and rules."),
    (re.compile(r"^的 F1 为 ([\d.]+%)，但没有满足该例任务规则。零通过表示没有一次满足全部任务门槛，不表示输出与参考毫无词语重合。这里的 F1 对照已保存的样本文本，只诊断文字重合，不承担实时事实核验。词频相似度允许部分文字得分，任务规则检查指定的内容或字段结构；二者应保留各自的含义。$"),
     r" has F1 \1 but does not satisfy its task rules. Zero passes means no call met every threshold, not zero textual overlap. This F1 compares saved sample text only and does not verify live facts. Token-frequency similarity can award partial text credit, while task rules check required content or field structure; the two retain distinct meanings."),
    (re.compile(r"^目标资源已命中，但答案字段还返回了非目标资源：(.+)$"),
     r"The target resource was found, but answer-bearing fields also returned unexpected resources: \1"),
    (re.compile(r"^未命中资源：(.+)；同时返回非目标资源：(.+)$"),
     r"Missing resources: \1; also returned unexpected resources: \2"),
    (re.compile(r"^已找到目标资源，但答案字段还返回了非目标资源：(.+)$"),
     r"The target resource was found, but answer-bearing fields also returned unexpected resources: \1"),
    (re.compile(r"^最终报告纳入 ([\d,]+) 次调用。任务与参考冻结后执行；每个方法内部串行，不复用个人登录状态。$"), r"The final report includes \1 calls. Execution began after tasks and references were frozen; calls were sequential within each method and did not reuse personal login state."),
    (re.compile(r"^初始源文档审计进一步缩小了这批样本的解释范围：静态网站正文 (\d+) 例中，(\d+) 例的冻结参考，可以从已保存的初始 HTML 经参考提取与规范化后精确复现。这是对参考文本可恢复性的检查，既不意味着它们都是可见静态正文，也不意味着 CowAgent 使用了同样的提取器。嵌入脚本的载荷仍需要正确解析。高网页通过率因此不能直接外推到初始响应之外才到达的内容。$"), r"The initial-source audit further narrows how this sample can be interpreted: for \1 general-web body tasks, \2 frozen references can be reproduced exactly from the saved initial HTML after reference extraction and normalization. This checks reference-text recoverability; it neither means that all of the text was statically visible nor that CowAgent used the same extractor. Embedded script payloads still require correct parsing. High web pass rates therefore cannot be extrapolated to content that arrives only after the initial response."),
    (re.compile(r"^这组另有 (\d+)/(\d+) 条返回标题能按详情链接对应到独立名称参考，据此计算标题完整度、纯净度和 F1。例如一部电影从第三名移到第十名，只要仍返回同一详情链接，就比较这部电影的名称，不比较名次，也不要求旧榜单里的电影继续出现。只返回电影名称可能取得很高的标题分，却仍不足以完成至少三条结果的任务。因此这三个分数须与任务通过率和参考覆盖量一起读，不能当作整页正文质量。新条目没有独立名称参考时不计文字分；同一条目的标题也可能改写，重合度只是相对已记录名称的诊断，不认证当前内容真假。$"), r"For this group, \1/\2 returned titles can be joined by detail URL to independent name references, enabling title recall, precision, and F1. If a film moves from third to tenth while retaining the same detail URL, its name is compared without comparing rank or requiring old list membership. Returning names alone can score highly on title overlap while still failing the task's minimum of three complete records. Read these three diagnostics together with task pass rate and reference coverage; they are not whole-page body-quality metrics. New entries without independent name references are excluded, and titles may be rewritten, so overlap is only a diagnostic against recorded names, not proof that current content is true."),
    (re.compile(r"^获取原图：(.+)$"), lambda match: "Download original: " + translate_text(match.group(1))),
    (re.compile(r"^图 (\d+) · (.+?)。(.+) 点击图像获取原图。$"),
     lambda match: f"Figure {match.group(1)} · {translate_text(match.group(2))}. "
                   f"{translate_text(match.group(3))} Select the image to download the original."),
    (re.compile(r"^同窗速度子集：预先声明 (\d+) 题，(\d+) 种方法使用同一完整任务样本。全调用耗时包含成功、失败和截止超时；通过调用耗时按各方法自己的成功子集计算，分母必须同时阅读。每种方法最多 (\d+) 个单次观察，P95 只作本次样本诊断。$"), r"Same-window latency cohort: \1 tasks were declared in advance and all \2 methods use the same complete task sample. All-call latency includes successes, failures, and deadline timeouts; successful-call latency uses each method's own successful subset, so its denominator must be read alongside it. Each method has at most \3 single observations; P95 is only a diagnostic for this sample."),
    (re.compile(r"^同窗速度子集：预先声明 (\d+) 题，(\d+) 种方法共同通过 (\d+) 题；所有方法使用完全相同的成功样本。P95 只作本次样本诊断。$"), r"Same-window latency cohort: \1 tasks were declared in advance and all \2 methods jointly passed \3; every method uses exactly the same successful sample. P95 is only a diagnostic for this sample."),
    (re.compile(r"^这份旧版测速汇总只使用 (\d+)/(\d+) 个全部 (\d+) 种方法共同通过的同窗任务；它保留原报告的共同成功样本口径，不能补充解释失败等待，也不能与跨批次主结果直接排名。$"), r"This legacy latency summary uses only the \1/\2 same-window tasks jointly passed by all \3 methods. It preserves the original report's shared-pass denominator, cannot account for time spent on failures, and cannot be ranked directly against cross-batch primary results."),
    (re.compile(r"^全部 (\d+) 种方法共同通过 (\d+)/(\d+) 题；共同通过数只作诊断，不用于筛选测速样本。报告保留预声明的全部 ([\d,]+) 次调用，不按结果重新选题；全调用耗时可在相同分母下阅读，通过调用耗时只描述各方法的条件分布，不能解释为同题速度排名。$"), r"All \1 methods jointly pass \2/\3 tasks; the shared-pass count is diagnostic only and is not used to select the latency sample. The report retains all \4 predeclared calls without reselecting tasks after observing outcomes. All-call latency uses a common denominator; successful-call latency only describes each method's conditional distribution and is not a matched-task speed ranking."),
    (re.compile(r"^全部 (\d+) 种受测配置共同通过$"), r"All \1 tested configurations pass"),
    (re.compile(r"^(\d+) 个普通网页中，GitHub 与 Python 官方文档占 (\d+) 个，样本偏向技术文档$"), r"Of \1 general-web tasks, \2 are from GitHub or the official Python documentation, so the sample is weighted toward technical documentation"),
    (re.compile(r"^统计按实际调用记录计算，未运行和缺少验收证据的项目不记为失败。静态网站正文、动态网站正文与热门网站正文分别陈述，文档、资源地址和原文件按各自契约验收。(\d+) 个普通网页中，GitHub 与 Python 官方文档占 (\d+) 个，样本偏向技术文档；每个方法与案例组合只有一次观察，不能估计波动或外推为全网成功率。调用日期相邻不构成同时间对照，公开练习站也不能代表生产网站分布。$"), r"Statistics use the calls actually run; items that were not run or lack acceptance evidence are not counted as failures. General-web, dynamic-web, and popular-site body text are reported separately, while documents, resource URLs, and original files follow their own contracts. Of \1 general-web tasks, \2 are from GitHub or the official Python documentation, so the sample is weighted toward technical documentation. Each method–task pair has only one observation, which cannot estimate variation or be extrapolated to a web-wide success rate. Nearby call dates are not a simultaneous control, and public practice sites do not represent the distribution of production websites."),
    (re.compile(r"^静态网页通过数：(.+)。综合榜按 (\d+) 个任务等权计数；(\d+) 类分榜仍分别解释，耗时和文字质量不混入综合通过率。$"), r"General-web passes: \1. The overall ranking counts \2 tasks equally. Each of the \3 family rankings is interpreted separately, and neither latency nor text quality is blended into overall task pass rate."),
    (re.compile(r"^数据截至 (.+)（UTC） · 正文、文档与媒体交付实测 ·$"), r"Data through \1 (UTC) · measured body-text, document, and media delivery ·"),
    (re.compile(r"^先看静态网站正文：([\d,]+) 个任务、([\d,]+) 种方法，共 ([\d,]+) 次调用。“请求成功”指接口正常返回，“任务通过”还要求正文满足该例验收规则。$"), r"Start with general-web text: \1 tasks, \2 methods, and \3 calls. “Request succeeded” means the interface returned normally; “Task passed” also requires the body to satisfy that task's acceptance contract."),
    (re.compile(r"^静态网站正文调用的互斥状态分解；(.+)。正常返回不等于源站 HTTP 200。$"), lambda match: f"Mutually exclusive outcomes for general-web calls; {translate_text(match.group(1))}. A normal provider response does not imply origin HTTP 200."),
    (re.compile(r"^(\d+) 个任务覆盖异步取数、客户端呈现与子文档获取。实际运行 (\d+) 种方法、([\d,]+) 次调用，([\d,]+) 次请求正常返回，([\d,]+) 次任务通过。任务难度、源站状态和调用日期没有与静态网站正文任务构成受控对照；两类结果分别比较。$"), r"The \1 tasks cover asynchronous retrieval, client-side presentation, and subdocument fetching. \2 methods made \3 calls; \4 returned normally and \5 passed. Task difficulty, origin state, and call date were not controlled against general-web tasks, so the two groups are compared separately."),
    (re.compile(r"^动态网站正文：([\d,]+) 个任务、([\d,]+) 种已运行方法；分母按每个任务实际运行数计算$"), r"Rendered web text: \1 tasks and \2 measured methods; each task uses its actual run count as denominator."),
    (re.compile(r"^CowAgent 在静态网站正文中请求成功 ([\d,]+)/([\d,]+)，任务通过 ([\d,]+)/([\d,]+)。一个以 HTTP 取页、再去除标签的实现，在这批网页上已能满足大部分关键内容规则。这使它值得作为正文入口的基线，但还不足以解释全部成功来自哪里。$"), r"CowAgent returned normally on \1/\2 general-web calls and passed \3/\4 tasks. An implementation that fetches via HTTP and strips tags satisfies most key content rules in this sample. It is worth considering as a body-text baseline, but this does not explain every success."),
    (re.compile(r"^同一配置的 Moli，在静态网站正文任务中通过 ([\d,]+)/([\d,]+)，在动态网站正文中通过 ([\d,]+)/([\d,]+)。网页高分为什么没有自然延伸到动态内容？关键在于目标内容在哪里、何时到达，以及接口最终保留了什么。$"), r"With the same configuration, Moli passes \1/\2 general-web tasks and \3/\4 rendered-web tasks. Why does strong general-web performance not extend automatically to dynamic content? The answer depends on where and when target content arrives and what the interface ultimately retains."),
    (re.compile(r"^(?:失败集中在正常返回之后|请求状态没有覆盖全部任务失败)：正常返回但任务未通过，占静态网站正文全部失败的 ([^。]+)。单看接口错误率，会漏掉相当一部分未交付任务。返回错误页面、正文缺失或内容规则不满足，都可能落在这里。接入服务后，应检查下游所需的内容，并保留可定位的失败原因。$"), lambda match: f"Normal responses that fail task acceptance account for {translate_text(match.group(1))} of all general-web failures. Interface error rate alone misses part of the undelivered workload. Error pages, missing body text, and unmet content rules can all fall here. After integration, validate downstream-required content and retain actionable failure reasons."),
    (re.compile(r"^Cloudflare Markdown 在 ([\d,]+) 次网页调用中通过 ([\d,]+) 次，Exa 为 ([\d,]+)/([\d,]+)。(按全文 F1 看，次序却倒过来了。|全文 F1 给出另一种观察角度。)任务验收关心指定内容是否交付，F1 关心输出与参考正文的词频重合。同一个“最好”，在这两个目标下可能指向不同方法。$"), lambda match: f"Cloudflare Markdown passes {match.group(2)} of {match.group(1)} web calls; Exa passes {match.group(3)}/{match.group(4)}. " + ("By full-text F1, the order reverses. " if match.group(5).startswith('按') else "Full-text F1 provides another perspective. ") + "Task acceptance asks whether specified content was delivered; F1 measures token-frequency overlap with reference body text. “Best” can name different methods for these two goals."),
    (re.compile(r"^在两者都有一次记录的 ([\d,]+) 个网页任务上，CowAgent 通过 ([\d,]+) 例，Moli 通过 ([\d,]+) 例。按案例 ID 对齐后，Moli 能补上 CowAgent 未通过的 ([\d,]+) 例；反方向，CowAgent 也能补上 Moli 未通过的 ([\d,]+) 例。只看单方法分数，会丢掉这层信息。$"), r"Across \1 web tasks with one result from each method, CowAgent passes \2 and Moli passes \3. After aligning task IDs, Moli recovers \4 CowAgent failures, while CowAgent recovers \5 Moli failures. A single-method score hides this complementarity."),
    (re.compile(r"^二者成功集的离线并集为 ([\d,]+)/([\d,]+)。这给出的决策是开展一次有内容校验的候选 fallback 试验。它尚未测试串行调用、失败识别与实时重试，更不构成线上全覆盖保证。$"), r"The offline union of their passing sets is \1/\2. This supports running a candidate fallback experiment with content validation. It has not tested sequential calls, failure detection, or live retries, and does not guarantee complete production coverage."),
    (re.compile(r"^在共同通过且都有耗时的 ([\d,]+) 例中，CowAgent 的客户端墙钟耗时中位数为 ([\d.]+) ms，Moli 为 ([\d.]+) ms。对齐成功任务后，这个比较排除了各自成功集不同的影响；失败等待仍未计入，不同时间批次、页面状态和网络条件也没有被消除。它不能用来相加计算生产 fallback 延迟，本轮也没有实测金额，不能据此推出成本节省。$"), r"Among \1 jointly passed tasks with latency, median client wall time is \2 ms for CowAgent and \3 ms for Moli. Aligning successful tasks removes differing success sets, but still excludes failure waits and does not control batch timing, page state, or network conditions. These values cannot be added to estimate production fallback latency; no cost was measured, so they do not imply savings."),
    (re.compile(r"^热门网站正文的 ([\d,]+) 个任务有 ([\d,]+) 次调用，其中 ([\d,]+) 次满足各例任务规则。单方法最好通过 ([\d,]+)/([\d,]+)，各方法成功集的离线并集为 ([\d,]+)/([\d,]+)。这里的通过指任务声明的内容与结构契约，并不认证全部文字与某份实时快照一致。$"), r"The \1 popular-site tasks have \2 calls, of which \3 satisfy their task rules. The best single method passes \4/\5; the offline union of method success sets is \6/\7. Passing means satisfying the declared content-and-structure contract, not certifying every word against a live snapshot."),
    (re.compile(r"^原文件任务尤其需要区分缺测与产品失败。本轮共有 ([\d,]+) 条记录，([\d,]+) 条没有写入$"), r"Original-file tasks especially require distinguishing unmeasured cases from product failures. This run has \1 records; \2 do not contain "),
    (re.compile(r"^，只有 ([\d,]+) 条形成已验证通过记录。缺失验收的记录显示为未测，不进入任务通过率分母或失败数；不能据此宣称参评产品不支持文件下载。后续调用通过统一文件入口核验返回字节、格式特征、长度及 SHA-256，已有独立文件哈希时还须匹配目标。历史调用没有这些证据，不能补认证为测过。$"), r"; only \1 are verified passes. Records without acceptance evidence are shown as unmeasured and excluded from pass-rate denominators and failure counts; they do not establish that the evaluated product lacks file-download support. Subsequent calls validate returned bytes, format signatures, length, and SHA-256 through the shared file path, and must match an independent target hash when available. Historical calls without this evidence cannot be retroactively certified as measured."),
    (re.compile(r"^其中 ([\d,]+)/([\d,]+) 个案例还登记了任务相关的固定文字：例如 IMDb 的榜单说明、JustWatch 的观看方式说明，以及商品或餐馆的字段标签。这些文字与列表条目共同决定任务是否通过，不能只拿到三个链接就算完成。每例命中多少必需文字组，由文字核对覆盖率回答；没有文字依据的案例不计入这项均值。少量核对句不是整页参考，因此不能拿它们计算整页纯净度和 F1，否则完整返回的其它正文反而会被当成杂文扣分。$"), r"Of these, \1/\2 tasks also register task-relevant stable text, such as IMDb ranking explanations, JustWatch viewing information, and product or restaurant field labels. This text and the records jointly determine acceptance; three links alone are insufficient. Required-text coverage reports matched groups per task, excluding tasks without textual evidence. A few check sentences are not a whole-page reference and must not be used for whole-page precision or F1, which would penalize other correctly returned text as noise."),
    (re.compile(r"^延迟名言页把等待条件的问题暴露得更直接。页面可以已经完成初始请求，网络暂时空闲，正文却还要等计时器触发。本次延迟页的 ([\d,]+) 次调用中，([\d,]+) 次满足完整内容规则。浏览器启动成功或 network-idle 到达，都不能单独充当内容完成的证据。应按目标条目出现、数量与关键内容稳定来判断何时取结果；本轮没有对各种等待策略做消融，不能从一次失败计算换策略后的收益。$"), r"The delayed-quote page exposes completion criteria directly. Its initial request may finish and the network may go idle while body text still awaits a timer. Of \1 calls, \2 satisfy the complete-content rule. Browser startup or network-idle alone is not evidence of completion. Results should be taken only after target records, counts, and key content stabilize. This run did not ablate waiting strategies, so one failure cannot estimate the benefit of another strategy."),
    (re.compile(r"^(.+) 在动态网站正文中通过 ([\d,]+)/([\d,]+)，平均 F1 却为 ([\d.]+%)。其中$"), lambda m: f"{translate_text(m.group(1))} passes {m.group(2)}/{m.group(3)} rendered-web tasks but has mean F1 {m.group(4)}. Among them, "),
    (re.compile(r"^的 F1 为 ([\d.]+%)，但没有满足该例任务规则。零通过表示没有一次满足全部任务门槛，不表示输出与参考毫无词语重合。这里的 F1 对照已保存的样本文本，只诊断文字重合，不承担实时事实核验。这份输出仍有$"), r"has F1 \1 but does not satisfy its task rules. Zero passes means no call met every threshold, not zero textual overlap. This F1 compares saved sample text only and does not verify live facts. The output still contains "),
    (re.compile(r"^：完整度 ([\d.]+%)，纯净度 ([\d.]+%)，F1 ([\d.]+%)。这些指标描述输出与该例参考的重合程度。参考外文字可能是导航，也可能是有用的目录或附录，仅凭低纯净度不能认定它们全是噪声，应先核对任务的正文范围。$"), r": recall \1, precision \2, and F1 \3. These metrics describe overlap with the task reference. Text outside the reference may be navigation or useful contents and appendices; low precision alone does not prove it is all noise. Check the task's body scope first."),
    (re.compile(r"^CSS 2\.1 标准是一个具体例子：文档目录属于本例要求保留的正文，CowAgent 这次输出相对完整范围的 F1 为 ([\d.]+%)。若参考剔除长目录，保留目录的方法会被大量扣分；这样的分差反映参考范围不一致，不能用来证明方法提取了大量噪声。$"), r"CSS 2.1 is a concrete example: its contents belong to the required body, and CowAgent's output has F1 \1 against the complete scope. Removing a long contents section from the reference would heavily penalize methods that retain it; such a difference reflects inconsistent reference scope, not proof of noisy extraction."),
    (re.compile(r"^要求书名、价格和库存核对点满足，返回长度至少为 ([\d,]+) 字符。$"), r"requires its title, price, and stock checks and at least \1 returned characters."),
    (re.compile(r"^有 ([\d,]+) 字符。$"), r"contains \1 characters."),
    (re.compile(r"^。原文正文规则通过；替换后长度规则通过，价格核对点未通过，正文规则未通过。请求状态没有改变，但内容验收改变了。这个离线反事实只改副本，不改实测记录、不进入排名。$"), r". The original passes the body rule; after replacement, length still passes but the price and body checks fail. Request status is unchanged while content acceptance changes. This offline counterfactual changes only a copy, not measured records or rankings."),
    (re.compile(r"^([\d]{4}-[\d]{2}-[\d]{2} [\d:]+) 至 ([\d]{4}-[\d]{2}-[\d]{2} [\d:]+) UTC$"), r"\1 to \2 UTC"),
    (re.compile(r"^离线成功集分解，分母 ([\d,]+) 个配对网页任务；每例只进入一个分组$"), r"Offline success-set partition over \1 paired web tasks; each task appears in exactly one group"),
    (re.compile(r"^评测集没有覆盖计划；存在 ([\d,]+) 个计划外调用(?:；存在 ([\d,]+) 个无法判定通过或失败的调用)?。本页排名不得用于结论或发布。$"),
     lambda match: f"The evaluation does not cover its plan; {match.group(1)} unplanned calls are present"
                   + (f"; {match.group(2)} calls have no pass/fail judgement" if match.group(2) else "")
                   + ". Rankings on this page must not be used for conclusions or publication."),
    (re.compile(r"^(\d+) 个案例 · (\d+) 种方法 · 本地未认证诊断快照。$"), r"\1 tasks · \2 methods · local uncertified diagnostic snapshot."),
    (re.compile(r"^(\d+) 个案例 · (\d+) 种方法 · (\d+)\s*类分榜与一个综合通过率总榜。$"), r"\1 tasks · \2 methods · \3 family rankings and one overall coverage ranking."),
    (re.compile(r"^(\d+)\s*类任务概览$"), r"\1 task families"),
    (re.compile(r"^(\d+)\s*类分榜$"), r"Rankings for \1 task families"),
    (re.compile(r"^(\d+)\s*类通过数$"), r"Passes across \1 task families"),
    (re.compile(r"^每种方法都执行当前评测集计划的 (\d+) 个任务，每题计一次通过或失败，直接相加；耗时单列展示，不参与通过率排序。总榜适合看整体交付覆盖，具体能力仍应回到 (\d+) 个分榜。$"), r"Every method runs the current dataset's \1 planned tasks. Each task contributes one pass or fail and the results are summed directly; latency is shown separately and does not affect pass-rate ranking. The overall ranking shows delivery coverage, while specific capabilities belong in the \2 family rankings."),
    (re.compile(r"^每种方法在同一组 (\d+) 个计划任务上的端到端耗时，包含通过与失败；三项不参与通过率排名。主矩阵调用跨日期、跨批次，不等同于同窗受控速度比较。$"), r"End-to-end latency for each method across the same \1 planned tasks, including both passes and failures. These metrics do not affect pass-rate ranking. Primary-matrix calls span dates and batches and are not a same-window controlled speed comparison."),
    (re.compile(r"^供应商明确声明返回内容已截断：(.*)$"), r"The provider explicitly declares that the returned body is truncated: \1"),
    (re.compile(r"^当前报告共 (\d+) 个案例、(\d+) 种方法和 ([\d,]+) 条权威逻辑结果；其中其他 ([\d,]+) 条未被本次文件补测改写。$"), r"The current report contains \1 tasks, \2 methods, and \3 authoritative logical results; the other \4 results were not changed by this file-task rerun."),
    (re.compile(r"^至少 (\d+) 条不同记录$"), r"At least \1 distinct records"),
    (re.compile(r"^排除：(.+)$"), r"Exclude: \1"),
    (re.compile(r"^至少 ([\d,]+) 字符$"), r"At least \1 characters"),
    (re.compile(r"^([\d,]+) 字符$"), r"\1 characters"),
    (re.compile(r"^至少 (\d+)/(\d+) 区达到 ([\d.]+%)；每区不低于 ([\d.]+%)$"), r"At least \1/\2 regions reach \3; every region is at least \4"),
    (re.compile(r"^(\d+) 个替换字符$"), r"\1 replacement characters"),
    (re.compile(r"^(\d+)/(\d+) 组命中$"), r"\1/\2 groups matched"),
    (re.compile(r"^至少 (\d+) 条，字段与链接规则合格$"), r"At least \1 records with valid fields and links"),
    (re.compile(r"^去重后至少 (\d+) 条$"), r"At least \1 after deduplication"),
    (re.compile(r"^至少 ([\d,]+) 个字符$"), r"At least \1 characters"),
    (re.compile(r"^([\d,]+)，达标$"), r"\1, meets requirement"),
    (re.compile(r"^区域 (\d+)$"), r"Region \1"),
    (re.compile(r"^文件获取 ([\d.]+) 秒；解析 ([\d.]+) 秒；端到端 ([\d.]+) 秒$"), r"File retrieval: \1 seconds; parsing: \2 seconds; end to end: \3 seconds"),
    (re.compile(r"^([\d,]+) / ([\d,]+) 个任务$"), r"\1 / \2 tasks"),
    (re.compile(r"^(Moli|pypdf) · ([\d.]+) 秒$"), r"\1 · \2 seconds"),
    (re.compile(r"^端到端 ([\d.]+) 秒$"), r"End to end: \1 seconds"),
    (re.compile(r"^输入《Attention Is All You Need》的 PDF 地址，目标是取得论文正文。Moli 获取文件约用 ([\d.]+) 秒，本地 pypdf 提取文字约用 ([\d.]+) 秒，整条路线共 ([\d.]+) 秒，返回 ([\d,]+) 个字符。五段参考正文全部覆盖，本题词级 Precision、Recall、F1 均为 100%。$"), r"The Attention Is All You Need PDF task requires the paper's body text. Moli retrieves the file in about \1 seconds, local pypdf extracts text in about \2 seconds, and the complete path takes \3 seconds, returning \4 characters. All five reference body regions are covered, with word-level precision, recall, and F1 each at 100%."),
    (re.compile(r"^代表案例：(.*)$"), r"Representative cases: \1"),
    (re.compile(r"^([\d,]+) 个字符$"), r"\1 characters"),
    (re.compile(r"^([\d,]+) 字节$"), r"\1 bytes"),
    (re.compile(r"^([\d.]+) 秒$"), r"\1 seconds"),
    (re.compile(r"^([\d,]+) 条$"), r"\1 records"),
    (re.compile(r"^原始字节 · ([\d.]+) 秒$"), r"Raw bytes · \1 seconds"),
    (re.compile(r"^提取文字 · ([\d.]+) 秒$"), r"Text extraction · \1 seconds"),
    (re.compile(r"^整条路线 ([\d.]+) 秒$"), r"Complete path: \1 seconds"),
    (re.compile(r"^([\d,]+) 个字符 · ([\d.]+) 秒$"), r"\1 characters · \2 seconds"),
    (re.compile(r"^(\d+) 个案例 · (\d+) 种方法 · 六类分榜与一个综合通过率总榜。$"), r"\1 tasks · \2 methods · six family rankings and one overall coverage ranking."),
    (re.compile(r"^(\d+) 个任务$"), r"\1 tasks"),
    (re.compile(r"^(\d+) 个案例 · (\d+) 种已测方法$"), r"\1 tasks · \2 measured methods"),
    (re.compile(r"^(\d+)/(\d+) · Codex 已核验$"), r"\1/\2 · reviewed by Codex"),
    (re.compile(r"^方法：(.*)$"), r"Method: \1"),
    (re.compile(r"^任务 ID：(.*)$"), r"Task ID: \1"),
    (re.compile(r"^运行 ID：(.*)$"), r"Run ID: \1"),
    (re.compile(r"^第 (\d+) 轮$"), r"Round \1"),
    (re.compile(r"^显示 (\d+) 条$"), r"Showing \1"),
    (re.compile(r"^找到 (\d+) 个任务$"), r"\1 tasks found"),
    (re.compile(r"^(\d+) 字符$"), r"\1 characters"),
    (re.compile(r"^(\d+) 字符 ·$"), r"\1 characters ·"),
    (re.compile(r"^(\d+) 条执行记录$"), r"\1 execution records"),
    (re.compile(r"^(\d+) 个案例，按任务类型查看。$"), r"\1 tasks, grouped by task family."),
    (re.compile(r"^(\d+) 个案例$"), r"\1 tasks"),
    (re.compile(r"^(\d+)/(\d+) 次均有结果。$"), r"\1/\2 calls have results."),
    (re.compile(r"^独立核验：主结果 ([\d,]+)/([\d,]+)；同窗测速 ([\d,]+)/([\d,]+)；未决 (\d+)$"), r"Independent review: primary results \1/\2; same-window latency \3/\4; \5 unresolved"),
    (re.compile(r"^独立核验：全量 ([\d,]+)/([\d,]+)；未决 (\d+)$"), r"Independent review: full matrix \1/\2; \3 unresolved"),
    (re.compile(r"^独立核验：机器失败 ([\d,]+)/([\d,]+)；未决 (\d+)$"), r"Independent review: machine failures \1/\2; \3 unresolved"),
    (re.compile(r"^主结果机器失败 ([\d,]+)/([\d,]+)；机器通过未逐条审查 ([\d,]+)；代理分歧 ([\d,]+)；已裁定 ([\d,]+)；未决 ([\d,]+)。$"), r"Primary machine failures reviewed: \1/\2; machine passes not reviewed individually: \3; reviewer disagreements: \4; adjudicated: \5; unresolved: \6."),
    (re.compile(r"^主结果全量 ([\d,]+)/([\d,]+)；代理分歧 ([\d,]+)；已裁定 ([\d,]+)；未决 ([\d,]+)。$"), r"Full primary-matrix review: \1/\2; reviewer disagreements: \3; adjudicated: \4; unresolved: \5."),
    (re.compile(r"^十二种方法主矩阵的 ([\d,]+) 条机器失败结果；([\d,]+) 条机器通过结果和 ([\d,]+) 条同窗测速结果未逐条独立审查。$"), r"Machine failures in the twelve-method primary matrix: \1. The \2 machine passes and \3 same-window latency results were not independently reviewed item by item."),
    (re.compile(r"^从结构化结果中读取标题与详情链接，共 ([\d,]+) 个标题。$"), r"Read titles and detail URLs from structured results: \1 returned titles."),
    (re.compile(r"^([\d,]+)/([\d,]+) 个标题找到独立名称证据；没有对应证据的不评分。$"), r"\1/\2 titles join to independent name evidence; titles without matching evidence are not scored."),
    (re.compile(r"^覆盖 (\d+)/(\d+) 个案例；这是标题诊断，不参与任务通过判定。$"), r"Coverage: \1/\2 tasks. This is a title diagnostic and does not determine task acceptance."),
    (re.compile(r"^(\d+)/(\d+) · (\d+)/(\d+) 例（无可评分标题）$"), r"\1/\2 · \3/\4 tasks (no scorable titles)"),
    (re.compile(r"^(\d+) 个任务中满足各自验收规则的数量；每题等权。$"), r"Number of the \1 tasks satisfying their own acceptance contracts; every task has equal weight."),
    (re.compile(r"^当前参评方法共同通过 (\d+) 个案例；仅在这个相同子集内比较耗时，不代表失败任务的成本或生产 SLA。$"), r"The evaluated methods jointly pass \1 tasks. Latency is compared only on this identical subset; it does not represent the cost of failed tasks or a production SLA."),
    (re.compile(r"^完整参考覆盖 (\d+)/(\d+)；缺少参考只影响全文指标，不影响任务结果。$"), r"Complete-reference coverage: \1/\2. A missing reference affects only full-text metrics, not the task verdict."),
    (re.compile(r"^文字核对范围：各方法均为 (\d+)/(\d+) 例。$"), r"Required-text coverage: \1/\2 tasks for every method."),
    (re.compile(r"^(\d+) 例完整参考$"), r"\1 tasks with complete references"),
    (re.compile(r"^(\d+) 例列表任务$"), r"\1 list tasks"),
    (re.compile(r"^文字 (\d+) 项$"), r"\1 text checks"),
    (re.compile(r"^资源 · (\d+) 项$"), r"\1 required resources"),
    (re.compile(r"^(\d+(?:\.\d+)?%) · (\d+)/(\d+) 组$"), r"\1 · \2/\3 groups"),
    (re.compile(r"^— · 0/0 组$"), "— · 0/0 groups"),
    (re.compile(r"^(\d+)\. 开头（标准答案 0–20%）$"), r"\1. Opening (reference 0–20%)"),
    (re.compile(r"^(\d+)\. 前部（标准答案 20–40%）$"), r"\1. Early section (reference 20–40%)"),
    (re.compile(r"^(\d+)\. 中部（标准答案 40–60%）$"), r"\1. Middle (reference 40–60%)"),
    (re.compile(r"^(\d+)\. 后部（标准答案 60–80%）$"), r"\1. Late section (reference 60–80%)"),
    (re.compile(r"^(\d+)\. 结尾（标准答案 80–100%）$"), r"\1. Ending (reference 80–100%)"),
    (re.compile(r"^开头（标准答案 0–20%）$"), "Opening (reference 0–20%)"),
    (re.compile(r"^前部（标准答案 20–40%）$"), "Early section (reference 20–40%)"),
    (re.compile(r"^中部（标准答案 40–60%）$"), "Middle (reference 40–60%)"),
    (re.compile(r"^后部（标准答案 60–80%）$"), "Late section (reference 60–80%)"),
    (re.compile(r"^结尾（标准答案 80–100%）$"), "Ending (reference 80–100%)"),
    (re.compile(r"^(\d+)\. 位置 (\d+)（标准答案 (\d+)–(\d+)%）$"), r"\1. Region \2 (reference \3–\4%)"),
    (re.compile(r"^位置 (\d+)（标准答案 (\d+)–(\d+)%）$"), r"Region \1 (reference \2–\3%)"),
    (re.compile(r"^必须出现的稳定文字 (\d+)（任一写法即可）$"), r"Required stable text \1 (any listed variant)"),
    (re.compile(r"^用于范围核对的稳定文字 (\d+)（任一写法即可）$"), r"Stable text for scope diagnosis \1 (any listed variant)"),
    (re.compile(r"^稳定文字 (\d+)$"), r"Stable text \1"),
    (re.compile(r"^正文核对点-(\d+)$"), r"Body check \1"),
    (re.compile(r"^至少 (\d+) 条完整且不重复的记录$"), r"At least \1 complete, unique records"),
    (re.compile(r"^实际交付 (\d+) 个文件$"), r"\1 files delivered"),
    (re.compile(r"^实际交付 (\d+) 个文件 ·$"), r"\1 files delivered ·"),
    (re.compile(r"^实际返回 (\d+) 个带详情链接的标题。$"), r"The output contains \1 titles with detail URLs."),
    (re.compile(r"^(\d+)/(\d+) 个标题能按详情链接找到参考名称。$"), r"\1/\2 titles have independent name references joined by detail URL."),
    (re.compile(r"^(\d+)/(\d+) 条；仅衡量已返回且有独立参考的标题$"), r"\1/\2; only returned titles with independent references are measured"),
    (re.compile(r"^未命中资源：(.+)$"), r"Missing resource: \1"),
    (re.compile(r"^展开 (\d+) 个较早版本$"), r"Expand \1 earlier releases"),
    (re.compile(r"^(\d+) 个仓库$"), r"\1 repositories"),
    (re.compile(r"^(\d+)/(\d+) · (\d+)/(\d+) 例$"), r"\1/\2 · \3/\4 tasks"),
    (re.compile(r"^(.+)（未纳入本轮）$"), r"\1 (not included in this benchmark)"),
    (re.compile(r"^(.+) · (\d+) 个仓库$"), r"\1 · \2 repositories"),
    (re.compile(r"^(.+) · 范围与官方依据$"), r"\1 · scope and official basis"),
    (re.compile(r"^(.+) · 官方文档（(\d{4}-\d{2}-\d{2}) 核对） ↗$"), r"\1 · official documentation (checked \2) ↗"),
    (re.compile(r"^(Moli [\d.]+) · 官方发布 ↗$"), r"\1 · official release ↗"),
    (re.compile(r"^取得至少 (\d+) 条完整、不重复的记录，每条包含(.+)。同时核对下方列明的稳定文字与页面要求。变化字段按声明的格式核验，不固定旧值。$"), r"Return at least \1 complete, unique records, each containing\2. Also satisfy the stable-text and page requirements below. Volatile fields are checked by their declared format, not frozen values."),
    (re.compile(r"^取得至少 (\d+) 条完整、不重复的记录，每条包含(.+)。变化字段按声明的格式核验，不固定旧值。$"), r"Return at least \1 complete, unique records, each containing\2. Volatile fields are checked by their declared format, not frozen values."),
    (re.compile(r"^检出 (\d+) 条，字段完整 (\d+) 条，完整且唯一 (\d+) 条，要求至少 (\d+) 条。稳定内容与结构检查通过。$"), r"Detected \1 records; \2 have complete fields and \3 are complete and unique, against a minimum of \4. Stable-content and structure checks passed."),
    (re.compile(r"^检出 (\d+) 条，字段完整 (\d+) 条，完整且唯一 (\d+) 条，要求至少 (\d+) 条。条目结构检查通过；本例未设置稳定文字核对点。$"), r"Detected \1 records; \2 have complete fields and \3 are complete and unique, against a minimum of \4. Record structure passed; this task has no stable-text checks."),
    (re.compile(r"^实际解析出 (\d+) 条完整且不重复的记录，要求至少 (\d+) 条；结构检查通过，稳定文字检查通过。$"), r"Parsed \1 complete, unique records against a minimum of \2; structure and stable-text checks passed."),
    (re.compile(r"^实际解析出 (\d+) 条完整且不重复的记录，要求至少 (\d+) 条；结构检查未通过，稳定文字检查未通过。$"), r"Parsed \1 complete, unique records against a minimum of \2; structure and stable-text checks failed."),
    (re.compile(r"^实际解析出 (\d+) 条完整且不重复的记录，要求至少 (\d+) 条；结构检查(通过|未通过)，稳定文字检查(通过|未通过)。$"), lambda m: f"Parsed {m.group(1)} complete, unique records against a minimum of {m.group(2)}; structure {'passed' if m.group(3) == '通过' else 'failed'} and stable-text checks {'passed' if m.group(4) == '通过' else 'failed'}."),
    (re.compile(r"^完整度 / 纯净度 / F1 为 ([^。]+)。$"), r"Recall / precision / F1: \1."),
    (re.compile(r"^正文没有抓全；低于目标的位置为：(.+)。下方会把完整标准答案与实际返回并排展示。$"), _body_region_summary),
    (re.compile(r"^这一段标准答案产生 ([\d,]+) 个连续六字符片段；在上方完整实际返回（([\d,]+) 字符）中命中 ([\d,]+) 个，所以覆盖率为 ([\d,]+) ÷ ([\d,]+) = ([\d.]+%)。目标 ([\d.]+%)，硬下限 ([\d.]+%)。$"), r"This reference region produces \1 contiguous six-character shingles. \3 are found in the complete output above (\2 characters), so coverage is \4 ÷ \5 = \6. Target: \7; hard floor: \8."),
    (re.compile(r"^· 采集时间 (.+)$"), r"· captured at \1"),
]


# Current standalone article: authored prose only.


def release_translations(history: dict) -> dict[str, str]:
    """Localize only the structured release records included in this report."""
    translations = {}

    def add(source, target):
        if source in translations and translations[source] != target:
            raise ValueError('Conflicting English release translation for ' + repr(source))
        translations[source] = target

    for release in history['releases']:
        if any(kind in release.get('comparison_kinds', []) for kind in (
                'webmainbench_frozen_html_to_markdown', 'public_benchmark_research',
                'wcxb_frozen_html_to_markdown', 'moli_historical_failure_retest',
                'moli_process_tree_resource_profile')):
            continue
        english = release.get('i18n', {}).get('en', {})
        if (not english.get('title') or not english.get('summary')
                or len(release.get('changes', [])) != len(english.get('changes', []))):
            raise ValueError('Web Fetch release requires complete English text: ' + release['id'])
        for key in ('title', 'summary'):
            add(release[key], english[key])
        for source_row, target_row in zip(release['changes'], english['changes'], strict=True):
            if len(source_row) != 4 or len(target_row) != 4:
                raise ValueError('Web Fetch release change rows must have four columns: ' + release['id'])
            for source, target in zip(source_row, target_row, strict=True):
                add(source, target)

    return translations


REGEX.insert(0, (
    re.compile(r"^(?:.+：)?\d{4}-\d{2}-\d{2}（\d+ 题）(?:、\d{4}-\d{2}-\d{2}（\d+ 题）)*$"),
    lambda m: m.group().replace("：", ": ").replace("（", " (").replace(" 题）", " tasks)").replace("、", "; "),
))


REGEX.insert(0, (
    re.compile(r"^相对 Chromium，Moli 的平均耗时减少 ([\d.]+)%，P50 减少 ([\d.]+)%，P95 减少 ([\d.]+)%。这三项在五款中均最低；跨批次条件下，尚不能确定差距有多少来自浏览器实现。$"),
    r"Compared with Chromium, Moli reduces mean latency by \1%, P50 by \2%, and P95 by \3%. All three are the lowest of the five; with collection across batches, the share of the difference attributable to browser implementation remains uncertain.",
))


REGEX.insert(0, (
    re.compile(r"^(\d+) 份真实日志文件已保存并通过 SHA-256 校验。$"),
    r"\1 real log files were retained and verified by SHA-256.",
))
REGEX.insert(0, (
    re.compile(r"^([\d,]+) 字节 · SHA-256：$"),
    r"\1 bytes · SHA-256: ",
))
REGEX.insert(0, (
    re.compile(r"^· ([\d,]+) 字节 · SHA-256：$"),
    r"· \1 bytes · SHA-256: ",
))
REGEX.insert(0, (
    re.compile(r"^(\d+) 份真实日志文件可以读取；其中至少一份没有在运行记录中声明 SHA-256。$"),
    r"\1 real log files are readable; at least one has no SHA-256 declared in the run record.",
))
REGEX.insert(0, (
    re.compile(r"^正文判定：本例分为 (\d+) 段。每段至少达到 ([\d.]+)%，其中至少 (\d+) 段达到 ([\d.]+)%。先看下表；需要核对原文时，再展开下面的左右对照。$"),
    r"Body-text rule: this case has \1 regions. Every region must reach at least \2%, and at least \3 regions must reach \4%. Check the table first, then expand the side-by-side evidence when needed.",
))
REGEX.insert(0, (
    re.compile(r"^本例分为 (\d+) 段。每段至少达到 ([\d.]+)%，其中至少 (\d+) 段达到 ([\d.]+)%。先看下表；需要核对原文时，再展开下面的左右对照。$"),
    r"This case has \1 regions. Every region must reach at least \2%, and at least \3 regions must reach \4%. Check the table first, then expand the side-by-side evidence when needed.",
))
REGEX.insert(0, (
    re.compile(r"^本段覆盖率为 ([\d.]+)%。每段至少达到 ([\d.]+)%；达到 ([\d.]+)% 时算作主要完整段落。$"),
    r"This region has \1% coverage. Every region must reach at least \2%; reaching \3% counts as a substantially complete region.",
))
REGEX.insert(0, (
    re.compile(r"^共 (\d+) 组稳定文字，全部属于标准答案；当前区域显示约 5 组高度，向下滚动查看全部。$"),
    r"All \1 stable-text groups belong to the reference answer. This area shows about 5 groups at a time; scroll to view all.",
))
REGEX.insert(0, (
    re.compile(r"^共 (\d+) 组稳定文字，只用于范围核对，不影响是否通过；当前区域显示约 5 组高度，向下滚动查看全部。$"),
    r"All \1 stable-text groups are scope diagnostics and do not affect acceptance. This area shows about 5 groups at a time; scroll to view all.",
))
REGEX.insert(0, (
    re.compile(r"^共 (\d+) 项稳定文字判定，全部参与评分；当前区域显示约 5 项高度，向下滚动查看全部。$"),
    r"All \1 stable-text checks contribute to scoring. This area shows about 5 at a time; scroll to view all.",
))
REGEX.insert(0, (
    re.compile(r"^共 (\d+) 项稳定文字判定，只用于范围核对，不影响是否通过；当前区域显示约 5 项高度，向下滚动查看全部。$"),
    r"All \1 stable-text checks are scope diagnostics and do not affect acceptance. This area shows about 5 at a time; scroll to view all.",
))

REGEX.insert(0, (
    re.compile(r"^(\d+) 个物理调用日志已保存并通过 SHA-256 校验。$"),
    r"Logs for \1 physical calls were retained and verified by SHA-256.",
))
REGEX.insert(0, (
    re.compile(r"^物理调用 (\d+)$"),
    r"Physical call \1",
))
REGEX.insert(0, (
    re.compile(r"^下载物理调用 (\d+)记录$"),
    r"Download physical call \1 record",
))
REGEX.insert(0, (
    re.compile(r"^本题的最终交付物是 (\d+) 个原始文件；文本输出为空不表示没有交付，文件字节在详情页下方单独列出。$"),
    r"This task delivered \1 original files. Empty text does not mean nothing was delivered; the file bytes are listed separately below.",
))
REGEX.insert(0, (
    re.compile(r"^最终输出确实为空。适配器记录的原因：(.+)$"),
    r"The final output is genuinely empty. The adapter recorded this reason: \1",
))
REGEX.insert(0, (
    re.compile(r"^最终输出确实为空。请求未成功，错误类型为 (.+)；历史记录没有保存更多错误文字。$"),
    r"The final output is genuinely empty. The request failed with error type \1; the historical record retained no further error text.",
))
REGEX.insert(0, (
    re.compile(r"^(成功|失败) · HTTP (.+)$"),
    lambda match: (("Success" if match.group(1) == "成功" else "Failure") + " · HTTP "
                   + ("not provided" if match.group(2) == "未提供" else match.group(2))),
))


REGEX.insert(0, (
    re.compile(r"^资源表覆盖预先确定的全部 (\d+) 个普通网页，包含失败调用。这组任务的通过数：(.*)。各项平均值、P50、P95 使用同一完整范围。$"),
    lambda match: (
        f"The resource table covers all {match.group(1)} predeclared general pages, including failed calls. "
        "Pass counts: " + match.group(2).replace("，", ", ")
        + ". Mean, P50, and P95 share this complete population."
    ),
))

REGEX.insert(0, (
    re.compile(r"^(\d+)/(\d+)$"),
    r"\1/\2",
))
REGEX.insert(0, (
    re.compile(r"^。当前报告的 (.+) 时延纠正仅替换 (\d+) 个耗时值，统一取配对实验的第 (\d+) 轮；旧耗时和原始物理调用保留在离线证据，当前投影的$"),
    r". In the current report, the latency correction for \1 replaces only \2 timings and consistently uses round \3 of the paired experiment. Previous timings and physical calls remain in the offline evidence; the projected",
))
REGEX.insert(0, (
    re.compile(r"^接收 (.+) / 发送 (.+)$"),
    lambda match: "Received " + match.group(1).replace("、", ", ")
    + " / sent " + match.group(2).replace("、", ", "),
))
REGEX.insert(0, (
    re.compile(r"^(?:Chromium|Lightpanda|Moli(?: \d+(?:\.\d+)*)?(?: · 独立资源批次)?|Obscura)(?:、(?:Chromium|Lightpanda|Moli(?: \d+(?:\.\d+)*)?(?: · 独立资源批次)?|Obscura))+$"),
    lambda match: match.group(0).replace("、", ", "),
))
REGEX.insert(0, (
    re.compile(r"^(.+) 是本轮资源效率领先者，在 (\d+) 个可按低消耗比较的指标中取得 (\d+) 项最低。这里的资源领先不等于综合任务通过率领先。$"),
    r"\1 is the resource-efficiency leader in this run, with the lowest use in \3 of \2 lower-is-better metrics. Resource leadership is not the same as leadership in overall task pass rate.",
))

REGEX.insert(0, (
    re.compile(r"^主结果 ([\d,]+)/([\d,]+)；同窗测速 ([\d,]+)/([\d,]+)；代理分歧 (\d+)；已裁定 (\d+)；未决 (\d+)。$"),
    r"Primary results \1/\2; same-window latency \3/\4; agent disagreements \5; adjudicated \6; unresolved \7.",
))
REGEX.insert(0, (
    re.compile(r"^全部 (\d+) 种受测配置共同通过$"),
    r"All \1 tested configurations pass",
))
REGEX.insert(0, (
    re.compile(r"^从结构化结果中读取标题与详情链接，共 (\d+) 个标题。$"),
    r"Read titles and detail URLs from structured results: \1 returned titles.",
))
REGEX.insert(0, (
    re.compile(r"^(\d+)/(\d+) 个标题找到独立名称证据；没有对应证据的不评分。$"),
    r"\1/\2 titles join to independent name evidence; titles without matching evidence are not scored.",
))
REGEX.insert(0, (
    re.compile(r"^独立资源 cohort：四种本地配置共同成功的 (\d+)/(\d+) 个普通网页。失败任务不进入资源汇总。$"),
    r"Independent resource cohort: \1/\2 general pages passed by all four local configurations. Failed tasks are excluded from resource aggregates.",
))
REGEX.insert(0, (
    re.compile(r"^在四种可完整观测的本地浏览器中，(.+) 的平均 CPU 时间最低，(.+) 的平均峰值内存最低。这两个最低值可能属于不同浏览器；CPU、内存和任务通过率也不能合成一个总分。Kitesurf 在远端运行，其服务端 CPU、内存和上游流量不可见，因此不进入资源表，也不与本地浏览器容器作引擎资源排名。$"),
    r"Among the four fully observable local browsers, \1 has the lowest mean CPU time and \2 the lowest mean peak memory. These minima may belong to different browsers; CPU, memory, and task pass rate must not be blended into a composite score. Kitesurf runs remotely; its server-side CPU, memory, and upstream traffic are invisible, so it is excluded from the resource table and local-browser resource ranking.",
))
REGEX.insert(0, (
    re.compile(r"^独立资源 cohort：五款展示配置共同成功的 (\d+)/(\d+) 个普通网页。失败任务不进入资源汇总。$"),
    r"Independent resource cohort: \1/\2 general pages passed by all five displayed configurations. Failed tasks are excluded from resource aggregates.",
))
REGEX.insert(0, (
    re.compile(r"^在四种可完整观测的本地浏览器中，(.+) 的平均 CPU 时间最低，(.+) 的平均峰值内存最低。这两个最低值可能属于不同浏览器；CPU、内存和任务通过率也不能合成一个总分。Kitesurf 在远端运行，表中只包含本机请求客户端；其远端上游流量也不可见，不能与本地浏览器容器作引擎资源排名。$"),
    r"Among the four fully observable local browsers, \1 has the lowest mean CPU time and \2 the lowest mean peak memory. These minima may belong to different browsers; CPU, memory, and task pass rate must not be blended into a composite score. Kitesurf runs remotely, so its row includes only the local request client; its remote upstream traffic is also unavailable, and it cannot enter an engine-resource ranking against local browser containers.",
))
REGEX.insert(0, (
    re.compile(r"^在四种可完整观测的本地浏览器中，(.+) 的平均 CPU 时间最低，(.+) 的平均峰值内存最低。这两个最低值可能属于不同浏览器；CPU、内存和任务通过率也不能合成一个总分。Kitesurf 在远端运行，表中只包含本机请求客户端；其远端上游流量也不可见，不能与本地浏览器进程树作引擎资源排名。$"),
    r"Among the four fully observable local browsers, \1 has the lowest mean CPU time and \2 the lowest mean peak memory. These minima may belong to different browsers; CPU, memory, and task pass rate must not be blended into a composite score. Kitesurf runs remotely, so its row includes only the local request client; its remote upstream traffic is also unavailable, and it cannot enter an engine-resource ranking against local browser process trees.",
))


REGEX[:0] = [
    (re.compile(r"^(\d+) 个案例 · (\d+) 次调用，机器判分。本轮未重新核验来源。$"), r"\1 cases · \2 calls, machine-scored. Sources were not reverified for this run."),
    (re.compile(r"^每种方法均使用同一组 (\d+) 个任务，包含通过、未通过、错误和超时。耗时是单次调用从输入到返回的时间，不包含调用之间的统一冷却间隔。各方法串行执行，仍受本地与远程环境、网络和产品内部限制影响。$"), r"Each method uses the same \1 tasks, including passes, failures, errors, and timeouts. Latency runs from input to return for a single call, excluding shared cooldown intervals. Each method runs serially and remains subject to local and remote environments, networks, and product limits."),
]

REGEX.extend([
    (re.compile(r"^核验于 (\d{4}-\d{2}-\d{2})。算力、网络、存储、代理和维护成本自担；没有测量这些成本，不把本地调用等同于零成本。官方托管产品或服务价格不属于本地执行模式。$"),
     r"Checked on \1. The caller pays compute, network, storage, proxy, and maintenance costs. These costs were not measured, so local calls are not treated as free. Official managed-product or service prices do not apply to local execution."),
    (re.compile(r"^本方法只使用官方 (\d+(?:\.\d+)+) 发行二进制，与其他 Moli 版本分开计分。网页正文、文档正文、结构题和网页内嵌媒体题统一使用原生 Markdown；动态网站正文整类统一启用 --layout、--with-frames 和 domstable，不按题目设置 selector。网页媒体只核对这份输出中的目标地址，不下载，也不切换 HTML、JSON 或网络跟踪接口。直接 PDF/DOCX 没有交付可读正文时按失败记录；直接媒体文件验收原始字节。评测端不使用外部 PDF/DOCX 解析器补正文，也不在失败后切换引擎或输出模式。$"),
     r"This method uses only the official \1 release binary and is scored separately from other Moli versions. Webpage body text, document body text, structured tasks, and webpage media tasks use native Markdown. Every dynamic-body task enables --layout, --with-frames, and domstable, without task-specific selectors. Webpage media tasks check only target URLs in that output; they do not download media or switch to HTML, JSON, or network-tracing interfaces. Direct PDF/DOCX tasks fail without readable body text; direct media files are evaluated as original bytes. The benchmark does not supply external PDF/DOCX parsing or switch engines or output modes after a failure."),
    (re.compile(r"^必需文字组 (\d+)（任一写法即可）$"), r"Required text group \1 (any alternative may match)"),
    (re.compile(r"^· 原始采集记录 ([\d,]+) 字节（可含协议返回） · 原始 SHA-256：$"), r"· Original capture: \1 bytes (may include protocol responses) · Original SHA-256:"),
    (re.compile(r"^本报告纳入 ([\d,]+) 个逻辑结果。运行参数见下表；保留的物理调用与输出见各结果详情。$"),
     r"This report includes \1 logical results. Run parameters are listed below; each result detail contains its retained physical calls and outputs."),
    (re.compile(r"^· 请求：(成功|失败) · 耗时：([\d,.]+) ms · 实际输出：([\d,]+) 字符$"),
     lambda m: f"· Request: {'success' if m[1] == '成功' else 'failure'} · Duration: {m[2]} ms · Actual output: {m[3]} characters"),
])


def _timeout_translation(match):
    budget = (f"{match['seconds']}-second outer deadline" if match['seconds'] else
              "Outer deadline not recorded")
    suffix = match['suffix']
    if suffix == 'Moli 总等待、HTTP 和连接超时保留该二进制默认值':
        return budget + "; Moli's total-wait, HTTP, and connection timeouts retain the measured binary's defaults"
    if suffix == 'Moli 内部超时以逐次调用记录为准':
        return budget + "; Moli's internal timeout policy is taken from each saved call record"
    milliseconds = re.search(r' ([\d,]+) 毫秒$', suffix)
    return budget + "; the measured command explicitly set Moli's total-wait, HTTP, and connection timeouts" + (f" to {milliseconds[1]} ms" if milliseconds else " (value not recorded)")


REGEX.append((re.compile(r"^(?:(?P<seconds>[\d.]+) 秒外层截止|外层截止时间未记录)；(?P<suffix>Moli 总等待、HTTP 和连接超时保留该二进制默认值|Moli 内部超时以逐次调用记录为准|实测命令同时显式传入 Moli 总等待、HTTP 和连接超时(?: [\d,]+ 毫秒)?)$"), _timeout_translation))


def translate_text(value: str, translations=None) -> str:
    leading = value[:len(value) - len(value.lstrip())]
    trailing = value[len(value.rstrip()):]
    core = value.strip()
    if not core:
        return value
    translated = (translations or {}).get(core, TEXT.get(core))
    if translated is None:
        for marker in (" ↕", " ↑", " ↓"):
            if core.endswith(marker) and core[:-len(marker)] in TEXT:
                translated = TEXT[core[:-len(marker)]] + marker
                break
    if translated is None:
        for pattern, replacement in REGEX:
            if pattern.fullmatch(core):
                translated = pattern.sub(replacement, core)
                break
    if translated is None:
        translated = re.sub(
            r"^完整参考覆盖 (\d+)/(\d+)；缺少参考只影响全文指标，不影响任务结果。",
            r"Complete-reference coverage: \1/\2. A missing reference affects only full-text metrics, not task acceptance. ",
            core,
        )
        translated = re.sub(
            r"文字核对范围：各方法均为 ([\d,]+/[\d,]+) 例。",
            r"Text-check coverage: \1 cases for every method. ", translated,
        )
        for source, target in sorted(PHRASES.items(), key=lambda item: len(item[0]), reverse=True):
            translated = translated.replace(source, target)
        if translated == core:
            translated = None
    return leading + (translated if translated is not None else core) + trailing


class Localizer(HTMLParser):
    def __init__(self, source: PurePosixPath | None = None, translations=None) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.stack: list[str] = []
        self.source = source
        self.translations = translations
        self.translate_stack = []
        self.model_text_stack = []

    def _allows_translation(self, attrs):
        mode = (dict(attrs).get("translate") or "").lower()
        if mode in {"yes", "no"}:
            return mode == "yes"
        return self.translate_stack[-1] if self.translate_stack else True

    def _start_tag(self, attrs) -> str:
        original = self.get_starttag_text()
        if (not self._allows_translation(attrs)
                or any(tag in self.stack for tag in ("pre", "code", "script", "style"))):
            return original
        values = dict(attrs)
        article = self.source in {PurePosixPath("guide/index.html"), PurePosixPath("guide/article.html")}

        def replace_attribute(markup, name, value):
            pattern = re.compile(r'''(?<![\w:-])(''' + re.escape(name)
                                 + r'''\s*=\s*)(?:"[^"]*"|'[^']*'|[^\s>]+)''', re.S)
            return pattern.sub(lambda match: match.group(1) + '"'
                               + html.escape(value, quote=True) + '"', markup)

        if article or self.source == PurePosixPath("methodology/index.html"):
            # The renderer supplies both language assets. Localization only selects
            # an existing URL; it never reads files or modifies measured evidence.
            for name in ("src", "href"):
                alternate = values.get("data-en-" + name)
                if alternate is not None:
                    original = replace_attribute(original, name, alternate)
            original = re.sub(r'''\s+data-en-[\w-]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?''',
                              "", original, flags=re.S)
        original = re.sub(r'''\s+data-i18n-attrs(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?''',
                          "", original, flags=re.S)
        for name in ("aria-label", "alt", "placeholder", "title"):
            label = values.get(name)
            if label is not None:
                original = replace_attribute(original, name, translate_text(label, self.translations))
        return original

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")

    def handle_starttag(self, tag: str, attrs) -> None:
        localized = dict(attrs).get('data-report-en') if tag in {'span', 'option'} else None
        parent_model = any(self.model_text_stack)
        self.model_text_stack.append(localized is not None or parent_model)
        self.translate_stack.append(self._allows_translation(attrs))
        self.stack.append(tag)
        self.parts.append(self._start_tag(attrs))
        if localized is not None and not parent_model:
            self.parts.append(html.escape(localized))

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.parts.append(self._start_tag(attrs))

    def handle_endtag(self, tag: str) -> None:
        if tag in self.stack:
            self.stack = self.stack[:len(self.stack) - 1 - self.stack[::-1].index(tag)]
            self.translate_stack = self.translate_stack[:len(self.stack)]
            self.model_text_stack = self.model_text_stack[:len(self.stack)]
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if any(self.model_text_stack):
            return
        if ((self.translate_stack and not self.translate_stack[-1])
                or any(tag in self.stack for tag in ("pre", "code", "script", "style"))):
            self.parts.append(data)
        else:
            self.parts.append(translate_text(data, self.translations))

    def handle_entityref(self, name: str) -> None:
        if any(self.model_text_stack):
            return
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if any(self.model_text_stack):
            return
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")


_VISIBLE_ATTRIBUTE = re.compile(
    r'(?P<prefix>\b(?:alt|aria-label|placeholder|title)=)(?P<quote>["\'])(?P<value>.*?)(?P=quote)',
    re.S,
)


def _translate_visible_attributes(start_tag: str) -> str:
    def replace(match: re.Match) -> str:
        value = html.unescape(match.group("value"))
        translated = html.escape(translate_text(value), quote=True)
        return match.group("prefix") + match.group("quote") + translated + match.group("quote")
    return _VISIBLE_ATTRIBUTE.sub(replace, start_tag)


class EnglishNarrativeAudit(HTMLParser):
    """Find CJK left in UI/prose while excluding preserved measured evidence."""

    def __init__(self, allowed_source_text=()) -> None:
        super().__init__(convert_charrefs=False)
        self.skip = 0
        self.allowed = sorted(set(filter(None, allowed_source_text)), key=len, reverse=True)
        self.untranslated: list[str] = []

    def _check(self, value: str) -> None:
        remainder = html.unescape(value).strip()
        for allowed in self.allowed:
            remainder = remainder.replace(allowed, "")
        if re.search(r"[\u4e00-\u9fff]", remainder):
            self.untranslated.append(html.unescape(value).strip())

    def handle_starttag(self, tag: str, attrs) -> None:
        if not self.skip:
            for name, value in attrs:
                if name in {"alt", "aria-label", "placeholder", "title"} and value:
                    self._check(value)
        if tag in {"pre", "code", "script", "style"}:
            self.skip += 1

    def handle_startendtag(self, tag: str, attrs) -> None:
        if not self.skip:
            for name, value in attrs:
                if name in {"alt", "aria-label", "placeholder", "title"} and value:
                    self._check(value)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"pre", "code", "script", "style"}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self._check(data)


def untranslated_english_narrative(page: str, allowed_source_text=()) -> list[str]:
    parser = EnglishNarrativeAudit(allowed_source_text)
    parser.feed(page)
    parser.close()
    return parser.untranslated


def _relative(source: PurePosixPath, target: PurePosixPath) -> str:
    return posixpath.relpath(str(target), str(source.parent))


def _rewrite_url(value: str, source: PurePosixPath, english: PurePosixPath) -> str:
    parsed = urlsplit(html.unescape(value))
    if parsed.scheme or parsed.netloc or value.startswith(("mailto:", "data:", "javascript:")):
        return value
    if not parsed.path:
        return value
    resolved = PurePosixPath(posixpath.normpath(posixpath.join(str(source.parent), parsed.path)))
    target = PurePosixPath("en") / resolved if resolved.suffix.lower() in {".html", ".htm"} else resolved
    rewritten = _relative(english, target)
    return html.escape(urlunsplit(("", "", rewritten, parsed.query, parsed.fragment)), quote=True)


def _rewrite_links(page: str, source: PurePosixPath, english: PurePosixPath) -> str:
    pattern = re.compile(r'(?P<prefix>\b(?:href|src)=)(?P<quote>["\'])(?P<url>.*?)(?P=quote)')
    return pattern.sub(lambda match: match.group("prefix") + match.group("quote")
                       + _rewrite_url(match.group("url"), source, english) + match.group("quote"), page)


def _header(source: PurePosixPath, english: PurePosixPath) -> str:
    brand = _relative(english, PurePosixPath("en/index.html"))
    chinese = _relative(english, source)
    links = "".join(
        f'<a href="{html.escape(_relative(english, PurePosixPath("en") / path), quote=True)}">{label}</a>'
        for path, label in NAV
    )
    return (f'<header class="top"><nav class="wrap"><a href="{brand}">Web Fetch</a>'
            f'{links}<a href="{chinese}" hreflang="zh-CN">中文</a>'
            '<button class="theme-toggle" onclick="theme()">Toggle theme</button></nav></header>')


def localize_html(page: str, source: PurePosixPath, *, translations=None) -> str:
    english = PurePosixPath("en") / source
    page = page.replace('<html lang="zh-CN">', '<html lang="en">', 1)
    page = re.sub(r'<header class="top">.*?</header>', _header(source, english), page, count=1, flags=re.S)
    # Raw evidence and executable/style blocks are preserved, never translated.
    # Mask them before parsing so large responses do not incur entity-by-entity work.
    preserved = []
    def preserve(match):
        preserved.append(match.group(0))
        return f"<!--LOCALIZATION-PRESERVED-{len(preserved)-1}-->"
    page = re.sub(r"<(pre|script|style)\b[^>]*>.*?</\1>", preserve, page, flags=re.S | re.I)
    parser = Localizer(source, translations)
    parser.feed(page)
    parser.close()
    localized = "".join(parser.parts)
    for index, block in enumerate(preserved):
        localized = localized.replace(f"<!--LOCALIZATION-PRESERVED-{index}-->", block)
    return _rewrite_links(localized, source, english)


REGEX.insert(0, (re.compile(r"^源码提交：(.+)$"), r"Source commit: \1"))


REGEX.insert(0, (re.compile(r"^(\d+) 个计划任务中的通过数 / 可判定数；每题等权，无法判定结果不参与分母。$"), r"Passed / decidable results among \1 planned tasks; each task has equal weight, and undecidable results are excluded from the denominator."))
REGEX.insert(0, (re.compile('^每种方法均使用同一组 (\\d+) 个任务，包含通过、未通过、错误和超时。耗时是单次调用从输入到返回的时间，不包含调用之间的统一冷却间隔。每种方法内部按案例依次执行；方法之间的并发安排和限制见调用链检查。耗时仍受本地与远程环境、网络和产品内部限制影响。$'), 'Each method uses the same \\1 tasks, including passes, failures, errors, and timeouts. Latency runs from input to return and excludes the common cooldown between calls. Cases execute sequentially within each method; inter-method scheduling and limits are documented in the call-chain audit. Local and remote environments, networks, and product limits still affect latency.'))
REGEX.insert(0, (re.compile(r"^(\d+)/(\d+) · 复核记录$"), r"\1/\2 · review records"))
REGEX.insert(0, (re.compile(r"^(\d+) 条通过结果的参考词精确率低于 30%。此比例用于定位检查对象，不是统一失败线；通过不保证干净正文，正文参考的范围和时间也会影响该指标。$"), r"\1 passing results have reference word precision below 30%. This locates inspection candidates, not a universal failure threshold. Passing does not establish clean body text; reference scope and time also affect this metric."))


def write_english_report(out: Path, *, source_routes=None) -> int:
    """Mirror Chinese HTML routes; a scoped refresh preserves other editions."""
    if source_routes is None:
        sources = sorted(path for path in out.rglob("*.html") if "en" not in path.relative_to(out).parts)
    else:
        from lexbench_webfetch.paths import contained_path
        sources = []
        for name in sorted(set(source_routes)):
            route = PurePosixPath(name)
            if route.is_absolute() or '..' in route.parts or not route.parts or route.parts[0] == 'en' or route.suffix != '.html':
                raise ValueError('Scoped English routes must be relative Chinese HTML paths')
            path = contained_path(out, str(route), reject_symlinks=True)
            contained_path(out, 'en/' + str(route), reject_symlinks=True)
            if not path.is_file():
                raise FileNotFoundError('Missing Chinese route: ' + str(route))
            sources.append(out / str(route))
    history_path = out / "report-changes.json"
    translations = release_translations(json.loads(history_path.read_text())) if history_path.is_file() else {}
    article_translations = archived_translations(out)
    english_root = out / "en"
    if source_routes is None and english_root.exists():
        shutil.rmtree(english_root)
    dataset_path = out / "dataset.json"
    allowed_source_text = {"中文"}
    if dataset_path.is_file():
        dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
        allowed_source_text.update(
            case.get("name") for case in dataset.get("cases", []) if case.get("name")
        )
    archived_manifest = out / "archived-reading.json"
    if archived_manifest.is_file():
        allowed_source_text.update(json.loads(archived_manifest.read_text()).get("preserved_source_names", []))
    for source_path in sources:
        source = PurePosixPath(source_path.relative_to(out).as_posix())
        target = out / "en" / Path(str(source))
        target.parent.mkdir(parents=True, exist_ok=True)
        raw=source_path.read_text(encoding="utf-8")
        if str(source)=='guide/index.html' and (out/'archived-reading.json').is_file():
            parsed = BeautifulSoup(raw, 'html.parser')
            article = parsed.select_one('[data-archived-reading="true"]')
            if article is None:
                raise ValueError('Historical article route lacks its scoped wrapper')
            article.replace_with(Comment('ARCHIVED-ARTICLE'))
            shell = localize_html(str(parsed), source, translations=translations)
            body = localize_html(render_archived_reading(out,'en'), source,
                                 translations=article_translations)
            localized = shell.replace('<!--ARCHIVED-ARTICLE-->', body)
        else:
            localized=localize_html(raw,source,translations=translations)
        if source in {PurePosixPath("guide/index.html"), PurePosixPath("guide/article.html")}:
            untranslated = untranslated_english_narrative(localized, allowed_source_text)
            if untranslated:
                raise ValueError(
                    "Untranslated English article UI or narrative: " + repr(untranslated[:20])
                )
        target.write_text(localized, encoding="utf-8")
    return len(sources)
