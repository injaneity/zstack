import argparse
import json
import os
from pathlib import Path
import plistlib
import socket
import subprocess
import tempfile
import uuid


def retrieve(service, account, keychain):
    with tempfile.TemporaryDirectory(prefix='cua-kc-', dir='/tmp') as directory:
        path = Path(directory)
        endpoint = str(path / 'reply.sock')
        label = 'com.trycua.e2e-credential.' + uuid.uuid4().hex
        domain = f'gui/{os.getuid()}'
        plist = path / 'launcher.plist'
        plist.write_bytes(plistlib.dumps({
            'Label': label,
            'ProgramArguments': ['/usr/bin/python3', str(Path(__file__).resolve()),
                                 '--socket', endpoint, '--service', service,
                                 '--account', account, '--keychain', keychain],
            'RunAtLoad': True,
            'LimitLoadToSessionType': 'Aqua',
            'StandardOutPath': '/dev/null',
            'StandardErrorPath': '/dev/null',
        }))
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(endpoint)
            os.chmod(endpoint, 0o600)
            server.listen(1)
            server.settimeout(25)
            admitted = False
            try:
                subprocess.run(['launchctl', 'bootstrap', domain, str(plist)],
                               check=True, capture_output=True, timeout=10)
                admitted = True
                with server.accept()[0] as connection:
                    connection.settimeout(20)
                    data = bytearray()
                    while True:
                        chunk = connection.recv(4096)
                        if not chunk:
                            break
                        data.extend(chunk)
                        if len(data) > 8192:
                            raise ValueError('credential response exceeded size limit')
                return decode(bytes(data))
            finally:
                if admitted:
                    subprocess.run(['launchctl', 'bootout', domain + '/' + label],
                                   capture_output=True, timeout=10, check=False)


def decode(data):
    if not data.startswith(b'OK\n'):
        raise RuntimeError('desktop keychain retrieval failed; unlock the configured keychain and approve access in the desktop session')
    secret = data[3:]
    if not secret or b'\n' in secret or len(secret) > 4096:
        raise ValueError('keychain credential must be a nonempty single line of at most 4096 bytes')
    return secret


def helper(args):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(20)
        connection.connect(args.socket)
        try:
            p = subprocess.run(['/usr/bin/security', 'find-generic-password',
                                '-s', args.service, '-a', args.account, '-w', args.keychain],
                               capture_output=True, timeout=15)
            secret = p.stdout.rstrip(b'\n')
            if p.returncode or not secret or b'\n' in secret or len(secret) > 4096:
                connection.sendall(b'ERROR\n')
            else:
                connection.sendall(b'OK\n' + secret)
        except Exception:
            connection.sendall(b'ERROR\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--socket', required=True)
    parser.add_argument('--service', required=True)
    parser.add_argument('--account', required=True)
    parser.add_argument('--keychain', required=True)
    helper(parser.parse_args())
