# Browser Use Evaluation — Latest

Run: `1721a7fae735`

## Methodology

- Scope: one fixed-limit browser container per engine; controller excluded.
- Network isolation: one dedicated bridge with no peer containers per engine.
- Browser processes stay warm; cold start and reset work are excluded.
- Browser process and context stay warm. Before each task, the harness clears every top-level navigation origin plus cookies, permissions, cache and history, then replaces only the renderer page outside the timer.
- Repeats: 5; memory/PID sample interval: 100 ms.
- Post-reset quiescence: 1500 ms, excluded from task duration.
- Functional scores use every attempt; resource comparisons use only matched attempts passed by every engine.

## Observed findings and decision

- Chromium passed 50/50 attempts; Moli passed 44/50. Moli failures were confined to Read the current PyPI requests version, Inspect RFC 9110 and navigate to a section.
- Across 44 matched passing attempts, Moli / Chromium medians were 2.11× duration, 1.32× CPU, 0.57× peak memory, 1.66× RX, and 4.37× TX.
- Matched tool-output tokens were 1.00×; this browser-only suite shows no token-cost advantage.
- For Moli, last/first baseline memory was 1.94× while last/first duration was 1.10×. The five repeats did not show systematic runtime slowdown, but the warm process retained allocations and did not return to its first-run footprint after state cleanup.
- Decision for this suite: keep Chromium as the default provider. Offer Moli as an opt-in memory-oriented provider after workload-specific validation; these results support lower peak memory, but do not support broad claims of higher speed, lower CPU, lower network use, or lower token cost.

## Functional results

| Engine | Pass / attempts | Pass rate | Duration median ms | Output tokens median |
|---|---:|---:|---:|---:|
| chromium | 50/50 | 100.00% | 1,288.03 | 1,277.00 |
| moli | 44/50 | 88.00% | 2,472.34 | 885.00 |

## Results by task complexity

### step_01_05

| Engine | Pass / attempts | Pass rate | Duration median ms |
|---|---:|---:|---:|
| chromium | 20/20 | 100.00% | 950.14 |
| moli | 19/20 | 95.00% | 1,044.57 |

Matched all-pass attempts: 19

| Engine | Duration median ms | CPU median ms | Peak memory median MiB | RX median KiB | TX median KiB |
|---|---:|---:|---:|---:|---:|
| chromium | 939.66 | 532.05 | 280.93 | 189.46 | 17.50 |
| moli | 1,044.57 | 331.29 | 148.89 | 697.14 | 36.42 |

### step_06_10

| Engine | Pass / attempts | Pass rate | Duration median ms |
|---|---:|---:|---:|
| chromium | 15/15 | 100.00% | 1,299.46 |
| moli | 15/15 | 100.00% | 4,461.72 |

Matched all-pass attempts: 15

| Engine | Duration median ms | CPU median ms | Peak memory median MiB | RX median KiB | TX median KiB |
|---|---:|---:|---:|---:|---:|
| chromium | 1,299.46 | 852.25 | 334.99 | 801.21 | 125.89 |
| moli | 4,461.72 | 2,814.78 | 227.73 | 1,574.56 | 360.33 |

### step_11_15

| Engine | Pass / attempts | Pass rate | Duration median ms |
|---|---:|---:|---:|
| chromium | 10/10 | 100.00% | 2,392.87 |
| moli | 5/10 | 50.00% | 4,894.77 |

Matched all-pass attempts: 5

| Engine | Duration median ms | CPU median ms | Peak memory median MiB | RX median KiB | TX median KiB |
|---|---:|---:|---:|---:|---:|
| chromium | 2,805.84 | 1,448.65 | 302.04 | 648.91 | 54.35 |
| moli | 4,894.77 | 1,497.92 | 159.47 | 2,490.05 | 295.90 |

### step_16_20

| Engine | Pass / attempts | Pass rate | Duration median ms |
|---|---:|---:|---:|
| chromium | 5/5 | 100.00% | 6,443.80 |
| moli | 5/5 | 100.00% | 9,251.06 |

Matched all-pass attempts: 5

| Engine | Duration median ms | CPU median ms | Peak memory median MiB | RX median KiB | TX median KiB |
|---|---:|---:|---:|---:|---:|
| chromium | 6,443.80 | 2,355.65 | 512.77 | 4,483.45 | 398.07 |
| moli | 9,251.06 | 4,714.43 | 243.92 | 5,139.78 | 1,140.71 |

## Task-level results

