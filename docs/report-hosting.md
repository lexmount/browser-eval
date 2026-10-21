# Publish a report

Publish the generated `report/` directory. Keep the rest of the run directory locally so you can rebuild and audit it.

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

Upload the entire report directory, including the English notice or explicitly generated edition, assets and evidence. The report root opens the selected language. Serve the directory over HTTP for evidence that loads on demand.

Only `en` and `zh` are supported. Unsupported codes fail before a run starts or an existing report is replaced. Browser or operating-system language does not silently change a report. To add a language, extend the owning renderer's translation catalog and route, value and evidence-link checks; see [contributing](../CONTRIBUTING.md).

## 1. Choose a site

[Artifact Site](https://github.com/lexmount/artifact-site) hosts multi-page HTML reports and keeps version history. Sign in or create an account on one of these deployments:

| Deployment | Website and CLI base | Publishing guide |
| --- | --- | --- |
| China | [https://artifact-site.lexmount.cn](https://artifact-site.lexmount.cn/) | [Guide](https://artifact-site.lexmount.cn/for-agents.md) |
| Global | [https://yours.lexmount.com/artifact-site](https://yours.lexmount.com/artifact-site) | [Guide](https://yours.lexmount.com/artifact-site/for-agents.md) |
| Your server | Follow the [self-hosting setup](https://github.com/lexmount/artifact-site#quick-start-local-deployment) | `<your-base>/for-agents.md` |

Use the complete base URL, including `/artifact-site` on the global site. The same deployment serves its agent guide at `/for-agents.md` and MCP at `/mcp`.

## 2. Upload the report

With Node.js and npm installed, authorize the CLI and upload:

```bash
npx --yes @artifact-site/cli@0.4.0 login --base https://artifact-site.lexmount.cn
npx --yes @artifact-site/cli@0.4.0 publish /absolute/path/to/run/report \
  --share none --base https://artifact-site.lexmount.cn
```

Follow the login page to approve access. For the global site, use `--base https://yours.lexmount.com/artifact-site` in both commands; for self-hosting, use your server's base URL.

These examples pin the tested CLI 0.4.0. `--share none` prevents that older client from creating an extra share link. New artifacts are private by default; check the returned owner, version, URL and access settings.

An agent can also publish through MCP using the deployment's guide. To update an existing report, supply its expected current version as the guide specifies. If an operation is interrupted, recover that upload rather than creating a duplicate.

## 3. Set access and check the report

Choose access in the website's sharing controls:

| Setting | Who can read it? |
| --- | --- |
| `private` | Authorized readers only |
| `unlisted` | Anyone with the main link; absent from public discovery |
| `public` | Publicly readable and discoverable |

CLI 0.4.0 has no `visibility` command. Use the website or the current API/MCP operations documented by your deployment.

Open a task result and follow its response, log and return links on the hosted report. Check that everything you share is intended to be public. Another static host works too if it preserves relative routes and evidence files.

Example runs remain diagnostic after upload. Publishing does not certify benchmark results.

## Maintained benchmark releases

Formal releases use the selected track's package/publish commands after certification, required review and release checks. See [architecture](architecture.md#reports-and-publication) and the [track guides](../README.md#what-it-tests).

| Requirement | Why it matters |
| --- | --- |
| Use the profile's host, site and expected remote version | Prevents overwriting another report or changing a historical experiment |
| Create your own profile and target for a new experiment | Historical publication targets belong to their original reports |
| Keep publication authorization separate from evaluation credentials | Repository publishers never read `.env` for publishing |

The repository publisher reads `ARTIFACT_SITE_TOKEN`, then the selected host's token under `~/.config/artifact-site/tokens/<host>`. Previous Artifact Hub locations are read-only migration fallbacks. Save authorizations per host without overwriting another deployment's token.
