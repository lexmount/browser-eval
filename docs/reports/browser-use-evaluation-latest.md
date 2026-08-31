# Browser Use Evaluation — Latest

Run: `35bdd5496c5e`

## Methodology

- Scope: one fixed-limit browser container per engine; controller excluded.
- Browser processes stay warm; cold start and reset work are excluded.
- Browser process and context stay warm. Before each task, the harness clears every top-level navigation origin plus cookies, permissions, cache and history, then replaces only the renderer page outside the timer.
- Repeats: 5; memory/PID sample interval: 100 ms.
- Post-reset quiescence: 1500 ms, excluded from task duration.
- Functional scores use every attempt; resource comparisons use only matched attempts passed by every engine.

## Observed findings and decision

- Chromium passed 50/50 attempts; Moli passed 45/50. Moli failures were confined to Inspect RFC 9110 and navigate to a section.
- Across 45 matched passing attempts, Moli / Chromium medians were 1.69× duration, 1.31× CPU, 0.57× peak memory, 1.65× RX, and 4.61× TX.
- Matched tool-output tokens were 1.00×; this browser-only suite shows no token-cost advantage.
- For Moli, last/first baseline memory was 2.21× while last/first duration was 1.03×. The five repeats did not show systematic runtime slowdown, but the warm process retained allocations and did not return to its first-run footprint after state cleanup.
- Decision for this suite: keep Chromium as the default provider. Offer Moli as an opt-in memory-oriented provider after workload-specific validation; these results support lower peak memory, but do not support broad claims of higher speed, lower CPU, lower network use, or lower token cost.

## Functional results

| Engine | Pass / attempts | Pass rate | Duration median ms | Output tokens median |
|---|---:|---:|---:|---:|
| chromium | 50/50 | 100.00% | 1,580.04 | 1,277.00 |
| moli | 45/50 | 90.00% | 2,212.84 | 885.00 |

## Results by task complexity

### step_01_05

| Engine | Pass / attempts | Pass rate | Duration median ms |
|---|---:|---:|---:|
| chromium | 20/20 | 100.00% | 937.10 |
| moli | 20/20 | 100.00% | 1,180.17 |

Matched all-pass attempts: 20

| Engine | Duration median ms | CPU median ms | Peak memory median MiB | RX median KiB | TX median KiB |
|---|---:|---:|---:|---:|---:|
| chromium | 937.10 | 553.43 | 280.86 | 398.26 | 28.99 |
| moli | 1,180.17 | 326.99 | 148.68 | 800.72 | 43.54 |

### step_06_10

| Engine | Pass / attempts | Pass rate | Duration median ms |
|---|---:|---:|---:|
| chromium | 15/15 | 100.00% | 2,000.33 |
| moli | 15/15 | 100.00% | 4,348.71 |

Matched all-pass attempts: 15

| Engine | Duration median ms | CPU median ms | Peak memory median MiB | RX median KiB | TX median KiB |
|---|---:|---:|---:|---:|---:|
| chromium | 2,000.33 | 886.97 | 334.37 | 801.48 | 135.35 |
| moli | 4,348.71 | 2,857.55 | 236.41 | 1,575.41 | 371.16 |

### step_11_15

| Engine | Pass / attempts | Pass rate | Duration median ms |
|---|---:|---:|---:|
| chromium | 10/10 | 100.00% | 2,403.52 |
| moli | 5/10 | 50.00% | 4,897.84 |

Matched all-pass attempts: 5

| Engine | Duration median ms | CPU median ms | Peak memory median MiB | RX median KiB | TX median KiB |
|---|---:|---:|---:|---:|---:|
| chromium | 2,453.44 | 1,332.66 | 302.36 | 649.03 | 55.13 |
| moli | 4,897.84 | 1,513.29 | 161.25 | 2,491.93 | 283.11 |

### step_16_20

| Engine | Pass / attempts | Pass rate | Duration median ms |
|---|---:|---:|---:|
| chromium | 5/5 | 100.00% | 5,075.62 |
| moli | 5/5 | 100.00% | 9,330.72 |

Matched all-pass attempts: 5

| Engine | Duration median ms | CPU median ms | Peak memory median MiB | RX median KiB | TX median KiB |
|---|---:|---:|---:|---:|---:|
| chromium | 5,075.62 | 2,076.80 | 462.00 | 4,478.16 | 379.97 |
| moli | 9,330.72 | 4,712.22 | 244.27 | 5,119.11 | 1,115.00 |

## Task-level results

