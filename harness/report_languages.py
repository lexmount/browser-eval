"""Explicit language output contract shared by report tracks."""

import html
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

class ReportLanguageError(ValueError):
    """Invalid report-language configuration, before evaluation starts."""


SUPPORTED_REPORT_LANGUAGES = ("en", "zh")
REPORT_PAGE_MARKER = '<meta name="browser-eval-report" content="1">'
REPORT_LANGUAGE_ENV = "BROWSER_EVAL_REPORT_LANGUAGE"
REPORT_LANGUAGE_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def configured_report_language(
    *, supported: tuple[str, ...] = SUPPORTED_REPORT_LANGUAGES
) -> str:
    """Return the environment-selected language, or the public English default."""
    from dotenv import dotenv_values

    configured = os.environ.get(REPORT_LANGUAGE_ENV)
    if configured is None:
        configured = dotenv_values(REPORT_LANGUAGE_ENV_FILE).get(REPORT_LANGUAGE_ENV)
    selected = configured.strip().lower() if configured else "en"
    if selected in SUPPORTED_REPORT_LANGUAGES and selected not in supported:
        return supported[0]
    if selected not in supported:
        if configured:
            raise ReportLanguageError(
                f"{REPORT_LANGUAGE_ENV} must be one of {', '.join(supported)}; got {selected}"
            )
        return supported[0]
    return selected


def includes_english(language: str | None, include_english: bool = False) -> bool:
    if language is not None and language not in SUPPORTED_REPORT_LANGUAGES:
        raise ReportLanguageError("Unsupported primary report language; choose en or zh")
    return include_english or language == "en"


def add_language_argument(parser, *, default=None) -> None:
    parser.add_argument(
        "--language", choices=SUPPORTED_REPORT_LANGUAGES, default=default,
        help="Primary report language: en (English, with a Chinese edition) or zh (Chinese)",
    )


def language_metadata_path(root: Path) -> Path:
    for name in ("report-languages.json", "report-manifest.json"):
        path = root / name
        if path.is_file():
            return path
    return root / "report-manifest.json"


def primary_report_language(root: Path, *, default: str | None = None) -> str:
    path = language_metadata_path(root)
    value = json.loads(path.read_text()) if path.is_file() else {}
    language = value.get("primary_language", "zh") if value else (
        default or configured_report_language()
    )
    if language not in SUPPORTED_REPORT_LANGUAGES:
        raise ReportLanguageError("Unsupported primary report language; choose en or zh")
    return language


def resolve_report_language(root: Path, language: str | None = None, *,
                            supported: tuple[str, ...] = SUPPORTED_REPORT_LANGUAGES) -> str:
    """Prefer explicit choice, then saved choice, then the renderer's default."""
    if language is not None:
        selected = language
    elif language_metadata_path(root).is_file():
        selected = primary_report_language(root)
    else:
        selected = configured_report_language(supported=supported)
    if selected not in supported:
        raise ReportLanguageError(f"This report supports only {', '.join(supported)}; requested {selected}")
    return selected


def select_report_language(root: Path, language: str) -> Path:
    """Set the primary entry in a freshly rendered report tree."""
    if language not in SUPPORTED_REPORT_LANGUAGES:
        raise ReportLanguageError("Unsupported primary report language; choose en or zh")
    if language not in generated_languages(root):
        raise ReportLanguageError(f"Report has no generated {language} edition")
    entry = "en/index.html" if language == "en" else "index.html"
    if not (root / entry).is_file():
        raise ReportLanguageError("Selected report homepage is missing")
    if language == "en":
        if REPORT_PAGE_MARKER not in (root / "index.html").read_text():
            raise ReportLanguageError("Report homepage is not a rendered report page")
        # Keep the established bilingual routes; explicit Chinese links bypass
        # the English default at the shared root homepage.
        for page in root.rglob("*.html"):
            # Saved source HTML may itself be a Browser Eval report.
            if page.relative_to(root).parts[0] in {"artifacts", "references", "inputs", "sources"}:
                continue
            text = page.read_text(encoding="utf-8")
            if REPORT_PAGE_MARKER not in text:
                continue
            base = re.search(r'<base\b[^>]*?href="([^"]*)"', text)
            route_base = page.parent / html.unescape(base[1]) if base else page.parent

            def chinese_home(match):
                parts = urlsplit(html.unescape(match[2]))
                if parts.scheme or parts.netloc or match[2].startswith("#"):
                    return match[0]
                target = (route_base / parts.path).resolve() if parts.path or base else page.resolve()
                if target not in {root.resolve(), (root / "index.html").resolve()}:
                    return match[0]
                query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                         if key != "report-language"]
                query.append(("report-language", "zh"))
                href = urlunsplit(parts._replace(query=urlencode(query)))
                return match[1] + html.escape(href, quote=True) + match[3]
            text = re.sub(
                r'<(?P<tag>header|nav)\b[^>]*>.*?</(?P=tag)>',
                lambda header: re.sub(r'(<a\b[^>]*?\bhref=")([^"]*)(")', chinese_home, header[0]),
                text, flags=re.S,
            )
            if page == root / "index.html":
                redirect = (
                    '<script data-report-language-entry>'
                    'if(new URLSearchParams(location.search).get("report-language")!=="zh")'
                    '{const u=new URL("en/index.html",document.baseURI);'
                    'u.search=location.search;u.searchParams.delete("report-language");'
                    'u.hash=location.hash;location.replace(u.href);}'
                    '</script>'
                )
                if "</head>" not in text:
                    raise ReportLanguageError("Report homepage has no head element")
                text = text.replace("</head>", redirect + "</head>", 1)
            page.write_text(text, encoding="utf-8")
    path = language_metadata_path(root)
    contract = json.loads(path.read_text())
    contract.update(primary_language=language, entrypoint=entry)
    path.write_text(json.dumps(contract) + "\n", encoding="utf-8")
    return root / entry


