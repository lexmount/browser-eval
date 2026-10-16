"""Data and generated-output acceptance checks for the current report."""

import argparse
import hashlib
import json
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from harness.record_io import load_jsonl
from harness.report_languages import check_language_contract, generated_languages, visible_numbers
from harness.reporting.article_assets import ArticleAssetParity
from harness.reporting.presentation import check_presentation
from harness.web_fetch.reference_lifecycle import validate_resource_source_evidence
from harness.web_fetch.reliability import require_reliable
from lexbench_webfetch.paths import contained_path, safe_identifier
from harness.web_fetch.safety import reject_evidence_symlinks
from harness.web_fetch.storage import require_canonical_inputs, require_current_report_profile


def require_public_evidence_page(content, relative, views):
    """Require inspectable public downloads and honest original/public hashes."""
    from bs4 import BeautifulSoup
    from urllib.parse import unquote, urljoin
    content = re.sub(r'<(pre|script|style)\b[^>]*>.*?</\1>', '', content, flags=re.I | re.S)
    soup = BeautifulSoup(content, 'html.parser')
    visible = soup.get_text(' ', strip=True)
    if '<span data-report-en=' in visible:
        raise ValueError('Public evidence link displays escaped interface markup')
    base = 'https://report.invalid/'+relative
    if soup.find('base', href=True):
        base = urljoin(base, soup.find('base')['href'])
    targets = set()
    for anchor in soup.select('a[href]'):
        href = anchor['href']
        parsed = urlsplit(href)
        if parsed.scheme or parsed.netloc or href.startswith('/'):
            continue
        targets.add(unquote(urlsplit(urljoin(base, href)).path).lstrip('/'))
    answer = views['scored_answer']
    if answer['original_sha256'] != answer['published_sha256']:
        if (answer['original_sha256'] not in visible or answer['published_sha256'] not in visible
                or answer['path'] not in targets or answer['receipt'] not in targets):
            raise ValueError('Public answer lacks visible original/public identities or download links')
    native = views.get('native_response')
    if native and (native['path'] not in targets or native['receipt'] not in targets):
        raise ValueError('Complete native public response or its identity receipt is not linked')


def check_public_evidence_pages(root, records):
    from .publication_privacy import public_render_views
    views = public_render_views(root, records)
    languages = generated_languages(root)
    for row in records:
        selected = views[row['attempt_id']]
        for language in languages:
            relative = ('en/' if language == 'en' else '')+'attempts/'+row['attempt_id']+'.html'
            path = root/relative
            if not path.is_file():
                raise ValueError('Public evidence result page is missing')
            require_public_evidence_page(path.read_text(), relative, selected)


class Tables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_th = False
        self.first = False
        self.headers = []
        self.unindexed_headers = []
        self.unindexed = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.first = True
            self.unindexed = dict(attrs).get('data-row-index') == 'false'
        if tag == "th" and self.first:
            self.in_th = True
            self.first = False
            self.headers.append("")
            self.unindexed_headers.append(self.unindexed)

    def handle_data(self, text):
        if self.in_th:
            self.headers[-1] += text

    def handle_endtag(self, tag):
        if tag == "th":
            self.in_th = False


def require_table_index_headers(parser, *, english=False, diagnostic=False):
    expected = 'No.' if english else '序号'
    for header, unindexed in zip(parser.headers, parser.unindexed_headers):
        if diagnostic and unindexed and header in {'方法', '方法 ↕', 'Method', 'Method ↕'}:
            continue
        if header != expected:
            raise ValueError('Unexpected table index header: ' + header)


def require_home_comparison_layout(home, track_count, *, diagnostic=False, sample_ranking=False):
    overall = ('任务判定汇总（非排名）' if diagnostic else
               '本轮任务通过率排名' if sample_ranking else '综合通过率总榜')
    details = '按任务类别查看诊断结果' if diagnostic else f'{track_count} 类分榜'
    markers = [f'{track_count} 类任务概览', overall, details]
    headings = [re.sub(r'<[^>]+>', '', value) for value in re.findall(r'<h2[^>]*>(.*?)</h2>', home, re.S)]
    if any(marker not in headings for marker in markers) or not (
            headings.index(markers[1]) < headings.index(markers[0]) < headings.index(markers[2])):
        raise ValueError('Task overview and comparison must precede detailed results')
    if '判分完整性' not in home or ('本类最高：' in home) == diagnostic:
        raise ValueError('Comparison winner disclosure differs from evidence state')


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"pre", "script", "style"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"pre", "script", "style"}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, text):
        if not self.skip:
            self.parts.append(text)


class EnglishNarrative(HTMLParser):
    def __init__(self, allowed):
        super().__init__()
        self.skip = 0
        self.allowed = sorted(allowed, key=len, reverse=True)
        self.untranslated = []

    def handle_starttag(self, tag, attrs):
        if tag in {"pre", "code", "script", "style"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"pre", "code", "script", "style"}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, text):
        value = text.strip()
        remainder = value
        for allowed in self.allowed:
            remainder = remainder.replace(allowed, "")
        if not self.skip and re.search("[\\u4e00-\\u9fff]", remainder):
            self.untranslated.append(value)


