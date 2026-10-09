"""Profile-bound report inputs shared by build and publication."""
import hashlib
from pathlib import Path

from harness.web_fetch.safety import unlinked_path


def verified_dataset_path(path: Path, digest: str) -> Path:
    path = unlinked_path(path)
    if not path.is_file():
        raise ValueError("bound corrected dataset is missing or symlinked")
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("bound corrected dataset changed")
    return path


def bound_dataset_path(profile: dict) -> Path:
    dataset = profile.get("dataset") or {}
    path_value = dataset.get("path")
    digest = dataset.get("sha256")
    if not isinstance(path_value, str) or not path_value or not isinstance(digest, str):
        raise ValueError("replay report profile requires a bound corrected dataset")
    path = Path(path_value)
    return verified_dataset_path(path, digest)
