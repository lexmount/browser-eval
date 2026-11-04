"""Public, byte-preserving HTML snapshots shared by native extraction APIs."""

import hashlib
import html
import json
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from harness.web_scraper.dataset import benchmark_config
from harness.web_scraper.pipeline import qualify_fixture, read_json, save_json, validate_dataset


def sha(data):
    return hashlib.sha256(data).hexdigest()


def inventory(home):
    validate_dataset(home)
    manifest = read_json(home / "dataset/manifest.json")
    files = {}
    for name, entry in manifest["files"].items():
        if not name.startswith("html/"):
            continue
        case = Path(name).stem
        if not re.fullmatch(r"[A-Za-z0-9_-]+", case) or name != f"html/{case}.html":
            raise ValueError("Unsafe snapshot case path")
        path = home / "dataset" / name
        if path.is_symlink() or sha(path.read_bytes()) != entry["sha256"]:
            raise ValueError("Snapshot differs from the frozen input")
        files[case] = entry["sha256"]
    if len(files) != manifest["cases"]:
        raise ValueError("Snapshot population differs")
    version = sha(json.dumps(files, sort_keys=True, separators=(",", ":")).encode())
    return version, files


def build(homes, output, *, huggingface):
    """Build one dataset snapshot with an explicit immutable HF source version."""
    if not huggingface.get("repository") or not re.fullmatch(
        r"[0-9a-f]{40}", huggingface.get("revision", "")
    ):
        raise ValueError("Snapshot requires an HF dataset repository and full commit")
    if output.is_symlink() or (output.exists() and any(output.iterdir())):
        raise ValueError("Build into a new empty directory; retain previous deployments")
    if len(homes) != 1:
        raise ValueError("Each dataset must have its own snapshot service")
    prepared = []
    for name, home in homes.items():
        if not re.fullmatch(r"[a-z0-9-]+", name):
            raise ValueError("Unsafe snapshot dataset name")
        version, files = inventory(home)
        profile = read_json(benchmark_config(home)["profile"])
        license_name = (
            "LICENSE.json"
            if "LICENSE.json" in read_json(home / "dataset/manifest.json")["files"]
            else "LICENSE"
        )
        license_path = home / "dataset" / license_name
        if license_path.is_symlink() or not license_path.is_file():
            raise ValueError("Original source license is required")
        prepared.append((name, home, version, files, profile, license_path))
    output.mkdir(parents=True, exist_ok=True)
    registry = {
        "schema": "web-scraper-public-snapshots/2",
        "huggingface": huggingface,
        "datasets": {},
    }
    for name, home, version, files, profile, license_path in prepared:
        prefix = f"{name}/{version}"
        folder = output / prefix
        folder.mkdir(parents=True, exist_ok=True)
        for case in files:
            (folder / f"{case}.html").write_bytes(
                (home / "dataset/html" / f"{case}.html").read_bytes()
            )
        (folder / "LICENSE.txt").write_bytes(license_path.read_bytes())
        notice = {
            "name": profile["name"],
            "source": profile["dataset"]["repository"],
            "commit": profile["dataset"]["commit"],
            "source_license": profile["source_license"],
            "scope": (
                "Original archived HTML only; website content retains its original rights. "
                "No reference answers or provider outputs."
            ),
        }
        save_json(folder / "NOTICE.json", notice)
        registry["datasets"][name] = {
            "path": prefix,
            "html_inventory_sha256": version,
            "cases": files,
            "source": notice,
        }
    (output / "_headers").write_text(
        "/*\n  X-Robots-Tag: noindex, nofollow\n"
        "  Cache-Control: public, max-age=31536000, immutable, no-transform\n"
        "  Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; "
        "img-src data:; base-uri 'none'; form-action 'none'\n",
        encoding="utf-8",
    )
    # Cloudflare Pages otherwise serves its root document as a missing-path fallback.
    (output / "404.html").write_text(
        "<!doctype html><title>Snapshot not found</title><h1>Snapshot not found</h1>",
        encoding="utf-8",
    )
    (output / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>LexBench HTML snapshots</title>'
        "<h1>LexBench HTML snapshots</h1><p>Frozen inputs for structured extraction APIs. "
        "Original HTML bytes are preserved. Scripts and external resources are disabled "
        "by response headers.</p><ul>"
        + "".join(
            f"<li>{name}: {len(item['cases'])} pages — "
            f'<a href="/{item["path"]}/NOTICE.json">source</a> · '
            f'<a href="/{item["path"]}/LICENSE.txt">license</a></li>'
            for name, item in registry["datasets"].items()
        )
        + '</ul><a href="/snapshots.json">Page inventory and SHA-256 hashes</a>',
        encoding="utf-8",
    )
    registry["files"] = {
        str(p.relative_to(output)): sha(p.read_bytes())
        for p in sorted(output.rglob("*"))
        if p.is_file()
    }
    save_json(output / "snapshots.json", registry, exclusive=True)
    return registry


