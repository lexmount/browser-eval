"""Download apt's resolved package plan concurrently into its archive cache."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shlex
import sys
import urllib.request


plan = Path(sys.argv[1])
archives = Path(sys.argv[2])
archives.mkdir(parents=True, exist_ok=True)
items = []
for line in plan.read_text(encoding="utf-8").splitlines():
    if not line.startswith("'"):
        continue
    fields = shlex.split(line)
    if len(fields) >= 2 and fields[0].startswith(("http://", "https://")):
        items.append((fields[0], archives / fields[1]))


def download(item: tuple[str, Path]) -> None:
    url, destination = item
    temporary = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(3):
        try:
            urllib.request.urlretrieve(url, temporary)
            temporary.replace(destination)
            return
        except Exception:
            temporary.unlink(missing_ok=True)
            if attempt == 2:
                raise


with ThreadPoolExecutor(max_workers=8) as pool:
    list(pool.map(download, items))
