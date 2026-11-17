import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import tarfile
import consent_acceptance
import containment_preflight
import electron_main

TARGET = 'pr4384-relative-profile'
CAPTURE = 'pr3140-consent-capture'
ENTRYPOINTS = {electron_main.TARGET: 'guest-electron-main.py', containment_preflight.TARGET: 'containment_preflight.py', consent_acceptance.TARGET: 'guest-consent-allow.py', TARGET: 'guest-daemon-relative.py', CAPTURE: 'guest-consent-capture.py'}
SCHEMA = 'cua-driver/lume-targeted-package@1'


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def package_check(directory, sha):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / 'targeted-package.json').read_text())
    if manifest.get('schema') != SCHEMA or manifest.get('id') not in ENTRYPOINTS:
        raise ValueError('unsupported targeted package; register and review its result contract first')
    if manifest.get('source_sha') != sha or manifest.get('entrypoint') != ENTRYPOINTS[manifest['id']]:
        raise ValueError('targeted package/source mismatch')
    if manifest['id'] != containment_preflight.TARGET and not re.fullmatch(r'[0-9a-f]{40}', manifest.get('signing_identity', '')):
        raise ValueError('targeted package needs one exact existing signing identity')
    files = manifest.get('files')
    if not isinstance(files, dict) or manifest['entrypoint'] not in files:
        raise ValueError('targeted entrypoint is not hash-pinned')
    if manifest['id'] == containment_preflight.TARGET:
        if set(files) != {'containment_preflight.py'} or manifest.get('expected_seed') != containment_preflight.EXPECTED_SEED or 'signing_identity' in manifest:
            raise ValueError('unexpected inventory-only contract')
    if manifest['id'] == consent_acceptance.TARGET:
        consent_acceptance.selected_locales(manifest.get('locales', consent_acceptance.LOCALES))
        if set(files) != {'guest-consent-allow.py', 'capture_support.py', 'consent_acceptance.py', 'source-manifest.json', 'scoped_preflight.py'} or manifest['signing_identity'] != 'ee7a0f341e06cdee7c1ec63929079f77d566adfc':
            raise ValueError('acceptance assets/identity differ from reviewed contract')
    if manifest['id'] == CAPTURE:
        if set(files) != {'guest-consent-capture.py', 'source-manifest.json'} or manifest['signing_identity'] != 'ee7a0f341e06cdee7c1ec63929079f77d566adfc':
            raise ValueError('capture assets/identity differ from reviewed contract')
    if manifest['id'] == electron_main.TARGET:
        if set(files) != electron_main.ASSETS or manifest['signing_identity'] != electron_main.IDENTITY:
            raise ValueError('Electron assets/identity differ from reviewed contract')
    total = 0
    for name, expected in files.items():
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name) or name == 'targeted-package.json':
            raise ValueError('unsafe targeted package filename')
        path = directory / name
        if path.is_symlink() or not path.is_file() or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise ValueError('targeted assets must be regular hash-pinned files')
        total += path.stat().st_size
        if total > 2 * 1024 ** 3 or digest(path) != expected:
            raise ValueError(f'targeted asset size/hash mismatch: {name}')
    return manifest


def transfer(directory, manifest, out, ssh, remote, command, env):
    directory = Path(directory).resolve()
    if package_check(directory, manifest['source_sha']) != manifest:
        raise ValueError('targeted package changed after preflight')
    with tarfile.open(out / 'targeted-package.tar', 'w') as archive:
        for name in ['targeted-package.json', *manifest['files']]:
            archive.add(directory / name, arcname='harness/' + name, recursive=False)
    with (out / 'targeted-package.tar').open('rb') as stream:
        result = subprocess.run(ssh + [f'tar -xf - -C {shlex.quote(remote)}'], stdin=stream,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=300)
        if result.returncode:
            raise ValueError('targeted package transfer failed')
    expected = digest(directory / 'targeted-package.json')
    verify = (
        'import hashlib,json,pathlib; p=pathlib.Path(' + repr(remote + '/harness') + '); '
        'raw=(p/"targeted-package.json").read_bytes(); '
        'assert hashlib.sha256(raw).hexdigest()==' + repr(expected) + '; '
        'm=json.loads(raw); '
        'assert all(hashlib.sha256((p/n).read_bytes()).hexdigest()==h for n,h in m["files"].items())'
    )
    command(ssh + ['/usr/bin/python3 -c ' + shlex.quote(verify)], timeout=180)