def verify(homes, binding, *, workers=8):
    """Verify every canonical URL against frozen bytes, without provider calls."""
    jobs = []
    for home in homes.values():
        selected = select(home, binding)
        _, files = inventory(home)
        jobs.extend((home, selected["fixture_base"], case) for case in files)

    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_verify_job, jobs))
    return {
        "schema": "web-scraper-snapshot-verification/1",
        "verified_pages": len(results),
        "pages": results,
    }


def select(home, binding):
    """Select an independent dataset service by its exact HTML inventory."""
    version, files = inventory(home)
    matches = [
        item
        for item in binding.get("datasets", {"selected": binding}).values()
        if item["html_inventory_sha256"] == version and item["cases"] == files
    ]
    if len(matches) != 1:
        raise ValueError("Snapshot registry must contain this exact dataset once")
    item = matches[0]
    base = item["fixture_base"]
    parts = urlsplit(base)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError("Snapshot base must be a public HTTPS URL")
    if parts.hostname != urlsplit(item["immutable_origin"]).hostname:
        raise ValueError("Use the dataset's immutable deployment for evaluation")
    return {
        "fixture_base": base.rstrip("/"),
        "html_inventory_sha256": version,
        "input_route": "url",
        **(
            {"hosting_provider": binding["hosting_provider"]}
            if "hosting_provider" in binding
            else {}
        ),
        **({"huggingface": binding["huggingface"]} if "huggingface" in binding else {}),
        **(
            {"snapshot_manifest": binding["snapshot_manifest"]}
            if "snapshot_manifest" in binding
            else {}
        ),
    }


def build_index(binding, output):
    """A navigation-only service; HTML inputs remain on independent services."""
    if output.exists() and any(output.iterdir()):
        raise ValueError("Build into a new empty directory")
    public = {"schema": binding["schema"], "datasets": {}}
    rows = []
    for name, item in binding["datasets"].items():
        base = item["fixture_base"]
        if not base.startswith("https://"):
            raise ValueError("Dataset service requires HTTPS")
        public["datasets"][name] = item
        rows.append(
            '<li><a href="'
            + html.escape(item["homepage"], quote=True)
            + '">'
            + html.escape(item["source"]["name"])
            + "</a>: "
            + str(len(item["cases"]))
            + " archived pages; independent service. "
            + '<a href="'
            + html.escape(base + "/" + sorted(item["cases"])[0], quote=True)
            + '">Example input</a></li>'
        )
    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>LexBench dataset snapshots</title>'
        "<h1>LexBench dataset snapshots</h1><p>Each dataset runs on its own public service. "
        "This page provides navigation only.</p><ul>"
        + "".join(rows)
        + '</ul><a href="snapshots.json">Versioned URL registry and page hashes</a>',
        encoding="utf-8",
    )
    save_json(output / "snapshots.json", public, exclusive=True)
    (output / "404.html").write_text(
        "<!doctype html><title>Not found</title>Not found", encoding="utf-8"
    )
    (output / "_headers").write_text(
        "/*\n  X-Robots-Tag: noindex, nofollow\n  Cache-Control: no-cache\n", encoding="utf-8"
    )
    return public


