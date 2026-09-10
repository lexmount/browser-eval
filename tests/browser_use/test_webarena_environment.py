from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from harness.browser_use.suite import BrowserTask
from harness.browser_use.webarena_environment import WebArenaEnvironment, _DockerClientBackend


def _task():
    return BrowserTask(
        task_id="webarena-4",
        title="Task",
        tool_calls=(),
        assertions=(),
        tags=(),
        difficulty="simple",
        ground_truth={"expected_actions": []},
        source={"url": "https://example.test"},
        evaluator={"kind": "webarena_verified", "task_id": 4, "revision": 2},
        environment={"kind": "webarena_verified", "sites": ["shopping_admin"]},
        trajectory={"schema": "lex_browser_trajectory/1", "actions": []},
    )


class _Image:
    id = "sha256:local-image"

    def __init__(self, digests):
        self.attrs = {"RepoDigests": digests}


class _Container:
    id = "container-id"
    status = "running"

    def __init__(self, digests):
        self.image = _Image(digests)
        self.exec_calls = []

    def reload(self):
        return None

    def exec_run(self, command, demux=False):
        self.exec_calls.append((command, demux))
        return SimpleNamespace(
            exit_code=0,
            output=b'{"success": true, "message": "configured", "details": {}}',
        )


class _Containers:
    def __init__(self, container, name="webarena_verified_shopping_admin"):
        self.container = container
        self.name = name

    def get(self, name):
        assert name == self.name
        return self.container


class _Docker:
    def __init__(self, digests, name="webarena_verified_shopping_admin"):
        self.containers = _Containers(_Container(digests), name)

    def info(self):
        return {"Architecture": "arm64"}


def _environment(tmp_path):
    pinned = "am1n3e/webarena-verified-shopping_admin@sha256:" + "a" * 64
    lock = tmp_path / "images.json"
    lock.write_text(
        json.dumps(
            {
                "schema": "lex_webarena_environment_images/1",
                "images": {"shopping_admin": {"pinned": pinned}},
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "image_lock": "images.json",
                "environments": {
                    "__SHOPPING_ADMIN__": {
                        "urls": ["http://host.docker.internal:17780/admin"]
                    }
                },
                "control_urls": {"shopping_admin": "http://127.0.0.1:17781"},
            }
        ),
        encoding="utf-8",
    )
    return WebArenaEnvironment(config), pinned


def test_runtime_verification_binds_running_container_to_image_lock(tmp_path):
    environment, pinned = _environment(tmp_path)

    result = environment.verify_runtime(_Docker([pinned]), (_task(),))

    assert result["sites"]["shopping_admin"]["pinned_image"] == pinned
    assert len(result["image_lock_sha256"]) == 64


def test_runtime_verification_rejects_mutable_or_wrong_image(tmp_path):
    environment, _ = _environment(tmp_path)

    with pytest.raises(RuntimeError, match="image mismatch"):
        environment.verify_runtime(_Docker([]), (_task(),))


def test_site_docker_client_defaults_or_uses_validated_unix_socket(tmp_path, monkeypatch):
    environment, _ = _environment(tmp_path)
    default_client = object()

    assert environment.site_docker_client(default_client) == (default_client, False)

    environment.config["site_docker_host"] = "tcp://docker.example:2375"
    with pytest.raises(ValueError, match="absolute unix socket"):
        environment.site_docker_client(default_client)

    created = SimpleNamespace(ping=lambda: True)
    monkeypatch.setattr(
        "harness.browser_use.webarena_environment.docker.DockerClient",
        lambda *, base_url, timeout: (
            created
            if base_url == "unix:///run/user/1016/docker.sock" and timeout == 900
            else pytest.fail("wrong Docker socket")
        ),
    )
    environment.config["site_docker_host"] = "unix:///run/user/1016/docker.sock"

    assert environment.site_docker_client(default_client) == (created, True)


