"""Authorize Artifact Hub and save its token in the user-scoped credential file."""
import argparse
import json
import os
from pathlib import Path
import time
import urllib.request
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.web_fetch.artifact_auth import save_token
from harness.web_fetch.config import load_runtime_env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--device-code')
    action.add_argument('--persist-existing', action='store_true')
    parser.add_argument('--timeout', type=int, default=540)
    parser.add_argument('--replace-existing', action='store_true', help='Explicitly back up and replace a different user token')
    parser.add_argument('--base-url',default=os.environ.get('ARTIFACT_HUB_BASE_URL','https://artifact-hub.app.lexmount.com'))
    args = parser.parse_args()
    if args.persist_existing:
        load_runtime_env()
        backup=save_token(os.environ.get('ARTIFACT_HUB_TOKEN', '').strip(),replace_existing=args.replace_existing)
        if backup:print('Previous credential backed up:',backup)
        print('Existing authorization saved to the user credential file; token not displayed.')
        return
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        request = urllib.request.Request(args.base_url.rstrip('/')+'/api/device/poll',
            data=json.dumps({'device_code': args.device_code}).encode(),
            headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
        if result.get('status') == 'approved':
            token = result['token']
            backup=save_token(token,replace_existing=args.replace_existing)
            if backup:print('Previous credential backed up:',backup)
            print('Authorized:', result.get('user', {}).get('email', '(identity unavailable)'), flush=True)
            return
        if result.get('status') != 'pending':
            raise RuntimeError('Device authorization: ' + str(result.get('status')))
        time.sleep(5)
    raise TimeoutError('Device authorization expired; no token stored')


if __name__ == '__main__':
    main()
