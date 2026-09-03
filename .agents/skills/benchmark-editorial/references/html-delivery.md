# HTML article delivery

The current dashboard is a report application. The reading page and standalone article are two presentations of one authored body, not competing report versions.

## Layout and interaction

- Prefer a restrained editorial layout: readable line length, clear headline/deck, generous paragraph leading and a small optional contents list. Avoid converting every paragraph into a card.
- The narrative and all essential conclusions must be visible without JavaScript, expanding accordions, selecting tabs or hovering. Expandable detail may contain derivations or extra evidence, never the main argument.
- Use real HTML tables and inline SVG/HTML figures with exact text labels. Keep charts local; no CDN chart library or screenshot of a data table. Provide figure captions explaining the comparison and its scope.
- Collapse the article to one column on small screens. Only data figures may scroll horizontally inside their containers; the page itself must not overflow. Keep touch targets and keyboard focus usable. Provide light/dark contrast and print rules that remove dashboard navigation and avoid clipping.
- Generate a self-contained HTML export with inline styles, no scripts, no local path references and no external font dependency. It must open independently of the dashboard and use working absolute evidence links. Reuse the same authored body so export and site prose cannot diverge.

## Safety and verification

Escape dataset-derived text before inserting it into HTML. Link only to validated IDs and known schemes. Never publish .env, cookies, tokens, internal review notes, personal file paths or the user's internal sample URLs. A secret scan supplements per-snippet review; it is not proof that a document is safe.

Run the project's unit and JS tests, report integrity and local link checks, then test the packaged reading page. Inspect a desktop and narrow-screen view, including one data figure. Check print CSS and standalone HTML without scripts. Preserve the existing Artifact Hub share link when publishing is authorized. Verify the remote export against the submitted bundle; never treat an upload acknowledgement as sufficient validation of the reader's page.

## Skills assessed

[html-research-reports](https://github.com/f-labs-io/agent-html-skills/blob/main/plugins/html-skills/skills/html-research-reports/SKILL.md) supplies useful semantic HTML, source attribution, secret hygiene, responsive and print requirements. Its Claude-local listener and feedback/publish callbacks do not belong in a public article, so the full plugin is not activated here. This project implements the relevant delivery requirements locally.

The installed `html-report-publisher` is primarily a packaging/publishing workflow for a different service. Its path and resource checks are useful, but it does not replace editorial analysis. Native OpenAI Sites is also a different service from this project's user-selected Artifact Hub. Do not migrate providers or create another public site as a side effect of writing.
