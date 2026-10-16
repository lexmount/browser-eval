# Narrated explanation: attention, continuity and regression

Read for conceptual films, diagram-heavy explanations and revisions involving pacing or synchronization. These are decision rules, not a fixed scene template or universal duration quota.

## Preserve meaning across revisions

Keep a small content contract beside production sources, never inside the public report. Map required ideas, facts, relationships and useful source figures to their current locations. Include earlier accepted functions such as narration, captions, chapter navigation, overview, conclusion and original-source access. A change request is a delta against this baseline. When merging or removing material, preserve its distinct meaning elsewhere or document the reason. Compare relationships as well as labels: retaining the word “architecture” while removing dependencies is a regression.

Classify the defect before editing: factual, editorial, attention/timing, visual layout, audio, implementation or delivery. Change the responsible layer first. Do not rewrite sound arguments to cure a renderer defect, or use a new renderer as evidence that narration is understandable. User examples explain an intended effect; they are not automatically titles, spoken lines or new sections.

## One attention timeline

Give each spoken clause a semantic target: an object, a relationship, an example or an explicit whole-view summary. The same target ID drives labels, reveal state, emphasis and any connecting path. Use speech timestamps plus an intentional lead/hold interval. A new layout must settle before its detailed narration begins. A speaker switching from one object to another requires a corresponding visible focus change, even if the slide stays unchanged.

Distinguish three operations:

- Reveal: add the next fact while keeping the surrounding model stable.
- Emphasize: point to the existing fact or edge being discussed; do not flash every object or darken context until it is unreadable.
- Reframe: change the viewer's mental workspace. Use only when the question changes, and budget time to find the new layout.

For exact diagrams, attach emphasis to the same geometry as the object. For raster figures, store regions in normalized source coordinates and apply the identical scale/offset/crop transform to the image and region. Inspect the resulting rectangle, not just its coordinates. Reuse a dense source figure as a map with guided inspection or detail views; merely showing it briefly does not preserve its explanatory role.

## Budget thinking time

Pacing depends on concurrent demands: unfamiliar terms, new spatial structure, reading, comparison and speech. Do not calculate it from words per minute alone. Avoid asking viewers to read a new paragraph while hearing a different paragraph. Use concise visual labels and concrete outputs; leave the full prose in the transcript.

Allow an orientation interval on a substantially new layout, pauses at meaningful boundaries, and a stable hold after a comparison or conclusion. Derive durations from the actual audio and viewing task. Slowing the entire soundtrack or adding arbitrary silence is insufficient if the visual target still changes mid-thought. Preserve the overall model between details and return to it with the relationships now explained.

Orientation also needs a spoken purpose. At a change of subject, connect the preceding idea to the question the new view answers before naming its first item. A visible heading cannot carry this job alone. Read adjacent scenes without their headings: the listener should understand why the topic changed and what to follow next. Keep a natural existing connection; do not mechanically announce every slide or read every title. Let a brief opening sentence accompany the whole view before focusing on details. Verify that the renderer's whole-view state is genuinely neutral and does not retain an unrelated detail label.

Count a scene's closing hold, the following lead and the natural recording gap together. Individually reasonable pauses can combine into an unexplained break. Preserve time to absorb the preceding idea, then use the spoken connection to orient the next view; do not fill all silence or remove comprehension holds. Review transitions as paired outgoing and incoming scenes, including their actual audio gaps, rather than judging each page in isolation.

## Review observable behavior

Technical checks can reject missing assets, invalid timestamps, nonexistent target IDs, frame overflow, truncated speech or cuts inside utterances. They cannot certify clarity. Test these general failure modes with deliberately broken fixtures, not exact wording snapshots.

A review command must stop production when it detects a defect. Save diagnostics, release browser resources, and return a failing exit status for runtime errors, overflow, the wrong scene or missing emphasis. Compare requested semantic targets with rendered objects; declare neutral views and grouped-target aliases explicitly. Exercise each failure separately through the command itself so one detected defect cannot conceal a missing check for another.

Inspect moving sequences around every reframe and changed focus, with captions and narration together. During the first pass, ask at each beat: what am I being told, where do I look, and has the previous idea had time to land? During the second pass, check the whole argument and the ending. Record time-coded defects and fixes privately. Replay corrected intervals plus adjacent context; then compare the final content contract to avoid losing an earlier improvement.