| Task | Tier | Engine | Pass / attempts | Duration median ms | Representative error |
|---|---|---|---:|---:|---|
| Read the Example Domain heading | step_01_05 | chromium | 5/5 | 278.39 | — |
| Read the Example Domain heading | step_01_05 | moli | 5/5 | 785.53 | — |
| Read Hacker News top story | step_01_05 | chromium | 5/5 | 945.74 | — |
| Read Hacker News top story | step_01_05 | moli | 5/5 | 898.79 | — |
| Read the current PyPI requests version | step_01_05 | chromium | 5/5 | 904.39 | — |
| Read the current PyPI requests version | step_01_05 | moli | 5/5 | 1,622.72 | — |
| Read the Transformer paper abstract | step_01_05 | chromium | 5/5 | 1,175.48 | — |
| Read the Transformer paper abstract | step_01_05 | moli | 5/5 | 1,798.02 | — |
| Inspect and scroll the Python Wikipedia article | step_06_10 | chromium | 5/5 | 1,194.57 | — |
| Inspect and scroll the Python Wikipedia article | step_06_10 | moli | 5/5 | 5,559.77 | — |
| Open the fixed Moli GitHub repository and inspect its README | step_06_10 | chromium | 5/5 | 2,490.79 | — |
| Open the fixed Moli GitHub repository and inspect its README | step_06_10 | moli | 5/5 | 4,348.71 | — |
| Follow MDN Fetch API documentation into Using Fetch | step_06_10 | chromium | 5/5 | 1,314.59 | — |
| Follow MDN Fetch API documentation into Using Fetch | step_06_10 | moli | 5/5 | 2,169.14 | — |
| Navigate Python tutorial chapters and history | step_11_15 | chromium | 5/5 | 2,453.44 | — |
| Navigate Python tutorial chapters and history | step_11_15 | moli | 5/5 | 4,897.84 | — |
| Inspect RFC 9110 and navigate to a section | step_11_15 | chromium | 5/5 | 2,074.65 | — |
| Inspect RFC 9110 and navigate to a section | step_11_15 | moli | 0/5 | unavailable | TimeoutError: Locator.click: Timeout 10000ms exceeded.
Call log:
  - waiting for locator("a.section-name.selfRef[href='#name-introduction']").first
    - locator resolved to <a href="#name-introduction" class="section-name selfRef">Introduction</a>
  - attempting click action
    - waiting for element to be visible, enabled and stable
    - element is visible, enabled and stable
    - scrolling into view if needed
    - done scrolling
 |
| Collect Python facts across four public sources | step_16_20 | chromium | 5/5 | 5,075.62 | — |
| Collect Python facts across four public sources | step_16_20 | moli | 5/5 | 9,330.72 | — |

## Repeat drift check

| Engine | Paired tasks | Last / first baseline memory | Last / first duration |
|---|---:|---:|---:|
| chromium | 10 | 1.07× | 0.72× |
| moli | 10 | 2.21× | 1.03× |
## All-pass intersection resources

Matched attempts: **45**

| Engine | Duration median ms | CPU median ms | Peak memory median MiB | RX median KiB | TX median KiB | Output tokens median |
|---|---:|---:|---:|---:|---:|---:|
| chromium | 1,310.27 | 713.30 | 310.87 | 716.64 | 61.40 | 885.00 |
| moli | 2,212.84 | 937.37 | 176.42 | 1,184.62 | 283.11 | 885.00 |

### Moli / Chromium ratios on matched passing attempts

| Metric | Ratio | Interpretation |
|---|---:|---|
| Duration | 1.69× | Below 1× favors Moli |
| CPU | 1.31× | Below 1× favors Moli |
| Peak memory | 0.57× | Below 1× favors Moli |
| Network RX | 1.65× | Below 1× favors Moli |
| Network TX | 4.61× | Below 1× favors Moli |
| Tool-output tokens | 1.00× | Below 1× favors Moli |

## Initial public task suite

| Task | Steps | Tier |
|---|---:|---|
| Read the Example Domain heading | 2 | step_01_05 |
| Read Hacker News top story | 3 | step_01_05 |
| Read the current PyPI requests version | 3 | step_01_05 |
| Read the Transformer paper abstract | 3 | step_01_05 |
| Inspect and scroll the Python Wikipedia article | 6 | step_06_10 |
| Open the fixed Moli GitHub repository and inspect its README | 6 | step_06_10 |
| Follow MDN Fetch API documentation into Using Fetch | 8 | step_06_10 |
| Navigate Python tutorial chapters and history | 11 | step_11_15 |
| Inspect RFC 9110 and navigate to a section | 13 | step_11_15 |
| Collect Python facts across four public sources | 16 | step_16_20 |

## CubeSandbox decision

- CubeSandbox officially supports headless Chromium in a MicroVM over CDP and is a valid future Browser Use backend: https://docs.cubesandbox.com/guide/tutorials/examples.html
- Its native resource endpoint exposes host-sandbox and guest-workload CPU/current-memory scopes, but defaults to 5 s collection, retains only the latest sample, does not export an exact epoch-scoped memory peak, and lists no per-sandbox network byte counters: https://docs.cubesandbox.com/guide/resource-metrics.html
- Therefore this first local reference uses 100 ms Docker cgroup/network-namespace accounting. CubeSandbox should be added as a separate production-topology backend, not mixed into the same aggregate.

## Interpretation limits

- Docker is the reproducible accounting boundary, not a claim that CowAgent must deploy browsers in Docker.
- Public-site latency and network bytes retain Internet/CDN variance; balanced ordering and repeated medians reduce but do not eliminate it.
- Memory peak is sampled, so a spike shorter than the sampling interval can be missed.
- Docker cgroup PID values count Linux tasks/threads, not only OS processes.
- Chromium includes a tiny in-container TCP forwarder because Debian Chromium binds CDP to loopback; its resource cost is included and slightly biases Chromium upward.

## Environment

```json
{
  "cgroup_version": "2",
  "docker_architecture": "aarch64",
  "docker_cpus": 4,
  "docker_memory_bytes": 6197440512,
  "docker_os": "Ubuntu 24.04.4 LTS",
  "docker_server_version": "29.5.2",
  "images": {
    "chromium": {
      "id": "sha256:60bfe43d8e12576d1665d779b40720786e755739d1669f6129216d2e45ed7339",
      "tags": [
        "lex-browser-use-eval-chromium:local"
      ]
    },
    "moli": {
      "id": "sha256:bfc9c941e75a92c091e2e6052da2fbd895c25663e41e728b07fd0ba369c4bc2a",
      "tags": [
        "lex-browser-use-eval-moli:local"
      ]
    }
  },
  "runner_platform": "Linux-6.8.0-117-generic-aarch64-with-glibc2.39"
}
```
