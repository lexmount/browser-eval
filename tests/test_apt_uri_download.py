import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from examples.web_fetch.docker.download_apt_uris import download, parse_plan


def _plan(tmp_path: Path, *, name: str = "fixture.deb", digest: str | None = None) -> Path:
    payload = b"package bytes"
    checksum = digest or hashlib.md5(payload).hexdigest()
    plan = tmp_path / "apt-uris.txt"
    plan.write_text(
        f"'https://example.invalid/{name}' {name} {len(payload)} MD5Sum:{checksum}\n"
    )
    return plan


def test_apt_download_checks_size_and_digest_before_cache_replace(tmp_path: Path):
    archives = tmp_path / "archives"
    archives.mkdir()
    item, = parse_plan(_plan(tmp_path), archives)
    with patch("examples.web_fetch.docker.download_apt_uris.urllib.request.urlretrieve",
               side_effect=lambda _url, target: Path(target).write_bytes(b"package bytes")):
        download(item)
    assert item.destination.read_bytes() == b"package bytes"

    item, = parse_plan(_plan(tmp_path, digest="0" * 32), archives)
    item.destination.unlink()
    with patch("examples.web_fetch.docker.download_apt_uris.urllib.request.urlretrieve",
               side_effect=lambda _url, target: Path(target).write_bytes(b"package bytes")):
        with pytest.raises(ValueError, match="checksum mismatch"):
            download(item)
    assert not item.destination.exists()
    assert not item.destination.with_suffix(".deb.part").exists()

    item, = parse_plan(_plan(tmp_path), archives)
    with patch("examples.web_fetch.docker.download_apt_uris.urllib.request.urlretrieve",
               side_effect=lambda _url, target: Path(target).write_bytes(b"truncated")):
        with pytest.raises(ValueError, match="size mismatch"):
            download(item)
    assert not item.destination.exists()


def test_apt_plan_rejects_path_escape_and_missing_checksum(tmp_path: Path):
    with pytest.raises(ValueError, match="unsafe apt package filename"):
        parse_plan(_plan(tmp_path, name="../../escape.deb"), tmp_path / "archives")
    plan = tmp_path / "apt-uris.txt"
    plan.write_text("'https://example.invalid/fixture.deb' fixture.deb 12\n")
    with pytest.raises(ValueError, match="invalid apt package plan entry"):
        parse_plan(plan, tmp_path / "archives")


def test_sha256_plan_preserves_encoded_url_and_checks_package_bytes(tmp_path: Path):
    payload = b"package bytes"
    plan = tmp_path / "apt-uris.txt"
    url = "https://example.invalid/fixture_1%2bdeb13_arm64.deb"
    name = "fixture_1+deb13_arm64.deb"
    digest = hashlib.sha256(payload).hexdigest()
    plan.write_text(f"'{url}' {name} {len(payload)} SHA256:{digest}\n")
    archives = tmp_path / "archives"
    archives.mkdir()
    item, = parse_plan(plan, archives)
    assert item.url == url
    assert item.destination.name == name
    with patch("examples.web_fetch.docker.download_apt_uris.urllib.request.urlretrieve",
               side_effect=lambda _url, target: Path(target).write_bytes(payload)):
        download(item)
    assert item.destination.read_bytes() == payload
    item.destination.unlink()
    with patch("examples.web_fetch.docker.download_apt_uris.urllib.request.urlretrieve",
               side_effect=lambda _url, target: Path(target).write_bytes(b"altered bytes")):
        with pytest.raises(ValueError, match="checksum mismatch"):
            download(item)
    assert not item.destination.exists()
