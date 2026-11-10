"""Public, byte-preserving HTML snapshots shared by native extraction APIs."""

import html
import json
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from threading import local
from urllib.parse import quote, urlsplit

from harness.benchmarks.data import canonical_source_name
from harness.record_io import bytes_sha256
from harness.web_extract.dataset import benchmark_config
from harness.web_extract.pipeline import (
    fixture_url,
    qualify_fixture,
    read_json,
    require_external_output,
    save_json,
    validate_dataset,
    validate_fixture_base,
)

_SNAPSHOT_HTTP = local()


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
        if path.is_symlink() or bytes_sha256(path.read_bytes()) != entry["sha256"]:
            raise ValueError("Snapshot differs from the frozen input")
        files[case] = entry["sha256"]
    if len(files) != manifest["cases"]:
        raise ValueError("Snapshot population differs")
    assets = resource_inventory(home)
    content = {"html": files, "assets": assets} if assets else files
    version = bytes_sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode())
    return version, files


def resource_inventory(home):
    """Bind captured page resources to the same immutable version as their HTML."""
    manifest = read_json(home / "dataset/manifest.json")
    resources = {}
    for name, entry in manifest["files"].items():
        if name.startswith("assets/"):
            relative = Path(name)
            path = home / "dataset" / relative
            if relative.is_absolute() or ".." in relative.parts or path.is_symlink():
                raise ValueError("Unsafe snapshot resource")
            if bytes_sha256(path.read_bytes()) != entry["sha256"]:
                raise ValueError("Snapshot resource differs from the frozen input")
            resources[name] = entry["sha256"]
    return resources


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
        "schema": "web-extract-public-snapshots/2",
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
        assets = resource_inventory(home)
        for relative in assets:
            path = folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((home / "dataset" / relative).read_bytes())
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
            **({"resources": assets} if assets else {}),
            "source": notice,
        }
    has_assets = any(item.get("resources") for item in registry["datasets"].values())
    (output / "_headers").write_text(
        "/*\n  X-Robots-Tag: noindex, nofollow\n"
        "  Cache-Control: public, max-age=31536000, immutable, no-transform\n"
        + (
            "  Content-Security-Policy: default-src 'none'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self' data:; base-uri 'none'; form-action 'none'\n"
            if has_assets
            else "  Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; "
            "img-src data:; base-uri 'none'; form-action 'none'\n"
        ),
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
        str(p.relative_to(output)): bytes_sha256(p.read_bytes())
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
        "schema": "web-extract-snapshot-verification/1",
        "verified_pages": len(results),
        "pages": results,
        "resources": [
            row
            for home in homes.values()
            for row in verify_resources(home, select(home, binding)["fixture_base"])
        ],
    }


