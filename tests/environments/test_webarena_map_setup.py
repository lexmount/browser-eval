"""Deployment scenarios: exact data, interrupted transfer, disk reserve and unsafe tar."""

import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from unittest.mock import patch

import pytest

from harness.environments.webarena.map_setup import (
    MAPPINGS,
    configure_browser_assets,
    configure_tiles,
    extract_archive,
    require_space,
    restore_compressed,
    serve,
    setup,
    transport_size,
    verify_archive,
    version_browser_assets,
)


def archive_at(path, members):
    with tarfile.open(path, "w") as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


def test_remote_browsers_receive_same_origin_urls_with_or_without_gzip(tmp_path):
    source = tmp_path / "application.js"
    source.write_bytes(b'const search="/nominatim/", route="/osrm/";')
    packed = tmp_path / "application.js.gz"
    packed.write_bytes(gzip.compress(b'const search="http://localhost:8080/nominatim/";'))
    configure_browser_assets(tmp_path)
    assert gzip.decompress(packed.read_bytes()) == source.read_bytes()
    previous = packed.read_bytes()
    configure_browser_assets(tmp_path)
    assert packed.read_bytes() == previous
    source.write_bytes(b'const route="http://localhost:8080/osrm/";')
    with pytest.raises(ValueError, match="localhost"):
        configure_browser_assets(tmp_path)


def test_corrupt_precompressed_script_is_rebuilt_from_valid_script(tmp_path):
    source = tmp_path / "application.js"
    source.write_bytes(b'const search="/nominatim/";')
    packed = tmp_path / "application.js.gz"
    packed.write_bytes(b"damaged gzip data")

    configure_browser_assets(tmp_path)

    assert gzip.decompress(packed.read_bytes()) == source.read_bytes()


def test_patched_browser_script_gets_a_new_url(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    old = "application-" + "a" * 64 + ".js"
    content = b'const route="/osrm/";'
    (assets / old).write_bytes(content)
    (assets / (old + ".gz")).write_bytes(gzip.compress(content))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "assets": {"application.js": old},
        "files": {old: {"logical_path": "application.js", "digest": "a" * 64,
                        "size": 123, "integrity": "old"}},
    }))
    version_browser_assets(assets, manifest)
    updated = json.loads(manifest.read_text())
    name = updated["assets"]["application.js"]
    assert name != old
    assert name == "application-" + hashlib.sha256(content).hexdigest() + ".js"
    assert (assets / name).read_bytes() == content
    assert gzip.decompress((assets / (name + ".gz")).read_bytes()) == content
    assert updated["files"][name]["digest"] == hashlib.sha256(content).hexdigest()
    before = manifest.read_bytes()
    version_browser_assets(assets, manifest)
    assert manifest.read_bytes() == before


def test_restores_database_and_sparse_flatnodes_without_changing_bytes(tmp_path):
    data = b"start" + bytes(32 * 1024**2) + b"end"
    archive = archive_at(tmp_path / "data.tar", [("db/PG_VERSION", b"14"), ("flat/flatnode", data)])
    root = tmp_path / "volume"
    root.mkdir()
    counts = extract_archive(archive, root, {"db": "postgres", "flat": "flatnode"}, 0)
    assert (root / "postgres/PG_VERSION").read_bytes() == b"14"
    output = root / "flatnode/flatnode"
    assert output.read_bytes() == data
    assert output.stat().st_blocks * 512 < len(data)
    assert counts == {"db": 1, "flat": 1}


def test_partial_and_corrupted_archives_are_rejected(tmp_path):
    path = tmp_path / "data.tar"
    path.write_bytes(b"valid")
    digest = hashlib.sha256(b"valid").hexdigest()
    verify_archive(path, 5, digest)
    path.write_bytes(b"other")
    with pytest.raises(ValueError, match="checksum"):
        verify_archive(path, 5, digest)
    path.write_bytes(b"va")
    with pytest.raises(ValueError, match="size"):
        verify_archive(path, 5, digest)