def _verify_job(job):
    home, base, case = job
    return qualify_fixture(home, base, case)


def publication_metadata(home, binding, verification):
    """Require a full matching online receipt before publishing address metadata."""
    selected = select(home, binding)
    version, cases = inventory(home)
    expected = {
        (case, value, selected["fixture_base"] + "/" + case) for case, value in cases.items()
    }
    actual = [
        (row["case_id"], row["sha256"], row["url"])
        for row in verification.get("pages", [])
        if row["url"].startswith(selected["fixture_base"] + "/") and row["http_status"] == 200
    ]
    if verification.get("mode") == "dataset_manifest":
        origin = "https://" + urlsplit(verification["manifest_url"]).netloc
        checked = check_manifest(home, origin, verification["huggingface"])
        if checked != verification or checked["fixture_base"] != selected["fixture_base"]:
            raise ValueError("Snapshot publication dataset manifest changed")
    elif len(actual) != len(expected) or set(actual) != expected:
        raise ValueError("Snapshot publication requires every matching verified page")
    source = next(
        item for item in binding["datasets"].values() if item["html_inventory_sha256"] == version
    )
    return {
        "schema": "web-scraper-hf-snapshots/1",
        **source,
        **(
            {"snapshot_manifest": verification}
            if verification.get("mode") == "dataset_manifest"
            else {}
        ),
        "synchronization": (
            "Original files are authoritative. Deploy unchanged HTML, verify the dataset manifest, "
            "then publish this binding. Pin the HF commit and immutable deployment; "
            "create a new version when HTML changes."
        ),
    }


def publish(home, binding, verification, *, revision):
    """Compare the pinned HF original inventory, then commit only URL metadata."""
    from huggingface_hub import hf_hub_download

    from harness.benchmarks.publication import publish_snapshot_metadata

    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Select a full HF source revision")
    metadata = publication_metadata(home, binding, verification)
    hf = metadata.get("huggingface") or benchmark_config(home)["snapshot_distribution"]
    originals = hf_inventory(hf, revision)
    if originals != metadata["cases"]:
        raise ValueError("HF originals differ from the verified public snapshots")
    metadata["huggingface_source_revision"] = revision
    readme_path = Path(
        hf_hub_download(hf["repository"], "README.md", repo_type="dataset", revision=revision)
    )
    readme = readme_path.read_text()
    marker = "\n## Public HTML snapshots\n"
    section = marker + (
        "\n[snapshots.json](snapshots.json) binds every page ID to its SHA-256 and an "
        "immutable public deployment. Native extraction APIs receive "
        "`fixture_base + '/' + id`; the original HTML files remain in this repository.\n\n"
        "Original files are authoritative. Publication verifies the declared dataset version "
        "and complete input inventory before adding this binding. Changed HTML requires "
        "a new deployment and a new "
        "HF commit; previous files and deployment URLs remain available. Pin the full "
        "HF commit for evaluation and verify page hashes before and after calls. "
        "A mismatch stops collection rather than silently replacing the input. "
        "The navigation index does not host dataset HTML.\n"
    )
    readme = (
        re.sub(
            r"\n## Public HTML snapshots\n.*?(?=\n## |\Z)", lambda _: section, readme, flags=re.S
        )
        if marker in readme
        else readme + section
    )
    existing = None
    try:
        existing = json.loads(
            Path(
                hf_hub_download(
                    hf["repository"], "snapshots.json", repo_type="dataset", revision=revision
                )
            ).read_bytes()
        )
    except Exception as error:
        from huggingface_hub.errors import EntryNotFoundError

        if not isinstance(error, EntryNotFoundError):
            raise

    def comparable(value):
        return {key: item for key, item in value.items() if key != "huggingface_source_revision"}

    if (
        existing is not None
        and comparable(existing) == comparable(metadata)
        and readme == readme_path.read_text()
    ):
        return {
            "repository": hf["repository"],
            "source_revision": revision,
            "revision": revision,
            "fixture_base": metadata["fixture_base"],
            "cases": len(originals),
            "changed": False,
        }
    result = publish_snapshot_metadata(
        hf["repository"], metadata, parent_revision=revision, readme=readme
    )
    return {
        "repository": hf["repository"],
        "source_revision": revision,
        "revision": result.oid,
        "fixture_base": metadata["fixture_base"],
        "cases": len(originals),
    }


