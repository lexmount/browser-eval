"""Validate existing local production inputs without copying or fetching them."""
from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parents[1]
bindings=json.loads((ROOT/'video/content-bindings.json').read_text())
for name,digest in bindings['files'].items():
    file=ROOT/name
    if not file.is_file():raise SystemExit(f'Missing production input: {file}. Supply the existing report bundle; no fallback is used.')
    if hashlib.sha256(file.read_bytes()).hexdigest()!=digest:raise SystemExit(f'Production input changed: {name}. Review and update its binding before release.')
print('Production input hashes match')
