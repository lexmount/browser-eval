"""Frozen public inputs and native URL-only cohort behavior."""

import json
from unittest.mock import patch

import pytest

from harness.web_extract import snapshots
from harness.web_extract.certification import create_plan, run
from harness.web_extract.pipeline import METHODS, SCHEMA, digest, provider_call

HF_INVENTORY = snapshots.hf_inventory
CHECK_MANIFEST = snapshots.check_manifest


def build_snapshot(homes, output):
    return snapshots.build(
        homes, output, huggingface={"repository": "owner/data", "revision": "a" * 40}
    )


def cf_lookup(*responses):
    """Realistic project lookups and an empty paginated deployment history."""
    from unittest.mock import MagicMock

    queue = iter(responses)

    def get(url, **kwargs):
        if url.endswith("/deployments"):
            response = MagicMock(status_code=200)
            response.json.return_value = {
                "success": True,
                "result": [],
                "result_info": {"total_pages": 1},
            }
            return response
        return next(queue)

    return get


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "dataset"
    (root / "html").mkdir(parents=True)
    html = b'<html><base href="https://original.example/"><p>Price 42</p></html>'
    (root / "html/case-a.html").write_bytes(html)
    (root / "LICENSE").write_text("Original source license", encoding="utf-8")
    (root / "dataset").mkdir()
    (root / "dataset/ground-truth.json").write_text('{"case-a":{"price":"42"}}', encoding="utf-8")
    files = {"html/case-a.html": {"sha256": digest(html)}}
    (root / "manifest.json").write_text(json.dumps({"cases": 1, "files": files}), encoding="utf-8")
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "name": "Original benchmark",
                "source_license": "Original license; website rights retained",
                "dataset": {
                    "id": "example",
                    "repository": "owner/source",
                    "commit": "a" * 40,
                    "files": {"evaluate.py": "b" * 64},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(snapshots, "validate_dataset", lambda _: None)
    monkeypatch.setattr(
        snapshots,
        "benchmark_config",
        lambda _: {
            "profile": profile,
            "snapshot_distribution": {
                "repository": "owner/data",
                "input_path": "inputs.jsonl",
                "input_format": "html-jsonl",
            },
        },
    )
    from unittest.mock import MagicMock

    latest = MagicMock()
    latest.dataset_info.return_value.sha = "a" * 40
    monkeypatch.setattr("huggingface_hub.HfApi", lambda: latest)
    monkeypatch.setattr(snapshots, "hf_inventory", lambda *args: {"case-a": digest(html)})
    metadata = tmp_path / "hf-snapshots.json"
    metadata.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("huggingface_hub.hf_hub_download", lambda *a, **k: str(metadata))
    monkeypatch.setattr("harness.web_extract.certification.validate_dataset", lambda _: None)
    monkeypatch.setattr("harness.web_extract.certification.selected_profile", lambda _: profile)
    monkeypatch.setattr(
        snapshots,
        "qualify_fixture",
        lambda home, base, case: {
            "case_id": case,
            "url": base + "/" + case,
            "sha256": digest(html),
            "http_status": 200,
        },
    )
    monkeypatch.setattr(
        snapshots,
        "check_manifest",
        lambda home, origin, hf: {
            "mode": "dataset_manifest",
            "fixture_base": origin + "/example/" + snapshots.inventory(home)[0],
            "manifest_sha256": "f" * 64,
            "manifest_url": origin + "/snapshots.json",
            "huggingface": hf,
        },
    )
    return home


def binding(home, tmp_path):
    value = build_snapshot({"example": home}, tmp_path / "site")
    item = value["datasets"]["example"]
    item["immutable_origin"] = "https://snapshots.example"
    item["homepage"] = "https://snapshots.example"
    item["fixture_base"] = "https://snapshots.example/" + item["path"]
    value["snapshot_manifest"] = {
        "manifest_url": "https://snapshots.example/snapshots.json",
        "manifest_sha256": "f" * 64,
        "huggingface": value["huggingface"],
        "fixture_base": item["fixture_base"],
    }
    return value


def test_public_package_contains_only_identical_html_and_source_notices(home, tmp_path):
    value = binding(home, tmp_path)
    item = value["datasets"]["example"]
    root = tmp_path / "site"
    assert (root / item["path"] / "case-a.html").read_bytes() == (
        home / "dataset/html/case-a.html"
    ).read_bytes()
    assert not any("ground-truth" in str(p) for p in root.rglob("*"))
    assert {p.name for p in (root / item["path"]).iterdir()} == {
        "case-a.html",
        "LICENSE.txt",
        "NOTICE.json",
    }
    assert "Original benchmark" in (root / item["path"] / "NOTICE.json").read_text(encoding="utf-8")
    assert "default-src 'none'" in (root / "_headers").read_text(encoding="utf-8")
    assert (root / "404.html").is_file()
    with pytest.raises(ValueError, match="empty directory"):
        build_snapshot({"example": home}, root)


def test_version_is_html_content_identity_not_reference_metadata(home, tmp_path):
    original, _ = snapshots.inventory(home)
    (home / "dataset/dataset/ground-truth.json").write_text(
        '{"case-a":{"price":"corrected"}}', encoding="utf-8"
    )
    assert snapshots.inventory(home)[0] == original
    (home / "dataset/html/case-a.html").write_text("Changed", encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        snapshots.inventory(home)


@pytest.mark.parametrize(
    "origin",
    ["http://host", "https://user:password@host", "https://host?key=secret"],
)
def test_snapshot_plan_rejects_unsafe_origins(home, tmp_path, origin):
    value = binding(home, tmp_path)
    value["datasets"]["example"]["fixture_base"] = origin
    with pytest.raises(ValueError, match="HTTPS URL"):
        snapshots.select(home, value)


def test_new_plan_freezes_one_url_for_every_participant(home, tmp_path):
    value = binding(home, tmp_path)
    source = tmp_path / "binding.json"
    source.write_text(json.dumps(value), encoding="utf-8")
    methods = [
        name for name in METHODS if name not in {"extruct", "firecrawl-parse-json", "zyte-custom"}
    ]
    plan = create_plan(home, methods, tmp_path / "plan.json", snapshots=source)
    assert all(
        row["input"] == "url" and row["fixture_suffix"] == ""
        for row in plan["participants"].values()
    )
    assert plan["public_snapshots"]["fixture_base"].startswith("https://snapshots.example/example/")
    with pytest.raises(ValueError, match="override"):
        run(
            home,
            tmp_path / "plan.json",
            methods[0],
            tmp_path / "calls",
            fixture_base="https://different.example",
        )


@pytest.mark.parametrize("method", ["extruct"])
def test_retired_interface_cannot_enter_url_cohort(home, tmp_path, method):
    value = binding(home, tmp_path)
    source = tmp_path / "binding.json"
    source.write_text(json.dumps(value), encoding="utf-8")
    with patch(
        "harness.web_extract.certification.benchmark_config", return_value={"adapter": object()}
    ):
        with pytest.raises(ValueError):
            create_plan(home, [method], tmp_path / "plan.json", snapshots=source)
    assert not (tmp_path / "plan.json").exists()


@pytest.mark.parametrize(
    "method",
    [
        "cloudflare-json",
        "zyte-product",
        "zyte-custom",
        "scrapegraph-extract",
        "firecrawl-json",
        "oxylabs-json",
        "browserbase-json",
    ],
)
def test_native_json_requests_send_snapshot_url_without_inline_html(method, monkeypatch):
    for key in METHODS[method]["keys"]:
        monkeypatch.setenv(key, "test-credential")
    options = {"input_route": "url"} if METHODS[method]["input"] != "url" else {}
    response = type("Response", (), {"status": 200, "headers": {}, "text": lambda _: "{}"})()
    with patch("harness.web_extract.pipeline.request", return_value=response) as transport:
        result = provider_call(
            method,
            "PRIVATE HTML SENTINEL",
            "https://original.example",
            "https://snapshots.example/version/case",
            native_options=options,
            schema=SCHEMA if method == "zyte-custom" else None,
        )
    body = transport.call_args.kwargs["json_body"]
    assert body["url"] == "https://snapshots.example/version/case"
    assert "PRIVATE HTML SENTINEL" not in json.dumps(body)
    assert "userHtml" not in json.dumps(body)
    assert result["request"]["input"] == "url"
    if method == "zyte-custom":
        assert "input_route" not in body.get("customAttributesOptions", {})


@pytest.mark.parametrize(
    "method,endpoint",
    [
        ("scrapfly-product", "https://api.scrapfly.io/scrape"),
        ("diffbot-product", "https://api.diffbot.com/v3/product"),
    ],
)
def test_native_product_url_routes_fetch_at_vendor_without_upload(method, endpoint, monkeypatch):
    from unittest.mock import MagicMock

    import harness.web_extract.pipeline as pipeline

    for key in METHODS[method]["keys"]:
        monkeypatch.setenv(key, "test-credential")
    session = MagicMock()
    response = session.get.return_value.__enter__.return_value
    response.raw.read.return_value = b"{}"
    response.status_code = 200
    response.headers = {}
    monkeypatch.setitem(pipeline._RAW_SESSIONS, method, session)
    result = provider_call(
        method,
        "PRIVATE HTML",
        "https://original.example",
        "https://snapshots.example/version/case",
        native_options={"input_route": "url"},
    )
    assert session.get.call_args.args[0].startswith(endpoint + "?")
    assert "data" not in session.get.call_args.kwargs
    assert result["request"]["effective_context_url"] == "https://snapshots.example/version/case"
    assert result["request"]["input"] == "url"
    session.post.assert_not_called()


def test_snapshot_build_requires_full_hf_dataset_commit(home, tmp_path):
    with pytest.raises(ValueError, match="full commit"):
        snapshots.build(
            {"example": home},
            tmp_path / "invalid",
            huggingface={"repository": "owner/data", "revision": "main"},
        )


def test_independent_services_share_only_the_navigation_index(home, tmp_path):
    value = binding(home, tmp_path)
    import copy

    second = copy.deepcopy(value["datasets"]["example"])
    second["homepage"] = "https://another-dataset.example"
    second["fixture_base"] = "https://another-version.example"
    second["immutable_origin"] = second["fixture_base"]
    value["datasets"]["second"] = second
    snapshots.build_index(value, tmp_path / "navigation")
    page = (tmp_path / "navigation/index.html").read_text(encoding="utf-8")
    assert "https://another-dataset.example" in page
    assert "https://snapshots.example" in page
    assert not list((tmp_path / "navigation").rglob("case-a.html"))
    with pytest.raises(ValueError, match="own snapshot service"):
        build_snapshot({"first": home, "second": home}, tmp_path / "combined")


def test_publication_requires_complete_same_version_online_verification(home, tmp_path):
    value = binding(home, tmp_path)
    item = value["datasets"]["example"]
    receipt = {
        "pages": [
            {
                "case_id": "case-a",
                "sha256": item["cases"]["case-a"],
                "url": item["fixture_base"] + "/case-a",
                "http_status": 200,
            }
        ]
    }
    metadata = snapshots.publication_metadata(home, value, receipt)
    assert snapshots.select(home, metadata)["fixture_base"] == item["fixture_base"]
    assert (
        metadata["snapshot_manifest"]["manifest_url"] == value["snapshot_manifest"]["manifest_url"]
    )
    value["hosting_provider"] = "cloudflare"
    managed = snapshots.publication_metadata(home, value, receipt)
    snapshots.save_json(tmp_path / "published-managed.json", managed)
    plan = create_plan(
        home,
        ["cloudflare-json"],
        tmp_path / "page-audit-plan.json",
        snapshots=tmp_path / "published-managed.json",
    )
    assert plan["public_snapshots"]["snapshot_manifest"]["hosting_provider"] == "cloudflare"
    wrong = {**value["snapshot_manifest"], "manifest_sha256": "wrong"}
    with pytest.raises(ValueError, match="dataset manifest changed"):
        snapshots.publication_metadata(home, {**value, "snapshot_manifest": wrong}, receipt)
    for row in [
        [],
        [{**receipt["pages"][0], "sha256": "incorrect"}],
        [{**receipt["pages"][0], "url": "https://different.example/case-a"}],
        receipt["pages"] * 2,
    ]:
        with pytest.raises(ValueError, match="every matching verified page"):
            snapshots.publication_metadata(home, value, {"pages": row})


def test_hf_publication_atomically_updates_only_card_and_binding():
    from unittest.mock import MagicMock

    from harness.benchmarks.publication import publish_snapshot_metadata

    api = MagicMock()
    revision = "a" * 40
    publish_snapshot_metadata(
        "owner/data",
        {"huggingface_source_revision": revision},
        parent_revision=revision,
        readme="Original source and license retained",
        api=api,
    )
    call = api.create_commit.call_args.kwargs
    assert call["parent_commit"] == revision
    assert {operation.path_in_repo for operation in call["operations"]} == {
        "README.md",
        "snapshots.json",
    }
    assert len(call["operations"]) == 2
    api.reset_mock()
    with pytest.raises(ValueError, match="source revision"):
        publish_snapshot_metadata(
            "owner/data",
            {"huggingface_source_revision": "other"},
            parent_revision=revision,
            readme="",
            api=api,
        )
    api.create_commit.assert_not_called()


def test_cloudflare_discovers_and_reuses_existing_dataset_service(home, tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "success": True,
        "result": {
            "canonical_deployment": {"id": "existing", "url": "https://version.dataset.pages.dev"}
        },
    }
    monkeypatch.setattr(
        "harness.web_fetch.config.repository_env",
        lambda: {"CLOUDFLARE_ACCOUNT_ID": "account", "CLOUDFLARE_API_TOKEN": "credential"},
    )
    monkeypatch.setattr(snapshots, "verify", lambda *a, **k: {"pages": [], "verified_pages": 1})
    with (
        patch("requests.get", side_effect=cf_lookup(response)) as get,
        patch("requests.post") as post,
        patch("subprocess.run") as deploy,
    ):
        result = snapshots.host(
            home, tmp_path / "host.json", provider="cloudflare", project="shared-dataset"
        )
    assert result["state"] == "reused_existing_deployment"
    assert result["fixture_base"].startswith("https://version.dataset.pages.dev/")
    assert get.call_args_list[0].args[0].endswith("/pages/projects/shared-dataset")
    post.assert_not_called()
    deploy.assert_not_called()


def test_external_host_requires_no_cloudflare_credentials(home, tmp_path, monkeypatch):
    monkeypatch.setattr(
        snapshots, "check_manifest", lambda *a: (_ for _ in ()).throw(ValueError("No manifest"))
    )
    monkeypatch.setattr(snapshots, "verify", lambda *a, **k: {"pages": [], "verified_pages": 1})
    with patch("harness.web_fetch.config.repository_env") as credentials:
        result = snapshots.host(
            home,
            tmp_path / "host.json",
            provider="external",
            public_base="https://other-service.example/frozen",
        )
    credentials.assert_not_called()
    assert result["state"] == "external_verified"
    assert result["fixture_base"] == "https://other-service.example/frozen"


def test_hf_cache_symlinks_are_read_but_changed_originals_block_publication(
    home, tmp_path, monkeypatch
):
    monkeypatch.setattr(snapshots, "hf_inventory", HF_INVENTORY)
    value = binding(home, tmp_path)
    item = value["datasets"]["example"]
    item["huggingface"] = {
        "repository": "owner/data",
        "input_path": "inputs.jsonl",
        "input_format": "html-jsonl",
    }
    receipt = {
        "pages": [
            {
                "case_id": "case-a",
                "sha256": item["cases"]["case-a"],
                "url": item["fixture_base"] + "/case-a",
                "http_status": 200,
            }
        ]
    }
    blob = tmp_path / "blob"
    html = (home / "dataset/html/case-a.html").read_text(encoding="utf-8")
    blob.write_text(
        json.dumps({"id": "case-a", "html": html, "html_sha256": digest(html.encode())}) + "\n",
        encoding="utf-8",
    )
    cached = tmp_path / "cached"
    cached.symlink_to(blob)
    readme = tmp_path / "README.md"
    readme.write_text(
        "Original source and license\n## Public HTML snapshots\nOld address\n"
        "## Citation\nKeep this citation",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "huggingface_hub.hf_hub_download",
        lambda repo, name, **kwargs: str(readme if name == "README.md" else cached),
    )
    from unittest.mock import MagicMock

    result = MagicMock(oid="new-revision")
    with patch(
        "harness.benchmarks.publication.publish_snapshot_metadata", return_value=result
    ) as publish:
        assert snapshots.publish(home, value, receipt, revision="a" * 40)["cases"] == 1
        assert "Original source and license" in publish.call_args.kwargs["readme"]
        assert "Keep this citation" in publish.call_args.kwargs["readme"]
        assert "Old address" not in publish.call_args.kwargs["readme"]
    changed = "Another page"
    blob.write_text(
        json.dumps({"id": "case-a", "html": changed, "html_sha256": digest(changed.encode())})
        + "\n",
        encoding="utf-8",
    )
    with patch("harness.benchmarks.publication.publish_snapshot_metadata") as publish:
        with pytest.raises(ValueError, match="HF originals differ"):
            snapshots.publish(home, value, receipt, revision="a" * 40)
        publish.assert_not_called()


def test_changed_cloudflare_snapshot_requires_approval_and_never_writes(
    home, tmp_path, monkeypatch
):
    from unittest.mock import MagicMock

    response = MagicMock(status_code=200)
    response.json.return_value = {
        "success": True,
        "result": {"canonical_deployment": {"id": "old", "url": "https://old.dataset.pages.dev"}},
    }
    monkeypatch.setattr(
        "harness.web_fetch.config.repository_env",
        lambda: {"CLOUDFLARE_ACCOUNT_ID": "account", "CLOUDFLARE_API_TOKEN": "credential"},
    )
    monkeypatch.setattr(
        snapshots, "check_manifest", MagicMock(side_effect=ValueError("Remote fixture differs"))
    )
    proposal = tmp_path / "proposal.json"
    with (
        patch("requests.get", side_effect=cf_lookup(response)),
        patch("requests.post") as create,
        patch("subprocess.run") as deploy,
    ):
        result = snapshots.host(home, proposal, provider="cloudflare", project="shared")
        assert result["state"] == "approval_required"
        create.assert_not_called()
        deploy.assert_not_called()
    saved = json.loads(proposal.read_text(encoding="utf-8"))
    assert saved["previous_deployment_id"] == "old"
    assert saved["huggingface"]["revision"] == "a" * 40
    response.json.return_value["result"]["canonical_deployment"]["id"] = "changed-after-proposal"
    with (
        patch("requests.get", side_effect=cf_lookup(response)),
        patch("requests.post") as create,
        patch("subprocess.run") as deploy,
    ):
        with pytest.raises(ValueError, match="changed since approval"):
            snapshots.host(
                home,
                tmp_path / "update.json",
                provider="cloudflare",
                project="shared",
                approve_plan=proposal,
            )
        create.assert_not_called()
        deploy.assert_not_called()


def test_hf_bound_immutable_deployment_uses_fast_identity_check(home, tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    item = binding(home, tmp_path)["datasets"]["example"]
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "success": True,
        "result": {"canonical_deployment": {"id": "existing", "url": "https://snapshots.example"}},
    }
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps(item), encoding="utf-8")
    monkeypatch.setattr("huggingface_hub.hf_hub_download", lambda *a, **k: str(metadata))
    monkeypatch.setattr(
        "harness.web_fetch.config.repository_env",
        lambda: {"CLOUDFLARE_ACCOUNT_ID": "account", "CLOUDFLARE_API_TOKEN": "credential"},
    )
    with (
        patch("requests.get", side_effect=cf_lookup(response)),
        patch.object(snapshots, "verify") as full_check,
        patch("subprocess.run") as deploy,
    ):
        result = snapshots.host(
            home, tmp_path / "host.json", provider="cloudflare", project="shared"
        )
    assert result["state"] == "reused_existing_deployment"
    full_check.assert_not_called()
    deploy.assert_not_called()
    assert (
        json.loads((tmp_path / "host.json").read_text(encoding="utf-8"))["verification"]["mode"]
        == "dataset_manifest"
    )

    hosted = snapshots.read_json(tmp_path / "host.json")
    metadata = snapshots.publication_metadata(home, hosted, hosted["verification"])
    snapshots.save_json(tmp_path / "published-cloudflare.json", metadata)
    plan = create_plan(
        home,
        ["cloudflare-json"],
        tmp_path / "cloudflare-plan.json",
        snapshots=tmp_path / "published-cloudflare.json",
    )
    assert plan["public_snapshots"]["hosting_provider"] == "cloudflare"
    assert plan["public_snapshots"]["snapshot_manifest"]["hosting_provider"] == "cloudflare"


@pytest.mark.parametrize(
    "receipt_mode", ["normal", "symlink", "unwritable", "persist-error", "broken-pipe"]
)
def test_approved_unchanged_proposal_deploys_and_checks_new_pages(
    home, tmp_path, monkeypatch, capsys, receipt_mode
):
    from unittest.mock import MagicMock

    old = MagicMock(status_code=200)
    old.json.return_value = {
        "success": True,
        "result": {"canonical_deployment": {"id": "old", "url": "https://old.dataset.pages.dev"}},
    }
    new = MagicMock(status_code=200)
    new.json.return_value = {
        "success": True,
        "result": {"canonical_deployment": {"id": "new", "url": "https://new.dataset.pages.dev"}},
    }
    monkeypatch.setattr(
        "harness.web_fetch.config.repository_env",
        lambda: {
            "CLOUDFLARE_ACCOUNT_ID": "account",
            "CLOUDFLARE_API_TOKEN": "credential",
            "UNRELATED_API_KEY": "must-not-forward",
        },
    )

    def verify(home, origin, hf):
        base = origin
        if "old.dataset" in base:
            raise ValueError("Remote fixture differs")
        return {
            "mode": "dataset_manifest",
            "fixture_base": base + "/example/" + snapshots.inventory(home)[0],
            "case_count": 1,
        }

    monkeypatch.setattr(snapshots, "check_manifest", verify)
    proposal = tmp_path / "proposal.json"
    with patch("requests.get", side_effect=cf_lookup(old)):
        assert (
            snapshots.host(home, proposal, provider="cloudflare", project="shared")["state"]
            == "approval_required"
        )
    output = tmp_path / "host.json"
    original_write = snapshots.save_json
    if receipt_mode == "symlink":
        linked = tmp_path / "linked"
        linked.symlink_to(tmp_path, target_is_directory=True)
        output = linked / "host.json"
    elif receipt_mode in {"unwritable", "persist-error"}:

        def write(path, data, **kwargs):
            if path == output and (
                (receipt_mode == "unwritable" and kwargs.get("exclusive"))
                or (
                    receipt_mode == "persist-error"
                    and data.get("state") == "deployed_pending_verification"
                )
            ):
                raise PermissionError("Synthetic unwritable receipt")
            return original_write(path, data, **kwargs)

        monkeypatch.setattr(snapshots, "save_json", write)
    if receipt_mode == "broken-pipe":

        def closed(*args, **kwargs):
            raise BrokenPipeError("Closed output")

        monkeypatch.setattr("builtins.print", closed)
    with (
        patch("requests.get", side_effect=cf_lookup(old, new)),
        patch("subprocess.run", return_value=MagicMock(returncode=0)) as deploy,
        patch("requests.post") as create_project,
    ):
        if receipt_mode != "normal":
            with pytest.raises((ValueError, PermissionError, BrokenPipeError)):
                snapshots.host(
                    home, output, provider="cloudflare", project="shared", approve_plan=proposal
                )
            if receipt_mode == "broken-pipe":
                assert deploy.called
                assert snapshots.read_json(output)["cloudflare"] == {
                    "project": "shared",
                    "deployment_id": "new",
                    "url": "https://new.dataset.pages.dev",
                }
            elif receipt_mode == "persist-error":
                assert deploy.called
                assert '"deployment_id": "new"' in capsys.readouterr().out
                assert snapshots.read_json(output)["state"] == "pending"
            else:
                deploy.assert_not_called()
                create_project.assert_not_called()
            return
        result = snapshots.host(
            home,
            output,
            provider="cloudflare",
            project="shared",
            approve_plan=proposal,
        )
    assert result["state"] == "deployed_new_input_version"
    assert result["fixture_base"].startswith("https://new.dataset.pages.dev/")
    assert "UNRELATED_API_KEY" not in deploy.call_args.kwargs["env"]
    assert "--commit-hash" in deploy.call_args.args[0]
    assert "a" * 40 in deploy.call_args.args[0]
    assert (
        json.loads((tmp_path / "host.json").read_text(encoding="utf-8"))["verification"][
            "case_count"
        ]
        == 1
    )


def test_stale_prepared_inputs_cannot_override_hf_latest(home, tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "hf_inventory", lambda *args: {"case-a": "different-hf-input"})
    with patch("requests.get") as cloudflare:
        with pytest.raises(ValueError, match="differs from HF latest"):
            snapshots.host(home, tmp_path / "host.json", provider="cloudflare")
        cloudflare.assert_not_called()


def test_selected_hf_edition_finds_matching_historical_deployment(home, tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    item = binding(home, tmp_path)["datasets"]["example"]
    item["fixture_base"] = item["immutable_origin"] = "https://older.dataset.pages.dev"
    metadata = tmp_path / "selected-hf-snapshots.json"
    metadata.write_text(json.dumps(item), encoding="utf-8")
    monkeypatch.setattr("huggingface_hub.hf_hub_download", lambda *a, **k: str(metadata))
    hf = MagicMock()
    hf.dataset_info.return_value.sha = "a" * 40
    monkeypatch.setattr("huggingface_hub.HfApi", lambda: hf)
    monkeypatch.setattr(
        "harness.web_fetch.config.repository_env",
        lambda: {"CLOUDFLARE_ACCOUNT_ID": "account", "CLOUDFLARE_API_TOKEN": "credential"},
    )

    def response(payload):
        value = MagicMock(status_code=200)
        value.json.return_value = {"success": True, **payload}
        return value

    project = response(
        {
            "result": {
                "canonical_deployment": {"id": "latest", "url": "https://latest.dataset.pages.dev"}
            }
        }
    )
    first = response(
        {
            "result": [{"id": "unrelated", "url": "https://unrelated.dataset.pages.dev"}],
            "result_info": {"total_pages": 2},
        }
    )
    second = response(
        {
            "result": [{"id": "older", "url": "https://older.dataset.pages.dev"}],
            "result_info": {"total_pages": 2},
        }
    )
    with (
        patch("requests.get", side_effect=[project, first, second]) as get,
        patch.object(snapshots, "verify") as full_check,
        patch("subprocess.run") as deploy,
    ):
        result = snapshots.host(home, tmp_path / "host.json", hf_revision="v1", project="shared")
    assert result["fixture_base"].startswith("https://older.dataset.pages.dev/")
    assert get.call_args_list[-1].kwargs["params"]["page"] == 2
    hf.dataset_info.assert_called_once_with("owner/data", revision="v1")
    full_check.assert_not_called()
    deploy.assert_not_called()


def test_entire_snapshot_is_identified_by_one_version_manifest(home, tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    monkeypatch.setattr(snapshots, "check_manifest", CHECK_MANIFEST)
    build_snapshot({"example": home}, tmp_path / "public")
    body = (tmp_path / "public/snapshots.json").read_bytes()
    response = MagicMock(
        status_code=200, content=body, url="https://immutable.example/snapshots.json"
    )
    response.json.return_value = json.loads(body)
    with (
        patch("requests.get", return_value=response) as fetch,
        patch.object(snapshots, "qualify_fixture") as page,
    ):
        receipt = snapshots.check_manifest(
            home, "https://immutable.example", {"repository": "owner/data", "revision": "a" * 40}
        )
    assert receipt["snapshot_huggingface"]["revision"] == "a" * 40
    assert receipt["manifest_sha256"] == digest(body)
    assert receipt["case_count"] == 1
    fetch.assert_called_once_with("https://immutable.example/snapshots.json", timeout=40)
    page.assert_not_called()
    for change in ["missing_version", "wrong_input", "wrong_file"]:
        metadata = json.loads(body)
        if change == "missing_version":
            metadata.pop("huggingface")
        elif change == "wrong_input":
            metadata["datasets"]["example"]["html_inventory_sha256"] = "wrong"
        else:
            metadata["files"][
                next(name for name in metadata["files"] if name.endswith("/case-a.html"))
            ] = "wrong"
        response.json.return_value = metadata
        with patch("requests.get", return_value=response), pytest.raises(ValueError):
            snapshots.check_manifest(
                home,
                "https://immutable.example",
                {"repository": "owner/data", "revision": "a" * 40},
            )


def test_external_hosting_remains_usable_without_cloudflare_manifest(home, tmp_path, monkeypatch):
    monkeypatch.setattr(
        snapshots, "check_manifest", lambda *a: (_ for _ in ()).throw(ValueError("No manifest"))
    )
    monkeypatch.setattr(snapshots, "verify", lambda *a, **k: {"verified_pages": 1, "pages": []})
    snapshots.host(
        home,
        tmp_path / "external.json",
        provider="external",
        public_base="https://custom.example/input",
    )
    plan = create_plan(
        home, ["cloudflare-json"], tmp_path / "plan.json", snapshots=tmp_path / "external.json"
    )
    assert plan["public_snapshots"]["hosting_provider"] == "external"
    assert "snapshot_manifest" not in plan["public_snapshots"]
    assert plan["public_snapshots"]["huggingface"]["revision"] == "a" * 40
    hosted = snapshots.read_json(tmp_path / "external.json")
    _, cases = snapshots.inventory(home)
    base = hosted["datasets"]["example"]["fixture_base"]
    verified = {
        "pages": [
            {"case_id": cid, "sha256": sha, "url": base + "/" + cid, "http_status": 200}
            for cid, sha in cases.items()
        ]
    }
    metadata = snapshots.publication_metadata(home, hosted, verified)
    snapshots.save_json(tmp_path / "published.json", metadata)
    restored = create_plan(
        home,
        ["cloudflare-json"],
        tmp_path / "restored-plan.json",
        snapshots=tmp_path / "published.json",
    )
    assert restored["public_snapshots"]["hosting_provider"] == "external"
    assert "snapshot_manifest" not in restored["public_snapshots"]


@pytest.mark.parametrize(
    "origin", ["https://external.example", "https://external.example/team/dataset"]
)
def test_external_host_manifest_output_is_usable_as_record_preview(
    home, tmp_path, monkeypatch, origin
):
    from unittest.mock import MagicMock

    from harness.web_extract.report import record_preview

    manifest = build_snapshot({"example": home}, tmp_path / "external-site")
    response = MagicMock(status_code=200)
    response.url = origin + "/snapshots.json"
    response.content = json.dumps(manifest).encode()
    response.json.return_value = manifest
    monkeypatch.setattr(snapshots, "check_manifest", CHECK_MANIFEST)
    with (
        patch("requests.get", return_value=response),
        patch.object(
            snapshots, "verify", return_value={"verified_pages": 1, "pages": []}
        ) as page_check,
    ):
        output = tmp_path / "external-manifest.json"
        snapshots.host(home, output, provider="external", public_base=origin)
        hosted = snapshots.read_json(output)
        base = hosted["snapshot_manifest"]["fixture_base"]
        preview = record_preview(
            home,
            {
                "snapshot_base": base,
                "preview_snapshot": {"path": str(output), "sha256": digest(output.read_bytes())},
            },
            local_example=False,
        )
    page_check.assert_called_once()
    assert preview == hosted["snapshot_manifest"]
    assert preview["case_count"] == 1
    assert hosted["hosting_provider"] == "external"
    with patch("requests.get", return_value=response) as fetch:
        metadata = snapshots.publication_metadata(home, hosted, preview)
    assert metadata["snapshot_manifest"] == preview
    assert metadata["hosting_provider"] == "external"
    published = tmp_path / "published-metadata.json"
    snapshots.save_json(published, metadata)
    plan = create_plan(
        home, ["cloudflare-json"], tmp_path / "roundtrip-plan.json", snapshots=published
    )
    assert plan["public_snapshots"]["hosting_provider"] == "external"
    assert plan["public_snapshots"]["snapshot_manifest"]["hosting_provider"] == "external"
    fetch.assert_called_once_with(origin + "/snapshots.json", timeout=40)


def test_snapshot_publication_reserves_receipt_before_remote_change(tmp_path, monkeypatch, capsys):
    from harness.web_extract.pipeline import read_json, save_json

    output = tmp_path / "receipt.json"
    calls = []

    def remote(*args, **kwargs):
        assert read_json(output)["state"] == "pending"
        calls.append(True)
        return {"revision": "c" * 40}

    monkeypatch.setattr(snapshots, "publish", remote)
    save_json(output, {"existing": True})
    with pytest.raises(FileExistsError):
        snapshots.publish_receipt(tmp_path, {}, {}, revision="a" * 40, output=output)
    assert not calls
    output.unlink()
    snapshots.publish_receipt(tmp_path, {}, {}, revision="a" * 40, output=output)
    assert read_json(output) == {"revision": "c" * 40}
    assert "c" * 40 in capsys.readouterr().out


def test_committed_hf_revision_is_emitted_even_if_receipt_finalization_fails(
    tmp_path, monkeypatch, capsys
):
    original = snapshots.save_json
    output = tmp_path / "receipt.json"

    def write(path, data, **kwargs):
        if kwargs.get("exclusive"):
            return original(path, data, **kwargs)
        raise OSError("Disk full after remote commit")

    monkeypatch.setattr(snapshots, "save_json", write)
    monkeypatch.setattr(snapshots, "publish", lambda *a, **kw: {"revision": "c" * 40})
    with pytest.raises(OSError, match="Disk full"):
        snapshots.publish_receipt(tmp_path, {}, {}, revision="a" * 40, output=output)
    assert "c" * 40 in capsys.readouterr().out
    assert snapshots.read_json(output)["state"] == "pending"


def test_committed_hf_receipt_is_saved_even_if_stdout_is_closed(tmp_path, monkeypatch):
    output = tmp_path / "receipt.json"
    monkeypatch.setattr(snapshots, "publish", lambda *a, **kw: {"revision": "c" * 40})

    def closed_pipe(*args, **kwargs):
        raise BrokenPipeError("Output consumer closed")

    monkeypatch.setattr("builtins.print", closed_pipe)
    with pytest.raises(BrokenPipeError):
        snapshots.publish_receipt(tmp_path, {}, {}, revision="a" * 40, output=output)
    assert snapshots.read_json(output) == {"revision": "c" * 40}


def test_new_snapshot_names_and_historical_input_decoding(home, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from harness.benchmarks.data import source_name_migrations

    manifest = build_snapshot({"example": home}, tmp_path / "canonical-site")
    canonical = "web-extract-public-snapshots/2"
    assert manifest["schema"] == canonical
    original = next(
        name for name, target in source_name_migrations()["schemas"].items() if target == canonical
    )
    manifest["schema"] = original
    raw = json.dumps(manifest).encode()
    origin = "https://snapshot.example"
    response = SimpleNamespace(
        status_code=200,
        url=origin + "/snapshots.json",
        content=raw,
        json=lambda: manifest,
        raise_for_status=lambda: None,
    )
    monkeypatch.setattr("requests.get", lambda *a, **k: response)
    receipt = CHECK_MANIFEST(home, origin, manifest["huggingface"])
    assert receipt["manifest_sha256"] == digest(raw)
    assert receipt["html_inventory_sha256"] == snapshots.inventory(home)[0]
    assert manifest["schema"] == original  # Decoding never rewrites the archived document.
    manifest["schema"] = "unrelated/2"
    with pytest.raises(ValueError, match="valid HF dataset version"):
        CHECK_MANIFEST(home, origin, manifest["huggingface"])


@pytest.mark.parametrize("value", [None, [], {}, 2])
def test_malformed_source_schema_is_not_a_known_format(value):
    from harness.benchmarks.data import canonical_source_name

    assert canonical_source_name("schemas", value) is None


@pytest.mark.parametrize(
    "base", ["http://example.org/archive", "https://u:p@example.org", "https://example.org?x=1"]
)
def test_external_host_rejects_unusable_base_before_remote_lookup(
    home, tmp_path, monkeypatch, base
):
    with patch.object(snapshots, "check_manifest") as lookup, pytest.raises(ValueError):
        snapshots.host(home, tmp_path / "output.json", provider="external", public_base=base)
    lookup.assert_not_called()


@pytest.mark.parametrize("expanded", [False, True])
def test_fixture_gzip_decodes_with_frozen_size_cap_inside_deadline(home, monkeypatch, expanded):
    import gzip
    import signal
    from types import SimpleNamespace

    from harness.web_extract import pipeline

    original = (home / "dataset/html/case-a.html").read_bytes()
    remote = original * 10000 if expanded else original
    url = "https://example.org/case-a"
    response = SimpleNamespace(
        status=200, headers={"content-encoding": "gzip"}, body=gzip.compress(remote), url=url
    )
    monkeypatch.setattr(pipeline, "request", lambda *a, **k: response)
    read = gzip.GzipFile.read
    sizes = []

    def bounded(stream, size=-1):
        assert signal.getitimer(signal.ITIMER_REAL)[0] > 0
        sizes.append(size)
        return read(stream, size)

    monkeypatch.setattr(gzip.GzipFile, "read", bounded)
    if expanded:
        with pytest.raises(ValueError, match="differs from frozen HTML"):
            pipeline.qualify_fixture(home, "https://example.org", "case-a")
    else:
        assert pipeline.qualify_fixture(home, "https://example.org", "case-a")["sha256"] == digest(
            original
        )
    assert sizes == [len(original) + 1]


@pytest.mark.parametrize("manifest_error", [None, "timeout"])
def test_external_host_checks_page_bytes_even_with_a_matching_manifest(
    home, tmp_path, monkeypatch, manifest_error
):
    import requests

    if manifest_error:
        monkeypatch.setattr(
            snapshots, "check_manifest", lambda *a: (_ for _ in ()).throw(requests.Timeout())
        )
    with patch.object(
        snapshots, "verify", side_effect=ValueError("Remote fixture differs")
    ) as verify:
        with pytest.raises(ValueError, match="Remote fixture differs"):
            snapshots.host(
                home,
                tmp_path / "host.json",
                provider="external",
                public_base="https://external.example",
            )
    verify.assert_called_once()


def test_managed_manifest_network_error_does_not_propose_redeployment(home, tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    import requests

    item = binding(home, tmp_path)["datasets"]["example"]
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "success": True,
        "result": {"canonical_deployment": {"id": "existing", "url": "https://snapshots.example"}},
    }
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps(item), encoding="utf-8")
    monkeypatch.setattr("huggingface_hub.hf_hub_download", lambda *a, **k: str(metadata))
    monkeypatch.setattr(
        "harness.web_fetch.config.repository_env",
        lambda: {"CLOUDFLARE_ACCOUNT_ID": "account", "CLOUDFLARE_API_TOKEN": "credential"},
    )
    monkeypatch.setattr(
        snapshots,
        "check_manifest",
        lambda *a: (_ for _ in ()).throw(requests.Timeout("Network unavailable")),
    )
    output = tmp_path / "no-proposal.json"
    with patch("requests.get", side_effect=cf_lookup(response)), patch("subprocess.run") as deploy:
        with pytest.raises(requests.Timeout):
            snapshots.host(home, output, provider="cloudflare", project="shared")
        deploy.assert_not_called()
    assert not output.exists()


@pytest.mark.parametrize("status", [404, 401, 403, 500])
def test_existing_deployment_missing_manifest_requires_approval_not_a_network_bypass(
    home, tmp_path, monkeypatch, status
):
    from unittest.mock import MagicMock

    import requests

    item = binding(home, tmp_path)["datasets"]["example"]
    project = MagicMock(status_code=200)
    project.json.return_value = {
        "success": True,
        "result": {"canonical_deployment": {"id": "existing", "url": "https://snapshots.example"}},
    }
    metadata = tmp_path / "metadata.json"
    snapshots.save_json(metadata, item)
    monkeypatch.setattr("huggingface_hub.hf_hub_download", lambda *a, **k: str(metadata))
    monkeypatch.setattr(
        "harness.web_fetch.config.repository_env",
        lambda: {"CLOUDFLARE_ACCOUNT_ID": "account", "CLOUDFLARE_API_TOKEN": "synthetic"},
    )
    monkeypatch.setattr(snapshots, "check_manifest", CHECK_MANIFEST)
    missing = requests.Response()
    missing.status_code = status
    missing.url = "https://snapshots.example/snapshots.json"
    output = tmp_path / "proposal.json"
    with (
        patch("requests.get", side_effect=cf_lookup(project, missing)),
        patch("subprocess.run") as deploy,
    ):
        if status == 404:
            result = snapshots.host(home, output, provider="cloudflare", project="shared")
            assert result["state"] == "approval_required"
        else:
            with pytest.raises(requests.HTTPError):
                snapshots.host(home, output, provider="cloudflare", project="shared")
            assert not output.exists()
        deploy.assert_not_called()
    if status == 404:
        with (
            patch("requests.get", side_effect=cf_lookup(project, missing)),
            patch(
                "subprocess.run", side_effect=RuntimeError("Synthetic approved deployment")
            ) as deploy,
        ):
            with pytest.raises(RuntimeError, match="Synthetic approved deployment"):
                snapshots.host(
                    home,
                    tmp_path / "approved.json",
                    provider="cloudflare",
                    project="shared",
                    approve_plan=output,
                )
            deploy.assert_called_once()
