import argparse
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import lume_e2e as app

REAL_PRESSURE_LEVEL = app.pressure_level
REAL_HOST_RUN_LOCK = app.HOST_RUN_LOCK

SHA = 'a' * 40


class Tests(unittest.TestCase):
    def setUp(self):
        pressure = patch.object(app, 'pressure_level', return_value=app.PRESSURE_NORMAL)
        pressure.start()
        self.addCleanup(pressure.stop)
        lock_dir = tempfile.TemporaryDirectory()
        self.addCleanup(lock_dir.cleanup)
        host_lock = patch.object(app, 'HOST_RUN_LOCK', Path(lock_dir.name) / 'run.lock')
        host_lock.start()
        self.addCleanup(host_lock.stop)

    @unittest.skipUnless(sys.platform == 'darwin', 'kern.memorystatus_vm_pressure_level is a macOS sysctl')
    def test_the_kernel_pressure_level_is_read_natively(self):
        level = REAL_PRESSURE_LEVEL()
        self.assertIn(level, (1, 2, 4))
        sysctl = subprocess.run(['sysctl', '-n', 'kern.memorystatus_vm_pressure_level'], capture_output=True, check=True)
        self.assertIn(int(sysctl.stdout), (1, 2, 4))

    def test_normal_pressure_starts_at_once_and_says_nothing(self):
        out = io.StringIO()
        with patch.object(app, 'pressure_level', return_value=1), patch.object(app.time, 'sleep') as sleep, patch('sys.stdout', out):
            app.wait_for_memory()
        sleep.assert_not_called()
        self.assertEqual(out.getvalue(), '')

    def test_raised_pressure_waits_until_normal_with_one_line(self):
        out = io.StringIO()
        with patch.object(app, 'pressure_level', side_effect=[4, 2, 2, 1]), patch.object(app.time, 'sleep') as sleep, patch('sys.stdout', out):
            app.wait_for_memory()
        self.assertEqual(sleep.call_count, 3)
        self.assertEqual(out.getvalue().count('waiting'), 1)

    def test_raised_pressure_holds_the_clone_and_stops_nothing(self):
        calls = []
        class Waiting(Exception):
            pass
        def sleep(_):
            raise Waiting()
        def command(args, **kwargs):
            calls.append(args[:2])
            return b'[]' if args[:2] == ['lume', 'ls'] else b''
        args = argparse.Namespace(source='.', sha=SHA, standalone_browser=False, timeout=10, focus_cell=None, fixture=None)
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'STATE', Path(tmp)), patch.object(app, 'config', return_value={'seed': 'seed', 'guest_user': 'lume', 'keychain_service': 's', 'keychain_account': 'a'}), patch.object(app, 'source_check'), patch.object(app, 'readiness'), patch.object(app, 'command', side_effect=command), patch.object(app, 'pressure_level', return_value=2), patch.object(app.time, 'sleep', side_effect=sleep), patch('sys.stdout', io.StringIO()):
            with self.assertRaises(Waiting):
                app.run(args)
        self.assertIn(['lume', 'ls'], calls)
        self.assertFalse(any(c[:2] in (['lume', 'clone'], ['lume', 'stop'], ['lume', 'run']) for c in calls))

    def test_the_default_runner_timeout_is_two_hours(self):
        with patch('sys.argv', ['lume-e2e', '--source', '.', '--sha', SHA]), patch.object(app, 'run', return_value=0) as run, patch.object(app.signal, 'signal'):
            app.main()
        self.assertEqual(run.call_args.args[0].timeout, 7200)

    def test_guest_script_syntax_and_exit_location(self):
        for browser in (False, True):
            script = app.guest_script('/Users/lume/cua-e2e-test', SHA, browser)
            subprocess.run(['bash', '-n'], input=script.encode(), check=True)
            self.assertIn('> /Users/lume/cua-e2e-test/exit.tmp', script)
            self.assertIn('${SSH_CONNECTION:-}', script)
            self.assertNotIn('unset SSH', script)
            self.assertEqual('--standalone-browser' in script, browser)
            self.assertNotIn('lume\n', script)

    def test_invalid_focus_is_refused_before_source_or_vm_access(self):
        for cell in ['', '../escape', 'macos-appkit-x;echo unsafe', 'macos-swiftui-ax-tree-ax-not-applicable']:
            with self.subTest(cell=cell), patch.object(app, 'command') as command:
                with self.assertRaisesRegex(ValueError, 'invalid focus cell'):
                    app.run(argparse.Namespace(focus_cell=cell, standalone_browser=False))
                command.assert_not_called()

    def test_typed_sdk_click_focus_is_exact_and_selects_shared_lane(self):
        for harness in ('electron', 'tauri', 'wkwebview'):
            cell = f'macos-{harness}-left-click-ax-background-typed-sdk'
            self.assertEqual(app.focus_selection(cell), (harness, 'shared'))
            script = app.guest_script('/Users/lume/cua-e2e-test', SHA, False, cell)
            subprocess.run(['bash', '-n'], input=script.encode(), check=True)
            self.assertIn(f'--retry-cell {cell} --retry-harness {harness} --retry-only --retry-attempts 1', script)
            for invalid in (cell + '-extra', cell.replace('background', 'foreground')):
                with self.assertRaises(ValueError):
                    app.validate_focus_cell(invalid)

    def test_focus_cannot_be_combined_with_standalone_browsers(self):
        with patch.object(app, 'command') as command:
            with self.assertRaisesRegex(ValueError, 'cannot combine'):
                app.run(argparse.Namespace(focus_cell='macos-appkit-ax-tree-ax-not-applicable', standalone_browser=True))
            command.assert_not_called()

    def test_focus_cell_must_belong_to_the_pinned_source_manifest(self):
        manifest = b'macos-appkit-ax-tree-ax-not-applicable\tharness_appkit_smoke\n'
        cell = 'macos-appkit-snapshot-publication-ax-foreground'
        source = Path('/tmp').resolve()
        with patch.object(app, 'command', side_effect=[SHA.encode(), str(source).encode(), b'', b'', manifest]) as command:
            with self.assertRaisesRegex(ValueError, 'not uniquely registered'):
                app.source_check(source, SHA, cell)
            self.assertEqual(command.call_args.args[0][-1], f'{SHA}:scripts/ci/macos/appkit-cells.tsv')

    def test_credential_response_validation(self):
        from credential_bridge import decode
        self.assertEqual(decode(b'OK\nexample'), b'example')
        for response in (b'', b'ERROR\n', b'OK\n', b'OK\na\nb', b'OK\n' + b'a' * 4097):
            with self.subTest(length=len(response)), self.assertRaises((ValueError, RuntimeError)):
                decode(response)

    def test_source_sha_rejected_before_subprocess(self):
        with patch.object(app, 'command') as command:
            with self.assertRaises(ValueError):
                app.source_check(Path('/tmp'), '--unsafe')
            command.assert_not_called()

    def test_dirty_or_wrong_source_refused(self):
        for head, dirty in [(SHA, b' M file'), ('b' * 40, b'')]:
            with patch.object(app, 'command', side_effect=[head.encode(), b'/tmp', dirty]):
                with self.assertRaises(ValueError):
                    app.source_check(Path('/tmp'), SHA)

    def test_safe_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'a.tar'
            with tarfile.open(archive, 'w') as tar:
                info = tarfile.TarInfo('source/artifacts/result.json')
                info.size = 2
                tar.addfile(info, io.BytesIO(b'{}'))
            app.safe_extract(archive, Path(tmp) / 'out')
            self.assertEqual((Path(tmp) / 'out/source/artifacts/result.json').read_text(), '{}')

    def test_unsafe_archive_refused(self):
        for name, kind in [('../escape', tarfile.REGTYPE), ('/escape', tarfile.REGTYPE), ('link', tarfile.SYMTYPE), ('hardlink', tarfile.LNKTYPE)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                archive = Path(tmp) / 'a.tar'
                with tarfile.open(archive, 'w') as tar:
                    info = tarfile.TarInfo(name)
                    info.type = kind
                    tar.addfile(info)
                with self.assertRaises(ValueError):
                    app.safe_extract(archive, Path(tmp) / 'out')

    def test_config_requires_values_not_certification_boolean(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'STATE', Path(tmp)):
            config = {'seed': 'worker', 'guest_user': 'lume', 'keychain_service': 's', 'keychain_account': 'a'}
            path = Path(tmp) / 'config.json'
            path.write_text(json.dumps(config))
            self.assertEqual(app.config(), config)
            config['seed'] = '../unsafe'
            path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, 'invalid seed'):
                app.config()

    def test_running_clone_source_is_refused(self):
        with patch.object(app.sys, 'platform', 'darwin'), patch.object(app.shutil, 'which', return_value='/bin/tool'), patch.object(app, 'command', side_effect=[b'user', b'user', b'']), patch.object(app, 'vm', return_value={'status': 'running'}):
            with self.assertRaisesRegex(ValueError, 'seed must be stopped'):
                app.readiness({'seed': 'worker'})

    def focused_evidence(self, tmp):
        cell = 'macos-appkit-ax-tree-ax-not-applicable'
        root = Path(tmp) / 'source/artifacts/cua-driver/macos'
        root.mkdir(parents=True)
        (root / 'requested-source-sha.txt').write_text(SHA)
        (root / 'failures.json').write_text(json.dumps({
            'failure_count': 0, 'preflight_failed': False, 'report_failed': False,
            'video_failures': [], 'lanes': [], 'suite': 'native',
            'filters': {'cell': cell, 'harness': 'appkit'},
        }))
        (root / 'results.jsonl').write_text(json.dumps({'cell_id': cell, 'harness': 'appkit', 'test_status': 'pass'}) + '\n')
        (root / 'summary.md').write_text('focused test evidence\n')
        (root / 'retry-record.json').write_text(json.dumps({
            'schema': 'cua-driver/e2e-retry-record@v1', 'source_sha': SHA,
            'mode': 'retry-only',
            'selection': {'cell_id': cell, 'harness': 'appkit', 'internal_lane': 'native', 'allowed_attempts': 1},
            'initial_attempt': None,
            'retry_attempts': [{'attempt': 1, 'exit_code': 0, 'status': 'pass', 'evidence': '.'}],
            'final_status': 'pass-retry-only',
        }))
        return root, cell

    def test_focused_evidence_requires_exactly_the_selected_passing_cell(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, cell = self.focused_evidence(tmp)
            self.assertEqual(app.evidence_check(Path(tmp), SHA, cell), 1)
            row = json.loads((root / 'results.jsonl').read_text())
            for rows in [[], [dict(row, cell_id='another-cell')], [row, row], [dict(row, test_status='fail')], [dict(row, harness='swiftui')]]:
                (root / 'results.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
                with self.subTest(rows=rows), self.assertRaises(ValueError):
                    app.evidence_check(Path(tmp), SHA, cell)

    def test_focused_evidence_cannot_be_claimed_as_full_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self.focused_evidence(tmp)
            with self.assertRaisesRegex(ValueError, 'full matrix'):
                app.evidence_check(Path(tmp), SHA)
            (root / 'retry-record.json').unlink()
            with self.assertRaisesRegex(ValueError, 'full matrix'):
                app.evidence_check(Path(tmp), SHA)

    def test_focused_evidence_rejects_missing_or_multiple_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, cell = self.focused_evidence(tmp)
            path = root / 'retry-record.json'
            record = json.loads(path.read_text())
            for change in [{'source_sha': 'b' * 40}, {'initial_attempt': {}}, {'retry_attempts': []}, {'retry_attempts': record['retry_attempts'] * 2}]:
                path.write_text(json.dumps(dict(record, **change)))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    app.evidence_check(Path(tmp), SHA, cell)
            path.unlink()
            with self.assertRaises(FileNotFoundError):
                app.evidence_check(Path(tmp), SHA, cell)

    def test_focused_run_selects_one_cell_and_stops_after_failure(self):
        cell = 'macos-appkit-ax-tree-ax-not-applicable'
        with tempfile.TemporaryDirectory() as tmp:
            rc, result = self.simulate(tmp, focus_cell=cell)
            script = (Path(tmp) / 'guest-command.sh').read_text()
            self.assertIn(f'--retry-cell {cell} --retry-harness appkit --retry-only --retry-attempts 1', script)
            self.assertEqual(result['mode'], 'focused')
            self.assertEqual(result['focus_cell'], cell)
            self.assertEqual(rc, 1)
            self.assertEqual(result['status'], 'test_failed')
            self.assertNotIn('passing_cells', result)

    def test_focused_success_is_not_reported_as_full_matrix_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, result = self.simulate(tmp, runner_exit='0', focus_cell='macos-appkit-ax-tree-ax-not-applicable', valid_evidence=True)
            self.assertEqual(rc, 0)
            self.assertEqual(result['status'], 'passed')
            self.assertEqual(result['mode'], 'focused')
            self.assertEqual(result['focused_passing_cells'], 1)
            self.assertNotIn('passing_cells', result)
            self.assertTrue(result['evidence_collected'])

    def test_focused_success_requires_standard_daemon_restoration(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, result = self.simulate(tmp, runner_exit='0', focus_cell='macos-appkit-ax-tree-ax-not-applicable', valid_evidence=True, daemon_standard=False)
            self.assertEqual(rc, 1)
            self.assertEqual(result['status'], 'incomplete')
            self.assertIn('standard daemon', result['validation_error'])

    def test_killed_lume_stop_reports_cleanup_error_without_changing_the_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, result = self.simulate(tmp, runner_exit='0', focus_cell='macos-appkit-ax-tree-ax-not-applicable', valid_evidence=True, stop_fails=True)
            self.assertEqual(rc, 0)
            self.assertEqual(result['status'], 'passed')
            self.assertFalse(result['worker_stopped'])
            self.assertIn('lume failed', result['cleanup_error'])

    def test_stop_gets_its_own_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.simulate(tmp, runner_exit='0', focus_cell='macos-appkit-ax-tree-ax-not-applicable', valid_evidence=True)
            self.assertEqual(self.stop_timeout, app.STOP_TIMEOUT)
            self.assertGreater(app.STOP_TIMEOUT, 90)

    def simulate(self, tmp, runner_exit='1', collect_fails=False, focus_cell=None, valid_evidence=False, daemon_standard=True, fixture=None, stop_fails=False, boot=()):
        config = {'seed': 'seed', 'guest_user': 'lume', 'keychain_service': 'service', 'keychain_account': 'account'}
        calls = []
        def command(args, **kwargs):
            calls.append(args)
            if args[:2] == ['lume', 'stop']:
                self.stop_timeout = kwargs.get('timeout')
                if stop_fails:
                    raise RuntimeError('lume failed (exit -9)')
            if args[0] == 'security':
                return b'secret\n'
            if args[0] == 'ssh':
                text = args[-1]
                if text == 'printf "%s" "$HOME"':
                    return b'/Users/lume'
                if text == 'id -u':
                    return b'501'
                if '/run.command;' in text:
                    (Path(tmp) / 'guest-command.sh').write_bytes(kwargs['data'])
                if text.startswith('if test -f'):
                    return runner_exit.encode()
                if text == '~/.local/bin/cua-driver-local status':
                    return b'permission mode: standard' if daemon_standard else b'permission mode: unrestricted'
                if 'tar -cf' in text:
                    if collect_fails:
                        raise RuntimeError('collection failed')
                    with tarfile.open(fileobj=kwargs['output'], mode='w') as tar:
                        info = tarfile.TarInfo('runner.log')
                        info.size = 4
                        tar.addfile(info, io.BytesIO(b'fail'))
                        if valid_evidence:
                            fixture = Path(tmp) / 'guest-evidence'
                            self.focused_evidence(fixture)
                            for file in sorted(fixture.rglob('*')):
                                if file.is_file():
                                    tar.add(file, arcname=str(file.relative_to(fixture)))
            return b''
        args = argparse.Namespace(source=tmp, sha=SHA, standalone_browser=False, timeout=10, focus_cell=focus_cell, fixture=fixture)
        with patch.object(app, 'STATE', Path(tmp)), patch.object(app, 'config', return_value=config), patch.object(app, 'source_check'), patch.object(app, 'readiness'), patch.object(app, 'retrieve_credential', return_value=b'secret'), patch.object(app, 'command', side_effect=command), patch.object(app, 'vm', side_effect=[{'status': s} for s in boot] + [{'status': 'running', 'ipAddress': '192.0.2.1'}, {'status': 'running' if stop_fails else 'stopped'}]), patch.object(app.time, 'sleep'), patch.object(app.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
            rc = app.run(args)
        result = json.loads(next((Path(tmp) / 'runs').glob('*/result.json')).read_text())
        self.assertEqual(result['worker_stopped'], not stop_fails)
        self.assertTrue(any(c[:2] == ['lume', 'stop'] for c in calls))
        self.assertFalse(any(c[:2] == ['lume', 'delete'] for c in calls))
        self.assertNotIn('secret', json.dumps(result))
        return rc, result

    def test_fixture_success_is_distinct_and_preserves_owned_worker_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app.fixture_e2e, 'evidence_check', return_value=10) as validate:
            rc, result = self.simulate(tmp, runner_exit='0', fixture='embedded-sdk-window')
            self.assertEqual(rc, 0)
            self.assertEqual(result['mode'], 'fixture')
            self.assertEqual(result['status'], 'fixture_passed')
            self.assertEqual(result['fixture_cases_passed'], 10)
            self.assertNotIn('passing_cells', result)
            self.assertNotIn('focused_passing_cells', result)
            validate.assert_called_once()

    def test_fixture_invalid_evidence_still_collects_and_stops(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app.fixture_e2e, 'evidence_check', side_effect=ValueError('missing native geometry')):
            rc, result = self.simulate(tmp, runner_exit='0', fixture='embedded-sdk-window')
            self.assertEqual(rc, 1)
            self.assertEqual(result['status'], 'incomplete')
            self.assertTrue(result['evidence_collected'])
            self.assertEqual(result['validation_error'], 'missing native geometry')

    def test_fixture_runner_failure_never_claims_execution_success(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app.fixture_e2e, 'evidence_check') as validate:
            rc, result = self.simulate(tmp, fixture='embedded-sdk-window')
            self.assertEqual(rc, 1)
            self.assertEqual(result['status'], 'test_failed')
            self.assertNotIn('fixture_cases_passed', result)
            validate.assert_not_called()

    def test_failed_runner_collected_and_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, result = self.simulate(tmp)
            self.assertEqual(rc, 1)
            self.assertEqual(result['status'], 'test_failed')
            self.assertEqual(result['runner_exit'], 1)
            self.assertTrue(result['evidence_collected'])

    def test_a_first_stopped_read_during_boot_is_not_a_boot_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, result = self.simulate(tmp, boot=('stopped', 'stopped'))
            self.assertEqual(result['status'], 'test_failed')
            self.assertTrue(result['evidence_collected'])
            self.assertNotIn('error', result)

    def test_a_worker_still_stopped_after_the_grace_is_a_boot_failure(self):
        config = {'seed': 'seed', 'guest_user': 'lume', 'keychain_service': 'service', 'keychain_account': 'account'}
        clock = iter(range(0, 10_000, 10))
        args = argparse.Namespace(source='.', sha=SHA, standalone_browser=False, timeout=10, focus_cell=None, fixture=None)
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'STATE', Path(tmp)), patch.object(app, 'config', return_value=config), patch.object(app, 'source_check'), patch.object(app, 'readiness'), patch.object(app, 'retrieve_credential', return_value=b'secret'), patch.object(app, 'command', return_value=b''), patch.object(app, 'vm', return_value={'status': 'stopped'}) as vm, patch.object(app.time, 'monotonic', side_effect=lambda: next(clock)), patch.object(app.time, 'sleep'):
            rc = app.run(args)
            result = json.loads(next((Path(tmp) / 'runs').glob('*/result.json')).read_text())
        self.assertEqual(rc, 1)
        self.assertIn('worker stopped during boot', json.dumps(result))
        self.assertGreater(vm.call_count, 2)
        self.assertTrue(all(0 < c.kwargs['timeout'] <= 180 for c in vm.call_args_list if 'timeout' in c.kwargs))
        self.assertGreaterEqual(sum('timeout' in c.kwargs for c in vm.call_args_list), 2)

    def test_the_boot_window_is_never_overrun(self):
        config = {'seed': 'seed', 'guest_user': 'lume', 'keychain_service': 'service', 'keychain_account': 'account'}
        now = [0.0]
        started = [None]
        stopped = [None]
        waits = []
        def command(args, **kwargs):
            if args[:2] == ['lume', 'run']:
                started[0] = now[0]
            if args[:2] == ['lume', 'stop']:
                stopped[0] = now[0]
            if args[0] == 'ssh':
                waits.append(kwargs['timeout'])
                now[0] += kwargs['timeout']
                raise RuntimeError('ssh failed')
            return b''
        def vm(name, timeout=60):
            waits.append(timeout)
            now[0] += min(7, timeout)
            return {'status': 'running', 'ipAddress': '192.0.2.1'}
        def sleep(seconds):
            waits.append(seconds)
            now[0] += seconds
        args = argparse.Namespace(source='.', sha=SHA, standalone_browser=False, timeout=10, focus_cell=None, fixture=None)
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'STATE', Path(tmp)), patch.object(app, 'config', return_value=config), patch.object(app, 'source_check'), patch.object(app, 'readiness'), patch.object(app, 'retrieve_credential', return_value=b'secret'), patch.object(app, 'command', side_effect=command), patch.object(app, 'vm', side_effect=vm), patch.object(app.time, 'monotonic', side_effect=lambda: now[0]), patch.object(app.time, 'sleep', side_effect=sleep):
            rc = app.run(args)
            result = json.loads(next((Path(tmp) / 'runs').glob('*/result.json')).read_text())
        self.assertEqual(rc, 1)
        self.assertIn('readiness timed out', json.dumps(result))
        self.assertTrue(all(w > 0 for w in waits))
        self.assertLessEqual(stopped[0] - started[0], 180)

    def test_a_probe_answering_after_the_window_is_not_readiness(self):
        config = {'seed': 'seed', 'guest_user': 'lume', 'keychain_service': 'service', 'keychain_account': 'account'}
        now = [0.0]
        def command(args, **kwargs):
            if args[:2] == ['lume', 'run']:
                now[0] = 1000.0
            if args[0] == 'ssh':
                now[0] = 1000.0 + 181
                return b'/Users/lume'
            return b''
        args = argparse.Namespace(source='.', sha=SHA, standalone_browser=False, timeout=10, focus_cell=None, fixture=None)
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'STATE', Path(tmp)), patch.object(app, 'config', return_value=config), patch.object(app, 'source_check'), patch.object(app, 'readiness'), patch.object(app, 'retrieve_credential', return_value=b'secret'), patch.object(app, 'command', side_effect=command), patch.object(app, 'vm', return_value={'status': 'running', 'ipAddress': '192.0.2.1'}), patch.object(app.time, 'monotonic', side_effect=lambda: now[0]), patch.object(app.time, 'sleep'):
            rc = app.run(args)
            result = json.loads(next((Path(tmp) / 'runs').glob('*/result.json')).read_text())
        self.assertEqual(rc, 1)
        self.assertIn('readiness timed out', json.dumps(result))

    def test_a_worker_an_earlier_run_left_running_blocks_the_next_run(self):
        listing = json.dumps([{'name': 'seed', 'status': 'stopped'}, {'name': 'cua-e2e-old', 'status': 'running'}, {'name': 'cua-e2e-done', 'status': 'stopped'}]).encode()
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'STATE', Path(tmp)):
            with patch.object(app, 'command', return_value=listing):
                with self.assertRaisesRegex(ValueError, 'cua-e2e-old') as e:
                    app.resolve_leftovers()
            self.assertNotIn('cua-e2e-done', str(e.exception))
            with patch.object(app, 'command', return_value=json.dumps([{'name': 'cua-e2e-done', 'status': 'stopped'}]).encode()):
                app.resolve_leftovers()

    def test_a_failed_cleanup_is_stopped_again_by_the_next_run_even_if_it_reads_stopped(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'STATE', Path(tmp)):
            self.simulate(tmp, runner_exit='0', focus_cell='macos-appkit-ax-tree-ax-not-applicable', valid_evidence=True, stop_fails=True)
            mark = Path(tmp) / 'unresolved'
            self.assertEqual([m.name for m in mark.iterdir()], [next(mark.iterdir()).name])
            worker = next(mark.iterdir()).name
            self.assertTrue(worker.startswith('cua-e2e-'))
            calls = []
            def command(args, **kwargs):
                calls.append(args)
                return b'[]' if args[:2] == ['lume', 'ls'] else b''
            with patch.object(app, 'command', side_effect=command), patch.object(app, 'vm', return_value={'status': 'stopped'}):
                app.resolve_leftovers()
            self.assertIn(['lume', 'stop', worker], calls)
            self.assertFalse(any(mark.iterdir()))

    def test_an_unresolved_worker_that_will_not_stop_blocks_the_next_run(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'STATE', Path(tmp)):
            (Path(tmp) / 'unresolved').mkdir()
            (Path(tmp) / 'unresolved' / 'cua-e2e-x').write_text('x\n')
            with patch.object(app, 'command', side_effect=RuntimeError('lume failed')), patch.object(app, 'vm', return_value={'status': 'running'}), patch.object(app, 'vm_process_alive', return_value=True):
                with self.assertRaisesRegex(ValueError, 'could not be stopped'):
                    app.resolve_leftovers()
            self.assertTrue((Path(tmp) / 'unresolved' / 'cua-e2e-x').exists())

    def test_a_failed_re_stop_blocks_while_the_worker_still_boots_though_it_reads_stopped(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'STATE', Path(tmp)):
            (Path(tmp) / 'unresolved').mkdir()
            (Path(tmp) / 'unresolved' / 'cua-e2e-x').write_text('x\n')
            with patch.object(app, 'command', side_effect=RuntimeError('lume failed')), patch.object(app, 'vm', return_value={'status': 'stopped'}), patch.object(app, 'vm_process_alive', return_value=True):
                with self.assertRaisesRegex(ValueError, 'could not be stopped'):
                    app.resolve_leftovers()
            self.assertTrue((Path(tmp) / 'unresolved' / 'cua-e2e-x').exists())

    def test_a_marked_worker_already_stopped_is_cleared_though_lume_stop_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'STATE', Path(tmp)):
            (Path(tmp) / 'unresolved').mkdir()
            (Path(tmp) / 'unresolved' / 'cua-e2e-x').write_text('x\n')
            def command(args, **kwargs):
                if args[:2] == ['lume', 'stop']:
                    raise RuntimeError('lume failed (exit 1)')
                return b'[]'
            with patch.object(app, 'command', side_effect=command), patch.object(app, 'vm', return_value={'status': 'stopped'}), patch.object(app, 'vm_process_alive', return_value=False):
                app.resolve_leftovers()
            self.assertFalse((Path(tmp) / 'unresolved' / 'cua-e2e-x').exists())

    def test_blocked_messages_never_ask_for_a_bare_lume_stop(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'STATE', Path(tmp)):
            (Path(tmp) / 'unresolved').mkdir()
            (Path(tmp) / 'unresolved' / 'cua-e2e-x').write_text('x\n')
            with patch.object(app, 'command', side_effect=RuntimeError('lume failed')), patch.object(app, 'vm', return_value={'status': 'running'}), patch.object(app, 'vm_process_alive', return_value=True):
                with self.assertRaises(ValueError) as marked:
                    app.resolve_leftovers()
            (Path(tmp) / 'unresolved' / 'cua-e2e-x').unlink()
            listing = json.dumps([{'name': 'cua-e2e-old', 'status': 'running'}]).encode()
            with patch.object(app, 'command', return_value=listing):
                with self.assertRaises(ValueError) as running:
                    app.resolve_leftovers()
        for e in (marked.exception, running.exception):
            self.assertIn('--stop-leftover', str(e))
            self.assertNotRegex(str(e), r'; stop (it|them)')

    def test_stop_leftover_refuses_while_a_live_run_holds_the_lock(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'STATE', Path(tmp)):
            lock_path = Path(tmp) / 'run.lock'
            holder = subprocess.Popen(['/usr/bin/python3', '-c', 'import fcntl,sys\nf=open(sys.argv[1],"a");fcntl.flock(f,fcntl.LOCK_EX);print("held",flush=True);sys.stdin.read()', str(lock_path)], stdout=subprocess.PIPE, stdin=subprocess.PIPE)
            try:
                self.assertEqual(holder.stdout.readline().strip(), b'held')
                with patch.object(app, 'command') as command, patch.object(app, 'vm', return_value={'status': 'running'}):
                    with self.assertRaisesRegex(ValueError, 'live'):
                        app.stop_leftover('cua-e2e-20261116T201924Z-5776bfb3', lock_path)
                command.assert_not_called()
            finally:
                holder.stdin.close()
                holder.wait()

    def test_stop_leftover_stops_an_unowned_worker_and_clears_its_mark(self):
        name = 'cua-e2e-20261116T201924Z-5776bfb3'
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'STATE', Path(tmp)):
            (Path(tmp) / 'unresolved').mkdir()
            (Path(tmp) / 'unresolved' / name).write_text('x\n')
            reads = iter([{'status': 'stopped'}])
            with patch.object(app, 'command') as command, patch.object(app, 'vm', side_effect=lambda n, **k: next(reads)), patch.object(app, 'vm_process_alive', return_value=False):
                self.assertIn('stopped', app.stop_leftover(name, Path(tmp) / 'run.lock'))
            command.assert_called_once_with(['lume', 'stop', name], timeout=app.STOP_TIMEOUT)
            self.assertFalse((Path(tmp) / 'unresolved' / name).exists())

    def test_stop_leftover_keeps_the_mark_when_the_worker_will_not_stop(self):
        name = 'cua-e2e-20261116T201924Z-5776bfb3'
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'STATE', Path(tmp)):
            (Path(tmp) / 'unresolved').mkdir()
            (Path(tmp) / 'unresolved' / name).write_text('x\n')
            with patch.object(app, 'command', side_effect=RuntimeError('lume failed')), patch.object(app, 'vm', return_value={'status': 'running'}), patch.object(app, 'vm_process_alive', return_value=True):
                with self.assertRaisesRegex(ValueError, 'not confirmed stopped'):
                    app.stop_leftover(name, Path(tmp) / 'run.lock')
            self.assertTrue((Path(tmp) / 'unresolved' / name).exists())

    def test_stop_leftover_locks_the_installed_wrappers_run_lock_from_any_copy(self):
        seen = []
        real_open = Path.open
        def spy(path, *a, **k):
            seen.append(path)
            return real_open(path, *a, **k)
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'STATE', Path(tmp)), patch.object(app, 'HOST_RUN_LOCK', Path(tmp) / 'host' / 'run.lock'), patch.object(Path, 'open', spy), patch.object(app, 'command'), patch.object(app, 'vm', return_value={'status': 'stopped'}), patch.object(app, 'vm_process_alive', return_value=False):
            app.stop_leftover('cua-e2e-20261116T201924Z-5776bfb3')
            self.assertIn(Path(tmp) / 'host' / 'run.lock', seen)
        self.assertEqual(REAL_HOST_RUN_LOCK, Path.home() / '.local/share/cua-lume-e2e/run.lock')

    def test_a_run_from_any_copy_holds_the_host_lock_so_stop_leftover_refuses(self):
        name = 'cua-e2e-20261116T201924Z-5776bfb3'
        refused = []
        def command(args, **kwargs):
            if args[:2] == ['lume', 'clone']:
                with patch.object(app, 'vm', return_value={'status': 'running'}):
                    try:
                        app.stop_leftover(name)
                    except ValueError as e:
                        refused.append(str(e))
                raise RuntimeError('stop here')
            return b'[]'
        args = argparse.Namespace(source='.', sha=SHA, standalone_browser=False, timeout=10, focus_cell=None, fixture=None)
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'STATE', Path(tmp) / 'some-other-copy'), patch.object(app, 'config', return_value={'seed': 'seed', 'guest_user': 'lume', 'keychain_service': 's', 'keychain_account': 'a'}), patch.object(app, 'source_check'), patch.object(app, 'readiness'), patch.object(app, 'retrieve_credential', return_value=b'secret'), patch.object(app, 'command', side_effect=command):
            app.run(args)
        self.assertEqual(len(refused), 1)
        self.assertIn('live', refused[0])

    def test_stop_leftover_takes_no_other_option(self):
        for extra in (['--timeout', '5'], ['--timeout', str(app.RUNNER_TIMEOUT)], ['--doctor'], ['--sha', SHA]):
            p = subprocess.run(['/usr/bin/python3', str(Path(app.__file__)), '--stop-leftover', 'cua-e2e-20261116T201924Z-5776bfb3', *extra], capture_output=True, text=True)
            self.assertEqual(p.returncode, 2)
            self.assertIn('takes no other option', p.stderr)

    def test_stop_leftover_refuses_a_name_that_is_not_a_worker(self):
        for name in ('cua-driver-browser-baseline-20261104-tk86-ax', 'onigiri-linux', 'cua-e2e-x'):
            with patch.object(app, 'command') as command, self.assertRaisesRegex(ValueError, 'not a lume-e2e worker'):
                app.stop_leftover(name)
            command.assert_not_called()

    def test_the_leftover_check_runs_under_the_lock_before_any_clone(self):
        calls = []
        def command(args, **kwargs):
            calls.append(args[:2])
            return json.dumps([{'name': 'cua-e2e-old', 'status': 'running'}]).encode() if args[:2] == ['lume', 'ls'] else b''
        args = argparse.Namespace(source='.', sha=SHA, standalone_browser=False, timeout=10, focus_cell=None, fixture=None)
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'STATE', Path(tmp)), patch.object(app, 'config', return_value={'seed': 'seed', 'guest_user': 'lume', 'keychain_service': 's', 'keychain_account': 'a'}), patch.object(app, 'source_check'), patch.object(app, 'readiness'), patch.object(app, 'command', side_effect=command):
            with self.assertRaisesRegex(ValueError, 'still running'):
                app.run(args)
        self.assertNotIn(['lume', 'clone'], calls)

    def test_a_held_lock_queues_with_one_waiting_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / 'run.lock'
            holder = subprocess.Popen(['/usr/bin/python3', '-c', 'import fcntl,sys,time\nf=open(sys.argv[1],"a");fcntl.flock(f,fcntl.LOCK_EX);print("held",flush=True);time.sleep(1.5)', str(lock_path)], stdout=subprocess.PIPE)
            self.assertEqual(holder.stdout.readline().strip(), b'held')
            out = io.StringIO()
            with patch('sys.stdout', out), lock_path.open('a') as lock:
                app.acquire_run_lock(lock)
            holder.wait()
            self.assertEqual(out.getvalue().count('waiting'), 1)

    def test_a_free_lock_prints_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with patch('sys.stdout', out), (Path(tmp) / 'run.lock').open('a') as lock:
                app.acquire_run_lock(lock)
            self.assertEqual(out.getvalue(), '')

    def test_collection_failure_not_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, result = self.simulate(tmp, runner_exit='0', collect_fails=True)
            self.assertEqual(rc, 1)
            self.assertEqual(result['status'], 'incomplete')
            self.assertFalse(result['evidence_collected'])


if __name__ == '__main__':
    unittest.main()