class PageShape(HTMLParser):
    def __init__(self, page=None, assets=None):
        super().__init__()
        self.tags = Counter()
        self.ids = set()
        self.data = []
        self.links = []
        self.skip = 0
        self.numbers = []
        self.page = page
        self.assets = assets
        self.figures = []
        self.images = []
        self.english_images = []

    def handle_starttag(self, tag, attrs):
        if tag == "figure":
            self.figures.append(dict(attrs).get("id"))
        if self.assets and self.figures:
            attrs = self.assets.rewrite(self.page, self.figures[-1], tag, attrs)
        values = dict(attrs)
        self.tags[tag] += 1
        if values.get("id"):
            self.ids.add(values["id"])
        self.data.extend(
            (
                (key, value)
                for key, value in attrs
                if key.startswith("data-") and key != "data-i18n-attrs"
                and not (key == "data-en-src" and self.page
                         and self.page.parts[-2:] == ("methodology", "index.html"))
            )
        )
        if values.get("href") and (not values.get("hreflang")):
            self.links.append(values["href"])
        if tag == "img" and values.get("src"):
            self.images.append(values["src"])
            self.english_images.append(values.get("data-en-src", values["src"]))
        if tag in {"pre", "code", "script", "style"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag == "figure" and self.figures:
            self.figures.pop()
        if tag in {"pre", "code", "script", "style"}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, text):
        if not self.skip:
            self.numbers.extend(visible_numbers(text))


def check_localized_image_sources(zh, en, canonical):
    """Require each declared English companion, never silently use the old image."""
    expected = Counter(canonical(zh.page, value) for value in zh.english_images)
    actual = Counter(canonical(en.page, value) for value in en.images)
    if expected != actual:
        raise ValueError('Localized image sources differ from the declared companions')


def check_saved_result_summary(root, dataset, records):
    from lexbench_webfetch.metrics import task_measured, task_passed
    cases = {case['id']: case for case in dataset['cases']}
    summary = json.loads((root / 'summary.json').read_text())
    groups = {}
    for row in records:
        key = (cases[row['case_id']].get('track', 'web'), row['provider'])
        groups.setdefault(key, []).append(row)
    for (track, provider), rows in groups.items():
        saved = summary.get(track, {}).get(provider, {})
        expected = {'attempts': len(rows),
                    'task_pass': sum(task_passed(row['ok'], row['metrics']) for row in rows),
                    'task_evaluated_attempts': sum(task_measured(row['metrics']) for row in rows),
                    'task_excluded_attempts': sum(bool(row['metrics'].get('evaluation_excluded')) for row in rows)}
        if any(saved.get(key) != value for key, value in expected.items()):
            raise ValueError('Saved result summary differs from current evidence: ' + provider + '/' + track)


def require_replacement_execution(root, binding, physical, logical):
    """Keep superseded task calls as history without counting them as current rows."""
    from .local_revision import read_bound
    receipt = json.loads(read_bound(root, binding).read_text())
    previous = load_jsonl(read_bound(root, receipt['baseline_results']))
    calls = load_jsonl(read_bound(root, receipt['cohort_results']))
    expected_physical = Counter(row['attempt_id'] for row in previous + calls)
    current = [row for row in previous if row['case_id'] != receipt['removed_case_id']]
    expected_logical = Counter(row['attempt_id'] for row in current + calls)
    if physical != expected_physical or logical != expected_logical:
        raise ValueError('Task replacement physical/logical call inventory differs')


class VisibleLocalScope(HTMLParser):
    """Disclosure attributes count only when attached to visible explanatory text."""
    def __init__(self):
        super().__init__()
        self.stack = []
        self.visible = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        inherited = self.stack[-1][1:] if self.stack else (False, set())
        style = re.sub(r'\s+', '', attrs.get('style', '')).lower()
        hidden = (inherited[0] or tag in {'script', 'style', 'template'}
                  or 'hidden' in attrs or attrs.get('aria-hidden') == 'true'
                  or 'display:none' in style or 'visibility:hidden' in style)
        kinds = set(inherited[1])
        if attrs.get('data-evidence-state') == 'local_measurement': kinds.add('evidence')
        if attrs.get('data-ranking-scope') == 'saved-task-pass-rate': kinds.add('ranking')
        if tag not in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:
            self.stack.append((tag, hidden, kinds))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if data.strip() and self.stack and not self.stack[-1][1]:
            self.visible.update(self.stack[-1][2])


def check_local_diagnostic_presentation(root: Path, *, ranked=False):
    """Track evidence state and scope any explicitly requested sample ranking."""
    for path in root.rglob('*.html'):
        page = path.read_text(encoding='utf-8')
        if '<meta name="browser-eval-report" content="1">' not in page:
            continue  # Original response/source HTML is evidence, not report prose.
        disclosure = VisibleLocalScope()
        disclosure.feed(page)
        if 'evidence' not in disclosure.visible:
            raise ValueError('Local diagnostic disclosure missing: ' + str(path.relative_to(root)))
        if ranked and 'ranking' not in disclosure.visible:
            raise ValueError('Sample ranking scope missing: ' + str(path.relative_to(root)))
        for table in re.findall(r'<table\b[^>]*\bid="ranking-[^"]+".*?</table>', page, re.S):
            if not ranked and ('data-dir="desc"' in table or re.search(r'<th>\s*(?:序号|Rank|No\.)\s*</th>', table)):
                raise ValueError('Uncertified diagnostic comparison assigns ranks: ' + str(path.relative_to(root)))


