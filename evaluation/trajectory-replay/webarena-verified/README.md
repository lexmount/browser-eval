# WebArena-Verified verified trajectories

`trajectories.json` is the compact, versioned trajectory portion of the benchmark dataset.
Each trajectory is one verified feasible BrowserTool path, not the task's only correct path.

- Browser Trajectory Replay consumes `tool_calls` as fixed inputs and uses
  `evaluation_input` only when invoking the official evaluator.
- Browser Use may use the inventory as hidden solvability and diagnostic evidence. The Agent
  must not receive the trajectory or evaluator input, and its score remains determined by the
  official task outcome contract.
- A Browser Use success is only a candidate. Promotion requires the same candidate to replay
  without a model call and pass both execution and official evaluation on every required browser.
- Full HAR, screenshots, traces, resource samples and replay receipts remain outside Git under
  `BROWSER_EVAL_DATA_HOME`; this directory stores deterministic actions and provenance hashes.
- More than one trajectory may be retained for a task when paths are materially different and
  independently satisfy the same promotion gate.

Regenerate the dataset with `scripts/promote_webarena_trajectories.py`; never edit a verdict or
verification claim by hand.
