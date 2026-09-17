# Current-retrieval contract candidates

This directory contains bounded, non-score-bearing current-source observation candidates for
21 of the 37 WebArena-Verified retrieval tasks. `recompiled-seed-bindings.json` freezes each
task's canonical action count and digest from the recompiled 90-task seed. A candidate can
extend that sequence only by preserving every canonical action exactly and appending declared
read-only observations; matching a final answer is not evidence that the action boundary holds.
`qualification-matrix.json` separately records prefix compatibility, contract validity,
current observation acceptance, diagnostic official evaluation, and remaining blockers.

These definitions are **not** bound by `current-run.json`, are not a qualified 90-task dataset,
and do not revise the published replay scores. A diagnostic score of 1 is not a new formal
benchmark result. Task 108 has current-source acceptance without an official evaluator score.
The additional nine-task batch is blocked: 50, 94, 188, and 231 replace frozen actions; 198
and 320 preserve the prefix but their offline observation scope remains unqualified; 77, 183,
and 386 lack an independent current-page observer. Tasks 243 and 313 remain blocked as well.
No blocked task has a promoted candidate definition here.
