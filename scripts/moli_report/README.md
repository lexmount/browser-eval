# Moli compatibility and resource comparison

Success rates use the same 372 historical Moli 0.1.1 failures. A case passes only when all three attempts pass. Moli 1.1.8 and the supplied patched Moli 1.1.9 binary are compared with layout fixed off and on. Official Moli 1.1.9 is evaluated with layout on. Moli 1.1.4 layout off uses a differential check of the sole candidate, with inherited failures explicitly identified.

Resources use a separate 557-task experiment, five calls per task. Each patched 1.1.9 layout configuration has one measured run (2,785 calls each); the official layout-on configuration has one measured run. The chart additionally uses the pre-frozen 1,045-call, 209-task population aligned with the historical resource comparison. Results from every task, including failures, remain in the declared population.

Historical Linux measurements report process-tree PSS; macOS measurements report process-tree RSS. These memory metrics are shown separately. Historical percentage changes use 1.1.4 layout off as baseline. The macOS comparison uses official 1.1.9 layout on as baseline when available. The patched debug binary and official release asset differ in build configuration as well as code.

Open index.html through a static HTTP server. Chinese and English routes share the same report model and evidence. Charts are rendered by the included Python script; RSS/PSS and CPU axes can be selected independently. Per-task results retain the exact program errors and sanitized grader, output, and protocol logs.