def guest_dispatch(remote, manifest, runner):
    if manifest['id'] == containment_preflight.TARGET:
        return '/usr/bin/python3 ' + shlex.quote(remote+'/harness/containment_preflight.py') + ' ' + shlex.quote(remote)
    identity = manifest['signing_identity']
    evidence = f"artifacts/cua-driver/targeted-preflight/{manifest['id']}.json"
    return f'''set +x
[[ -n "${{CUA_E2E_SIGNING_KEYCHAIN_PASSWORD:-}}" ]] || {{ echo 'Missing signing prerequisite'; exit 2; }}
export CUA_E2E_RUNNER_LIB_ONLY=1
export CUA_E2E_REPO_ROOT="$PWD"
export CUA_E2E_SIGNING_KEYCHAIN="$HOME/Library/Keychains/cua-driver-signing.keychain-db"
export CUA_E2E_SIGNING_IDENTITY={identity.upper()}
source {shlex.quote(runner)}
prepare_keychain 'Dedicated signing keychain' "$SIGNING_KEYCHAIN" "$CUA_E2E_SIGNING_KEYCHAIN_PASSWORD" signing
unset CUA_E2E_SIGNING_KEYCHAIN_PASSWORD
probe_signing_keychain "$SIGNING_KEYCHAIN"
output_contains "$CUA_E2E_SIGNING_IDENTITY" run_bounded_command security find-identity -v -p codesigning "$SIGNING_KEYCHAIN"
mkdir -p artifacts/cua-driver/targeted-preflight
printf '%s\\n' '{{"schema":"cua-driver/targeted-signing-preflight@1","identity":"{identity}","dedicated_keychain":true,"probe_passed":true}}' > {shlex.quote(evidence)}
unset CUA_E2E_RUNNER_LIB_ONLY
/usr/bin/python3 {shlex.quote(remote + '/harness/' + manifest['entrypoint'])} {shlex.quote(remote)}'''


def observe(directory, sha, manifest=None):
    if manifest and manifest['id'] == electron_main.TARGET:
        return electron_main.observe(directory/'source/artifacts/cua-driver/targeted'/electron_main.TARGET, sha)
    if manifest and manifest['id'] == containment_preflight.TARGET:
        return {'e2e_executed':False,'scope':'metadata_only','full_matrix':False}
    if manifest and manifest['id'] == consent_acceptance.TARGET:
        path = directory/'source/artifacts/cua-driver/targeted'/consent_acceptance.TARGET/'targeted-result.json'
        if not path.exists(): return {'e2e_executed':False,'scope':'native_allow_only'}
        result=json.loads(path.read_text())
        if result.get('source_sha') != sha or result.get('mode') != consent_acceptance.TARGET:
            raise ValueError('acceptance source/scope mismatch')
        return {'e2e_executed':result.get('e2e_executed') is True,'scope':'native_allow_only','full_matrix':False}

    if manifest and manifest["id"] == CAPTURE:
        path = directory / "source/artifacts/cua-driver/targeted" / CAPTURE / "targeted-result.json"
        if not path.exists():
            return {"e2e_executed": False, "diagnostic_capture_executed": False}
        result = json.loads(path.read_text())
        if result.get("source_sha") != sha or result.get("mode") != CAPTURE:
            raise ValueError("capture outcome source/scope mismatch")
        return {"e2e_executed": False, "diagnostic_capture_executed": result.get("diagnostic_capture_executed") is True,
                "completeness_status": "unknown"}
    path = directory / 'source/artifacts/cua-driver/targeted' / TARGET / 'targeted-result.json'
    if not path.exists():
        return {'e2e_executed': False}
    result = json.loads(path.read_text())
    if result.get('source_sha') != sha or result.get('mode') != 'targeted-daemon-relative':
        raise ValueError('targeted outcome source/scope mismatch')
    return {'e2e_executed': result.get('e2e_executed') is True,
            'scenario_outcomes': result.get('scenarios', [])}


