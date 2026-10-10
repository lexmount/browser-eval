# Hosting reports

The evaluation CLI writes local HTML and evidence. It does not register accounts, upload results or change report visibility. Keep the evidence directory for rebuilding and auditing; share the generated report directory, not the entire run tree.

## Your own diagnostic report

[Artifact Site](https://github.com/lexmount/artifact-site) is an open-source host for static multi-file reports. Choose a deployment, then sign in or create an account on its website to keep and manage your reports:

| Deployment | Website / CLI base | Agent and API guide |
| --- | --- | --- |
| China | [https://artifact-site.lexmount.cn](https://artifact-site.lexmount.cn/) | [Publishing guide](https://artifact-site.lexmount.cn/for-agents.md) |
| Global | [https://yours.lexmount.com/artifact-site](https://yours.lexmount.com/artifact-site) | [Publishing guide](https://yours.lexmount.com/artifact-site/for-agents.md) |
| Self-hosted | Your deployment URL; follow the [upstream setup](https://github.com/lexmount/artifact-site#quick-start-local-deployment) | `<your-base>/for-agents.md` |

Each deployment's guide documents its authentication, limits and visibility. An agent can read it or connect to `<your-base>/mcp`; it should operate on your account and chosen target. Keep the global site's `/artifact-site` prefix in CLI, guide and MCP addresses.

With Node.js and npm installed, authorize the official CLI for this host:

```bash
npx --yes @artifact-site/cli@0.4.0 login --base https://artifact-site.lexmount.cn
```

Follow the authorization page to sign in and approve access. Publish the report using the same host:

```bash
npx --yes @artifact-site/cli@0.4.0 publish /absolute/path/to/run/report \
  --share none --base https://artifact-site.lexmount.cn
```

For the global site, replace `--base https://artifact-site.lexmount.cn` in both commands with `--base https://yours.lexmount.com/artifact-site`. For your own deployment, use its complete base URL. Authorize the deployment you intend to publish to.

These commands use the checked CLI version 0.4.0. `--share none` suppresses the older client's automatic independent share link. The service creates new artifacts privately by default. Inspect the returned owner, version and URL, then check visibility in the website. To let anyone with the main link read the report, select `unlisted` in its sharing controls. CLI 0.4.0 has no `visibility` command; current API/MCP visibility operations are documented in the service guide.

`unlisted` permits link access without listing the artifact in public discovery. Choose `public` only when you intend public search/discovery. Updating an existing artifact requires its expected current version and preserves access; follow the service guide instead of publishing a duplicate after an interrupted operation.

Keep the diagnostic label and evaluation limits visible. Check that result pages, responses and logs contain only material you intend to share. Hosting a diagnostic run does not certify its results. You may use another static host, but it must preserve the report's relative routes and evidence files. Test nested result and evidence links on the deployed host.

## Certified benchmark releases

Repository-maintained releases use a separate path. The selected track validates its frozen population, identities, evidence, certification and required review; its release gates validate presentation and public packaging. Use that track's documented package/publish commands after those checks pass. See the [architecture](architecture.md#reports-and-publication) and the track guides linked from [README](../README.md).

Each release profile binds a host, site and expected remote version. Some historical tracks use different deployments. A command-line host override cannot redirect an existing bound report. Declare a new experiment and your own publication authority rather than copying another release's slug or changing historical measurement contracts.

The repository publisher reads `ARTIFACT_SITE_TOKEN` or the selected host's token under `~/.config/artifact-site/tokens/<host>`. It never reads publication credentials from `.env`; evaluation credentials stay separate. Previous Artifact Hub locations are read-only migration fallbacks, not new authorization destinations. Host-specific authorizations must not overwrite another host's token.