def snapshot_request(url, *, headers, timeout, max_bytes):
    """Reuse static-host connections and bound transient transport retries and bytes."""
    import time

    import requests

    from harness.web_fetch.providers.http_client import HttpResponse

    if not hasattr(_SNAPSHOT_HTTP, "session"):
        _SNAPSHOT_HTTP.session = requests.Session()
    for attempt in range(3):
        try:
            with _SNAPSHOT_HTTP.session.get(
                url, headers=headers, timeout=timeout, stream=True
            ) as response:
                chunks, size = [], 0
                for chunk in response.iter_content(64 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("Snapshot response exceeds its frozen byte size")
                    chunks.append(chunk)
                return HttpResponse(
                    response.status_code,
                    response.url,
                    {k.lower(): v for k, v in response.headers.items()},
                    b"".join(chunks),
                )
        except (OSError, requests.RequestException):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def verify_resources(home, base, *, workers=8):
    """External services must serve the declared resource bytes, not only their hashes."""
    from concurrent.futures import ThreadPoolExecutor

    assets = resource_inventory(home)

    def one(name):
        url = base.rstrip("/") + "/" + quote(name, safe="/")
        response = snapshot_request(
            url,
            headers={"Accept-Encoding": "identity"},
            timeout=60,
            max_bytes=(home / "dataset" / name).stat().st_size,
        )
        if response.status != 200 or bytes_sha256(response.body) != assets[name]:
            raise ValueError("Remote snapshot resource differs: " + name)
        canonical = {url}
        if url.endswith(".html"):
            canonical.add(url[:-5])
        if url.endswith("/index.html"):
            canonical.add(url.removesuffix("index.html"))
        if response.url not in canonical:
            raise ValueError(
                "Use the canonical resource URL without redirects: " + url + " -> " + response.url
            )
        return {
            "case_id": "resource:" + name,
            "url": url,
            "sha256": assets[name],
            "http_status": 200,
        }

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, assets))


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
        **({"cloudflare": binding["cloudflare"]} if "cloudflare" in binding else {}),
        **(
            {
                "snapshot_manifest": {
                    **binding["snapshot_manifest"],
                    **({"cloudflare": binding["cloudflare"]} if "cloudflare" in binding else {}),
                    **(
                        {"hosting_provider": binding["hosting_provider"]}
                        if binding.get("hosting_provider") in {"external", "cloudflare"}
                        else {}
                    ),
                }
            }
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
    snapshot = verification if verification.get("mode") == "dataset_manifest" else None
    managed = managed_cloudflare(binding, selected["fixture_base"])
    if (snapshot is None or not managed) and (
        len(actual) != len(expected) or set(actual) != expected
    ):
        raise ValueError("Snapshot publication requires every matching verified page")
    if snapshot is None:
        snapshot = binding.get("snapshot_manifest")
    if managed:
        deployment_proof(home, binding, selected["fixture_base"])
    else:
        expected_resources = {
            ("resource:" + name, value, selected["fixture_base"] + "/" + quote(name, safe="/"))
            for name, value in resource_inventory(home).items()
        }
        actual_resources = {
            (row["case_id"], row["sha256"], row["url"])
            for row in verification.get("resources", [])
            if row.get("http_status") == 200
            and row["url"].startswith(selected["fixture_base"] + "/")
        }
        if actual_resources != expected_resources:
            raise ValueError("Snapshot publication requires every matching verified resource")
    checked = None
    if snapshot is not None:
        manifest_url = snapshot["manifest_url"]
        if not manifest_url.endswith("/snapshots.json"):
            raise ValueError("Snapshot manifest requires its canonical path")
        origin = manifest_url.removesuffix("/snapshots.json")
        checked = check_manifest(home, origin, snapshot["huggingface"])
        if (
            any(
                checked.get(k) != v
                for k, v in snapshot.items()
                if k not in {"hosting_provider", "cloudflare"}
            )
            or checked["fixture_base"] != selected["fixture_base"]
        ):
            raise ValueError("Snapshot publication dataset manifest changed")
    elif binding.get("hosting_provider") == "cloudflare":
        raise ValueError("Managed snapshot publication requires a dataset manifest")
    source = next(
        item for item in binding["datasets"].values() if item["html_inventory_sha256"] == version
    )
    provider = binding.get("hosting_provider")
    if provider is not None and provider not in {"cloudflare", "external"}:
        raise ValueError("Unsupported snapshot hosting identity")
    return {
        "schema": "web-extract-hf-snapshots/1",
        **source,
        **({"hosting_provider": provider} if provider is not None else {}),
        **({"cloudflare": binding["cloudflare"]} if "cloudflare" in binding else {}),
        **({"snapshot_manifest": checked} if checked is not None else {}),
        "synchronization": (
            "Original files are authoritative. Deploy unchanged HTML, verify the dataset manifest, "
            "then publish this binding. Pin the HF commit and immutable deployment; "
            "create a new version when HTML changes."
        ),
    }


def publish_receipt(home, binding, verification, *, revision, output):
    """Reserve a safe receipt before external mutation and expose any returned revision."""
    require_external_output(output)
    save_json(output, {"state": "pending", "source_revision": revision}, exclusive=True)
    result = publish(home, binding, verification, revision=revision)
    try:
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        save_json(output, result)
    return result


def publish(home, binding, verification, *, revision):
    """Compare the pinned HF original inventory, then commit only URL metadata."""
    from huggingface_hub import hf_hub_download

    from harness.benchmarks.publication import publish_snapshot_metadata

    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Select a full HF source revision")
    metadata = publication_metadata(home, binding, verification)
    hf = metadata.get("huggingface") or benchmark_config(home)["snapshot_distribution"]
    originals = hf_inventory(hf, revision)
    if hf_resources(hf, revision) != resource_inventory(home):
        raise ValueError("Prepared page resources differ from the selected HF edition")
    if originals != metadata["cases"]:
        raise ValueError("HF originals differ from the verified public snapshots")
    metadata["huggingface_source_revision"] = revision
    readme_path = Path(
        hf_hub_download(hf["repository"], "README.md", repo_type="dataset", revision=revision)
    )
    readme = readme_path.read_text(encoding="utf-8")
    marker = "\n## Public HTML snapshots\n"
    section = marker + (
        "\n[snapshots.json](snapshots.json) binds every page ID to its SHA-256 and an "
        "immutable public deployment. Native extraction APIs receive "
        "`fixture_base + '/' + id`; the original HTML files remain in this repository.\n\n"
        "Original files are authoritative. Publication verifies the declared dataset version "
        "and complete input inventory before adding this binding. Changed HTML requires "
        "a new deployment and a new "
        "HF commit; previous files and deployment URLs remain available. Pin the full "
        "HF commit for evaluation. Managed immutable deployments are checked through the "
        "whole-dataset manifest before and after calls; external hosts have every page "
        "byte-checked when first bound. "
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
        and readme == readme_path.read_text(encoding="utf-8")
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
        rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
        originals = {row["id"]: bytes_sha256(row["html"].encode("utf-8")) for row in rows}
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


def hf_resources(hf, revision):
    """Captured resources are versioned inputs, not unbound deployment decorations."""
    if hf["input_format"] != "html-manifest":
        return {}
    from huggingface_hub import hf_hub_download

    source = Path(
        hf_hub_download(hf["repository"], hf["input_path"], repo_type="dataset", revision=revision)
    )
    # HF's revision-pinned cache uses symlinks to content-addressed blobs.
    manifest = json.loads(source.read_bytes())
    return {
        name: item["sha256"]
        for name, item in manifest["files"].items()
        if name.startswith("assets/")
    }


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

    require_external_output(output)
    reserved = False
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
    if hf_resources(hf, revision) != resource_inventory(home):
        raise ValueError("Prepared page resources differ from the selected HF edition")
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
            "schema": "web-extract-public-snapshots/1",
            "datasets": {name: item},
            "huggingface": hf_identity,
        }

    if provider == "external":
        if not public_base:
            raise ValueError("External hosting requires a public HTTPS base URL")
        validate_fixture_base(public_base)
        try:
            receipt = check_manifest(home, public_base, hf_identity)
        except (ValueError, requests.RequestException):
            binding = bind(public_base)
            receipt = verify({name: home}, binding)
        else:
            binding = bind(receipt["fixture_base"])
            binding["snapshot_manifest"] = receipt
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
                    params={"page": page, "per_page": 25},
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
                        and len(data["result"]) < info.get("per_page", 25)
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
            except ValueError:
                pass
        if binding is not None:
            state = "reused_existing_deployment"
        else:
            proposal = {
                "schema": "web-extract-deployment-approval/1",
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
            save_json(output, {"state": "pending", "proposal": proposal}, exclusive=True)
            reserved = True
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
                        "npm_config_registry": "https://registry.npmjs.org",
                        # Wrangler's registry-url dependency reads rc's npm_ prefix.
                        "npm_registry": "https://registry.npmjs.org",
                    },
                    capture_output=True,
                    text=True,
                    timeout=900,
                )
                if result.returncode:
                    diagnostic = (result.stderr or result.stdout or "").strip()
                    for secret in credentials.values():
                        if secret:
                            diagnostic = diagnostic.replace(secret, "[REDACTED]")
                    raise RuntimeError(
                        "Cloudflare deployment failed; existing deployments retained: "
                        + diagnostic[-4000:]
                    )
            response = requests.get(api + "/" + project, headers=headers, timeout=40)
            response.raise_for_status()
            deployment = response.json()["result"]["canonical_deployment"]
            pending = {
                "state": "deployed_pending_verification",
                "proposal": proposal,
                "cloudflare": {
                    "project": project,
                    "deployment_id": deployment["id"],
                    "url": deployment["url"],
                },
            }
            try:
                save_json(output, pending)
            finally:
                print(json.dumps(pending), flush=True)
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
    if provider == "cloudflare":
        base = next(iter(binding["datasets"].values()))["fixture_base"]
        if managed_cloudflare(binding, base):
            deployment_proof(home, binding, base)
        else:
            raise ValueError("Cloudflare immutable deployment identity could not be verified")
    binding["verification"] = receipt
    save_json(output, binding, exclusive=not reserved)
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
    if response.status_code == 404:
        raise ValueError("Snapshot manifest is missing (HTTP 404)")
    response.raise_for_status()
    if response.url != url:
        raise ValueError("Snapshot manifest must use its immutable canonical URL")
    manifest = response.json()
    source = manifest.get("huggingface", {})
    if (
        canonical_source_name("schemas", manifest.get("schema")) != "web-extract-public-snapshots/2"
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
    resources = resource_inventory(home)
    if item.get("resources", {}) != resources:
        raise ValueError("Snapshot resource inventory differs from selected HF dataset")
    path = item.get("path", "")
    if not re.fullmatch(r"[a-z0-9-]+/" + version, path):
        raise ValueError("Snapshot path does not identify the declared input version")
    expected_html = {path + "/" + case + ".html": value for case, value in cases.items()}
    declared_html = {
        name: value
        for name, value in manifest.get("files", {}).items()
        if name.endswith(".html") and name not in {"index.html", "404.html"}
    }
    expected_html.update(
        {path + "/" + name: value for name, value in resources.items() if name.endswith(".html")}
    )
    if declared_html != expected_html:
        raise ValueError("Snapshot file inventory differs from selected HF dataset")
    license_name = "LICENSE.json" if (home / "dataset/LICENSE.json").is_file() else "LICENSE"
    expected_license = bytes_sha256((home / "dataset" / license_name).read_bytes())
    if manifest.get("files", {}).get(path + "/LICENSE.txt") != expected_license:
        raise ValueError("Snapshot does not bind the selected source license")
    if any(
        manifest.get("files", {}).get(path + "/" + name) != value
        for name, value in resources.items()
    ):
        raise ValueError("Snapshot resource file binding differs")
    return {
        "mode": "dataset_manifest",
        "manifest_url": url,
        "manifest_sha256": bytes_sha256(response.content),
        "huggingface": hf_identity,
        "snapshot_huggingface": source,
        "html_inventory_sha256": version,
        "case_count": len(cases),
        "fixture_base": origin.rstrip("/") + "/" + path,
    }


def cloudflare_identity(binding, origin):
    """Validate retained deployment identity without requiring online credentials."""
    if binding.get("hosting_provider") != "cloudflare":
        return None
    parts = urlsplit(origin)
    match = re.fullmatch(r"([0-9a-f]{8})\.([a-z0-9-]+)\.pages\.dev", parts.hostname or "")
    identity = binding.get("cloudflare", {})
    if not isinstance(identity, dict) or not identity.get("account_id"):
        return None
    deployment = identity.get("deployment_id", "")
    if (
        not match
        or parts.scheme != "https"
        or parts.port
        or parts.username
        or not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", deployment)
        or deployment[:8] != match[1]
        or identity.get("project") != match[2]
    ):
        return None
    return identity


def managed_cloudflare(binding, origin):
    """Only an independently confirmed immutable Pages deployment permits reuse."""
    identity = cloudflare_identity(binding, origin)
    if identity is None:
        return False
    parts = urlsplit(origin)
    deployment = identity["deployment_id"]
    import requests

    from harness.web_fetch.config import repository_env

    credentials = repository_env()
    account = credentials.get("CLOUDFLARE_ACCOUNT_ID")
    if (
        not account
        or not credentials.get("CLOUDFLARE_API_TOKEN")
        or identity.get("account_id") != account
    ):
        return False
    try:
        response = requests.get(
            f"https://api.cloudflare.com/client/v4/accounts/{account}/pages/projects/{identity['project']}/deployments/{deployment}",
            headers={"Authorization": "Bearer " + credentials["CLOUDFLARE_API_TOKEN"]},
            timeout=40,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        return False
    if not isinstance(data, dict) or not data.get("success"):
        return False
    result = data.get("result")
    if (
        not isinstance(result, dict)
        or result.get("id") != deployment
        or result.get("url") != f"https://{parts.hostname}"
    ):
        raise ValueError("Cloudflare deployment identity differs from the immutable origin")
    return True


def deployment_proof(home, binding, fixture_base):
    """Audit immutable bytes once; cached proof never replaces the HF input authority."""
    from concurrent.futures import ThreadPoolExecutor
    from datetime import UTC, datetime

    from harness.web_extract.pipeline import data_home, unsafe_parents

    version, cases = inventory(home)
    identity = binding["cloudflare"]
    proof = {
        "scope": "deployment",
        "case_id": "deployment-bytes",
        "schema": "web-extract-deployment-bytes/1",
        "fixture_base": fixture_base,
        "cloudflare": identity,
        "html_inventory_sha256": version,
        "page_count": len(cases),
        "resource_count": len(resource_inventory(home)),
    }
    path = data_home() / "snapshot-verifications" / identity["deployment_id"] / (version + ".json")
    if path.is_symlink() or unsafe_parents(path):
        raise ValueError("Unsafe snapshot verification cache")
    import fcntl

    lock_path = path.with_suffix(".lock")
    if lock_path.is_symlink() or unsafe_parents(lock_path):
        raise ValueError("Unsafe snapshot verification lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("ab") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            cached = read_json(path)
            if cached == {**proof, "verified_at": cached.get("verified_at")} and cached.get(
                "verified_at"
            ):
                return cached
            raise ValueError("Snapshot verification cache differs from the immutable deployment")

        def check_page(cid):
            url = fixture_base + "/" + cid
            response = snapshot_request(
                url,
                headers={"Accept-Encoding": "identity"},
                timeout=60,
                max_bytes=(home / "dataset/html" / (cid + ".html")).stat().st_size,
            )
            if (
                response.status != 200
                or response.url != url
                or bytes_sha256(response.body) != cases[cid]
            ):
                raise ValueError("Remote snapshot page differs: " + cid)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(check_page, cases))
        verify_resources(home, fixture_base, workers=32)
        proof["verified_at"] = datetime.now(UTC).isoformat()
        try:
            save_json(path, proof, exclusive=True)
        except FileExistsError:
            # Concurrent first users must return the same committed proof.
            cached = read_json(path)
            if cached != {**proof, "verified_at": cached.get("verified_at")} or not cached.get(
                "verified_at"
            ):
                raise ValueError("Concurrent snapshot verification proof differs") from None
            return cached
        return proof


def validate_fixture_receipts(
    home, rows, fixture_base, case_ids, *, suffix="", bound=None, allow_legacy_manifest=False
):
    """Check retained online observations offline, including archived minimal receipts."""
    remaining = list(rows)
    if bound is not None:
        expected = manifest_receipt(home, bound, fixture_base)
        if remaining[:1] != expected:
            raise ValueError("Repair dataset snapshot qualification differs")
        remaining = remaining[1:]
    if len(remaining) == 1 and remaining[0].get("scope") == "deployment":
        row = remaining[0]
        version, cases = inventory(home)
        if (
            suffix != ""
            or bound is None
            or cloudflare_identity(bound, fixture_base) is None
            or row.get("schema") != "web-extract-deployment-bytes/1"
            or row.get("case_id") != "deployment-bytes"
            or row.get("fixture_base") != fixture_base
            or row.get("cloudflare") != bound.get("cloudflare")
            or row.get("html_inventory_sha256") != version
            or row.get("page_count") != len(cases)
            or row.get("resource_count") != len(resource_inventory(home))
            or not row.get("verified_at")
        ):
            raise ValueError("Repair deployment byte proof differs")
        return
    # Historical asset-free managed receipts consisted solely of a dataset manifest.
    if (
        allow_legacy_manifest
        and not remaining
        and bound
        and bound.get("hosting_provider") != "external"
        and not resource_inventory(home)
    ):
        return
    expected = {
        cid: (
            fixture_url(fixture_base, cid, suffix),
            bytes_sha256((home / "dataset/html" / (cid + ".html")).read_bytes()),
        )
        for cid in case_ids
    }
    expected.update(
        {
            "resource:" + name: (fixture_base.rstrip("/") + "/" + quote(name, safe="/"), sha)
            for name, sha in resource_inventory(home).items()
        }
    )
    actual = {}
    for row in remaining:
        cid = row.get("case_id")
        if cid in actual or row.get("http_status", 200) != 200:
            raise ValueError("Repair fixture qualification is duplicated or failed")
        actual[cid] = (row.get("url"), row.get("sha256"))
    if actual != expected:
        raise ValueError("Repair fixture qualification source changed or is incomplete")


def verify_bound_manifest(home, bound, fixture_base, *, case_ids=None, suffix=""):
    """Recheck a frozen dataset manifest before collection or failure repair."""
    manifest_url = bound.get("manifest_url", "")
    if not manifest_url.endswith("/snapshots.json"):
        raise ValueError("Frozen dataset snapshot manifest URL changed")
    origin = manifest_url.removesuffix("/snapshots.json")
    receipt = check_manifest(home, origin, bound["huggingface"])
    expected = {k: v for k, v in bound.items() if k not in {"hosting_provider", "cloudflare"}}
    actual = {k: v for k, v in receipt.items() if k not in {"hosting_provider", "cloudflare"}}
    if actual != expected or receipt["fixture_base"] != fixture_base:
        raise ValueError("Frozen dataset snapshot manifest changed")
    rows = manifest_receipt(home, bound, fixture_base)
    if managed_cloudflare(bound, origin):
        if suffix != "":
            raise ValueError("Managed snapshot URLs require an empty fixture suffix")
        rows.append(deployment_proof(home, bound, fixture_base))
    else:
        from harness.web_extract.pipeline import qualify_fixture

        _, cases = inventory(home)
        selected = sorted(cases) if case_ids is None else list(case_ids)
        if not selected or not set(selected).issubset(cases):
            raise ValueError("Snapshot verification requires declared cases")
        rows += [qualify_fixture(home, fixture_base, cid, suffix=suffix) for cid in selected]
        rows += verify_resources(home, fixture_base)
    return rows


def manifest_receipt(home, receipt, fixture_base):
    """Validate a retained whole-dataset receipt without re-fetching each page."""
    version, cases = inventory(home)
    validate_fixture_base(fixture_base)
    if (
        receipt.get("mode") != "dataset_manifest"
        or receipt.get("fixture_base") != fixture_base
        or receipt.get("html_inventory_sha256") != version
        or receipt.get("case_count") != len(cases)
        or not re.fullmatch(r"[0-9a-f]{64}", receipt.get("manifest_sha256", ""))
    ):
        raise ValueError("Dataset snapshot receipt differs from selected inputs")
    return [
        {
            "scope": "dataset",
            "case_id": "dataset-snapshot",
            "sha256": receipt["manifest_sha256"],
            "http_status": 200,
            "snapshot_manifest": receipt,
        }
    ]
