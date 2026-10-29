# Create and share a report

Report generation is optional. When you need a report, build it locally from saved results. Local builds need no Artifact Site account or publication token.

The Web Fetch CLI builds a local report after `run` and `rescore` by default. Add `--no-report` to either command to save the evaluation or scoring outputs without generating HTML. `rescore --no-report` leaves any existing report unchanged; rebuild it to inspect the updated scores.

To build a report later from a saved Web Fetch CLI run:

```sh
uv run --frozen python -m harness.web_fetch report /absolute/run
```

The generated pages are saved in `<run>/report/`. Other tracks have their own [report builders](../README.md#evaluation-tracks). Keep the run's saved results and evidence locally so you can inspect and rebuild the report.

## Report instances

Keep report profiles, evidence, authored content and publication receipts outside
the checkout. Pass `--profile /absolute/instance-profile.json` to report build,
check and package commands, or `PROFILE=/absolute/instance-profile.json` to their
Make targets. A new instance reuses the same commands with different inputs.
Historical full-cohort formats use an explicit `--authority-root` bundle that
retains their original relative paths and hash-bound profile bytes.

An optional `benchmark_release` path/hash binding in the external profile fixes
the selected benchmark edition. Builds and packages retain
`benchmark-release.json`; presentation rebuilds keep its dataset and scoring
identity. See [benchmark editions](benchmarks.md#benchmark-editions) for the
version and comparison rules. A newer task package must not silently update
an older report.

## Report languages

The Web Fetch CLI falls back to English. `BROWSER_EVAL_REPORT_LANGUAGE=zh` changes the environment default, and `--language` overrides it. A rebuild without `--language` keeps the saved choice.

| Builder | Language selection |
| --- | --- |
| `browser-harness-eval run`, `report`, `rescore` | `--language en` or `--language zh`; fallback English, environment configurable |
| `scripts/build_report.py`: standard Web Fetch, local audit, WCXB, WebMainBench sample, research companion | `--language en` or `--language zh`; fallback English, environment configurable |
| `scripts/build_browser_use_report.py` | `--language en` or `--language zh`; fallback English, environment configurable |
| `scripts/build_webarena_replay_report.py` | `--language en` or `--language zh`; fallback English, environment configurable |
| Complete 545-case WebMainBench and diagnostic-cohort reports | Chinese-only content; omitted language keeps the historical Chinese default; explicit `en` is rejected |
| Curated replay example, public WebArena recording report and catalog | Chinese-only renderers; no language option |

Builders keep the saved language when rebuilding without `--language`. Make follows the same rule; set an explicit override through `REPORT_LANGUAGE=en` or `REPORT_LANGUAGE=zh`.

English builds retain a Chinese edition so readers can switch languages. Chinese-only builds show a notice at the English entry. These choices affect report text and navigation, not scores, timings or saved evidence. Task names, source quotations, responses and logs stay in their original language.

Serve the local report directory over HTTP to use evidence that loads on demand. The report root opens the selected language. When sharing, include the generated editions or English notice, assets and declared public evidence in the report bundle.

Only `en` and `zh` are supported. Unsupported codes fail before a run starts or an existing report is replaced. Browser or operating-system language does not silently change a report. To add a language, extend the owning renderer's translation catalog and route, value and evidence-link checks; see [contributing](../CONTRIBUTING.md).

## Optional sharing

Upload to Artifact Site only when the user has authorized publication to the selected target. Until then, keep the report local.

To share a report, open Artifact Site ([Global .com](https://yours.lexmount.com/artifact-site) · [China .cn](https://yours.lexmount.cn/artifact-site)) and install the skill provided there. Use it to publish the report bundle and set access permissions. Keep the remaining run files local for rebuilding and auditing.

## Maintained benchmark releases

For an authorized formal release, use the selected track's package/publish commands after certification, required review and release checks. See [architecture](architecture.md#reports-and-publication) and the [track guides](../README.md#evaluation-tracks).

| Requirement | Why it matters |
| --- | --- |
| Use the profile's host, site and expected remote version | Prevents overwriting another report or changing a historical experiment |
| Create your own profile and target for a new experiment | Historical publication targets belong to their original reports |
| Keep publication authorization separate from evaluation credentials | Repository publishers never read `.env` for publishing |

The repository publisher reads `ARTIFACT_SITE_TOKEN`, then the selected host's token under `~/.config/artifact-site/tokens/<host>`. Previous Artifact Hub locations are read-only migration fallbacks. Save authorizations per host without overwriting another deployment's token.