def test_compressed_mirror_without_length_fails_clearly():
    with patch("harness.environments.webarena.map_setup.urlopen") as opening:
        opening.return_value.__enter__.return_value.headers = {}
        with pytest.raises(ValueError, match="No Content-Length"):
            transport_size("https://example.invalid/map.tar.zst")


def test_low_disk_stops_before_any_extraction_write(tmp_path):
    archive = archive_at(tmp_path / "data.tar", [("db/file", b"data")])
    root = tmp_path / "volume"
    root.mkdir()
    with patch("harness.environments.webarena.map_setup.shutil.disk_usage") as usage:
        usage.return_value.free = 100
        with pytest.raises(RuntimeError, match="Disk guard"):
            require_space(root, 101)
        with pytest.raises(RuntimeError, match="Disk guard"):
            extract_archive(archive, root, {"db": "postgres"}, 101)
    assert not (root / "postgres/file").read_bytes()


@pytest.mark.parametrize("name", ["db/../../escape", "db/../escape"])
def test_tar_cannot_escape_target(tmp_path, name):
    archive = archive_at(tmp_path / "data.tar", [(name, b"bad")])
    root = tmp_path / "volume"
    root.mkdir()
    with pytest.raises(ValueError, match="Unsafe"):
        extract_archive(archive, root, {"db": "postgres"}, 0)
    assert not (tmp_path / "escape").exists()


def test_incomplete_dataset_is_not_success(tmp_path):
    archive = archive_at(tmp_path / "data.tar", [("db/PG_VERSION", b"14")])
    root = tmp_path / "volume"
    root.mkdir()
    with pytest.raises(ValueError, match="Missing archive population"):
        extract_archive(archive, root, {"db": "postgres", "flat": "flatnode"}, 0)