def test_recorded_action_origins_are_materialized_from_runtime_config(tmp_path):
    environment, _ = _environment(tmp_path)
    environment.environments.update(
        {
            "__GITLAB__": {"urls": ["http://10.2.131.41:18023"]},
            "__REDDIT__": {"urls": ["http://10.2.131.41:19999"]},
        }
    )
    task = BrowserTask(
        task_id="webarena-682",
        title="Cross-site copy",
        tool_calls=(
            {
                "action": "fill",
                "text": (
                    "http://ec2-3-135-39-80.us-east-2.compute.amazonaws.com:8023/"
                    "convexegg/chatgpt"
                ),
                "target": {
                    "by": "playwright",
                    "value": (
                        'internal:attr=[title="http://old.example:9999/f/technology"i]'
                    ),
                },
            },
        ),
        assertions=(),
        tags=("mutate",),
        difficulty="simple",
        ground_truth={"expected_actions": ["fill"]},
        source={},
        evaluator={"kind": "webarena_verified"},
        environment={"kind": "webarena_verified", "sites": ["gitlab", "reddit"]},
        trajectory={"schema": "lex_browser_trajectory/1", "actions": []},
    )

    materialized = environment.materialize_task_actions(task)

    assert materialized.tool_calls[0]["text"] == (
        "http://10.2.131.41:18023/convexegg/chatgpt"
    )
    assert materialized.tool_calls[0]["target"]["value"] == (
        'internal:attr=[title="http://10.2.131.41:19999/f/technology"i]'
    )
    assert materialized.trajectory is task.trajectory


def test_unrelated_external_urls_are_not_rewritten(tmp_path):
    environment, _ = _environment(tmp_path)

    assert environment.rewrite_legacy_origins("https://example.com:8443/path") == (
        "https://example.com:8443/path"
    )


def test_admin_origin_rewrite_does_not_duplicate_the_admin_base_path(tmp_path):
    environment, _ = _environment(tmp_path)

    rewritten = environment.rewrite_legacy_origins(
        "http://ec2-3-135-39-80.us-east-2.compute.amazonaws.com:7780/"
        "admin/review/product/index"
    )

    assert rewritten == "http://host.docker.internal:17780/admin/review/product/index"


