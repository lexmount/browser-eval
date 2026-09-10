"""Artifact Hub's user-scoped publishing credential, separate from eval secrets."""
import os
from pathlib import Path
import stat
import tempfile
import uuid
from urllib.parse import urlsplit


def token_path(base_url=None):
    root = Path.home() / '.config/artifact-hub'
    if base_url is None:
        return root / 'token'
    parsed = urlsplit(base_url)
    host = parsed.netloc.lower()
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or host in {'.', '..'} or any(c.isspace() or c in '/\\' for c in host)):
        raise ValueError('Artifact Hub base URL requires an HTTP(S) host without credentials')
    # Include an explicit port, matching the server manual's host key.
    return root / 'tokens' / host


def validate_token(token):
    if not token.startswith('ahp_') or any(c.isspace() for c in token):
        raise ValueError('Invalid Artifact Hub token format')
    return token


def load_token(path=None, *, base_url=None):
    # Explicit process environment, target host, then the legacy file. Never .env.
    token = os.environ.get('ARTIFACT_HUB_TOKEN', '').strip()
    if not token:
        if path is not None:
            token = Path(path).read_text().strip()
        else:
            try:
                token = token_path(base_url).read_text().strip()
            except FileNotFoundError:
                if base_url is None:
                    raise
            if not token:
                token = token_path().read_text().strip()
    return validate_token(token)


def save_token(token, path=None, *, base_url=None, replace_existing=False):
    token = validate_token(token)
    destination = Path(path) if path is not None else token_path(base_url)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             getattr(os, 'O_NOFOLLOW', 0), 0o600)
    except FileExistsError:
        try:
            existing = os.open(destination, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        except OSError as error:
            raise ValueError('Refusing non-regular credential destination') from error
        try:
            if not stat.S_ISREG(os.fstat(existing).st_mode):
                raise ValueError('Refusing non-regular credential destination')
            with os.fdopen(existing, 'rb', closefd=False) as stream:
                original = stream.read()
            if original.decode().strip() == token:
                os.fchmod(existing, 0o600)
                return
        finally:
            os.close(existing)
        if original.decode().strip() != token:
            if not replace_existing:
                raise ValueError('A different user token already exists; will not overwrite it') from None
            backup=destination.with_name(destination.name+'.backup-'+uuid.uuid4().hex)
            descriptor=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(descriptor,'wb') as stream:
                stream.write(original)
            with tempfile.NamedTemporaryFile(dir=destination.parent,prefix='token-new-',delete=False) as stream:
                temporary=Path(stream.name)
                stream.write((token+'\n').encode())
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.chmod(temporary,0o600)
                os.replace(temporary,destination)
            finally:
                temporary.unlink(missing_ok=True)
            return backup
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(token + '\n')
