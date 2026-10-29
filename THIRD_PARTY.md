# Third-party content and licenses

The root Apache-2.0 license covers project-owned code and documentation. It does not replace licenses for third-party code, pages, images, documents or datasets. Existing copyright notices and directory licenses remain in effect.

| Content | Source and binding | Distribution boundary |
| --- | --- | --- |
| LexBench Web Fetch / Web Search code | Original source commits and packaging/input changes recorded in `harness/benchmarks/code-migration.json` | Project-owned Apache-2.0 code; retained names and copyright in NOTICE |
| LexBench datasets | Hugging Face commits and hashes in `harness/benchmarks/data-lock.json` | Downloaded separately; preserve each snapshot's upstream notices. SimpleQA Verified data retains MIT attribution |
| WebArena-Verified | Source repository and commit pinned in `pyproject.toml` and `uv.lock` | Installed as a dependency; retain upstream license and attribution |
| Humanizer / Humanizer Chinese | [blader/humanizer](https://github.com/blader/humanizer), [jiji262/humanizer-chinese](https://github.com/jiji262/humanizer-chinese); commits and file hashes in each skill's `SOURCE.json` | MIT; upstream skill and license files are copied unchanged. See [skill sources and updates](.agents/skills/README.md) |
| WebMainBench | Pinned scorer and versioned dataset inputs selected by `harness/benchmarks/data-lock.json`; historical evidence retains its own bindings | Preserve upstream Apache-2.0 notices and source-page terms |
| WCXB dataset and metric adaptations | [Murrough Foley's Web Content Extraction Benchmark](https://github.com/Murrough-Foley/web-content-extraction-benchmark); the metric owner records the original evaluator hash | CC-BY-4.0. Token multiset F1 and snippet checks are adapted in `harness/web_fetch/wcxb_scoring.py`; retain the [original license](harness/licenses/WCXB-CC-BY-4.0.txt). Dataset snapshots retain upstream attribution, selection provenance and original input hashes |
| Independently captured pages, PDFs, media and reference snapshots | Task provenance, `evaluation/source-inputs.json` and each track's external evidence bindings | Private data storage; import approved copies through manifests. Public accessibility does not imply redistribution permission |
| Human trajectories and task definitions | Replay configurations, provenance and qualification evidence | Retain origins and usage terms; distinguish definitions, recordings and measured outputs |
| Repository editorial skills | Origins and notices under `.agents/skills/` | Retain their notices; they are not browser-package runtime dependencies |
| Synthetic offline examples | `harness/web_fetch/data/` | Project-owned test inputs with no third-party captures |
| Resource-collection overhead calibration | `harness/resource_calibration.py`, derived from a [pinned Lexbench source](https://github.com/lexmount/Lexbench-Headless-Browser/blob/fd8d64c7623d46cd165607f216a0e28967d03040/runner/resources.py); source hash recorded in the module | Apache-2.0; retain the [full license](harness/licenses/Lexbench-Apache-2.0.txt) and attribution in source and wheel distributions |

Measured results, sessions, raw provider responses and private review material are outside the source distribution. Public evidence packages also need privacy checks and the track's release gates. A source URL or SHA-256 alone does not prove permission; material without a clear authorization basis must stay out of public source packages.
