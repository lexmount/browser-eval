from harness.web_fetch.report_quality import quality_observations


def test_target_coverage_and_challenge_content_are_distinct():
    record={'ok':True,'metrics':{'content_pass':True,'word_precision':.2,'diagnostic_hits':['CAPTCHA']}}
    case={'ground_truth_main_content':'Reference body','task_kind':'body'}
    actual=quality_observations(record,case,'hCaptcha\nPlease try again.\nVerify\nhttps://hcaptcha.com/privacy')
    assert actual['task_pass'] and actual['low_precision'] and actual['challenge_component_text']
    normal=quality_observations(record,case,'This documentation describes CAPTCHA integration.')
    assert normal['task_pass'] and not normal['challenge_component_text']
    assert normal['hint_words']==['CAPTCHA']


def test_missing_reference_is_unavailable_not_zero_precision():
    record={'ok':True,'metrics':{'content_pass':True,'word_precision':0}}
    actual=quality_observations(record,{'task_kind':'body'})
    assert actual['word_precision'] is None and not actual['low_precision']
