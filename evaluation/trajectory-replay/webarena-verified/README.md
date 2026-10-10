# WebArena-Verified verified trajectories

`current-run.json.dataset.path` and its SHA-256 identify the authoritative trajectory
input for a formal build; currently this is `trajectories-corrected.json`.
`trajectories.json` is the retained predecessor used by qualification and correction
provenance. It is not a second current dataset. Each trajectory is one verified feasible
BrowserTool path, not the task's only correct path.

WebArena task definitions and shared evaluator fixes belong in the
`lexmount/webarena-verified` fork. BrowserTool actions, selector compilation, local
qualification and method comparisons belong here: the upstream task dataset does not
define those browser-tool trajectories. Historical correction catalogs in `revisions/`
are immutable inputs bound by retained cohorts, not copies of report output; removing
them must not make historical contracts impossible to resolve.

Authored report content and illustrations live outside Git. The report command takes
`--content` (default `$BROWSER_EVAL_DATA_HOME/report-assets/trajectory-replay`, with the
data home defaulting to `~/.browser-eval`). `research/trajectory-replay-content.json`
binds every consumed file. The prior Git revision retains migration recovery copies;
published report versions and physical execution evidence remain unchanged.

`current-run.json` is the run authority for the next formal replay. It freezes the same 90-task
selection used by the current Browser Use evaluation, the registered browser methods, resource
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
  containers between methods. It does not reset the site between tasks. Parallel methods are valid
  only after separate site stacks also have separate containers, data stores, ports, gateways, and
  lock namespaces; multiple browser
  containers against one site stack are not independent environments.
- Formal execution binds each method to frozen actions, a browser-binary hash, the site map, and a
  method-scoped site reset receipt. A fresh BrowserTool context is created for each task; browser
  storage does not carry between tasks, while site data is not reset between tasks. Every physical
  execution and its evidence is retained separately.
- More than one trajectory may be retained for a task when paths are materially different and
  independently satisfy the same promotion gate.

Freeze each method's exact 90-task inputs with `scripts/freeze_webarena_three_method_inputs.py`,
run each complete method cohort with `scripts/run_remote_three_method_cohort.py`, and bind the
certified cohorts with `scripts/aggregate_webarena_three_method_cohorts.py`. Each method
receives one site reset before its first task. The runner rejects a changed binary, task set,
action sequence, or reset policy. Diagnostic subsets never enter the formal matrix. Never edit a
verdict or verification claim by hand.

Use the same selection file at every stage; the runner accepts it through `--selection`.
Freezing resolves relative source revision paths against `--reference-root` (the repository
root by default), independently of the current working directory. Static inputs must remain
unchanged through preflight, site reset and execution. A complete cohort can be certified
with failed tasks; certification establishes protocol completeness, not a passing score.

The report presents every registered method with its recorded protocol. Diagnostic-only cohorts cannot
enter its score-bearing matrix or add a separate result appendix.

Adding a browser version appends a method and its complete cohort; existing results remain
unchanged. Historical failure diagnostics may retain their original cohort binding through
`inherited_result_cohorts` in the review record. Each inherited cohort must be hash-bound, use
the same dataset, and have every logical result preserved exactly in the expanded matrix.
When appended cohorts use a corrected evaluator, declare the exact method-specific correction ID
and corrected task hash under `acceptance_revisions`. The task page retains both acceptance
versions, and measurement notes identify the affected task IDs. Identical frozen trajectory
hashes do not prove identical evaluator revisions. Historical verdicts are not rewritten.
