"""Resolve official WebArena-Verified Docker tags to immutable registry digests."""

from harness.environments.webarena.config import main_image_lock


if __name__ == "__main__":
    main_image_lock()
