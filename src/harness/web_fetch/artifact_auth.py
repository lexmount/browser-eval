"""Artifact Site's user-scoped publishing credential, separate from eval secrets."""

import argparse
import json
import os
import stat
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from harness.web_fetch.publication_target import validate_service_base


def token_path(base_url):
    root = Path.home() / '.config/artifact-site'
    parsed = urlsplit(base_url)
    host = parsed.netloc.lower()
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or host in {'.', '..'} or any(c.isspace() or c in '/\\' for c in host)):
        raise ValueError('Artifact Site base URL requires an HTTP(S) host without credentials')
    # Include an explicit port, matching the server manual's host key.
    return root / 'tokens' / host


def validate_token(token):
    if not token.startswith('ahp_') or any(c.isspace() for c in token):
        raise ValueError('Invalid Artifact Site token format')
    return token


def load_token(*, base_url):
    # Explicit process environment, then the target host. Never .env. Previous
    # product-name locations remain read-only migration fallbacks.
    host = urlsplit(base_url).netloc.lower()
    token = os.environ.get('ARTIFACT_SITE_TOKEN', '').strip()
    if not token:
        token = os.environ.get('ARTIFACT_HUB_TOKEN', '').strip()
    candidates = (
        token_path(base_url),
        Path.home() / '.config/artifact-hub' / 'tokens' / host,
        Path.home() / '.config/artifact-site' / 'token',
        Path.home() / '.config/artifact-hub' / 'token',
    )
    if not token:
        for path in candidates:
            try:
                token = path.read_text().strip()
            except FileNotFoundError:
                continue
            if token:
                break
    return validate_token(token)


def save_token(token, *, base_url, replace_existing=False):
    token = validate_token(token)
    destination = token_path(base_url)
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


def authorize_main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--device-code')
    action.add_argument('--persist-existing', action='store_true')
    parser.add_argument('--timeout', type=int, default=540)
    parser.add_argument('--replace-existing', action='store_true', help='Explicitly back up and replace a different user token')
    parser.add_argument('--base-url', default=os.environ.get(
        'ARTIFACT_SITE_BASE_URL',
        os.environ.get('ARTIFACT_HUB_BASE_URL', 'https://artifact-hub.app.lexmount.com'),
    ))
    args = parser.parse_args(argv)
    if args.persist_existing:
        token = (os.environ.get('ARTIFACT_SITE_TOKEN', '').strip()
                 or os.environ.get('ARTIFACT_HUB_TOKEN', '').strip())
        backup=save_token(token,base_url=args.base_url,replace_existing=args.replace_existing)
        if backup:print('Previous credential backed up:',backup)
        print('Existing authorization saved to the user credential file; token not displayed.')
        return

    args.base_url = validate_service_base(args.base_url)
    if args.timeout <= 0:
        raise ValueError("Device authorization timeout must be positive")
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        request = urllib.request.Request(args.base_url.rstrip('/')+'/api/device/poll',
            data=json.dumps({'device_code': args.device_code}).encode(),
            headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
        if result.get('status') == 'approved':
            token = result['token']
            backup=save_token(token,base_url=args.base_url,replace_existing=args.replace_existing)
            if backup:print('Previous credential backed up:',backup)
            print('Authorized:', result.get('user', {}).get('email', '(identity unavailable)'), flush=True)
            return
        if result.get('status') != 'pending':
            raise RuntimeError('Device authorization: ' + str(result.get('status')))
        time.sleep(5)
    raise TimeoutError('Device authorization expired; no token stored')