def hf_inventory(hf, revision):
    """Read original input hashes at one immutable HF commit."""
    from huggingface_hub import hf_hub_download

    source = Path(
        hf_hub_download(hf["repository"], hf["input_path"], repo_type="dataset", revision=revision)
    )
    if hf["input_format"] == "html-jsonl":
        rows = [json.loads(line) for line in source.read_text().splitlines()]
        originals = {row["id"]: sha(row["html"].encode("utf-8")) for row in rows}
        if len(originals) != len(rows) or any(
            originals[row["id"]] != row["html_sha256"] for row in rows
        ):
            raise ValueError("HF original HTML integrity differs")
    elif hf["input_format"] == "html-manifest":
        manifest = json.loads(source.read_bytes())
        originals = {
            Path(name).stem: item["sha256"]
            for name, item in manifest["files"].items()
            if name.startswith("html/")
        }
    else:
        raise ValueError("Unknown HF original input format")
    return originals


def host(
    home,
    output,
    *,
    provider="cloudflare",
    public_base=None,
    project=None,
    approve_plan=None,
    hf_revision=None,
    wrangler=None,
):
    """Read HF latest once; reuse matching hosting or request an explicit update approval."""
    import os
    import subprocess
    import tempfile

    import requests
    from huggingface_hub import HfApi, hf_hub_download

    from harness.web_fetch.config import repository_env

    if output.exists():
        raise ValueError("Snapshot output already exists")
    config = benchmark_config(home)
    profile = read_json(config["profile"])
    version, cases = inventory(home)
    hf = config["snapshot_distribution"]
    revision = HfApi().dataset_info(hf["repository"], revision=hf_revision).sha
    if hf_inventory(hf, revision) != cases:
        raise ValueError(
            "Prepared dataset differs from HF latest; "
            "prepare that HF edition before evaluating or deploying"
        )
    metadata = None
    try:
        metadata = json.loads(
            Path(
                hf_hub_download(
                    hf["repository"], "snapshots.json", repo_type="dataset", revision=revision
                )
            ).read_bytes()
        )
    except Exception as error:
        from huggingface_hub.errors import EntryNotFoundError

        if not isinstance(error, EntryNotFoundError):
            raise
    name = profile["dataset"]["id"]
    entry = {
        "html_inventory_sha256": version,
        "cases": cases,
        "source": {
            "name": profile["name"],
            "repository": profile["dataset"]["repository"],
            "commit": profile["dataset"]["commit"],
            "license": profile["source_license"],
        },
    }
    hf_identity = {"repository": hf["repository"], "revision": revision}

    def bind(base):
        item = {
            **entry,
            "fixture_base": base,
            "immutable_origin": "https://" + urlsplit(base).netloc,
            "homepage": base,
        }
        return {
            "schema": "web-scraper-public-snapshots/1",
            "datasets": {name: item},
            "huggingface": hf_identity,
        }

    if provider == "external":
        if not public_base:
            raise ValueError("External hosting requires a public HTTPS base URL")
        binding = bind(public_base)
        receipt = verify({name: home}, binding)
        state = "external_verified"
    elif provider == "cloudflare":
        credentials = repository_env()
        if not all(
            credentials.get(key) for key in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")
        ):
            raise ValueError(
                "Repository .env requires CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN"
            )
        project = project or config.get("snapshot_project") or "lexbench-" + name
        if not re.fullmatch(r"[a-z0-9-]{1,58}", project):
            raise ValueError("Invalid Cloudflare Pages project name")
        api = (
            "https://api.cloudflare.com/client/v4/accounts/"
            + credentials["CLOUDFLARE_ACCOUNT_ID"]
            + "/pages/projects"
        )
        headers = {"Authorization": "Bearer " + credentials["CLOUDFLARE_API_TOKEN"]}
        response = requests.get(api + "/" + project, headers=headers, timeout=40)
        existing = response.status_code != 404
        deployment = None
        if existing:
            response.raise_for_status()
            data = response.json()
            if not data.get("success"):
                raise ValueError("Cloudflare project lookup failed")
            deployment = data["result"].get("canonical_deployment") or data["result"].get(
                "latest_deployment"
            )
        canonical_deployment = deployment
        wanted_url = metadata.get("immutable_origin") if metadata else None
        if existing and (not deployment or deployment["url"] != wanted_url):
            page = 1
            while True:
                history = requests.get(
                    api + "/" + project + "/deployments",
                    headers=headers,
                    params={"page": page, "per_page": 100},
                    timeout=40,
                )
                history.raise_for_status()
                data = history.json()
                if not data.get("success"):
                    raise ValueError("Cloudflare deployment discovery failed")
                found = next(
                    (
                        item
                        for item in data["result"]
                        if item["url"] == wanted_url
                        or item.get("deployment_trigger", {}).get("metadata", {}).get("commit_hash")
                        == revision
                    ),
                    None,
                )
                if found:
                    deployment = found
                    break
                info = data.get("result_info", {})
                if (
                    not data["result"]
                    or (info.get("total_pages") is not None and page >= info["total_pages"])
                    or (
                        info.get("total_pages") is None
                        and len(data["result"]) < info.get("per_page", 100)
                    )
                ):
                    break
                page += 1
        binding = receipt = None
        if deployment:
            try:
                receipt = check_manifest(home, deployment["url"], hf_identity)
                binding = bind(receipt["fixture_base"])
                binding["snapshot_manifest"] = receipt
            except (ValueError, requests.HTTPError):
                pass
        if binding is not None:
            state = "reused_existing_deployment"
        else:
            proposal = {
                "schema": "web-scraper-deployment-approval/1",
                "huggingface": hf_identity,
                "html_inventory_sha256": version,
                "project": project,
                "account_id": credentials["CLOUDFLARE_ACCOUNT_ID"],
                "previous_deployment_id": canonical_deployment["id"]
                if canonical_deployment
                else None,
                "page_count": len(cases),
            }
            if approve_plan is None:
                save_json(output, proposal, exclusive=True)
                return {
                    "state": "approval_required",
                    "proposal": str(output),
                    "pages": len(cases),
                    "reason": (
                        "Public snapshot missing or differs from HF latest; no remote changes made"
                    ),
                }
            if read_json(approve_plan) != proposal:
                raise ValueError(
                    "HF version or Cloudflare deployment changed since approval; "
                    "approve a fresh proposal"
                )
            if not existing:
                created = requests.post(
                    api,
                    headers=headers,
                    json={"name": project, "production_branch": "main"},
                    timeout=40,
                )
                created.raise_for_status()
                if not created.json().get("success"):
                    raise ValueError("Cloudflare project creation failed")
            with tempfile.TemporaryDirectory(prefix="lexbench-snapshot-") as temporary:
                folder = Path(temporary) / "public"
                build({name: home}, folder, huggingface=hf_identity)
                command = (
                    [str(wrangler)]
                    if wrangler
                    else [
                        "npx",
                        "--yes",
                        "--registry=https://registry.npmjs.org",
                        "wrangler@4.43.0",
                    ]
                )
                result = subprocess.run(
                    command
                    + [
                        "pages",
                        "deploy",
                        str(folder),
                        "--project-name",
                        project,
                        "--branch",
                        "main",
                        "--commit-hash",
                        revision,
                        "--commit-message",
                        "HF " + hf["repository"] + "@" + revision + " HTML " + version,
                    ],
                    env={
                        **os.environ,
                        **{
                            key: credentials[key]
                            for key in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")
                        },
                        "WRANGLER_SEND_METRICS": "false",
                    },
                    capture_output=True,
                    text=True,
                    timeout=900,
                )
                if result.returncode:
                    raise RuntimeError(
                        "Cloudflare deployment failed; existing deployments retained"
                    )
            response = requests.get(api + "/" + project, headers=headers, timeout=40)
            response.raise_for_status()
            deployment = response.json()["result"]["canonical_deployment"]
            binding = bind(deployment["url"].rstrip("/") + "/" + name + "/" + version)
            receipt = check_manifest(home, deployment["url"], hf_identity)
            binding["snapshot_manifest"] = receipt
            state = "deployed_new_input_version"
        binding["cloudflare"] = {
            "project": project,
            "account_id": credentials["CLOUDFLARE_ACCOUNT_ID"],
            "deployment_id": deployment["id"],
        }
    else:
        raise ValueError("Unknown snapshot hosting provider")
    binding["hosting_provider"] = provider
    binding["hosting_state"] = state
    binding["verification"] = receipt
    save_json(output, binding, exclusive=True)
    return {
        "state": state,
        "pages": len(cases),
        "huggingface_revision": revision,
        "fixture_base": next(iter(binding["datasets"].values()))["fixture_base"],
    }


