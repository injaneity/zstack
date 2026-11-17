#!/usr/bin/python3
import argparse
import ctypes
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import uuid

from credential_bridge import retrieve as retrieve_credential
import targeted_e2e
import fixture_e2e

STATE = Path.home() / '.local/share/cua-lume-e2e'
HOST_RUN_LOCK = STATE / 'run.lock'
RUNNER = 'libs/cua-driver/tests/runners/macos-lume/run-all.sh'
STOP_TIMEOUT = 300
BOOT_STOPPED_GRACE = 30
RUNNER_TIMEOUT = 7200
PRESSURE_NORMAL = 1
PRESSURE_POLL = 15
ENV = dict(os.environ, PATH=f'{Path.home()}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:' + os.environ.get('PATH', ''))


def command(args, *, data=None, timeout=60, output=None):
    p = subprocess.run(args, input=data, stdout=output or subprocess.PIPE,
                       stderr=subprocess.PIPE, env=ENV, timeout=timeout)
    if p.returncode:
        raise RuntimeError(f'{args[0]} failed (exit {p.returncode})')
    return p.stdout or b''


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def config():
    if not (STATE / 'config.json').is_file():
        raise ValueError(f'one-time setup required: create {STATE / "config.json"} from ' + str(Path(__file__).resolve().parent / 'config.example.json'))
    c = json.loads((STATE / 'config.json').read_text())
    for key in ('seed', 'guest_user', 'keychain_service', 'keychain_account'):
        if not isinstance(c.get(key), str) or not c[key]:
            raise ValueError(f'configure {key} in {STATE / "config.json"}')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', c['seed']):
        raise ValueError('invalid seed name')
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*', c['guest_user']):
        raise ValueError('invalid guest user')
    return c


def validate_focus_cell(cell, standalone=False):
    if cell is None:
        return
    if not isinstance(cell, str) or not (
        re.fullmatch(r'macos-appkit-[a-z0-9]+(?:-[a-z0-9]+)*', cell)
        or cell == 'macos-textedit-type-text-ax-background'
        or re.fullmatch(r'macos-(electron|tauri|wkwebview)-scroll-ax-(foreground|background)', cell)
        or re.fullmatch(r'macos-(electron|tauri|wkwebview)-left-click-ax-background-typed-sdk', cell)
    ):
        raise ValueError('invalid focus cell: select one supported exact native, shared scroll, or typed-SDK click cell')
    if standalone:
        raise ValueError('cannot combine --focus-cell and --standalone-browser')


def focus_selection(cell):
    validate_focus_cell(cell)
    harness = cell.split('-')[1]
    return harness, 'native' if harness in ('appkit', 'textedit') else 'shared'


def source_check(source, sha, focus_cell=None):
    validate_focus_cell(focus_cell)
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('sha must be a full lowercase commit SHA')
    head = command(['git', '-C', str(source), 'rev-parse', 'HEAD']).decode().strip()
    top = command(['git', '-C', str(source), 'rev-parse', '--show-toplevel']).decode().strip()
    dirty = command(['git', '-C', str(source), 'status', '--porcelain', '--untracked-files=normal'])
    if head != sha or Path(top).resolve() != source or dirty:
        raise ValueError('source must be a clean repository root at the requested SHA')
    command(['git', '-C', str(source), 'cat-file', '-e', f'{sha}:{RUNNER}'])
    if focus_cell is not None and focus_selection(focus_cell)[0] == 'appkit':
        manifest = command(['git', '-C', str(source), 'show', f'{sha}:scripts/ci/macos/appkit-cells.tsv']).decode()
        rows = [line.split('\t') for line in manifest.splitlines()]
        matches = [row for row in rows if row[0] == focus_cell]
        if len(matches) != 1 or len(matches[0]) != 2 or not re.fullmatch(r'[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*', matches[0][1]):
            raise ValueError('focus cell is not uniquely registered in the pinned source manifest')


def vm(name, timeout=60):
    return json.loads(command(['lume', 'get', name, '--format', 'json'], timeout=timeout))[0]


def unresolved_dir():
    return STATE / 'unresolved'


