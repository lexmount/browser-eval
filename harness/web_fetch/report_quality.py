"""Present coverage and extra-content diagnostics as separate observations."""
import re

from lexbench_webfetch.metrics import task_passed


def quality_observations(record, case, output=''):
    metrics = record['metrics']
    precision = metrics.get('word_precision') if case.get('ground_truth_main_content') else None
    # A bare word in ordinary documentation is not a captured challenge widget.
    component = bool(re.search(r'hcaptcha', output, re.I)
                     and re.search(r'Please try again', output, re.I)
                     and re.search(r'\bVerify\b', output)
                     and re.search(r'hcaptcha\.com/(?:privacy|what-is-hcaptcha)', output, re.I))
    return {'task_pass': task_passed(record['ok'], metrics, case.get('task_kind')),
            'word_precision': precision, 'low_precision': precision is not None and precision < .3,
            'hint_words': list(dict.fromkeys(metrics.get('diagnostic_hits', []) + metrics.get('blocked_page_hits', []))),
            'challenge_component_text': component}


def render_quality_observations(record, case, output, *, esc):
    info = quality_observations(record, case, output)
    verdict = '排除比较' if record['metrics'].get('evaluation_excluded') else '通过' if info['task_pass'] else '未通过'
    precision = '未评测' if info['word_precision'] is None else f'{info["word_precision"]:.2%}'
    component = '包含验证码组件文字' if info['challenge_component_text'] else '未检测到该组件文字特征'
    hints = '、'.join(info['hint_words']) or '无命中'
    hint_html='<code>'+esc(hints)+'</code>' if info['hint_words'] else '<span>无命中</span>'
    return ('<div data-quality-diagnostics="true"><p><b>目标要求：</b>'+verdict+'</p>'
            '<p><b>附加内容诊断：</b><span>'+component+'</span>；'+hint_html+'</p>'
            '<p><b>参考词精确率：</b>'+precision+'</p>'
            '<p>通过只表示满足登记的目标要求，不保证干净正文。提示词命中不证明整页被拦截；词精确率也受参考范围和时间影响。低于 30% 仅用于定位检查对象，不是统一失败线。</p></div>')