Playback/seek tests must actually operate the controls and observe resulting media state. A failed interaction or console error must be investigated; reading the handler source is not an executed test. A metadata or contact-sheet check must not be described as a full viewing review.

## Provenance and selective reuse

Recovered source: [Lexmount creating-videos](https://github.com/lexmount/lex-agent-plugins/tree/main/skills/creating-videos), including `references/playbook.md` and `references/acceptance-checklist.md`, retrieved 2026-10-14. Its useful principles include audience calibration, shared comparison dimensions, actual audio durations, narration-linked in-scene animation, deliberate closing and rendered-output review.

Do not inherit unconditional scene-by-scene TTS, generated art or multiple music variants. Those are production choices. Voice continuity, information-bearing visuals and comprehension are the invariant outcomes. Remotion supplies a deterministic frame timeline; the author still has to specify meaningful targets and reading time.

## Minimal executable guard

Use `scripts/validate_timeline.py TIMELINE --contract CONTENT_CONTRACT --asset-root ROOT` when the production uses the documented simple schema. Adapt existing validation when a different schema already exists; do not introduce a second timeline. Supply the scene's actual allowed target IDs when possible, and inspect the rendered active objects as well as the metadata. The guard rejects invalid speech/caption boundaries and missing retained material. It deliberately does not issue a "clarity passed" verdict.

Keep failure scenarios beside the project: a removed retained diagram, a renamed visual target, a scene cut before the utterance ends, a caption on the wrong scene, and an intentional comprehension hold that must remain valid. Test the invariant and observable behavior, not a frozen count of slides or exact title wording. A revision can change layout and scene count while preserving meaning.

## Voice changes and purposeful pauses

Diagnose vocal delivery separately from reading time. A flat or tired voice may come from the chosen speaker, missing style parameters, compounded speed reduction or sentence segmentation. Verify the actual request payload before changing services. Audition a representative passage containing a question, a comparison, names and a conclusion; an API success or pitch statistic does not establish naturalness. Prefer continuous paragraphs or chapters where continuity matters, and align new word timestamps after every voice regeneration. Do not reuse the old time axis or apply another global slowdown to compensate for crowded visuals.

Thinking time must still tell the viewer what is happening. Retain the current model, restate one takeaway visually and guide attention across already-explained elements. A subtle progress cue or slow emphasis can distinguish an intentional recap from stalled playback. Do not fill every silence with music, speech or decorative motion: those can remove the time needed to think. Check the exported pause interval at more than one instant; sparse rendering must include every changing frame.

A strategic ending should apply the explanation to the audience's real situation. Show existing capabilities, proposed connections and possible future offerings with distinct status. Give each suggested next step a concrete dependency and a reason to proceed. Preserve the original source model as the closing reference, but do not let repeating it replace the audience-specific synthesis.

## Timing sources and attention load

Provider timestamps can be internally consistent yet lag the audible words, especially across languages. Validate actual speech onset, not merely cue ordering. If a timestamp source drifts, repair the common alignment used by captions, focus and cuts; fixing cuts alone leaves the viewer looking at the wrong object. Independent recognition may supply timing, with normalization for script variants and equivalent number forms, but must never rewrite reviewed wording or silently accept missing clauses. Keep audio hashes with timing results so regenerated speech cannot use stale cues.

Preserve quantities before removing punctuation: decimal points, signs and units can change the claim. Check omitted and inserted words in every language, including abbreviations inside product names. Similarity percentages cannot establish word completeness. Keep accepted recognition equivalences explicit and test changed quantities and missing words in both directions alongside equivalent written and spoken forms.

Review referents within sentences. A sentence moving from a left branch to a right branch needs two corresponding focus events. A rapid list of indicators is not six useful animation beats: group related questions or accumulate emphasis while preserving context. During a comprehension pause, keep one summary view and a subtle progress cue. Do not turn the pause into a fast tour of every object. An unselected relationship should remain legible where removing it would make nodes appear disconnected.

When a closing recommendation gives decision criteria, show the same criteria in the same order rather than a broader slogan. Test these defects with general scenarios (late time source, missing word, mismatched referent, fast recap switching), then inspect the changed export with adjacent context. Passing geometry checks alone does not close a synchronization or comprehension issue.
