# WebArena-Verified full replay ownership

This directory owns the compact, versioned inputs and release bindings for the
812-task Browser Trajectory Replay split.

- `dataset.json`, `scope.json`, `environment.json`, and the action override
  files define the reusable benchmark inputs.
- `current-report.json` binds the Moli-qualified canonical catalog. It is the
  acceptance authority used before comparing browsers.
- `current-comparison.json` binds the current multi-browser results, reviewed
  failures, report package, and Artifact Site version by relative path and
  SHA-256. The measured files remain under `BROWSER_EVAL_DATA_HOME` and are not
  committed to Git.
- `../webarena-verified/current-run.json` remains the independent 90-task split.

The evaluator and canonical WebArena task fixes live in the pinned
`lexmount/webarena-verified` fork. Replay execution, browser adapters, evidence
aggregation, review, report generation, and publication live in this
`browser-eval` repository.
