# Moli compatibility and resource comparison

The report compares seven fixed configurations: Moli 1.1.4 with layout off, Moli 1.1.8 with layout off and on, Moli 1.1.9 with layout off and on, and patched Moli 1.1.9 with layout off and on. Success rates use the same 372 cases failed by Moli 0.1.1. A case passes only when all three attempts pass. Moli 1.1.4 uses a direct check of the sole candidate and 371 inherited failures, explicitly identified in the report.

Resources use a separate 557-task experiment, five calls per task. Each configuration has 2,785 measured calls, paired with the official profiler-off calibration. The chart and overview table use the 1,045-call, 209-task population frozen before execution; the detailed tables also report all 557 tasks. Passing and failing calls remain in both declared populations.

All seven resource configurations run in Docker Linux on the Mac host, using Linux ARM64 release binaries and process-tree PSS and CPU measurements. Resource changes use Moli 1.1.4 with layout off as the baseline. Success-rate changes use percentage points. Functional results retain their own binary identities; patched functional outcomes use commit 87c87e95, while patched resource measurements use the f315ff26 release build. The report does not treat these resource runs as new accuracy measurements.

Open index.html through a static HTTP server. Chinese and English routes share the same report model and evidence. Charts are rendered by the included Python script; memory and CPU panels can be selected independently. Per-task results retain exact program errors and sanitized grader, output, and protocol logs.

New benchmark runs select a fixed `--moli-layout off` (default) or `--moli-layout on` for every attempt. Failed cases keep the selected layout and repetition count. The harness does not schedule layout recovery or replace results from a second batch.