| Task | Tier | Engine | Pass / attempts | Duration median ms | Representative error |
|---|---|---|---:|---:|---|
| Read the Example Domain heading | step_01_05 | chromium | 5/5 | 316.84 | — |
| Read the Example Domain heading | step_01_05 | moli | 5/5 | 714.82 | — |
| Read Hacker News top story | step_01_05 | chromium | 5/5 | 930.41 | — |
| Read Hacker News top story | step_01_05 | moli | 5/5 | 1,025.60 | — |
| Read the current PyPI requests version | step_01_05 | chromium | 5/5 | 985.47 | — |
| Read the current PyPI requests version | step_01_05 | moli | 4/5 | 1,494.25 | TimeoutError: Locator.wait_for: Timeout 30000ms exceeded.
Call log:
  - waiting for locator("h1").first to be visible
    60 × locator resolved to hidden <h1 class="project-header__name">requests 2.34.2</h1>
 |
| Read the Transformer paper abstract | step_01_05 | chromium | 5/5 | 1,149.14 | — |
| Read the Transformer paper abstract | step_01_05 | moli | 5/5 | 1,792.32 | — |
| Inspect and scroll the Python Wikipedia article | step_06_10 | chromium | 5/5 | 1,122.93 | — |
| Inspect and scroll the Python Wikipedia article | step_06_10 | moli | 5/5 | 5,584.75 | — |
| Open the fixed Moli GitHub repository and inspect its README | step_06_10 | chromium | 5/5 | 3,371.47 | — |
| Open the fixed Moli GitHub repository and inspect its README | step_06_10 | moli | 5/5 | 4,523.49 | — |
| Follow MDN Fetch API documentation into Using Fetch | step_06_10 | chromium | 5/5 | 1,276.61 | — |
| Follow MDN Fetch API documentation into Using Fetch | step_06_10 | moli | 5/5 | 2,413.69 | — |
| Navigate Python tutorial chapters and history | step_11_15 | chromium | 5/5 | 2,805.84 | — |
| Navigate Python tutorial chapters and history | step_11_15 | moli | 5/5 | 4,894.77 | — |
| Inspect RFC 9110 and navigate to a section | step_11_15 | chromium | 5/5 | 1,937.14 | — |
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
| Collect Python facts across four public sources | step_16_20 | chromium | 5/5 | 6,443.80 | — |
| Collect Python facts across four public sources | step_16_20 | moli | 5/5 | 9,251.06 | — |

## Repeat drift check

| Engine | Paired tasks | Last / first baseline memory | Last / first duration |
|---|---:|---:|---:|
| chromium | 10 | 1.06× | 0.78× |
| moli | 10 | 1.94× | 1.10× |
## All-pass intersection resources

Matched attempts: **44**

| Engine | Duration median ms | CPU median ms | Peak memory median MiB | RX median KiB | TX median KiB | Output tokens median |
|---|---:|---:|---:|---:|---:|---:|
| chromium | 1,170.95 | 730.15 | 306.85 | 715.57 | 60.87 | 885.00 |
| moli | 2,472.34 | 961.57 | 173.38 | 1,184.54 | 265.74 | 885.00 |

### Moli / Chromium ratios on matched passing attempts

| Metric | Ratio | Interpretation |
|---|---:|---|
| Duration | 2.11× | Below 1× favors Moli |
| CPU | 1.32× | Below 1× favors Moli |
| Peak memory | 0.57× | Below 1× favors Moli |
| Network RX | 1.66× | Below 1× favors Moli |
| Network TX | 4.37× | Below 1× favors Moli |
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
- Chromium's nested setuid browser sandbox is unavailable in this non-privileged container and is disabled; the container boundary is security-relevant.
- Chromium includes a tiny in-container TCP forwarder because Debian Chromium binds CDP to loopback; its resource cost is included and slightly biases Chromium upward.

## Environment

```json
{
  "browser_versions": {
    "chromium": "Chromium 151.0.7922.173 built on Debian GNU/Linux 12 (bookworm)",
    "moli": "moli 1.0.6"
  },
  "cgroup_version": "2",
  "docker_architecture": "aarch64",
  "docker_cpus": 4,
  "docker_memory_bytes": 6197440512,
  "docker_os": "Ubuntu 24.04.4 LTS",
  "docker_server_version": "29.5.2",
  "images": {
    "chromium": {
      "id": "sha256:53fdd905206d807aecb82dbf5c86682622c0de16e2b1642305b16eb532c77a34",
      "tags": [
        "lex-browser-use-eval-chromium:local"
      ]
    },
    "moli": {
      "id": "sha256:1ef56ea579ecba1f561f5ead61d07d21a4c68124e64647d538457a97f4531733",
      "tags": [
        "lex-browser-use-eval-moli:local"
      ]
    }
  },
  "runner_platform": "Linux-6.8.0-117-generic-aarch64-with-glibc2.39"
}
```
