"""Compare a deployed Artifact Site export with the submitted report tree."""

import hashlib
import io
import zipfile
from pathlib import Path


def verify_published_tree(source, archive, *, excluded=('.generated-report',)):
    """Compare every deployed file with the submitted tree without extracting ZIPs."""
    source = Path(source)
    paths = list(source.rglob('*'))
    if any(p.is_symlink() for p in paths):
        raise ValueError('Publication source contains a symlink')
    files = {p.relative_to(source).as_posix(): p for p in paths
             if p.is_file() and p.relative_to(source).as_posix() not in excluded}
    with zipfile.ZipFile(io.BytesIO(archive) if isinstance(archive, bytes) else archive) as bundle:
        members = [info for info in bundle.infolist() if not info.is_dir()]
        if len(members) != len(files) or {i.filename for i in members} != set(files):
            raise ValueError('Deployed file inventory differs from the submitted report')
        for info in members:
            path = files[info.filename]
            if (info.file_size != path.stat().st_size
                    or hashlib.sha256(bundle.read(info)).digest() != hashlib.sha256(path.read_bytes()).digest()):
                raise ValueError('Deployed file differs: ' + info.filename)
    return len(files)