def generated_languages(root: Path) -> tuple[str, ...]:
    path = language_metadata_path(root)
    if not path.exists():
        return ("zh", "en")
    value = json.loads(path.read_text(encoding="utf-8"))
    # Older trajectory manifests predate explicit language metadata and are bilingual.
    legacy = path.name == "report-manifest.json" and "primary_language" not in value
    languages = value.get("languages", ["zh", "en"] if legacy else None)
    if languages not in (["zh"], ["zh", "en"]):
        raise ReportLanguageError("unsupported generated report languages")
    return tuple(languages)


def english_notice(*, nested: bool = True) -> str:
    """Use the report root as the preview base for either supported entry location."""
    directory = "en/" if nested else "./"
    return_href = "../index.html" if nested else "index.html"
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<script>(function(){const b=document.querySelector("base[href]");'
        f'if(b)b.href=new URL("{directory}",b.href).href;}})();</script>'
        '<title>英文报告未生成</title></head><body><main data-english-not-generated>'
        '<h1>英文报告未生成</h1><p>English report has not been generated.</p>'
        '<p>当前报告仅提供中文版。英文版需单独生成。</p>'
        f'<a href="{return_href}">返回中文报告</a></main></body></html>'
    )


def write_language_contract(root: Path, *, include_english: bool) -> None:
    (root / "report-languages.json").write_text(
        json.dumps({"languages": ["zh", "en"] if include_english else ["zh"],
                    "english_status": "generated" if include_english else "not_generated"}) + "\n",
        encoding="utf-8",
    )
    if include_english:
        return
    folder = root / "en"
    folder.mkdir(exist_ok=True)
    if any(folder.iterdir()):
        raise ReportLanguageError("English output must not be generated in Chinese-only mode")
    (folder / "index.html").write_text(
        english_notice(),
        encoding="utf-8",
    )


def check_primary_report_language(root: Path) -> None:
    path = language_metadata_path(root)
    contract = json.loads(path.read_text()) if path.is_file() else {}
    if "primary_language" in contract:
        language = primary_report_language(root)
        entry = "en/index.html" if language == "en" else "index.html"
        if (language not in generated_languages(root)
                or contract.get("entrypoint") != entry or not (root / entry).is_file()):
            raise ReportLanguageError("Primary report language and entrypoint differ from generated output")
        redirected = 'data-report-language-entry' in (root / "index.html").read_text()
        if redirected != (language == "en"):
            raise ReportLanguageError("Report root does not open the selected language")


def check_language_contract(root: Path) -> None:
    check_primary_report_language(root)
    if "en" in generated_languages(root):
        return
    files = {p.relative_to(root / "en").as_posix() for p in (root / "en").rglob("*") if p.is_file()}
    if files != {"index.html"}:
        raise ReportLanguageError("Chinese-only report contains stale English output")
    notice = (root / "en/index.html").read_text(encoding="utf-8")
    if "data-english-not-generated" not in notice or 'href="../index.html"' not in notice:
        raise ReportLanguageError("English not-generated notice is missing")


def localized_report_routes(root: Path, expected: set[str]) -> dict[str, dict[str, Path]]:
    """Check complete route inventories for the explicitly generated editions."""
    check_language_contract(root)
    inventories = {}
    for language in generated_languages(root):
        base = root if language == 'zh' else root / 'en'
        routes = {path.relative_to(base).as_posix(): path for path in base.rglob('*.html')
                  if language != 'zh' or path.relative_to(base).parts[0] != 'en'}
        if set(routes) != expected:
            raise ReportLanguageError(f'{language} report route inventory differs: '
                             f'missing={sorted(expected - set(routes))[:5]}, '
                             f'extra={sorted(set(routes) - expected)[:5]}')
        inventories[language] = routes
    return inventories


def visible_numbers(text: str) -> list[str]:
    """Read Arabic numeric values next to either Chinese or English prose."""
    return re.findall(r"\d+(?:[.,]\d+)*(?:%|(?=$|[^A-Za-z0-9_]))", text)