def test_stateful_reset_recreates_container_from_pinned_image(tmp_path, monkeypatch):
    environment, pinned = _environment(tmp_path)
    starts = []

    class Manager:
        container_name = "webarena_verified_shopping_admin"

        def __init__(self, **kwargs):
            assert kwargs["config"].docker_img == pinned
            assert kwargs["backend"].client is docker_client

        def start(self, **kwargs):
            starts.append(kwargs)
            return SimpleNamespace(
                url="http://host.docker.internal:17780",
                env_ctrl_url="http://host.docker.internal:17781",
            )

    docker_client = _Docker([pinned])
    monkeypatch.setattr("harness.browser_use.webarena_environment.ContainerManager", Manager)
    monkeypatch.setattr(environment, "_wait_ready", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(environment, "_wait_site_ready", lambda *_args, **_kwargs: None)

    result = environment.reset(_task(), docker_client)

    assert starts[0]["wait"] is False
    assert starts[0]["port"] == 17780
    assert starts[0]["env_ctrl_port"] == 17781
    assert result["sites"]["shopping_admin"]["reset"] == ("container_recreated_from_pinned_image")
    assert result["sites"]["shopping_admin"]["repo_digests"] == [pinned]
    assert result["sites"]["shopping_admin"]["external_url_configuration"] == {
        "external_url": "http://host.docker.internal:17780"
    }
    assert docker_client.containers.container.exec_calls == [
        (
            [
                "env-ctrl",
                "--verbose",
                "init",
                "--base-url",
                "http://host.docker.internal:17780",
            ],
            False,
        )
    ]


def test_gitlab_reset_preserves_internal_port_and_sets_external_url(tmp_path, monkeypatch):
    pinned = "am1n3e/webarena-verified-gitlab@sha256:" + "b" * 64
    lock = tmp_path / "images.json"
    lock.write_text(
        json.dumps(
            {
                "schema": "lex_webarena_environment_images/1",
                "images": {"gitlab": {"pinned": pinned}},
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "image_lock": "images.json",
                "docker_site_hostname": "10.2.131.41",
                "environments": {
                    "__GITLAB__": {"urls": ["http://10.2.131.41:18023"]}
                },
                "control_urls": {"gitlab": "http://127.0.0.1:18024"},
            }
        ),
        encoding="utf-8",
    )
    environment = WebArenaEnvironment(config)
    task = BrowserTask(
        task_id="webarena-1",
        title="Task",
        tool_calls=(),
        assertions=(),
        tags=(),
        difficulty="simple",
        ground_truth={"expected_actions": []},
        source={"url": "https://example.test"},
        evaluator={"kind": "webarena_verified", "task_id": 1, "revision": 2},
        environment={"kind": "webarena_verified", "sites": ["gitlab"]},
        trajectory={"schema": "lex_browser_trajectory/1", "actions": []},
    )
    captured = {}

    class Manager:
        container_name = "webarena_verified_gitlab"

        def __init__(self, **kwargs):
            captured["config"] = kwargs["config"]

        def start(self, **kwargs):
            return SimpleNamespace(
                url="http://10.2.131.41:18023",
                env_ctrl_url="http://10.2.131.41:18024",
            )

    docker_client = _Docker([pinned], "webarena_verified_gitlab")
    monkeypatch.setattr("harness.browser_use.webarena_environment.ContainerManager", Manager)
    monkeypatch.setattr(environment, "_wait_ready", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(environment, "_wait_site_ready", lambda *_args, **_kwargs: None)

    environment.reset(task, docker_client)

    assert captured["config"].host_port == 18023
    assert captured["config"].container_port == 8023
    assert docker_client.containers.container.exec_calls == [
        (
            [
                "env-ctrl",
                "--verbose",
                "init",
                "--base-url",
                "http://10.2.131.41:18023",
            ],
            False,
        )
    ]


@pytest.mark.parametrize("site", ["shopping_admin", "shopping", "reddit", "gitlab"])
def test_stateful_backend_disables_duplicate_entrypoint_reconfigure(site):
    calls = []

    class Containers:
        def run(self, *args, **kwargs):
            calls.append((args, kwargs))

    backend = _DockerClientBackend(SimpleNamespace(containers=Containers()))

    backend.container_run(
        name=f"webarena_verified_{site}",
        image=f"{site}@sha256:digest",
        port_mappings={18023: 8023, 18024: 8877},
        volume_mappings={},
        env_vars={"WA_ENV_CTRL_EXTERNAL_SITE_URL": "http://10.2.131.41:18023"},
    )

    assert calls[0][1]["ports"] == {"8023/tcp": 18023, "8877/tcp": 18024}
    assert calls[0][1]["environment"] == {
        "WA_ENV_CTRL_EXTERNAL_SITE_URL": "http://10.2.131.41:18023",
        "WA_ENV_CTRL_SKIP_RECONFIGURE": "true",
    }


def test_stateful_reset_requires_explicit_distinct_ports(tmp_path):
    environment, _ = _environment(tmp_path)
    environment.environments["__SHOPPING_ADMIN__"]["urls"] = ["http://localhost/admin"]

    with pytest.raises(ValueError, match="explicit port"):
        environment.reset(_task(), _Docker([]))

    environment.environments["__SHOPPING_ADMIN__"]["urls"] = ["http://localhost:17781/admin"]
    with pytest.raises(ValueError, match="must differ"):
        environment.reset(_task(), _Docker([]))


def test_read_only_reset_checks_public_site_without_requiring_control_plane(
    tmp_path, monkeypatch
):
    pinned = "am1n3e/webarena-verified-map@sha256:" + "c" * 64
    lock = tmp_path / "images.json"
    lock.write_text(
        json.dumps(
            {
                "schema": "lex_webarena_environment_images/1",
                "images": {"map": {"pinned": pinned, "architecture": "amd64"}},
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "image_lock": "images.json",
                "environments": {"__MAP__": {"urls": ["http://map.test:23000"]}},
                "control_urls": {"map": "http://127.0.0.1:23001"},
            }
        ),
        encoding="utf-8",
    )
    environment = WebArenaEnvironment(config)
    task = BrowserTask(
        task_id="webarena-map",
        title="Map task",
        tool_calls=(),
        assertions=(),
        tags=(),
        difficulty="simple",
        ground_truth={"expected_actions": []},
        source={"url": "https://example.test"},
        evaluator={"kind": "webarena_verified", "task_id": 1, "revision": 2},
        environment={"kind": "webarena_verified", "sites": ["map"]},
        trajectory={"schema": "lex_browser_trajectory/1", "actions": []},
    )
    site_checks = []
    monkeypatch.setattr(
        environment,
        "_wait_ready",
        lambda *_args, **_kwargs: pytest.fail("read-only site must not require env-ctrl"),
    )
    monkeypatch.setattr(
        environment,
        "_wait_site_ready",
        lambda url, **_kwargs: site_checks.append(url),
    )

    result = environment.reset(task, object())

    assert site_checks == ["http://map.test:23000"]
    assert result["sites"]["map"] == {
        "mode": "local_docker",
        "reset": "site_health_check_only",
    }


def test_shopping_admin_login_waits_for_navigation_commit_then_reloads(tmp_path):
    environment, _ = _environment(tmp_path)
    environment.environments["__SHOPPING_ADMIN__"]["credentials"] = {
        "username": "fixture-user",
        "password": "fixture-password",
    }
    calls = []

    class Field:
        def __init__(self, page, name):
            self.page = page
            self.name = name

        def count(self):
            return 0 if self.page.logged_in and self.name == "user name" else 1

        def fill(self, value):
            calls.append(("fill", self.name, value))

    class Button:
        def __init__(self, page):
            self.page = page

        def click(self, **kwargs):
            calls.append(("click", kwargs))
            self.page.logged_in = True

    class Navigation:
        def __enter__(self):
            calls.append(("expect_navigation_enter",))

        def __exit__(self, *_args):
            calls.append(("expect_navigation_exit",))

    class Page:
        logged_in = False

        def goto(self, url, **kwargs):
            calls.append(("goto", url, kwargs))

        def get_by_placeholder(self, name):
            return Field(self, name)

        def get_by_role(self, role, **kwargs):
            assert role == "button"
            assert kwargs == {"name": "Sign in", "exact": False}
            return Button(self)

        def expect_navigation(self, **kwargs):
            calls.append(("expect_navigation", kwargs))
            return Navigation()

    environment._login_shopping_admin(Page())

    assert calls == [
        (
            "goto",
            "http://host.docker.internal:17780/admin",
            {"wait_until": "domcontentloaded", "timeout": 60_000},
        ),
        ("fill", "user name", "fixture-user"),
        ("fill", "password", "fixture-password"),
        ("expect_navigation", {"wait_until": "commit", "timeout": 120_000}),
        ("expect_navigation_enter",),
        ("click", {"no_wait_after": True, "timeout": 30_000}),
        ("expect_navigation_exit",),
        (
            "goto",
            "http://host.docker.internal:17780/admin",
            {"wait_until": "domcontentloaded", "timeout": 60_000},
        ),
    ]


def test_site_readiness_requires_two_successful_browser_facing_requests(monkeypatch):
    calls = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def urlopen(request, timeout):
        calls.append((request.full_url, timeout))
        return Response()

    monkeypatch.setattr("harness.browser_use.webarena_environment.urllib.request.urlopen", urlopen)
    monkeypatch.setattr("harness.browser_use.webarena_environment.time.sleep", lambda *_: None)

    WebArenaEnvironment._wait_site_ready("http://site.test/", timeout_seconds=1)

    assert calls == [("http://site.test/", 30), ("http://site.test/", 30)]


def test_credentials_resolve_from_environment_without_persisting_values(tmp_path, monkeypatch):
    environment, _ = _environment(tmp_path)
    config = environment.environments["__SHOPPING_ADMIN__"]
    config["credentials_env"] = {
        "username": "TEST_WEBARENA_USERNAME",
        "password": "TEST_WEBARENA_PASSWORD",
    }
    monkeypatch.setenv("TEST_WEBARENA_USERNAME", "fixture-user")
    monkeypatch.setenv("TEST_WEBARENA_PASSWORD", "fixture-password")

    assert environment._credentials("shopping_admin") == {
        "username": "fixture-user",
        "password": "fixture-password",
    }
    assert "fixture-user" not in environment.config_path.read_text()
    assert "fixture-password" not in environment.config_path.read_text()


def test_credentials_fail_closed_on_missing_or_conflicting_sources(tmp_path, monkeypatch):
    environment, _ = _environment(tmp_path)
    config = environment.environments["__SHOPPING_ADMIN__"]
    config["credentials_env"] = {
        "username": "TEST_WEBARENA_USERNAME",
        "password": "TEST_WEBARENA_PASSWORD",
    }
    monkeypatch.setenv("TEST_WEBARENA_USERNAME", "fixture-user")

    with pytest.raises(ValueError, match="TEST_WEBARENA_PASSWORD"):
        environment._credentials("shopping_admin")

    config["credentials"] = {"username": "duplicate", "password": "duplicate"}
    with pytest.raises(ValueError, match="configured twice"):
        environment._credentials("shopping_admin")


def test_environment_tokens_resolve_exact_urls_and_path_suffixes(tmp_path):
    environment, _ = _environment(tmp_path)

    assert environment.resolve_url("__SHOPPING_ADMIN__") == (
        "http://host.docker.internal:17780/admin"
    )
    assert environment.resolve_url("__SHOPPING_ADMIN__/catalog/product/edit/id/1481/") == (
        "http://host.docker.internal:17780/admin/catalog/product/edit/id/1481/"
    )
    assert environment.resolve_url("https://public.example/path") == (
        "https://public.example/path"
    )


class _LoginLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def count(self):
        return 0 if self.page.authenticated else 1

    def fill(self, value):
        self.page.actions.append(("fill", self.selector, value))

    def click(self, **kwargs):
        self.page.actions.append(("click", self.selector, kwargs))
        self.page.authenticated = True

    def wait_for(self, **kwargs):
        self.page.actions.append(("wait_for", self.selector, kwargs))

    def all_inner_texts(self):
        return []


class _LoginPage:
    def __init__(self):
        self.actions = []
        self.authenticated = False
        self.url = ""

    def goto(self, url, **_kwargs):
        self.url = url
        self.actions.append(("goto", url))

    def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def expect_navigation(self, **kwargs):
        self.actions.append(("expect_navigation", kwargs))

        class Navigation:
            def __enter__(self):
                return None

            def __exit__(self, *_args):
                return False

        return Navigation()

    def get_by_label(self, value, **_kwargs):
        return _LoginLocator(self, f"label:{value}")

    def get_by_placeholder(self, value, **_kwargs):
        return _LoginLocator(self, f"placeholder:{value}")

    def get_by_role(self, role, **kwargs):
        return _LoginLocator(self, f"role:{role}:{kwargs.get('name')}")

    def locator(self, selector):
        return _LoginLocator(self, selector)


def test_official_form_login_paths_cover_all_stateful_sites(tmp_path, monkeypatch):
    lock = tmp_path / "images.json"
    lock.write_text(
        json.dumps({"schema": "lex_webarena_environment_images/1", "images": {}}),
        encoding="utf-8",
    )
    environments = {}
    variables = {}
    for site in ("shopping", "shopping_admin", "reddit", "gitlab"):
        upper = site.upper()
        username_variable = f"TEST_{upper}_USERNAME"
        password_variable = f"TEST_{upper}_PASSWORD"
        variables[username_variable] = f"{site}-user"
        variables[password_variable] = f"{site}-password"
        environments[f"__{upper}__"] = {
            "urls": [f"http://{site}.test"],
            "credentials_env": {
                "username": username_variable,
                "password": password_variable,
            },
        }
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"image_lock": "images.json", "environments": environments}),
        encoding="utf-8",
    )
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    environment = WebArenaEnvironment(config)

    pages = {}
    for site in ("shopping", "shopping_admin", "reddit", "gitlab"):
        page = _LoginPage()
        getattr(environment, f"_login_{site}")(page)
        pages[site] = page

    assert pages["shopping"].url == "http://shopping.test/wishlist/"
    assert pages["shopping_admin"].authenticated is True
    assert pages["reddit"].url == "http://reddit.test/user/reddit-user/account"
    assert pages["gitlab"].authenticated is True
    assert ("fill", "label:Email", "shopping-user") in pages["shopping"].actions
    assert ("fill", "placeholder:user name", "shopping_admin-user") in pages[
        "shopping_admin"
    ].actions
    assert ("fill", "label:Username", "reddit-user") in pages["reddit"].actions
    assert ("fill", "#user_login", "gitlab-user") in pages["gitlab"].actions
    assert (
        "expect_navigation",
        {"wait_until": "commit", "timeout": 120_000},
    ) in pages["shopping_admin"].actions
    assert pages["shopping_admin"].actions.count(
        ("goto", "http://shopping_admin.test")
    ) == 2
