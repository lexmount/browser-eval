# Report maintenance

- Keep one current implementation: `harness/web_fetch/report.py`, built by `scripts/build_report.py` from `evaluation/` into `reports/webfetch/`.
- Do not create report-version filenames, compatibility renderers, redirect pages, or builds that depend on older report directories. Update the current implementation directly.
- Keep task rules, measured outputs, timestamps, runtime settings and hashes as evidence. A presentation change must not alter measured outputs or silently change scores.
- The published report is a formal deliverable: tasks, reference scope, methodology, results, provider interfaces and evidence. Do not publish assistant work logs, iteration notes, assistance requests or user to-do lists.
- Ask for any required manual action in the conversation only. Zhihu and Dianping snapshots have already been provided. Browser access restrictions alone do not invalidate a task.
- Dynamic lists use stable field requirements, not fixed current titles, positions or prices. Disclose when factual correctness is not measured.
- Keep the three result filters (pass, fail, all), default to fail, and show failure classification. Preserve every registered provider's interface examples and adoption statistics.
- Credentials default only to this repository's `.env`, never a user's home directory. Keep `.env.example` current; do not commit secrets.
- Before publication, run unit tests, JavaScript UI tests, report integrity and link checks. Publish through the existing Artifact Hub site and retain its share link.
