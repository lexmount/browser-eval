"""The resource image receives runtime code without evaluation credentials."""
from pathlib import Path
import tarfile
import tempfile
import unittest

from docker.utils.build import tar

ROOT = Path(__file__).resolve().parents[1]


class ResourceBuildContextTests(unittest.TestCase):
    def test_build_context_excludes_credentials_and_host_dependencies(self):
        excluded = {'.env', '.env.local', 'nested/.env', 'nested/.env.production',
                    'node_modules/example/package.json', 'nested/node_modules/example/package.json'}
        required = {'pyproject.toml', 'uv.lock', 'package.json', 'package-lock.json',
                    'harness/provider.py', 'scripts/worker.py',
                    'examples/web_fetch/docker/resource.Dockerfile'}
        patterns = [line.strip() for line in (ROOT / '.dockerignore').read_text().splitlines()
                    if line.strip() and not line.lstrip().startswith('#')]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in excluded | required:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture')
            with tar(str(root), exclude=patterns) as context:
                with tarfile.open(fileobj=context) as archive:
                    names = {item.name.removeprefix('./') for item in archive.getmembers() if item.isfile()}
        self.assertFalse(names & excluded, names & excluded)
        self.assertTrue(required <= names, required - names)
