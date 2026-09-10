---
name: benchmark-editorial
description: Write or revise standalone articles interpreting this project's benchmark evidence for official blogs and public accounts. Use for editorial argument, explanation and reading formats, not routine benchmark maintenance or release notes.
---

# Benchmark editorial

Produce a reader-facing article whose conclusions, examples and background remain understandable without opening the source report. The benchmark supplies evidence; the article explains what the results mean and why they matter.

Keep this skill and its dependencies in the repository. Read [editorial-standard.md](references/editorial-standard.md) before writing or revising, and [html-delivery.md](references/html-delivery.md) for HTML. Use the complete [Humanizer Chinese](../humanizer-chinese/SKILL.md) for Chinese prose and [Humanizer](../humanizer/SKILL.md) for English. Apply them in embedded/file mode: only finished prose enters the article.

## Choose the work being requested

For a new article, establish its audience, question and supported argument before choosing a structure. For a revision, read the entire current article and the still-applicable conversation requirements first. Preserve working explanations, evidence and format unless the feedback or a verified problem requires changing them. Local corrections do not authorize a new thesis or wholesale compression. A requested outline is an outline; a cancelled article is not an invitation to generate a replacement.

Treat feedback as editorial input, not publishable copy. Identify the reader problem behind it and fix that problem in the article. Keep revision plans, compliance reminders, reviewer discussion and prompt language out of headings, captions and prose. Legitimate methodological scope still belongs in the article, expressed once where it helps the reader interpret a result.

## Evidence and argument

Read the supplied reports and relevant reference articles, including their charts, cases and methodology. Track material that could not be inspected privately; do not claim full coverage from a landing page. Study how reference articles develop their reasoning as well as how they look. Their facts are not evidence for this benchmark.

For this repository, use `evaluation/current-report.json`, the normalized persisted results and the current scoring implementation. Read [benchmark-report](../benchmark-report/SKILL.md) when building or auditing them. Keep a private ledger for consequential claims: evidence, calculation, interpretation and limits. Do not rerun providers, alter scores or select better attempts to improve a narrative.

Choose an argument that the evidence supports, including exceptions that change its meaning. Comparative claims need nearby magnitudes, units and a defined comparison set, including when they appear in a heading or summary card. A correct ranking alone does not tell the reader the size of a difference. See the publication standard for interpreting distributions, task acceptance and quality metrics.

## Write and revise

Develop the reader's understanding through concrete results, necessary background and inspectable examples. Explain unfamiliar concepts through their purpose and operation; a sentence excluding them from the benchmark is not an explanation. Use charts and specimens to carry data so prose can explain the result instead of repeating it. There is no fixed chapter count, case count, headline formula or required hero image.

When revising, check the whole article for the same underlying problem, then make the smallest coherent repair. Review any removed material for lost explanations, evidence, limitations or transitions. Broader restructuring is justified when the argument or organization is defective, not simply because a paragraph received criticism. If a needed fact is absent, investigate it or narrow the claim; do not replace it with confidence or invented precision.

Run the language-appropriate Humanizer pass on headings, prose and captions. This is a prose edit, not a substitute for evidence or editorial judgment. Preserve technical meaning, natural language conventions and original measured text. Do not manufacture casual language or personal experience.

## Review and delivery

Use separate evidence and reader reviews for substantial articles. The evidence review traces claims, versions, denominators, routes and timing to sources. The reader review reads the actual artifact without the conversation, checking whether its argument and examples work for the intended audience. For revisions, also compare what was retained and removed. A style checklist or successful build is not reader approval. Fix findings and re-review the affected artifact. After structural changes, repeat the whole-article reader review; a previous sign-off does not establish that the new reading sequence works. Keep review records outside the public bundle.

Use the existing renderer and authored template for HTML, not a parallel implementation. Inspect the actual delivered page at desktop and phone sizes, including all interactive states needed to inspect evidence. Keep localized facts and original evidence aligned. Run the repository validation gates when rebuilding a report.

Publishing and benchmark changes require their own authorization. When publication is authorized, use the target and expected version bound by `evaluation/current-report.json`. Otherwise deliver the requested local artifact and any material unresolved limitation. Do not present a draft as approved or promise that a skill makes editorial failures impossible.
