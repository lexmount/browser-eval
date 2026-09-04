"""Write a clearly scoped, unpublished diagnostic summary from retained calls."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.report_explainer import headless_evidence, TRACK_NAMES


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    dataset=json.loads((args.source/'dataset.json').read_text())
    all_rows=[json.loads(line) for line in (args.source/'results.jsonl').read_text().splitlines()]
    blocked=set(dataset['comparison']['unverified_reference_cases'])
    cases={c['id']:c for c in dataset['cases'] if c['id'] not in blocked}
    rows=[r for r in all_rows if r['case_id'] in cases]
    evidence=headless_evidence(cases,rows)
    text=['# 五种浏览器 Web Fetch：未发布诊断摘要','',
        f'全部 {len(dataset["cases"])} 题 × 5 种浏览器均已执行，共保留 {len(all_rows)} 次主选调用。',
        f'下表仅包含已完成参考核验或使用稳定契约的 {len(cases)} 题、{len(rows)} 次调用；不是最终已发布报告。',
        '','暂不纳入下表的参考题：'+('、'.join(sorted(blocked)) or '无')+'。原始调用及任务规则诊断完整保留，没有将参考失败计为浏览器失败。','',
        '| 浏览器 | '+' | '.join(f'{label}（{evidence["tracks"][track]["cases"]}题）' for track,label in TRACK_NAMES.items())+' |',
        '|---|'+'---:|'*len(TRACK_NAMES)]
    for name in evidence['providers']:
        text.append('| '+name+' | '+' | '.join(str(evidence['tracks'][t]['providers'][name]['passed']) for t in TRACK_NAMES)+' |')
    text+=['','数字为满足任务规则的调用数；六类不合成总分。','',
        '## 普通网页共同通过样本的配对耗时','',
        '| 相对 Chromium | 共同通过题数 | 本方法 P50 ms | Chromium P50 ms |','|---|---:|---:|---:|']
    for pair in evidence['pairs']:
        text.append(f'| {pair["provider"]} | {len(pair["both_pass_ids"])} | {pair["provider_p50_ms"]:.2f} | {pair["chromium_p50_ms"]:.2f} |')
    text+=['','每行两侧是相同 URL 集合，不同行样本可能不同。包含客户端、启动、取 HTML 与统一转换开销；Kitesurf 是远程公共 CDP，其余为本地。未隔离资源，不能视为纯引擎性能或成本比较。','',
        '## 已核对的解释','',
        '- 动态任务排序改变：Kitesurf 6/10、Obscura 5/10，Chromium 和 Lightpanda 各 1/10，Moli 0/10。统一等待名称并不代表相同的页面完成状态。',
        '- Wikipedia 一题中，本地三次成功调用取 HTML 约 2—3 秒，统一转换约 40 秒。不能把这部分耗时全归给浏览器，也不能猜测另外两次超时的阶段分布。',
        '- 本轮没有启用 PDF/Office 解析、媒体理解或专用下载，相关任务结果只约束当前 HTML 适配器。','',
        '## 保留的审计边界','',
        '参考验证失败后的替换覆盖完整五方短批次，不按成绩挑选。此前输出和失败证书在 diagnostics/replaced-cases/ 中，替换范围见 selection-audit.json。',
        ('仍有未验证参考：'+ '、'.join(sorted(blocked))+'；正式报告构建器拒绝这些生命周期。' if blocked else '所有精确正文参考已完成独立前后核验；稳定契约不套用实时快照有效期。'),
        '本文仅描述本地测量证据；线上发布状态需以 Artifact Hub 的实际版本和校验回执为准。','']
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text('\n'.join(text))
    print(args.output)


if __name__=='__main__':main()