def test_successful_setup_removes_archives_and_restart_never_downloads(tmp_path):
    state = tmp_path / ".map-setup"
    state.mkdir()
    contract = {"data_urls": [], "data_archive_bytes": {}, "data_archive_sha256": {}}
    for name, mappings in MAPPINGS.items():
        path = archive_at(state / name, [(prefix + "/data", b"verified") for prefix in mappings])
        contract["data_urls"].append("https://example.invalid/" + name)
        contract["data_archive_bytes"][name] = path.stat().st_size
        contract["data_archive_sha256"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    with patch(
        "harness.environments.webarena.map_setup.download",
        side_effect=AssertionError("unexpected network"),
    ):
        setup(tmp_path, contract, 0)
        assert not list(state.glob("*.tar"))
        assert len(list(state.glob("*.json"))) == 3
        setup(tmp_path, contract, 0)


def test_existing_partial_extraction_does_not_count_as_ready(tmp_path):
    state = tmp_path / ".map-setup"
    state.mkdir()
    contract = {"data_urls": [], "data_archive_bytes": {}, "data_archive_sha256": {}}
    for name, mappings in MAPPINGS.items():
        path = archive_at(state / name, [(prefix + "/data", b"verified") for prefix in mappings])
        contract["data_urls"].append("https://example.invalid/" + name)
        contract["data_archive_bytes"][name] = path.stat().st_size
        contract["data_archive_sha256"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
        for target in mappings.values():
            (tmp_path / target).mkdir(parents=True)
            (tmp_path / target / "data").write_bytes(b"partial")
    setup(tmp_path, contract, 0)
    for mappings in MAPPINGS.values():
        for target in mappings.values():
            assert (tmp_path / target / "data").read_bytes() == b"verified"


def test_receipt_with_only_empty_directories_does_not_count_as_ready(tmp_path):
    state = tmp_path / ".map-setup"
    state.mkdir()
    contract = {"data_urls": [], "data_archive_bytes": {}, "data_archive_sha256": {}}
    for name, mappings in MAPPINGS.items():
        contract["data_urls"].append("https://example.invalid/" + name)
        contract["data_archive_bytes"][name] = 1
        contract["data_archive_sha256"][name] = "a" * 64
        for target in mappings.values():
            (tmp_path / target / "empty").mkdir(parents=True)
    first = next(iter(MAPPINGS))
    (state / (first + ".json")).write_text(json.dumps({
        "sha256": "a" * 64, "bytes": 1, "query_only": False,
    }))
    with pytest.raises(ValueError, match="Receipt exists but dataset is missing"):
        setup(tmp_path, contract, 0)


@pytest.mark.skipif(shutil.which("zstd") is None, reason="requires zstd transport tool")
def test_compressed_transport_preserves_locked_archive_and_rejects_wrong_payload(tmp_path):
    original = archive_at(tmp_path / "original.tar", [("db/data", b"contents" + bytes(1024**2))])
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    packed = tmp_path / "transfer.zst"
    subprocess.run(["zstd", "-q", str(original), "-o", str(packed)], check=True)
    root = tmp_path / "volume"
    root.mkdir()
    restore_compressed(packed, root, {"db": "postgres"}, original.stat().st_size, digest, 0)
    assert (root / "postgres/data").read_bytes() == b"contents" + bytes(1024**2)
    assert not list(root.rglob("*.tar"))
    with pytest.raises(ValueError, match="checksum"):
        restore_compressed(packed, root, {"db": "postgres"}, original.stat().st_size, "0" * 64, 0)


@pytest.mark.skipif(shutil.which("zstd") is None, reason="requires zstd transport tool")
def test_stream_checks_trailing_bytes_and_never_qualifies_corrupted_dataset(tmp_path):
    state = tmp_path / ".map-setup"
    state.mkdir()
    name = "nominatim_volumes.tar"
    original = archive_at(tmp_path / "original.tar", [
        (prefix + "/data", b"valid") for prefix in MAPPINGS[name]
    ])
    original_bytes = original.read_bytes()
    digest = hashlib.sha256(original_bytes).hexdigest()
    # Change padding beyond tar's end marker: extraction succeeds, whole-file hash must fail.
    original.write_bytes(original_bytes[:-1] + b"x")
    packed = state / (name + ".zst")
    subprocess.run(["zstd", "-q", str(original), "-o", str(packed)], check=True)
    contract = {
        "data_urls": ["https://example.invalid/" + n for n in MAPPINGS],
        "data_archive_bytes": {n: original.stat().st_size for n in MAPPINGS},
        "data_archive_sha256": {n: digest for n in MAPPINGS},
    }
    with patch("harness.environments.webarena.map_setup.transport_size",
               return_value=packed.stat().st_size):
        with pytest.raises(ValueError, match="checksum"):
            setup(tmp_path, contract, 0, "https://example.invalid", "zstd")
    assert not list(state.glob("*.json"))
    with pytest.raises(ValueError, match="exceeds locked size"):
        restore_compressed(packed, tmp_path, MAPPINGS[name], 1, digest, 0)


@pytest.mark.skipif(shutil.which("zstd") is None, reason="requires zstd transport tool")
def test_compressed_setup_cleans_transport_and_restarts_without_source(tmp_path):
    state = tmp_path / ".map-setup"
    state.mkdir()
    contract = {"data_urls": [], "data_archive_bytes": {}, "data_archive_sha256": {}}
    sizes = {}
    for name, mappings in MAPPINGS.items():
        original = archive_at(tmp_path / name, [(p + "/data", b"verified") for p in mappings])
        contract["data_urls"].append("https://example.invalid/" + name)
        contract["data_archive_bytes"][name] = original.stat().st_size
        contract["data_archive_sha256"][name] = hashlib.sha256(original.read_bytes()).hexdigest()
        packed = state / (name + ".zst")
        subprocess.run(["zstd", "-q", str(original), "-o", str(packed)], check=True)
        sizes[packed.name] = packed.stat().st_size
        original.unlink()
    with patch("harness.environments.webarena.map_setup.transport_size",
               side_effect=lambda url: sizes[url.rsplit("/", 1)[-1]]):
        setup(tmp_path, contract, 0, "https://example.invalid", "zstd")
    assert len(list(state.glob("*.json"))) == 3
    assert not list(state.glob("*.tar")) and not list(state.glob("*.zst"))
    with patch("harness.environments.webarena.map_setup.transport_size",
               side_effect=AssertionError("source is offline")):
        setup(tmp_path, contract, 0, "https://example.invalid", "zstd")


def test_restored_database_does_not_keep_source_process_lock(tmp_path):
    archive = archive_at(
        tmp_path / "data.tar", [("db/postmaster.pid", b"1"), ("db/PG_VERSION", b"14")]
    )
    root = tmp_path / "volume"
    root.mkdir()
    extract_archive(archive, root, {"db": "postgres"}, 0)
    assert not (root / "postgres/postmaster.pid").exists()
    assert (root / "postgres/PG_VERSION").read_bytes() == b"14"


def test_service_never_starts_below_disk_reserve(tmp_path):
    marker = tmp_path / "started"
    with pytest.raises(RuntimeError, match="Disk guard"):
        serve(tmp_path, 10**20, [sys.executable, "-c", f"open({str(marker)!r},'w').close()"])
    assert not marker.exists()


def test_service_exit_status_is_preserved(tmp_path):
    assert serve(tmp_path, 0, [sys.executable, "-c", "raise SystemExit(7)"]) == 7


def test_map_stops_if_detail_service_exits(tmp_path):
    with pytest.raises(RuntimeError, match="Map detail service exited"):
        serve(tmp_path, 0, [sys.executable, "-c", "import time; time.sleep(60)"],
              [sys.executable, "-c", "raise SystemExit(7)"])


def test_process_group_exit_during_cleanup_preserves_service_failure(tmp_path, monkeypatch):
    original_killpg = os.killpg

    def exited_after_poll(pid, sig):
        original_killpg(pid, sig)
        raise ProcessLookupError(pid)

    monkeypatch.setattr(os, "killpg", exited_after_poll)
    with pytest.raises(RuntimeError, match="Map detail service exited"):
        serve(tmp_path, 0, [sys.executable, "-c", "import time; time.sleep(60)"],
              [sys.executable, "-c", "raise SystemExit(7)"])


def test_resume_compares_bytes_and_repairs_same_size_corruption_and_stale_tail(tmp_path):
    data = bytes(1024**2) + b"correct"
    archive = archive_at(tmp_path / "data.tar", [("db/file", data)])
    root = tmp_path / "volume"
    (root / "postgres").mkdir(parents=True)
    target = root / "postgres/file"
    for old in (b"x" * len(data), data + b"stale tail", data[:100], data):
        target.write_bytes(old)
        extract_archive(archive, root, {"db": "postgres"}, 0)
        assert target.read_bytes() == data


@pytest.mark.skipif(shutil.which("zstd") is None, reason="requires zstd transport tool")
def test_query_mode_omits_only_update_cache_but_checks_its_original_bytes(tmp_path):
    state = tmp_path / ".map-setup"
    state.mkdir()
    contract = {"data_urls": [], "data_archive_bytes": {}, "data_archive_sha256": {}}
    sizes = {}
    for name, mappings in MAPPINGS.items():
        original = archive_at(tmp_path / name, [(p + "/data", b"verified") for p in mappings])
        contract["data_urls"].append("https://example.invalid/" + name)
        contract["data_archive_bytes"][name] = original.stat().st_size
        contract["data_archive_sha256"][name] = hashlib.sha256(original.read_bytes()).hexdigest()
        packed = state / (name + ".zst")
        subprocess.run(["zstd", "-q", str(original), "-o", str(packed)], check=True)
        sizes[packed.name] = packed.stat().st_size
        original.unlink()
    stale = tmp_path / "nominatim/flatnode/flatnode.file"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"partial interrupted cache")
    with patch("harness.environments.webarena.map_setup.transport_size",
               side_effect=lambda url: sizes[url.rsplit("/", 1)[-1]]):
        setup(tmp_path, contract, 0, "https://example.invalid", "zstd", query_only=True)
    assert not list(stale.parent.iterdir())
    for mappings in MAPPINGS.values():
        for target in mappings.values():
            if target != "nominatim/flatnode":
                assert (tmp_path / target / "data").read_bytes() == b"verified"
    with pytest.raises(ValueError, match="Dataset mode"):
        setup(tmp_path, contract, 0)

    original = archive_at(tmp_path / "changed.tar", [("db/data", b"same"),
                                                    ("flat/data", b"original")])
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    archive_at(original, [("db/data", b"same"), ("flat/data", b"tampered")])
    packed = tmp_path / "changed.zst"
    subprocess.run(["zstd", "-q", str(original), "-o", str(packed)], check=True)
    with pytest.raises(ValueError, match="checksum"):
        restore_compressed(packed, tmp_path, {"db": "postgres"},
                           original.stat().st_size, digest, 0)


def test_tile_cache_survives_container_directory_recreation(tmp_path):
    root = tmp_path / "volume"
    cache = tmp_path / "image-cache"
    (cache / "default").mkdir(parents=True)
    (cache / "default/world.meta").write_bytes(b"rendered world")
    apache = tmp_path / "apache.conf"
    apache.write_text("<VirtualHost *:8080>\n    ModTileMissingRequestTimeout 30\n"
                      "    ProxyPass / http://localhost:3000/\n</VirtualHost>\n")
    renderd = tmp_path / "renderd.conf"
    renderd.write_text("[renderd]\nnum_threads=4\n")
    configure_tiles(root, cache, apache, renderd)
    assert cache.is_symlink()
    assert (root / "tiles/default/world.meta").read_bytes() == b"rendered world"
    (cache / "default/city.meta").write_bytes(b"new city")
    # A new container gets an empty image cache but the same persistent volume.
    cache.unlink()
    shutil.rmtree(cache.with_name(cache.name + ".image-baseline"))
    cache.mkdir()
    configure_tiles(root, cache, apache, renderd)
    configure_tiles(root, cache, apache, renderd)
    assert (cache / "default/city.meta").read_bytes() == b"new city"
    assert apache.read_text().count("ModTileMissingRequestTimeout 120") == 1
    assert apache.read_text().count("ModTileMaxLoadMissing 1000") == 1
    assert apache.read_text().count("ProxyPassMatch") == 1
    assert apache.read_text().index("ProxyPassMatch") < apache.read_text().index("ProxyPass /")
    assert "num_threads=2" in renderd.read_text()


def test_tile_configuration_rejects_multiple_virtual_hosts(tmp_path):
    root = tmp_path / "volume"
    tiles = root / "tiles"
    tiles.mkdir(parents=True)
    cache = tmp_path / "image-cache"
    cache.symlink_to(tiles, target_is_directory=True)
    apache = tmp_path / "apache.conf"
    original = ("<VirtualHost *:8080>\n    ModTileMissingRequestTimeout 30\n"
                "    ProxyPass / http://localhost:3000/\n</VirtualHost>\n"
                "<VirtualHost *:80>\n</VirtualHost>\n")
    apache.write_text(original)
    renderd = tmp_path / "renderd.conf"
    renderd.write_text("[renderd]\nnum_threads=4\n")
    with pytest.raises(ValueError, match="virtual host"):
        configure_tiles(root, cache, apache, renderd)
    assert apache.read_text() == original
