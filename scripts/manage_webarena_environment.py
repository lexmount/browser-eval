"""Start pinned WebArena-Verified sites and record their concrete runtime identity."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import docker
from webarena_verified.environments.container.config import get_container_config
from webarena_verified.environments.container.manager import ContainerManager
from webarena_verified.environments.setup import setup_init
from webarena_verified.types.task import WebArenaSite

from harness.browser_use.webarena_environment import (
    _DockerClientBackend,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _locked_image(lock_path: Path, site: str) -> dict:
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    if payload.get("schema") != "lex_webarena_environment_images/1":
        raise ValueError("invalid WebArena environment image lock")
    return dict(payload["images"][site])


def _manager(
    site_name: str,
    lock_path: Path,
    *,
    allow_emulation: bool = False,
    hostname: str = "localhost",
    port: int | None = None,
    control_port: int | None = None,
) -> ContainerManager:
    site = WebArenaSite(site_name)
    locked = _locked_image(lock_path, site_name)
    daemon_arch = str(docker.from_env().info().get("Architecture") or "")
    aliases = {"aarch64": "arm64", "x86_64": "amd64"}
    daemon_arch = aliases.get(daemon_arch, daemon_arch)
    image_arch = str(locked.get("architecture") or "")
    if not allow_emulation and image_arch and image_arch != daemon_arch:
        raise RuntimeError(
            f"{site_name} image requires native {image_arch}; Docker daemon is {daemon_arch}. "
            "Cross-architecture execution is disabled because it can change behavior or crash."
        )
    updates: dict[str, object] = {"docker_img": str(locked["pinned"])}
    if port is not None:
        updates["host_port"] = port
    if control_port is not None:
        updates["host_env_ctrl_port"] = control_port
    config = get_container_config(site=site).model_copy(update=updates)
    return ContainerManager(
        site=site,
        config=config,
        backend=_DockerClientBackend(docker.from_env()),
        hostname=hostname,
    )


def _write_receipt(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    def port_number(value: str) -> int:
        port = int(value)
        if not 1 <= port <= 65535:
            raise argparse.ArgumentTypeError("port must be between 1 and 65535")
        return port

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("setup", "start", "status", "stop"))
    parser.add_argument(
        "--site",
        required=True,
        choices=("shopping_admin", "shopping", "reddit", "gitlab", "wikipedia", "map"),
    )
    parser.add_argument(
        "--image-lock",
        type=Path,
        default=Path("evaluation/browser-use-public/webarena-environment-images.json"),
    )
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--port", type=port_number)
    parser.add_argument("--control-port", type=port_number)
    parser.add_argument("--allow-emulation", action="store_true")
    parser.add_argument("--hostname", default="localhost")
    args = parser.parse_args()
    site = WebArenaSite(args.site)
    if (args.port is None) != (args.control_port is None):
        parser.error("--port and --control-port must be provided together")
    if args.port is not None and args.port == args.control_port:
        parser.error("--port and --control-port must differ")

    if args.command == "setup":
        if args.site not in {"wikipedia", "map"} or args.data_dir is None:
            parser.error("setup requires --site wikipedia|map and --data-dir")
        result = setup_init(site=site, data_dir=args.data_dir)
        files = [
            {
                "name": path.name,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in sorted(args.data_dir.iterdir())
            if path.is_file()
        ]
        receipt = {"command": "setup", "site": args.site, "result": result, "files": files}
    else:
        manager = _manager(
            args.site,
            args.image_lock,
            allow_emulation=args.allow_emulation or args.command != "start",
            hostname=args.hostname,
            port=args.port,
            control_port=args.control_port,
        )
        if args.command == "start":
            config = manager.config
            started = manager.start(
                port=int(config.host_port),
                env_ctrl_port=int(config.host_env_ctrl_port),
                timeout=args.timeout,
                data_dir=args.data_dir,
            )
            container = docker.from_env().containers.get(manager.container_name)
            container.reload()
            daemon_architecture = docker.from_env().info().get("Architecture")
            receipt = {
                "command": "start",
                "site": args.site,
                "requested_image": config.docker_img,
                "container_name": manager.container_name,
                "container_id": container.id,
                "image_id": container.image.id,
                "repo_digests": sorted(container.image.attrs.get("RepoDigests") or []),
                "daemon_architecture": daemon_architecture,
                "site_url": started.url,
                "control_url": started.env_ctrl_url,
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        elif args.command == "status":
            receipt = manager.status().model_dump(mode="json")
        else:
            manager.stop()
            receipt = {"command": "stop", "site": args.site}
    if args.receipt:
        _write_receipt(args.receipt, receipt)
    print(json.dumps(receipt, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
