# WebArena-Verified verified trajectories

`trajectories.json` is the compact, versioned trajectory portion of the benchmark dataset.
Each trajectory is one verified feasible BrowserTool path, not the task's only correct path.

`current-run.json` is the run authority for the next formal replay. It freezes the same 90-task
selection used by the current Browser Use evaluation, the three registered browser methods, resource
limits, and the isolation schedule. The private ready-to-run seed manifest must contain exactly one
canonical candidate for each of those 90 tasks. The current inventory is assembled from 60 clean
autonomous successes and 30 reviewed repairs; successful executions from different methods are
candidate evidence, not additional tasks.

- Browser Trajectory Replay consumes `tool_calls` as fixed inputs and uses
  `evaluation_input` only when invoking the official evaluator.
- Autonomous Browser Use snapshots assign run-local numeric element refs. Seed generation compiles
  those refs from the bound snapshot text into fixed selectors; a canonical trajectory containing
  a raw ref is invalid. The compiled call records that it came from a snapshot ref and preserves
  BrowserTool's original DOM click, focused keyboard fill, or change-event select semantics. It
  must not silently switch to Playwright's selector fallback, whose viewport, overlay and navigation
  waiting rules would change the measured trajectory.
- Browser Use may use the inventory as hidden solvability and diagnostic evidence. The Agent
  must not receive the trajectory or evaluator input, and its score remains determined by the
  official task outcome contract.
- A Browser Use success is only a candidate. A reviewed expert repair may also provide a candidate
  when every autonomous run failed or a successful trace contains failed actions. Expert candidates
  retain their repair basis and repair-spec hash and may not contain ephemeral accessibility refs.
- Before comparison, promotion requires each canonical candidate to replay without a model call and
  pass both execution and evaluation on the frozen reference browser. The measured Chromium and
  Moli runs consume that already-qualified trajectory set; their failures remain benchmark outcomes
  and never remove a task from the comparison. Where `task-corrections.json` records a proven
  upstream contract defect, the qualification proof binds its correction ID, catalog hash and
  corrected task hash; historical Browser Use scores remain unchanged.
- Full HAR, screenshots, traces, resource samples and replay receipts remain outside Git under
  `BROWSER_EVAL_DATA_HOME`; this directory stores deterministic actions and provenance hashes.
- The current self-hosted WebArena deployment has one fixed-port site stack, so a formal suite owns
  the physical environment exclusively. It runs every task for one browser before starting the next
  browser, restores the site stack once before each browser method, and tears down the browser and site
  containers between methods. It does not reset the site between tasks. Parallel methods are valid only after separate site stacks also have
  separate containers, data stores, ports, gateways, and lock namespaces; multiple browser
  containers against one site stack are not independent environments.
- Formal execution requires clean browser-eval and CowAgent checkouts and binds their commits, the
  resolved browser and site image identities, and the frozen CPU, memory, and sampling limits into
  the resumable receipt. A resume with different runtime identity is rejected. Browser storage,
  cache, cookies, permissions, service workers, and IndexedDB are cleared before every task; failed
  teardown aborts the suite. Any pre-action context recovery writes a new physical-evidence
  directory instead of overwriting the first one.
- More than one trajectory may be retained for a task when paths are materially different and
  independently satisfy the same promotion gate.

Run the frozen suite with `scripts/run_webarena_trajectory_replay.py`. Diagnostic subsets are
explicitly non-promotable. `--qualification-engine chromium` runs the complete frozen task set on
the reference browser and is the only scope that may promote the standard trajectory dataset.
After all 90 qualify, promote that receipt with `scripts/promote_webarena_trajectories.py`, then run
the measured method matrix with `--qualified-dataset` and no scope option. The runner rejects formal
execution when that dataset is missing, incomplete, or differs from the frozen profile, seeds, or
canonical actions. Never edit a verdict or verification claim by hand.

The report presents one same-protocol, three-browser comparison. Diagnostic-only cohorts cannot
enter its score-bearing matrix or add a separate result appendix.
