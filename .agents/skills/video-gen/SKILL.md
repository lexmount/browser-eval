---
name: video-gen
description: Design and produce explanatory, product, strategy, demo, or promotional videos from source material, with a coherent story, purposeful visuals, narration, and reviewed outputs. Also use for script-only or storyboard review; do not turn a script review into automatic rendering.
---

# Video Gen

Create a video for its audience, rather than convert every source section into a scene. A research report, slide deck, or product document is evidence and material; its structure need not become the film's structure.

## Revise cumulatively

Before revising an existing film, read the earlier brief, feedback and shipped artifacts. Maintain a compact content contract outside the deliverable: the audience's intended takeaway, required relationships and evidence, retained assets, and accepted features. New feedback amends this contract; it does not replace it. Record why a substantive element is removed or changed. A renderer migration changes implementation, not the editorial baseline. Preserve successful work unless the requested change or evidence requires revising it.

For substantial narrated revisions, read [references/attention-and-continuity.md](references/attention-and-continuity.md). It defines semantic timing, preservation checks and playback review. A still-image audit cannot close an audiovisual defect.

## Decide what the viewer should retain

Infer the audience, viewing context, purpose and scope from the request. Ask only when an unresolved choice materially changes the result. State the core idea plainly and decide what viewers should understand or do after watching. Choose supporting information that establishes that idea; keep detailed references accessible outside the film when they do not belong in the viewing experience.

Background goes where it helps orientation. A company explainer usually needs an early answer to who the company is and what business it is in. A tutorial or short announcement may need a different opening. Do not impose a universal hook, runtime, chapter count or total–parts–total formula. For a conceptual or strategy film, an overview and a closing return to the same model often make the argument easier to retain.

## Build a continuous argument and visual model

Read the source argument and its substantive figures before compressing it. Abstraction should expose the relationships that explain the subject, not erase distinctive information into a generic workflow. For a product ecosystem, distinguish resources, capabilities, assets, service boundaries and consumers where useful; explain dependency, composition and reuse rather than assigning every product to one box. Do not impose these categories on unrelated genres.

Write the story before choosing tools or making assets. Give each transition a reason: a question just raised, a cause and consequence, a comparison, or an application of the same model. Transitions must exist in the spoken text and visual change; a `transition` field alone does not create continuity. Read adjacent paragraphs together without headings to check this.

Choose the visual language that carries the information:

- Relationship or architecture: diagram, with correct direction, optional paths and boundaries.
- Evolution: timeline showing what changed, not just a list of dates.
- Comparison: a common set of dimensions, with examples mapped to them.
- Product action or factual proof: actual UI, recorded operation, or measured evidence.
- Concept or mood: generated imagery when it clarifies something the other forms cannot show.

A concept film can keep one model visible, reveal parts, place customers or products into it, and return to the whole. Avoid defaulting every scene to new text cards. Do not narrate every diagram label; let the image carry part of the explanation. Keep distinct products distinct, and never turn a value stack into a mandatory purchase sequence or a chronological launch claim.

## Script and review scope

Prepare only the artifacts the current task needs. A reviewable script normally includes actual narration, key diagrams or frames, visual progression, transitions, evidence and estimated duration. Estimates are provisional until speech and reading time are measured.

If the user requests script alignment before production, deliver the reviewed script and stop before new TTS, rendering or publication. When the user has delegated complete production, proceed through ordinary reviews autonomously; do not invent repeated approval gates.

Before production, use [references/editorial-review.md](references/editorial-review.md). Evaluate narrative and viewer understanding separately from file, timing and playback checks. A playable MP4 or readable contact sheet is not proof that the film teaches its central idea. Review actual outcomes, address demonstrated defects, and then recheck the affected passage or scene.

## Production choices

Keep each film's narration, customer examples, scene layouts and production project with its external deliverable sources. This skill owns reusable guidance and the optional timeline guard described in [attention and continuity](references/attention-and-continuity.md). A film-specific renderer is a production asset; moving it into a skill does not make it reusable. The guard's synthetic behavior tests live in the repository's `tests/` and run through the existing Python test suite.

Use the user's chosen tools when suitable. Otherwise select the simplest sufficient pipeline for the material. Exact diagrams and data benefit from editable SVG or code-based animation; raster generation and video models suit other kinds of visual material. Do not require image generation, music, multiple variants, a particular renderer, or a full orchestration framework just because it is available.

When using a framework, distinguish inspecting its documentation, borrowing its workflow, and actually executing through it. Describe only the integration that exists.

Preserve source facts, measured outputs, dates, permissions, attribution and uncertainty. Keep evidence captions concise; make full sources accessible without turning narration into footnote reading. Use natural spoken language, and a relevant language-editing skill when available. Check names, years, abbreviations and segment transitions for pronunciation and continuity.

Choose the audio unit for vocal continuity, not the visual page count. Continuous or chapter-length synthesis may be split locally at verified silence; reusable segments are also suitable if joins sound natural. Speech completion is not the deadline for changing the picture: allow orientation before new material and comprehension time after it. Align spoken referents to named visual objects and relationships, not merely to slide boundaries. Captions should use the reviewed text; recognition output can assist timing but must not silently replace names or numbers. Do not send sensitive source material to a third-party service without authorization covering that content and destination.

Motion should reveal relationships, changes or relevant actions. Fixed visual roles and stable positions can aid comprehension. A simple scene does not need decorative motion to appear active.

## Review the deliverable

For a completed film, inspect the actual beginning, ending, important transitions, diagrams, dense scenes, proof and changed scenes. Check voice continuity and comprehension as well as text clipping, subtitles, audio level, silence, duration and streams. Test the intended playback and delivery controls. Use independent review when requested or when it adds meaningful confidence and delegation is authorized.

Review the finished timeline in order, at playback speed, with speech and visual focus together. For each defect, fix the cause and replay the affected interval with its preceding and following context. Regression-check retained content as well as new changes. State precisely which checks actually ran; neither a successful export nor an unexecuted checklist is evidence of comprehension. If the environment cannot present synchronized sound and pictures to the reviewer, report that review limit rather than claiming to have watched and listened.

Fix a storytelling problem in the script or visual model before adjusting fonts or render settings. Keep technical checks proportional to the artifact. Use the relevant publisher only when publication is in scope, preserve existing access settings, and report the actual deliverable and access state.