def check(root: Path, *, authority_root: Path, profile: Path | None = None, presentation_binding: Path | None = None, source_dataset: Path | None = None):
    reject_evidence_symlinks(root)
    root = root.resolve()
    repository = authority_root.resolve()
    profile_path = None
    if profile:
        profile_path = (
            (repository / profile).resolve() if not profile.is_absolute() else profile.resolve()
        )
        if not profile_path.is_relative_to((repository / "evaluation").resolve()):
            raise ValueError("--profile must stay under evaluation/")
    if (root / "wcxb-score-summary.json").is_file():
        if profile_path and profile_path != repository / "evaluation/wcxb/current-report.json":
            raise ValueError("WCXB report requires its own current profile")
        from harness.web_fetch.wcxb_report_check import check as check_wcxb

        check_wcxb(root, authority_root=repository)
        return None
    if profile_path:
        require_current_report_profile(root, profile_path)
        canonical = (
            profile_path.parent
            if (profile_path.parent / "dataset.json").is_file()
            else repository / "evaluation"
        )
        if not json.loads(profile_path.read_text()).get('frozen_inputs'):
            require_canonical_inputs(root, canonical)
    d = json.loads((root / "dataset.json").read_text())
    manifest_path = root / 'report-manifest.json'
    if manifest_path.is_file():
        from harness.reporting.templates import resolve_template
        manifest = json.loads(manifest_path.read_text())
        expected_template = resolve_template('webfetch', root=repository)
        if manifest.get('template') != expected_template:
            raise ValueError('Report manifest differs from the maintained template')
        for name in ('dataset', 'results'):
            path = root / ('dataset.json' if name == 'dataset' else 'results.jsonl')
            if manifest.get(name + '_sha256') != hashlib.sha256(path.read_bytes()).hexdigest():
                raise ValueError('Report manifest evidence changed: ' + name)
        if manifest.get('generated_languages') != list(generated_languages(root)):
            raise ValueError('Report manifest languages differ from generated routes')
    rs = load_jsonl(root / "results.jsonl")
    if source_dataset:
        if not profile_path:
            raise ValueError('Frozen source dataset requires an explicit report profile')
        profile_data = json.loads(profile_path.read_text())
        if source_dataset.is_symlink():
            raise ValueError('Frozen source dataset rejects symlinks')
        source_raw = source_dataset.read_bytes()
        if hashlib.sha256(source_raw).hexdigest() != profile_data.get('frozen_inputs', {}).get('dataset_sha256'):
            raise ValueError('Frozen source dataset differs from current profile')
        if json.loads(source_raw)['cases'] != d['cases']:
            raise ValueError('Report cases differ from frozen source dataset')
    check_language_contract(root)
    has_english = "en" in generated_languages(root)
    if d.get("comparison", {}).get("kind") == "webmainbench_frozen_html_to_markdown":
        from harness.web_fetch.webmainbench_report_check import check_sample_report

        check_sample_report(root, d, rs, authority_root=repository)
        return (
            "PASS: WebMainBench preselected-200 matrix, sample, scores, retries, "
            "declared-language routes and reference assets verified"
        )
    public_payload_views = False
    names = {r["provider"] for r in rs}
    local_measurement = d.get("comparison", {}).get("kind") == "uniform_native_local_measurement"
    if presentation_binding and not local_measurement:
        raise ValueError("Presentation binding requires a frozen uniform-native diagnostic snapshot")
    from .report import local_pass_rate_ranking
    sample_ranking = local_measurement and local_pass_rate_ranking(d)
    if local_measurement:
        check_local_diagnostic_presentation(root, ranked=sample_ranking)
        if (root / 'result-review.json').is_file():
            from .adjudication_evidence import require_adjudication_evidence, require_current_review_state
            review=json.loads((root / 'result-review.json').read_text())
            require_current_review_state(rs,review)
            require_adjudication_evidence(root,d,rs,review)
        if (root / 'local-evidence/result-ledger.json').is_file():
            from .result_ledger import validate_ledger
            ledger = validate_ledger(json.loads((root / 'local-evidence/result-ledger.json').read_text()), rs, source=root)
            if ledger['states'][-1]['source_results_sha256'] != hashlib.sha256((root / 'results.jsonl').read_bytes()).hexdigest():
                raise ValueError('Result ledger source hash differs from current results')
        from .local_audit import require_local_measurement, local_measurement_cases
        if presentation_binding:
            if profile:
                raise ValueError("Presentation binding cannot certify a release profile")
            from .report_build import validate_local_presentation
            checked_binding = validate_local_presentation(root, presentation_binding)[0]
            public_payload_views = bool(checked_binding.get("publication_evidence_views"))
        else:
            require_local_measurement(root, d, rs)
        inventory = {track: {"case_ids": [case["id"] for case in d["cases"] if case.get("track", "web") == track], "providers": providers} for track, providers in d["coverage_plan"].items()}
        local_contract = json.loads((root / "local-run-contract.json").read_text())
        canonical_cases = ({case["id"]: case for case in d["cases"]}
                           if presentation_binding and d.get('comparison', {}).get('task_replacement')
                           else {case["id"]: case for case in local_measurement_cases(local_contract)})
        canonical_dataset = (root / "dataset.json" if local_contract.get("dataset_sources")
                             else repository / local_contract["dataset_path"])
    elif source_dataset or (profile_path and (json.loads(profile_path.read_text()).get('frozen_inputs') or (profile_path.parent / 'dataset.json').is_file())):
        inventory = {
            track: {
                "case_ids": [
                    case["id"] for case in d["cases"] if case.get("track", "web") == track
                ],
                "providers": providers,
            }
            for track, providers in d.get("coverage_plan", {}).items()
        }
        canonical_dataset = source_dataset or (root / 'dataset.json' if json.loads(profile_path.read_text()).get('frozen_inputs') else profile_path.parent / "dataset.json")
    else:
        inventory = json.loads((repository / "configs/evaluation-inventory.json").read_text())
        canonical_dataset = repository / "evaluation/dataset.json"
    if d.get("comparison", {}).get("kind") in {"headless_html", "headless_markdown"}:
        headless = json.loads((repository / "configs/providers.headless.json").read_text())
        profile = json.loads(
            (profile_path or repository / "evaluation/current-report.json").read_text()
        )
        if profile.get("comparison_kind") not in {"headless_html", "headless_markdown"}:
            raise ValueError(
                "Legacy headless report cannot be checked against a different "
                "current report profile"
            )
        current_providers = profile["providers"]
        if not set(current_providers) <= set(headless["providers"]):
            raise ValueError("Current report references an unknown provider configuration")
        inventory = {
            track: {**group, "providers": current_providers} for track, group in inventory.items()
        }
    for group in inventory.values():
        providers = group["providers"]
        if not providers or len(providers) != len(set(providers)):
            raise ValueError("Inventory providers must be nonempty and unique")
        for provider in providers:
            safe_identifier(provider)
    if not len({c["id"] for c in d["cases"]}) == len(d["cases"]):
        raise ValueError("Duplicate dataset case IDs")
    inventory_ids = [cid for group in inventory.values() for cid in group["case_ids"]]
    if not len(set(inventory_ids)) == len(inventory_ids):
        raise ValueError("Duplicate inventory case IDs")
    if not {(c["id"], c.get("track", "web")) for c in d["cases"]} == {
        (cid, t) for t, v in inventory.items() for cid in v["case_ids"]
    }:
        raise ValueError("Dataset differs from reviewed independent inventory")
    plan = d.get("coverage_plan", {})
    if any(not providers or len(providers) != len(set(providers)) for providers in plan.values()):
        raise ValueError("Coverage providers must be nonempty and unique")
    if not {t: set(p) for t, p in plan.items()} == {
        t: set(v["providers"]) for t, v in inventory.items()
    }:
        raise ValueError("Provider coverage differs from reviewed inventory")
    expected_pairs = {
        (p, c["id"]) for c in d["cases"] for p in plan.get(c.get("track", "web"), names)
    }
    expected = len(expected_pairs)
    if not len(rs) == expected:
        raise ValueError("Report acceptance check failed")
    if not {(r["provider"], r["case_id"]) for r in rs} == expected_pairs:
        raise ValueError("Report acceptance check failed")
    if not len({r["attempt_id"] for r in rs}) == expected:
        raise ValueError("Report acceptance check failed")
    if not len({(r["provider"], r["case_id"]) for r in rs}) == expected:
        raise ValueError("Report acceptance check failed")
    if public_payload_views:
        check_public_evidence_pages(root, rs)
    case_by_id = {c["id"]: c for c in d["cases"]}
    for case in d["cases"]:
        safe_identifier(case["id"])
        validate_resource_source_evidence(case, canonical_dataset)
    if local_measurement:
        from .local_audit import require_complete_local_inventory
        reliability = require_complete_local_inventory(d, rs)
    else:
        reliability = require_reliable(d, rs)
    saved_reliability = json.loads((root / "reliability.json").read_text())
    if not saved_reliability == reliability:
        raise ValueError("Saved reliability status differs from report evidence")
    if local_measurement and d.get('comparison', {}).get('result_ledger'):
        check_saved_result_summary(root, d, rs)
    for r in rs:
        safe_identifier(r["attempt_id"])
        safe_identifier(r["provider"])
        safe_identifier(r["case_id"])
        if public_payload_views:
            from .publication_privacy import read_evidence_binding
            read_evidence_binding(root, {'path': r['artifact_path'],
                                         'sha256': r['metrics']['content_sha256']})
        else:
            p = contained_path(root, r['artifact_path'], reject_symlinks=True)
            if hashlib.sha256(p.read_bytes()).hexdigest() != r['metrics']['content_sha256']:
                raise ValueError(p)
        if not r["ok"]:
            if not not r["metrics"]["content_pass"]:
                raise ValueError("Report acceptance check failed")
        if r["metrics"].get("word_f1") is not None:
            if not case_by_id[r["case_id"]].get("ground_truth_main_content"):
                raise ValueError("Report acceptance check failed")
    for c in d["cases"]:
        if c.get("track", "web") in {"web", "dynamic", "document"} and (
            not c.get("ground_truth_main_content")
        ):
            stable_checks = bool(
                c.get("required_facts")
                or c.get("required_patterns")
                or c.get("acceptance_contract")
            )
            if local_measurement:
                from lexbench_webfetch.stable_tasks import validate_acceptance_contract
                if c.get("acceptance_contract"):
                    validate_acceptance_contract(c["acceptance_contract"])
                elif not (stable_checks or c.get("task_kind") == "file"):
                    raise ValueError(c["id"])
            elif not stable_checks:
                raise ValueError(c["id"])
        if c.get("ground_truth_main_content"):
            expected = c["ground_truth_main_content"].encode()
            if local_measurement:
                valid_reference = expected == canonical_cases[c["id"]]["ground_truth_main_content"].encode()
            elif source_dataset:
                digest = hashlib.sha256(expected).hexdigest()
                valid_reference = all(r['metrics'].get('scored_reference_sha256') == digest for r in rs if r['case_id'] == c['id'])
            else:
                valid_reference = hashlib.sha256(expected).hexdigest() == c["source_verification"]["reference_sha256"]
            if not valid_reference:
                raise ValueError(c["id"])
            if not (root / "references" / f"{c['id']}.txt").read_bytes() == expected:
                raise ValueError(c["id"])
        if not {r["provider"] for r in rs if r["case_id"] == c["id"]} == set(
            plan.get(c.get("track", "web"), names)
        ):
            raise ValueError(c["id"])
    zh_pages = {
        p.relative_to(root) for p in root.rglob("*.html") if p.relative_to(root).parts[0] != "en"
    }
    en_pages = {Path(*p.relative_to(root).parts[1:]) for p in (root / "en").rglob("*.html")}
    if has_english:
        if not zh_pages == en_pages:
            raise ValueError(("Chinese/English route inventories differ", zh_pages ^ en_pages))

    def canonical_link(page, value):
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc:
            return value
        target = ((page.parent / parsed.path) if parsed.path else page).resolve().relative_to(root)
        if (
            target.parts
            and target.parts[0] == "en"
            and (target.suffix.lower() in {".html", ".htm"})
        ):
            target = Path(*target.parts[1:])
        return (
            target.as_posix()
            + ("?" + parsed.query if parsed.query else "")
            + ("#" + parsed.fragment if parsed.fragment else "")
        )

    bilingual_issues = []
    asset_manifest = repository / "research/editorial-figures.json"
    article_assets = (
        ArticleAssetParity(root, asset_manifest)
        if has_english and asset_manifest.exists() and (root / "assets/editorial").exists()
        else None
    )
    if has_english:
        for relative in sorted(zh_pages):
            zh_path = root / relative
            en_path = root / "en" / relative
            zh = PageShape(zh_path, article_assets)
            zh.feed(zh_path.read_text())
            en = PageShape(en_path, article_assets)
            en.feed(en_path.read_text())
            if zh.tags != en.tags:
                bilingual_issues.append((relative, "semantic tag counts differ"))
            if zh.ids != en.ids:
                bilingual_issues.append((relative, "element IDs differ"))
            if Counter(zh.data) != Counter(en.data):
                bilingual_issues.append((relative, "data attributes differ"))
            zh_numbers = Counter(zh.numbers)
            en_numbers = Counter(en.numbers)
            if zh_numbers != en_numbers:
                bilingual_issues.append(
                    (
                        relative,
                        "visible numeric values differ",
                        zh_numbers - en_numbers,
                        en_numbers - zh_numbers,
                    )
                )
            zh_links = Counter(canonical_link(zh_path, value) for value in zh.links)
            en_links = Counter(canonical_link(en_path, value) for value in en.links)
            if zh_links != en_links:
                bilingual_issues.append((relative, "evidence or navigation links differ"))
            if relative == Path("methodology/index.html"):
                check_localized_image_sources(zh, en, canonical_link)
            if relative in {Path("guide/index.html"), Path("guide/article.html")}:
                if Counter(canonical_link(zh_path, value) for value in zh.images) != Counter(
                    canonical_link(en_path, value) for value in en.images
                ):
                    bilingual_issues.append((relative, "article image sources differ"))
    if not not bilingual_issues:
        raise ValueError(
            ("Chinese/English page parity failed", bilingual_issues[:200], len(bilingual_issues))
        )
    english_primary = [
        Path(name)
        for name in (
            "index.html",
            "methodology/index.html",
            "review/index.html",
            "guide/index.html",
            "adoption/index.html",
            "reproduce/index.html",
            "changes/index.html",
            "accounts/index.html",
        )
    ]
    allowed_english_source_text = {"中文"} | {
        case["name"] for case in d["cases"] if case.get("name")
    }
    archived_manifest = root / "archived-reading.json"
    if archived_manifest.is_file():
        from .report_reading import render_archived_reading
        # Validate the archived source before allowing its original case names.
        render_archived_reading(root, "en" if has_english else "zh")
        allowed_english_source_text.update(json.loads(archived_manifest.read_text()).get("preserved_source_names", []))
    if has_english:
        for relative in english_primary:
            if relative not in en_pages:
                continue
            parser = EnglishNarrative(allowed_english_source_text)
            parser.feed((root / "en" / relative).read_text())
            if not not parser.untranslated:
                raise ValueError(
                    (relative, "untranslated English report narrative", parser.untranslated[:20])
                )
    for p in root.rglob("*.html"):
        text = p.read_text()
        check_presentation(text)
        reader = VisibleText()
        reader.feed(text)
        visible = " ".join(reader.parts)
        for banned in [
            "人工复核",
            "人工检查",
            "短参考答案词级",
            "组 · 查看",
            "已确认有效",
            "标准答案漂移候选队列",
            "需辅助",
            "需用户辅助",
            "请自行登录",
            "补采记录",
            "本轮解析修正",
            "内容已合并",
            "build_v8_report",
            "report_v8.py",
            "任务修订",
            "评分 8.2",
            "已执行/总案例",
            "已判分/已调用",
        ]:
            if not banned not in visible:
                raise ValueError((p, banned))
        if not not re.search("\\bHIL\\b", visible):
            raise ValueError((p, "HIL"))
        parser = Tables()
        parser.feed(text)
        relative_parts = p.relative_to(root).parts
        if "browser-use" not in relative_parts:
            require_table_index_headers(parser, english=relative_parts[0] == 'en',
                                        diagnostic=local_measurement)
        if p.parent.name == "attempts":
            record = next(r for r in rs if r["attempt_id"] == p.stem)
            case = case_by_id[record["case_id"]]
            heading = (
                "目标文件"
                if case.get("task_kind") == "file"
                else "标准答案与验收规则"
                if case.get("task_kind") == "structure"
                else "验收规则（无全文参考）"
                if case.get("task_kind", "body") == "body" and not (case.get("ground_truth_main_content") or case.get("reference_answer"))
                else "完整标准答案"
            )
            actual_heading = (
                {
                    "目标文件": "Target file",
                    "标准答案与验收规则": "Reference and acceptance contract",
                    "完整标准答案": "Complete reference answer",
                    "验收规则（无全文参考）": "Acceptance rules (no full-text reference)",
                }[heading]
                if p.relative_to(root).parts[0] == "en"
                else heading
            )
            if not text.count("<h3>" + actual_heading + "</h3>") == 1:
                raise ValueError(p)
            if (
                not text.count(
                    "<h3>"
                    + ("Actual output" if p.relative_to(root).parts[0] == "en" else "实际返回")
                    + "</h3>"
                )
                == 1
            ):
                raise ValueError(p)
            for delivered in (
                record.get("provider_metadata", {}).get("file_evaluation", {}).get("files", [])
            ):
                if not ("../" + delivered["artifact_path"] in text and delivered["sha256"] in text):
                    raise ValueError(p)
    execution = json.loads((root / "execution.json").read_text())["batches"]
    physical_attempts = Counter(i for b in execution for i in b["attempt_ids"])
    logical_attempts = Counter(r["attempt_id"] for r in rs)
    if d.get('comparison', {}).get('task_replacement'):
        require_replacement_execution(root, d['comparison']['task_replacement'],
                                      physical_attempts, logical_attempts)
    elif d.get("comparison", {}).get("superseded_formal_provider_calls"):
        if not set(physical_attempts) == set(logical_attempts):
            raise ValueError("Report acceptance check failed")
        if (
            not sum(physical_attempts.values()) - sum(logical_attempts.values())
            == d["comparison"]["superseded_formal_provider_calls"]
        ):
            raise ValueError("Report acceptance check failed")
    elif not physical_attempts == logical_attempts:
        raise ValueError("Report acceptance check failed")
    home = (root / "index.html").read_text()
    track_count = len({case.get("track", "web") for case in d["cases"]})
    require_home_comparison_layout(home, track_count, diagnostic=local_measurement and not sample_ranking, sample_ranking=sample_ranking)
    if (root / "resource-cohort/resource-comparison.json").is_file():
        from .report_resources import check_resource_presentation
        resource_summary = json.loads((root / "resource-cohort/resource-comparison.json").read_text())
        guide = (root / "guide/index.html").read_text()
        for page_name, text in [("home", home), ("guide", guide)]:
            check_resource_presentation(text, resource_summary)
            if "data-resource-winner=" not in text:
                raise ValueError((page_name, "missing resource winner summary"))
            if not all(label in text for label in ("CPU 与计算", "内存", "网络")):
                raise ValueError((page_name, "missing grouped resource tables"))
        if has_english:
            for page_name, text in [
                ("English home", (root / "en/index.html").read_text()),
                ("English guide", (root / "en/guide/index.html").read_text()),
            ]:
                if "data-resource-winner=" not in text:
                    raise ValueError((page_name, "missing resource winner summary"))
                if not all(label in text for label in ("CPU and compute", "Memory", "Network")):
                    raise ValueError((page_name, "missing grouped resource tables"))
                check_resource_presentation(text, resource_summary)
    methodology = (root / "methodology/index.html").read_text()
    for phrase in [
        "计入耗时",
        "不计入耗时",
        "供应商上报的服务端耗时只作附加证据",
        "形成可送入统一评分器的最终答案",
    ]:
        if phrase not in methodology:
            raise ValueError(phrase)
    return (
        f"PASS: {len(d['cases'])} tasks / {len(rs)} records / {len(names)} providers; "
        f"{len(list(root.rglob('*.html')))} logical report routes; "
        "reference integrity, all numbered tables, no workflow leftovers."
    )


