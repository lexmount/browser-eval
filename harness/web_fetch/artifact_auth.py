"""Artifact Hub's user-scoped publishing credential, separate from eval secrets."""
import os
from pathlib import Path
import tempfile
import uuid


def token_path():
    return Path.home() / '.config/artifact-hub/token'


def validate_token(token):
    if not token.startswith('ahp_') or any(c.isspace() for c in token):
        raise ValueError('Invalid Artifact Hub token format')
    return token


def load_token(path=None):
    # Explicit environment/repository configuration takes precedence.
    token = os.environ.get('ARTIFACT_HUB_TOKEN', '').strip()
    if not token:
        token = (path or token_path()).read_text().strip()
    return validate_token(token)


def save_token(token, path=None, *, replace_existing=False):
    token = validate_token(token)
    destination = path or token_path()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.is_symlink():
        raise ValueError('Refusing symlink credential destination')
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if destination.read_text().strip() != token:
            if not replace_existing:
                raise ValueError('A different user token already exists; will not overwrite it') from None
            backup=destination.with_name('token.backup-'+uuid.uuid4().hex)
            descriptor=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(descriptor,'wb') as stream:
                stream.write(destination.read_bytes())
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
        os.chmod(destination, 0o600)
        return
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(token + '\n')