def evidence_check(directory, sha, manifest):
    base = directory / 'source/artifacts/cua-driver'
    if manifest['id'] == containment_preflight.TARGET:
        return containment_preflight.evidence_check(base/'targeted'/containment_preflight.TARGET,sha)
    preflight = json.loads((base / 'targeted-preflight' / (manifest['id'] + '.json')).read_text())
    if preflight != {'schema': 'cua-driver/targeted-signing-preflight@1',
                     'identity': manifest['signing_identity'], 'dedicated_keychain': True, 'probe_passed': True}:
        raise ValueError('targeted signing preflight incomplete')
    if manifest['id'] == electron_main.TARGET:
        return electron_main.evidence_check(base/'targeted'/electron_main.TARGET, sha, manifest)
    if manifest['id'] == consent_acceptance.TARGET:
        return consent_acceptance.evidence_check(base/'targeted'/consent_acceptance.TARGET, sha, manifest.get('locales', consent_acceptance.LOCALES))
    if manifest['id'] == CAPTURE:
        return capture_evidence_check(base / 'targeted' / CAPTURE, sha)
    result = json.loads((base / 'targeted' / TARGET / 'targeted-result.json').read_text())
    if (result.get('source_sha'), result.get('mode'), result.get('status'), result.get('e2e_executed')) != (
            sha, 'targeted-daemon-relative', 'pass', True):
        raise ValueError('targeted scenario failed, did not execute, or used the wrong source')
    loader = result.get('loader_validation', {})
    if loader.get('status') != 'pass' or loader.get('host_exited') is not True or loader.get('cleanup_errors'):
        raise ValueError('targeted loader validation/cleanup incomplete')
    scenarios = result.get('scenarios', [])
    if len(scenarios) != 1:
        raise ValueError('targeted evidence must contain exactly one relative scenario')
    scenario = scenarios[0]
    if (scenario.get('phase'), scenario.get('mode'), scenario.get('status')) != ('daemon-relative', 'relative-owned', 'pass'):
        raise ValueError('wrong or failed targeted scenario')
    if scenario.get('cleanup_errors') or not all(scenario.get(key) is True for key in (
            'host_exited', 'relative_target_exited', 'relative_target_profile_removed')):
        raise ValueError('targeted cleanup failed')
    if result.get('host_unchanged_after') is not True:
        raise ValueError('frozen Electron fixture preservation not proved')
    return 1


def capture_evidence_check(base, sha):
    result = json.loads((base / 'targeted-result.json').read_text())
    expected = {'source_sha': sha, 'mode': CAPTURE, 'status': 'captured',
                'diagnostic_capture_executed': True, 'e2e_executed': False,
                'completeness_status': 'unknown', 'consent_actions': 0, 'cdp_messages': 0,
                'negative_cases': 'not_executed', 'profile_removed': True, 'browser_exited': True,
                'chrome_version': '152.0.7977.83', 'source_comparison': 'pending_independent_review',
                'three_actions_status': 'pending_independent_review'}
    if any(result.get(k) != v for k, v in expected.items()) or result.get('cleanup_errors') != []:
        raise ValueError('capture outcome/cleanup incomplete or overclaims acceptance')
    snapshots = result.get('snapshots', [])
    if [s.get('file') for s in snapshots] != ['snapshot-1.json', 'snapshot-2.json']:
        raise ValueError('capture requires exactly two snapshots')
    launch = json.loads((base / 'browser-launch.json').read_text())
    endpoint = json.loads((base / 'endpoint-owner.json').read_text())
    config = json.loads((base / 'daemon-config.json').read_text())
    chrome = json.loads((base / 'chrome-identity.json').read_text())
    build = json.loads((base / 'build-identity.json').read_text())
    controller = json.loads((base / 'controller-after.json').read_text())
    pid = result.get('browser_pid')
    window = result.get('window_id')
    if not isinstance(pid, int) or pid <= 0 or not isinstance(window, int) or window <= 0:
        raise ValueError('capture target identity missing')
    if (launch.get('pid') != pid or endpoint.get('pid') != pid or launch.get('watchdog_seconds') != 90
            or '--user-data-dir=' + launch.get('profile', '') not in launch.get('argv', [])
            or any(a.startswith(('--remote-debugging-port', '--remote-debugging-pipe', '--remote-allow-origins')) for a in launch.get('argv', []))):
        raise ValueError('capture browser isolation mismatch')
    if (config.get('response', {}).get('source_sha') != sha or chrome.get('version') != '152.0.7977.83'
            or build.get('source_sha') != sha or not build.get('installed_sha256')
            or build.get('installed_sha256') != build.get('staged_sha256')
            or build.get('installed_sha256') != controller.get('binary_sha256')):
        raise ValueError('capture serving source/identity mismatch')
    if controller.get('permissions', {}).get('accessibility') is not True:
        raise ValueError('capture existing grant missing')
    for item in snapshots:
        path = base / item['file']
        if digest(path) != item.get('sha256'):
            raise ValueError('capture snapshot hash mismatch')
        record = json.loads(path.read_text())
        request = record.get('request', {})
        args = request.get('args', {})
        data = record.get('response', {})
        if (request.get('name') != 'get_window_state' or args.get('include_screenshot') is not False
                or args.get('pid') != pid or args.get('window_id') != window
                or data.get('pid') != pid or data.get('window_id') != window
                or not data.get('tree_markdown') or data.get('elements_complete') is not False
                or not isinstance(data.get('truncated'), bool)):
            raise ValueError('capture snapshot scope/data mismatch')
    return 2