def main(argv=None, *, authority_root: Path | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument('--source-dataset', type=Path)
    parser.add_argument("--authority-root", type=Path, default=authority_root or Path.cwd())
    parser.add_argument("--presentation-binding", type=Path)
    args = parser.parse_args(argv)
    result = check(args.root, authority_root=args.authority_root, profile=args.profile, presentation_binding=args.presentation_binding, source_dataset=args.source_dataset)
    if result is not None:
        print(result)


def check_revision(out: Path, *, authority_root: Path):
    reject_evidence_symlinks(out)
    out = out.resolve()
    repository = authority_root.resolve()
    if (out / "wcxb-score-summary.json").is_file():
        from harness.web_fetch.wcxb_report_check import check as check_wcxb

        check_wcxb(out, authority_root=repository)
        return "PASS: WCXB release scope and report acceptance checks verified"
    dataset = json.loads((out / "dataset.json").read_text())
    from bs4 import BeautifulSoup

    if dataset.get("comparison", {}).get("kind") == "webmainbench_frozen_html_to_markdown":
        from harness.web_fetch.webmainbench_report_check import check_sample_report

        check_sample_report(out, authority_root=repository)
        return (
            "PASS: WebMainBench preselected-200 release scope and report acceptance checks verified"
        )
    binding = dataset.get('comparison', {}).get('report_interpretation')
    if binding:
        from .report_explainer import check_current_browser_article
        check_current_browser_article(out, {case['id']:case for case in dataset['cases']},
                                      load_jsonl(out/'results.jsonl'), binding)
    provenance_path = out / "implementation-provenance.json"
    if provenance_path.is_file():
        from .methods import native_browser_engine
        provenance = json.loads(provenance_path.read_text())
        records = load_jsonl(out / "results.jsonl")
        home = BeautifulSoup((out / "index.html").read_text(), "html.parser")
        for item in provenance.get("implementations", []):
            name = item.get("provider")
            if native_browser_engine(name) != "moli" or not item.get("version"):
                continue
            hashes = {r.get("provider_metadata", {}).get("binary_sha256")
                      for r in records if r["provider"] == name}
            if hashes != {item.get("binary_sha256")}:
                continue
            expected = "Moli " + item["version"]
            links = home.select('a[href="providers/' + name + '.html"]')
            if not links or any(anchor.get_text() != expected for anchor in links):
                raise ValueError("Measured method version label: " + name)
    check_language_contract(out)
    has_english = "en" in generated_languages(out)
    for path in out.rglob("*.html"):
        if not has_english and path.relative_to(out).parts[0] == "en":
            continue
        soup = BeautifulSoup(path.read_text(), "html.parser")
        relative_parts = path.relative_to(out).parts
        is_english = relative_parts[0] == "en"
        is_browser_use = "browser-use" in relative_parts
        if path in {out / "index.html", out / "en/index.html"}:
            overview = soup.select_one(".home-overview, #overview")
            if not (overview and (overview.name != "details" or not overview.has_attr("open"))):
                raise ValueError(path)
            panels = soup.select('#home-rankings > [role="tabpanel"]')
            if not sum(not p.has_attr("hidden") for p in panels) == 1:
                raise ValueError(path)
            for panel in panels:
                if not len(panel.select(".track-guide")) == 1:
                    raise ValueError(path)
                if not len(panel.select(".track-flow li")) >= 4:
                    raise ValueError(path)
                for selector in (".track-meaning", ".track-flow", ".pass-contract"):
                    if panel.select_one(selector).find_parent("details") is not None:
                        raise ValueError(path)
                for selector in (".track-more", ".ranking-metric-help"):
                    if not not panel.select_one(selector).has_attr("open"):
                        raise ValueError(path)
        for pre in soup.select("pre"):
            pre.decompose()
        if not ("条件通过率" not in soup.get_text() and "条件达标率" not in soup.get_text()):
            raise ValueError(path)
        if not is_browser_use:
            for table in soup.select("table"):
                width = len(table.select("thead th"))
                if not all(
                    len(row.find_all("td", recursive=False)) == width
                    for row in table.select("tbody > tr")
                ):
                    raise ValueError((path, width))
        for table in soup.select("table.ranking"):
            headers = table.select("thead th")
            body_families = {"web", "dynamic", "challenge", "risk_control", "document"}
            family = table.get("id", "").removeprefix("ranking-")
            if family in body_families and any(
                c.get("track", "web") == family and c.get("task_kind", "body") in {"body", "structure"}
                for c in dataset["cases"]
            ):
                labels = [h.get_text() for h in headers]
                required = (
                    ["Recall", "Precision", "F1"] if is_english else ["完整度", "纯净度", "F1"]
                )
                forbidden = (
                    ["Resource hit rate", "File retrieval rate"]
                    if is_english else ["资源命中率", "文件取得率"]
                )
                if not all(any(label in h for h in labels) for label in required) or any(
                    label in h for label in forbidden for h in labels
                ):
                    raise ValueError((path, "Body ranking metric columns", family))
            sort = int(table["data-col"])
            expected = (
                (["Matched repositories"] if is_english else ["命中仓库数"])
                if path.parent.name == "adoption"
                else ["Task pass rate", "Body pass rate", "Overall task pass rate"]
                if is_english
                else ["任务通过率", "正文通过率", "综合任务通过率"]
            )
            if not any(label in headers[sort].get_text() for label in expected):
                raise ValueError((path, sort))
            legend = table.parent.find_previous_sibling(
                "div", class_="metric-help"
            ) or table.parent.find_previous_sibling("details", class_="ranking-metric-help")
            if not (legend and legend.select("dt")):
                raise ValueError(path)
        track = soup.select_one('select[data-filter="track"]')
        if track:
            if not track.select("option")[0]["value"] == "":
                raise ValueError(path)
        if path.parent.name in {"providers", "analysis"}:
            if not not soup.select(
                'select[data-filter="transport"],select[data-filter="content"],select[data-filter="resources"]'
            ):
                raise ValueError(path)
            expected_results = (
                ["Pass", "Fail", "All"] if is_english else ["任务通过", "任务失败", "全部"]
            )
            if (
                not [x.get_text() for x in soup.select('select[data-filter="result"] option')]
                == expected_results
            ):
                raise ValueError(path)
            if (
                not soup.select_one('select[data-filter="result"] option[selected]')["value"]
                == "fail"
            ):
                raise ValueError(path)
            expected_failure = "Failure class" if is_english else "失败分类"
            if expected_failure not in [h.get_text() for h in soup.select("#filtered-table th")]:
                raise ValueError(path)
    records = load_jsonl(out / 'results.jsonl')
    from .methods import native_browser_engine
    synthetic = dataset.get('comparison', {}).get('kind') == 'synthetic_browser_report'
    if records and all(row.get('provider_type') == 'fixture' for row in records) and (
        synthetic or all(not native_browser_engine(row['provider']) for row in records)
    ):
        methods = BeautifulSoup((out / 'adoption/index.html').read_text(), 'html.parser')
        for name in {row['provider'] for row in records}:
            if methods.find(id='interface-' + name) is None:
                raise ValueError('Synthetic adapter interface is missing: ' + name)
        return 'PASS: synthetic report presentation, declared adapters and bound interpretation verified'
    counts = json.loads((out / "adoption/counts.json").read_text())["methods"]
    names = {row["provider"] for row in load_jsonl(out / "results.jsonl")}
    from harness.web_fetch.report_methods import PROFILES

    if not {item["provider"] for item in counts} == set(PROFILES) | names:
        raise ValueError("Report presentation check failed")
    if len(counts) != len({item["provider"] for item in counts}):
        raise ValueError("Duplicate adoption provider counts")
    interfaces = BeautifulSoup((out / "adoption/index.html").read_text(), "html.parser").select(
        '.interface-panel[id^="interface-"]'
    )
    from harness.web_fetch.report_interfaces import SPECS
    from harness.web_fetch.methods import native_browser_engine
    interface_names = set(SPECS) | {name for name in names if native_browser_engine(name)}

    if not (
        len(interfaces) == len(interface_names)
        and sum(p.has_attr("open") for p in interfaces) == bool(interfaces)
    ):
        raise ValueError("Report presentation check failed")
    if {panel.get("id") for panel in interfaces} != {"interface-" + name for name in interface_names}:
        raise ValueError("Interface inventory differs from registry")
    for panel in interfaces:
        if not len(panel.select("pre")) == 2:
            raise ValueError("Report presentation check failed")
    for item in counts:
        if item["repository_count"] is None:
            continue
        if not item["repository_count"] == len({x["repo"].lower() for x in item["repositories"]}):
            raise ValueError("Report presentation check failed")
        if not item["file_count"] == len(
            {(r["repo"].lower(), m["path"]) for r in item["repositories"] for m in r["matches"]}
        ):
            raise ValueError("Report presentation check failed")
    return (
        "PASS: all tables align, obsolete metrics absent, active sort columns correct, "
        "three-state results, all-type defaults, adoption counts traceable"
    )


def revision_main(argv=None, *, authority_root: Path | None = None):
    parser = argparse.ArgumentParser(description="Report presentation and adoption checks")
    parser.add_argument("root", type=Path, nargs="?")
    parser.add_argument("--authority-root", type=Path, default=authority_root or Path.cwd())
    args = parser.parse_args(argv)
    result = check_revision(
        args.root or args.authority_root / "reports/webfetch", authority_root=args.authority_root
    )
    print(result)
