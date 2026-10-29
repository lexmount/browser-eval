# LexBench-WebArena-Replay

WebArena task definitions paired with fixed browser actions for Browser Eval. These inputs compare browser execution on the same sequence of work. The pinned WebArena-Verified program evaluates task completion from the browser response and the self-hosted site's state.

The configurations below contain {{total_records}} records in total. They are separate editions with overlapping task IDs. Adding their row counts does not produce a unique benchmark population or a combined score.

{{dataset_summary}}

## Editions

- **Human-qualified:** the eligible subset of the public human-recording import. The original import included candidates that were skipped during qualification; those candidates are outside this configuration.
- **Corrected:** historical corrected fixed-action inputs and their task metadata. Historical qualification receipts stay external and retain their original contracts. Loading this edition does not qualify it under a newer observation protocol.
- **Full:** the complete WebArena-Verified task scope with accepted fixed actions. Its catalog keeps portable trajectories and direct action records distinct. The separate portable suite contains only the portable rows; the catalog covers the full scope.

Task questions keep their upstream wording. Public string IDs such as `webarena-23` accompany the original numeric task IDs. Intents are in English; source content and the requested answers may use other languages.

## Evaluation flow

| Stage | What happens |
| --- | --- |
| Inputs | Select one edition and its fixed actions. |
| Environment | Prepare the required self-hosted WebArena sites. |
| Replay | Execute the fixed actions through the browser adapter. |
| Scoring | Run the pinned WebArena-Verified evaluator and save scores and evidence. |
| Optional report | Build HTML locally. Share only on request and with authorization. |
| Optional verification | Review the results and evidence with an LLM verifier or human sampling. |

The replay executes declared actions without an agent choosing the next step. Compiled selectors preserve the semantics of the original recorded DOM actions. Task completion is checked by the official evaluator program. An LLM can review its verdict and supporting evidence when verification is requested; verification is separate from scoring.

## Data and provenance

The definition bundle contains runtime suites, a full-scope catalog, corrected input definitions, task scopes, a canonical site map, and a registry with file hashes. Auxiliary candidate inventories and a historical normalized example preserve their own source hashes for authoring, diagnostics, and report reconstruction; they are outside the scored configurations above. Dataset viewer records expose the task question and its fixed actions alongside the numeric upstream ID.

The canonical site map uses reserved example origins. Bind it to your self-hosted deployment before execution. Browser Eval uses its existing origin rebinding rules and checks the resulting call hashes. The public projection checks the original and exported action views under explicit HTTP and HTTPS deployment bindings. Compiled selectors and action options are preserved. Catalog action amendments are applied once, with their source hashes retained in the publication receipt.

Retrieval contracts retain their observation rules and projections. Their action references are rebound to the exported calls, while the receipt records both the original and public contract hashes. Cookie or response-cookie expectations in evaluator rules retain their meaning; they are benchmark checks, not captured browser sessions.

`definitions/publication-input-bindings.json` records the original catalog, suite, and action hashes and the exported input hashes. The corrected edition retains only the declarative task type from the historical evaluator envelope. Prior response bodies, claimed success states, and retrieved answers are excluded; their original hashes remain in the publication receipt. Official task reference answers and assertions remain benchmark inputs. Raw qualification runs, HAR files, screenshots, browser sessions, measured responses, and generated results remain in separately bound evidence releases. Private deployment origins and local evidence paths are excluded from the public inputs.

## Use with Browser Eval

Use the [Browser Eval implementation](https://github.com/lexmount/browser-eval). The [fixed-trajectory reproduction guide](https://github.com/lexmount/browser-eval/blob/main/docs/webarena-fixed-trajectory-reproduction.md) and [human-recording replay guide](https://github.com/lexmount/browser-eval/blob/main/examples/trajectory_replay/public_webarena/README.md) explain environment setup, qualification, and execution.

The replay CLI accepts `--no-report` to save scores and evidence without building local HTML. A later explicit report rebuild uses those saved files.

The current default input files resolve through Browser Eval's immutable Hugging Face data lock. Downloads are public and cached locally after hash verification. An explicit historical input path continues to load its own bytes.

For example, inspect the default qualified human suite from the checked-out project:

```python
from harness.trajectory_replay.public_webarena.suite import load_suite

suite = load_suite()
print(suite.tasks[0].title)
print(suite.tasks[0].evaluator)
```

The full-catalog runner binds the default public catalog to the explicit deployment site map using the same catalog binding and validation code used for historical bundles. The official evaluator remains a pinned upstream code dependency. Publishing its task inputs here does not replace that program or relax its qualification and environment checks.

Optional local HTML reports let readers inspect and rebuild results from saved evidence. Share a report only when publication is explicitly requested and authorized. Reports support drilldown from aggregate results to each task's actions, responses, evaluator messages, and logs. Rebuilding a report uses the saved evidence; it does not require repeating browser calls.

## Sources and limits

The benchmark derives from [WebArena](https://github.com/web-arena-x/webarena) and [WebArena-Verified](https://github.com/ServiceNow/webarena-verified). The human-recording source is documented in [WebArena's resource collection](https://github.com/web-arena-x/webarena/tree/main/resources). The upstream task dataset is also available as [AmineHA/WebArena-Verified](https://huggingface.co/datasets/AmineHA/WebArena-Verified). Browser Eval pins its evaluator dependency in code, including the patches used by its supported releases. Source revisions and dataset checksums accompany the inputs.

This is a derivative replay dataset. It adds selected or corrected fixed-action inputs and their provenance. A fixed trajectory evaluates execution of that trajectory; it does not test an agent's ability to plan a solution. Site preparation, service health, application state, browser binaries, and evaluator revisions still affect reproduction and must be bound by the run contract.

A historical accepted action sequence does not establish that a new browser, site deployment, or observation protocol has passed qualification. Keep the selected edition, qualification scope, and measured result denominator together when reporting a comparison.

WebArena and WebArena-Verified use Apache-2.0 licenses. The self-hosted applications and linked resources retain their own terms. See the bundled `NOTICE.txt` for source attribution and the pinned evaluator repository.

## Citation

```bibtex
@misc{browser_eval,
  author = {Lexmount},
  title = {Browser Eval: Benchmarking Browsers and Web Tools for AI Agents},
  year = {2026},
  url = {https://github.com/lexmount/browser-eval}
}
```

Please also credit WebArena, WebArena-Verified, and the public recording authors when using the corresponding inputs.
