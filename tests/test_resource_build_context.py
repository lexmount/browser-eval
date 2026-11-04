"""The resource image receives runtime code without evaluation credentials.

No test here builds the image. They read the Dockerfile's COPY instructions
against the real build context, so a moved source path fails without a daemon.
"""
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import tomllib
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from docker.utils.build import exclude_paths, tar

from harness.web_fetch.container_resources import ContainerResourceRunner

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / 'examples/web_fetch/docker/resource.Dockerfile'
WORKER = 'src/harness/web_fetch/providers/headless_worker.py'


def _ignore_patterns():
    return [line.strip() for line in (ROOT / '.dockerignore').read_text(encoding='utf-8').splitlines()
            if line.strip() and not line.lstrip().startswith('#')]


def _context_copies():
    """Yield (workdir, sources, destination) for COPY lines that read the build context."""
    workdir = '/'
    for line in DOCKERFILE.read_text(encoding='utf-8').splitlines():
        words = line.split()
        if not words:
            continue
        if words[0] == 'FROM':
            workdir = '/'
        elif words[0] == 'WORKDIR':
            workdir = words[1]
        elif words[0] == 'COPY' and not words[1].startswith('--from='):
            yield workdir, words[1:-1], words[-1]


def _image_path(workdir, source, destination, relative):
    """Map one context file to its image path under the COPY it comes from."""
    target = Path(workdir, destination)
    if relative == source:
        return str(target / Path(source).name) if destination.endswith('/') else str(target)
    return str(target / Path(relative).relative_to(source))


def _image_files(context):
    files = {}
    for workdir, sources, destination in _context_copies():
        for source in sources:
            for relative in context:
                if relative == source or relative.startswith(source + '/'):
                    files[_image_path(workdir, source, destination, relative)] = relative
    return files


class ResourceBuildContextTests(unittest.TestCase):
    def test_build_context_excludes_credentials_and_host_dependencies(self):
        excluded = {'.env', '.env.local', 'nested/.env', 'nested/.env.production',
                    'node_modules/example/package.json', 'nested/node_modules/example/package.json',
                    'src/browser_harness_eval.egg-info/PKG-INFO'}
        required = {'pyproject.toml', 'uv.lock', 'package.json', 'package-lock.json',
                    WORKER, 'scripts/worker.py',
                    'examples/web_fetch/docker/resource.Dockerfile'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in excluded | required:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture', encoding='utf-8')
            with tar(str(root), exclude=_ignore_patterns()) as context:
                with tarfile.open(fileobj=context) as archive:
                    names = {item.name.removeprefix('./') for item in archive.getmembers() if item.isfile()}
        self.assertFalse(names & excluded, names & excluded)
        self.assertTrue(required <= names, required - names)


class ResourceImageLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = exclude_paths(str(ROOT), _ignore_patterns())
        cls.image = _image_files(cls.context)

    def test_every_copy_source_exists_in_the_build_context(self):
        sources = [source for _, names, _ in _context_copies() for source in names]
        self.assertTrue(sources)
        self.assertEqual([], [source for source in sources if source not in self.context])

    def test_image_receives_every_input_of_the_project_install(self):
        project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
        inputs = {'pyproject.toml', 'setup.py', project['readme'], *project['license-files']}
        self.assertEqual(set(), inputs - set(self.image.values()))

    def test_runtime_hashes_the_worker_where_the_image_holds_it(self):
        client = MagicMock()
        client.info.return_value = {'OSType': 'linux', 'CgroupVersion': '2'}
        client.version.return_value = {'Version': 'fixture'}
        client.images.get.return_value = SimpleNamespace(id='sha256:image', attrs={})
        client.containers.run.return_value = hashlib.sha256((ROOT / WORKER).read_bytes()).hexdigest().encode() + b'  worker\n'
        with patch('harness.web_fetch.container_resources.docker.from_env', return_value=client), \
             patch('harness.web_fetch.container_resources.shutil.which', return_value='/usr/bin/docker'):
            environment = ContainerResourceRunner('fixture:local').validate()
        hashed = client.containers.run.call_args.args[1][0]
        self.assertEqual(WORKER, self.image.get(hashed))
        # The recorded environment names hashes and image identity, never a container path.
        self.assertNotIn('/workspace', json.dumps(environment))
