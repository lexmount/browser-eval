"""Poll an explicitly requested device authorization; keep its token repo-local."""
import argparse
import json
import os
from pathlib import Path
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device-code', required=True)
    parser.add_argument('--timeout', type=int, default=540)
    args = parser.parse_args()
    destination = Path(__file__).resolve().parents[1]/'.env'
    if destination.exists() and 'ARTIFACT_HUB_TOKEN=' in destination.read_text():
        parser.error('Repository already has a token; will not overwrite it')
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        request = urllib.request.Request('https://artifact-hub.dev.lexmount.net/api/device/poll',
            data=json.dumps({'device_code': args.device_code}).encode(),
            headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
        if result.get('status') == 'approved':
            token = result['token']
            if not token.startswith('ahp_') or any(c.isspace() for c in token):
                raise ValueError('Unexpected token format')
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(descriptor, 'a') as stream:
                stream.write('\nARTIFACT_HUB_TOKEN=' + token + '\n')
            os.chmod(destination, 0o600)
            print('Authorized:', result.get('user', {}).get('email', '(identity unavailable)'), flush=True)
            return
        if result.get('status') != 'pending':
            raise RuntimeError('Device authorization: ' + str(result.get('status')))
        time.sleep(5)
    raise TimeoutError('Device authorization expired; no token stored')


if __name__ == '__main__':
    main()