def check_manifest(home, origin, hf_identity):
    """Identify a whole snapshot from one manifest, without fetching HTML pages."""
    import requests

    url = origin.rstrip("/") + "/snapshots.json"
    response = requests.get(url, timeout=40)
    response.raise_for_status()
    if response.url != url:
        raise ValueError("Snapshot manifest must use its immutable canonical URL")
    manifest = response.json()
    source = manifest.get("huggingface", {})
    if (
        manifest.get("schema") != "web-scraper-public-snapshots/2"
        or source.get("repository") != hf_identity["repository"]
        or not re.fullmatch(r"[0-9a-f]{40}", source.get("revision", ""))
    ):
        raise ValueError("Snapshot lacks a valid HF dataset version")
    version, cases = inventory(home)
    datasets = manifest.get("datasets", {})
    if len(datasets) != 1:
        raise ValueError("A snapshot must identify exactly one dataset edition")
    item = next(iter(datasets.values()))
    if item.get("html_inventory_sha256") != version or item.get("cases") != cases:
        raise ValueError("Snapshot inventory differs from selected HF dataset")
    path = item.get("path", "")
    if not re.fullmatch(r"[a-z0-9-]+/" + version, path):
        raise ValueError("Snapshot path does not identify the declared input version")
    expected_html = {path + "/" + case + ".html": value for case, value in cases.items()}
    declared_html = {
        name: value
        for name, value in manifest.get("files", {}).items()
        if name.endswith(".html") and name not in {"index.html", "404.html"}
    }
    if declared_html != expected_html:
        raise ValueError("Snapshot file inventory differs from selected HF dataset")
    return {
        "mode": "dataset_manifest",
        "manifest_url": url,
        "manifest_sha256": sha(response.content),
        "huggingface": hf_identity,
        "snapshot_huggingface": source,
        "html_inventory_sha256": version,
        "case_count": len(cases),
        "fixture_base": origin.rstrip("/") + "/" + path,
    }


def verify_bound_manifest(home, bound, fixture_base):
    """Recheck a frozen dataset manifest before collection or failure repair."""
    origin = "https://" + urlsplit(bound["manifest_url"]).netloc
    receipt = check_manifest(home, origin, bound["huggingface"])
    if receipt != bound or receipt["fixture_base"] != fixture_base:
        raise ValueError("Frozen dataset snapshot manifest changed")
    return [
        {
            "scope": "dataset",
            "case_id": "dataset-snapshot",
            "sha256": receipt["manifest_sha256"],
            "http_status": 200,
            "snapshot_manifest": receipt,
        }
    ]