def vm_process_alive(name):
    p = subprocess.run(['pgrep', '-f', f'lume run {name}( |$)'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=ENV)
    if p.returncode not in (0, 1):
        raise RuntimeError(f'pgrep failed (exit {p.returncode})')
    return p.returncode == 0


def stop_and_confirm(name):
    try:
        command(['lume', 'stop', name], timeout=STOP_TIMEOUT)
    except Exception:
        pass
    try:
        return vm(name)['status'] == 'stopped' and not vm_process_alive(name)
    except Exception:
        return False


def stop_hint(name):
    return (f'no lume-e2e run owns it now. Stop it with `lume-e2e --stop-leftover {name}`, which refuses while '
            'any lume-e2e run is live; never `lume stop` a cua-e2e worker by hand, since a running one may belong '
            'to a live run')


def resolve_leftovers():
    marks = unresolved_dir()
    for mark in sorted(marks.glob('cua-e2e-*')) if marks.is_dir() else []:
        if not stop_and_confirm(mark.name):
            raise ValueError(f'earlier worker {mark.name} could not be stopped; {stop_hint(mark.name)}')
        mark.unlink()
    running = sorted(v['name'] for v in json.loads(command(['lume', 'ls', '--format', 'json']) or b'[]')
                     if v.get('name', '').startswith('cua-e2e-') and v.get('status') != 'stopped')
    if running:
        raise ValueError(f'earlier worker(s) still running: {", ".join(running)}; {stop_hint("<worker>")}')


def stop_leftover(name, lock_path=None):
    if not re.fullmatch(r'cua-e2e-\d{8}T\d{6}Z-[0-9a-f]{8}', name):
        raise ValueError(f'{name} is not a lume-e2e worker name (cua-e2e-<run id>)')
    lock_path = Path(lock_path or HOST_RUN_LOCK)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(f'a lume-e2e run is live and holds run.lock; {name} may be its worker, which it stops '
                             'itself. Not stopping it; try again after that run ends') from None
        if not stop_and_confirm(name):
            raise ValueError(f'{name} is not confirmed stopped after lume stop (it reads running, or its lume run '
                             'process is still up)')
        (unresolved_dir() / name).unlink(missing_ok=True)
    return f'{name} is stopped; no lume-e2e run owned it'


def readiness(c):
    if sys.platform != 'darwin':
        raise ValueError('run on the configured macOS execution host')
    for name in ('lume', 'git', 'ssh', 'scp', 'security', 'tar'):
        if not shutil.which(name, path=ENV['PATH']):
            raise ValueError(f'missing dependency: {name}')
    console = command(['stat', '-f', '%Su', '/dev/console']).decode().strip()
    user = command(['id', '-un']).decode().strip()
    if console != user:
        raise ValueError('host must have a logged-in console session for the execution user')
    command(['launchctl', 'print', f'gui/{os.getuid()}'])
    if vm(c['seed'])['status'] != 'stopped':
        raise ValueError('seed must be stopped; it will never be booted or stopped by this command')
    free = shutil.disk_usage(STATE).free
    if free < c.get('minimum_free_gib', 80) * 1024 ** 3:
        raise ValueError('insufficient free disk space')


def guest_script(remote, sha, standalone, focus_cell=None, targeted=None, fixture=None, accessibility_handoff=False):
    q = shlex.quote
    selection = f' --retry-cell {q(focus_cell)} --retry-harness {q(focus_selection(focus_cell)[0])} --retry-only --retry-attempts 1' if focus_cell else ''
    dispatch = targeted_e2e.guest_dispatch(remote, targeted, RUNNER) if targeted else f'bash {RUNNER}' + (' --standalone-browser' if standalone else '') + selection
    if fixture:
        fixture_e2e.validate_selection(fixture, focus_cell, standalone, targeted, accessibility_handoff)
        dispatch = 'bash ' + fixture_e2e.ENTRYPOINT + (' --accessibility-handoff' if accessibility_handoff else '')
    inventory_only = targeted and targeted['id'] == targeted_e2e.containment_preflight.TARGET
    admission = '' if inventory_only else '''IFS= read -r CUA_E2E_SIGNING_KEYCHAIN_PASSWORD < unlock
rm unlock
export CUA_E2E_SIGNING_KEYCHAIN_PASSWORD'''
    return f'''#!/bin/bash
set -euo pipefail
umask 077
cd {q(remote)}
exec > runner.log 2>&1
finish() {{
  rc=$?
  trap - EXIT
  unset CUA_E2E_SIGNING_KEYCHAIN_PASSWORD
  printf '%s\\n' "$rc" > {q(remote + '/exit.tmp')}
  mv {q(remote + '/exit.tmp')} {q(remote + '/exit')}
}}
trap finish EXIT
[[ -z "${{SSH_CONNECTION:-}}" && -z "${{SSH_TTY:-}}" ]]
[[ "$(stat -f '%Su' /dev/console)" == "$(id -un)" ]]
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
[[ "$(tr -d '[:space:]' < source/.cua-e2e-source-sha)" == {q(sha)} ]]
{admission}
cd source
{dispatch}
'''


def safe_extract(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        total = 0
        for m in members:
            path = Path(m.name)
            if path.is_absolute() or '..' in path.parts or not (m.isdir() or m.isfile()):
                raise ValueError(f'unsafe artifact member: {m.name!r}')
            total += m.size
            if total > 20 * 1024 ** 3:
                raise ValueError('artifact archive exceeds 20 GiB limit')
        for m in members:
            target = destination / m.name
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(m) as src, target.open('xb') as dst:
                    shutil.copyfileobj(src, dst)


def evidence_check(directory, sha, focus_cell=None):
    a = directory / 'source/artifacts/cua-driver/macos'
    if (a / 'requested-source-sha.txt').read_text().strip() != sha:
        raise ValueError('evidence SHA mismatch')
    failures = json.loads((a / 'failures.json').read_text())
    rows = [json.loads(line) for line in (a / 'results.jsonl').read_text().splitlines() if line.strip()]
    if not rows or failures['failure_count'] or failures['preflight_failed'] or failures['report_failed'] or failures['video_failures'] or failures['lanes']:
        raise ValueError('canonical evidence records failure or is incomplete')
    if any(row['test_status'] != 'pass' for row in rows):
        raise ValueError('non-passing typed result')
    if not (a / 'summary.md').is_file():
        raise ValueError('missing canonical summary')
    retry_path = a / 'retry-record.json'
    if focus_cell is None:
        if retry_path.exists() or (failures.get('suite'), failures.get('filters')) != ('all', {'cell': '', 'harness': ''}):
            raise ValueError('selected or incomplete-scope evidence cannot certify the full matrix')
    else:
        harness, lane = focus_selection(focus_cell)
        if len(rows) != 1 or rows[0].get('cell_id') != focus_cell or rows[0].get('harness') != harness:
            raise ValueError('focused evidence must contain exactly the selected cell')
        if (failures.get('suite'), failures.get('filters')) != (lane, {'cell': focus_cell, 'harness': harness}):
            raise ValueError('focused evidence selection does not match the requested native lane')
        expected = {
            'schema': 'cua-driver/e2e-retry-record@v1', 'source_sha': sha,
            'mode': 'retry-only',
            'selection': {'cell_id': focus_cell, 'harness': harness, 'internal_lane': lane, 'allowed_attempts': 1},
            'initial_attempt': None,
            'retry_attempts': [{'attempt': 1, 'exit_code': 0, 'status': 'pass', 'evidence': '.'}],
            'final_status': 'pass-retry-only',
        }
        if json.loads(retry_path.read_text()) != expected:
            raise ValueError('focused evidence must record exactly one successful selected attempt')
    return len(rows)


def pressure_level():
    libc = ctypes.CDLL(None, use_errno=True)
    level, size = ctypes.c_int(), ctypes.c_size_t(ctypes.sizeof(ctypes.c_int))
    if libc.sysctlbyname(b'kern.memorystatus_vm_pressure_level', ctypes.byref(level), ctypes.byref(size), None, 0):
        raise OSError(ctypes.get_errno(), 'sysctlbyname kern.memorystatus_vm_pressure_level failed')
    return level.value


def wait_for_memory():
    level = pressure_level()
    if level > PRESSURE_NORMAL:
        print(f'waiting for host memory pressure to return to normal (level {level})', flush=True)
    while level > PRESSURE_NORMAL:
        time.sleep(PRESSURE_POLL)
        level = pressure_level()


def acquire_run_lock(lock):
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('waiting for another local lume-e2e run to release run.lock', flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)


def run(args):
    focus_cell = getattr(args, 'focus_cell', None)
    validate_focus_cell(focus_cell, getattr(args, 'standalone_browser', False))
    targeted_path = getattr(args, 'targeted_package', None)
    fixture = getattr(args, 'fixture', None)
    accessibility_handoff = getattr(args, 'accessibility_handoff', False)
    fixture_e2e.validate_selection(fixture, focus_cell, getattr(args, 'standalone_browser', False), targeted_path, accessibility_handoff)
    if targeted_path and (focus_cell or args.standalone_browser):
        raise ValueError('cannot combine --targeted-package with other test selections')
    targeted = targeted_e2e.package_check(targeted_path, args.sha) if targeted_path else None
    inventory_only = targeted and targeted['id'] == targeted_e2e.containment_preflight.TARGET
    c = config()
    native_allow = targeted and targeted['id'] == 'pr3140-consent-allow'
    if native_allow:
        if c['seed'] != targeted['expected_seed'] or c.get('seed_note') != targeted['expected_seed_note']:
            raise ValueError('native seed differs from reviewed test-only lineage')
        binary = Path.home()/'.local/share/lume/lume.app/Contents/MacOS/lume'
        if targeted_e2e.digest(binary) != targeted['lume_binary_sha256']:
            raise ValueError('reviewed Lume internal-share implementation changed')
    if inventory_only and (c['seed'] != targeted['expected_seed'] or c.get('seed_note') != targeted.get('expected_seed_note')):
        raise ValueError('inventory seed differs from reviewed contract')
    if targeted and c['guest_user'] != 'lume':
        raise ValueError('this reviewed targeted fixture requires the lume guest user')
    source = Path(args.source).resolve()
    source_check(source, args.sha, focus_cell)
    if fixture:
        command(['git', '-C', str(source), 'cat-file', '-e', f'{args.sha}:{fixture_e2e.ENTRYPOINT}'])
    STATE.mkdir(parents=True, exist_ok=True)
    HOST_RUN_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with HOST_RUN_LOCK.open('a') as lock:
        acquire_run_lock(lock)
        readiness(c)
        resolve_leftovers()
        wait_for_memory()
        password = b''
        if not inventory_only:
            password = retrieve_credential(c['keychain_service'], c['keychain_account'],
                                           str(Path(c.get('keychain_path', '~/Library/Keychains/login.keychain-db')).expanduser()))
            if not password or b'\n' in password:
                raise ValueError('keychain credential must be a nonempty single line')
        run_id = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8]
        worker = 'cua-e2e-' + run_id
        out = STATE / 'runs' / run_id
        out.mkdir(parents=True)
        result = {'run_id': run_id, 'sha': args.sha, 'worker': worker,
                  'mode': 'fixture' if fixture else ('targeted' if targeted else ('focused' if focus_cell else 'full')), 'focus_cell': focus_cell,
                  'standalone_browser': args.standalone_browser, 'clone_source': c['seed'],
                  'clone_source_note': c.get('seed_note', 'provenance not recorded'), 'status': 'incomplete',
                  'runner_exit': None, 'evidence_collected': False, 'worker_stopped': False}
        if fixture:
            result['fixture'] = fixture
            result['accessibility_handoff'] = accessibility_handoff
        if targeted:
            result.update(targeted_id=targeted['id'], targeted_package_sha256=targeted_e2e.digest(Path(targeted_path) / 'targeted-package.json'), e2e_executed=False)
        save(out / 'result.json', result)
        print(f'run={run_id}\nevidence={out}', flush=True)
        ssh = None
        remote = None
        owned = False
        try:
            with (out / 'source.tar').open('wb') as f:
                command(['git', '-C', str(source), 'archive', '--format=tar', args.sha], output=f, timeout=180)
            command(['lume', 'clone', c['seed'], worker], timeout=180)
            owned = True
            command(['lume', 'run', worker, '--detach', '--display', 'none'], timeout=60)
            boot_started = time.monotonic()
            deadline = boot_started + 180
            def left():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('guest SSH readiness timed out')
                return remaining
            while True:
                info = vm(worker, timeout=left())
                if (inventory_only or native_allow) and info.get('sharedDirectories', '__missing__') not in (None, []):
                    raise ValueError('inventory worker has shared host directories')
                if info['status'] == 'stopped':
                    if time.monotonic() - boot_started < BOOT_STOPPED_GRACE:
                        time.sleep(min(2, left()))
                        continue
                    raise ValueError('worker stopped during boot')
                ip = info.get('ipAddress')
                if ip:
                    ipaddress.ip_address(ip)
                    ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=accept-new', f'{c["guest_user"]}@{ip}']
                    try:
                        home = command(ssh + ['printf "%s" "$HOME"'], timeout=min(15, left())).decode()
                        if home != f'/Users/{c["guest_user"]}':
                            raise ValueError('unexpected guest home')
                        left()
                        break
                    except RuntimeError:
                        pass
                time.sleep(min(3, left()))
            remote = f'{home}/cua-e2e-{run_id}'
            qremote = shlex.quote(remote)
            command(ssh + [f'umask 077; mkdir {qremote}; mkdir {qremote}/source'])
            with (out / 'source.tar').open('rb') as f:
                p = subprocess.run(ssh + [f'tar -xf - -C {qremote}/source'], stdin=f, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=ENV, timeout=300)
                if p.returncode:
                    raise ValueError('source transfer failed')
            command(ssh + [f'printf %s {args.sha} > {qremote}/source/.cua-e2e-source-sha'])
            if targeted:
                targeted_e2e.transfer(targeted_path, targeted, out, ssh, remote, command, ENV)
            script = guest_script(remote, args.sha, args.standalone_browser, focus_cell, targeted, fixture, accessibility_handoff)
            fifo = '' if inventory_only else f'; mkfifo -m 600 {qremote}/unlock'
            command(ssh + [f'cat > {qremote}/run.command; chmod 700 {qremote}/run.command'+fifo], data=script.encode())
            label = 'com.trycua.e2e.' + uuid.uuid4().hex
            plist = plistlib.dumps({'Label': label, 'ProgramArguments': ['/usr/bin/open', '-a', 'Terminal', remote + '/run.command'], 'RunAtLoad': True, 'LimitLoadToSessionType': 'Aqua'})
            command(ssh + [f'cat > {qremote}/launcher.plist'], data=plist)
            uid = command(ssh + ['id -u']).decode().strip()
            if not uid.isdigit():
                raise ValueError('invalid guest UID')
            command(ssh + [f'launchctl bootstrap gui/{uid} {qremote}/launcher.plist'])
            if not inventory_only:
                command(ssh + [f'cat > {qremote}/unlock'], data=password + b'\n', timeout=60)
            password = b''
            deadline = time.monotonic() + args.timeout
            while True:
                status = command(ssh + [f'if test -f {qremote}/exit; then cat {qremote}/exit; else echo running; tail -n 1 {qremote}/runner.log 2>/dev/null || true; fi'], timeout=30).decode().strip()
                if re.fullmatch(r'\d+', status):
                    result['runner_exit'] = int(status)
                    result['status'] = 'test_failed' if int(status) else 'passed'
                    break
                print(f'{time.strftime("%H:%M:%S")} {status}', flush=True)
                if time.monotonic() >= deadline:
                    raise TimeoutError('runner exceeded configured timeout')
                time.sleep(15)
        except (Exception, KeyboardInterrupt) as e:
            result['status'] = 'cancelled' if isinstance(e, KeyboardInterrupt) else 'infrastructure_failed'
            result['error'] = str(e) or type(e).__name__
        finally:
            password = b''
            if ssh and remote:
                try:
                    with (out / 'evidence.tar').open('wb') as f:
                        command(ssh + [f'cd {shlex.quote(remote)} && set --; for item in runner.log exit source/artifacts; do if test -e "$item"; then set -- "$@" "$item"; fi; done; test "$#" -gt 0 && tar -cf - "$@"'], output=f, timeout=300)
                    safe_extract(out / 'evidence.tar', out / 'evidence')
                    result['evidence_collected'] = True
                    if targeted:
                        result.update(targeted_e2e.observe(out / 'evidence', args.sha, targeted))
                except Exception as e:
                    result['collection_error'] = str(e)
                    if result['status'] == 'passed':
                        result['status'] = 'incomplete'
                if result['status'] == 'passed':
                    try:
                        if fixture:
                            result['fixture_cases_passed'] = fixture_e2e.evidence_check(out / 'evidence', args.sha, accessibility_handoff)
                        elif targeted:
                            count = targeted_e2e.evidence_check(out / 'evidence', args.sha, targeted)
                            if inventory_only:
                                result.update(inventory_checks_passed=count,e2e_executed=False,scope='metadata_only')
                            elif targeted['id'] == targeted_e2e.CAPTURE:
                                result.update(diagnostic_captures_passed=count, e2e_executed=False)
                            else:
                                result.update(targeted_scenarios_passed=count, e2e_executed=True)
                        else:
                            count = evidence_check(out / 'evidence', args.sha, focus_cell)
                            result['focused_passing_cells' if focus_cell else 'passing_cells'] = count
                        if args.standalone_browser:
                            browser = out / 'evidence/source/artifacts/cua-driver/macos-standalone-browser'
                            browser_rows = [json.loads(line) for line in (browser / 'results.jsonl').read_text().splitlines() if line.strip()]
                            if not browser_rows or any(row['test_status'] != 'pass' for row in browser_rows) or not (browser / 'summary.md').is_file():
                                raise ValueError('standalone browser evidence is incomplete or failed')
                            result['standalone_passing_cells'] = len(browser_rows)
                        scoped_stop = targeted and targeted['id'] == targeted_e2e.consent_acceptance.TARGET
                        if not inventory_only and not scoped_stop:
                            daemon = command(ssh + ['~/.local/bin/cua-driver-local status']).decode()
                            (out / 'daemon-after.txt').write_text(daemon)
                            if 'permission mode: standard' not in daemon:
                                raise ValueError('standard daemon restoration not verified')
                    except Exception as e:
                        result['status'] = 'incomplete'
                        result['validation_error'] = str(e)
            if owned:
                try:
                    command(['lume', 'stop', worker], timeout=STOP_TIMEOUT)
                    result['worker_stopped'] = vm(worker)['status'] == 'stopped'
                    if not result['worker_stopped']:
                        raise ValueError('worker remains running')
                except Exception as e:
                    result['cleanup_error'] = str(e) or type(e).__name__
                    unresolved_dir().mkdir(exist_ok=True)
                    (unresolved_dir() / worker).write_text(run_id + '\n')
            if fixture and result['status'] == 'passed':
                result['status'] = 'fixture_passed'
            if targeted and result['status'] == 'passed':
                result['status'] = 'targeted_passed'
            save(out / 'result.json', result)
        print(json.dumps(result, indent=2), flush=True)
        return 0 if result['status'] in ('passed', 'targeted_passed', 'fixture_passed') else 1


def main():
    parser = argparse.ArgumentParser(description='Private synchronous Lume E2E wrapper; never installs, repairs, or deletes VMs.')
    parser.add_argument('--doctor', action='store_true')
    parser.add_argument('--source')
    parser.add_argument('--sha')
    parser.add_argument('--standalone-browser', action='store_true')
    parser.add_argument('--focus-cell', help='Run one supported exact cell once; not full-matrix certification')
    parser.add_argument('--targeted-package', help='Run one reviewed targeted package; never full-matrix certification')
    parser.add_argument('--fixture', choices=[fixture_e2e.FIXTURE], help='Run the reviewed embedded SDK fixture; not full-matrix certification')
    parser.add_argument('--accessibility-handoff', action='store_true', help='Wait in this owned fixture VM for an explicitly authorized app-only Accessibility grant; never requests or changes permissions')
    parser.add_argument('--timeout', type=int)
    parser.add_argument('--stop-leftover', metavar='WORKER', help='Stop one leftover cua-e2e worker; refuses while any lume-e2e run is live')
    args = parser.parse_args()
    if args.stop_leftover:
        if args.doctor or args.source or args.sha or args.focus_cell or args.targeted_package or args.fixture or args.accessibility_handoff or args.standalone_browser or args.timeout is not None:
            parser.error('--stop-leftover takes no other option')
        print(stop_leftover(args.stop_leftover))
        return 0
    validate_focus_cell(args.focus_cell, args.standalone_browser)
    fixture_e2e.validate_selection(args.fixture, args.focus_cell, args.standalone_browser, args.targeted_package, args.accessibility_handoff)
    if args.doctor and (args.fixture or args.accessibility_handoff):
        parser.error('cannot combine --doctor and --fixture')
    if args.doctor and args.targeted_package:
        parser.error('cannot combine --doctor and --targeted-package')
    if args.doctor and args.focus_cell is not None:
        parser.error('cannot combine --doctor and --focus-cell')
    def interrupted(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    if args.doctor:
        readiness(config())
        print('host and configured seed preflight passed; unlock and desktop launch are verified during execution')
        return 0
    if args.timeout is None:
        args.timeout = RUNNER_TIMEOUT
    if not args.source or not args.sha or args.timeout < 1:
        parser.error('--source, --sha, and a positive --timeout are required')
    return run(args)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print(f'blocked: {e}', file=sys.stderr)
        sys.exit(2)
