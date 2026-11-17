import argparse
import copy
import hashlib
import json
import plistlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import fixture_e2e as fixture
import lume_e2e as app

SHA = 'a' * 40
APP = '/Users/lume/Applications/Cua Embedded SDK Window Fixture.app'
BINARY = APP + '/Contents/MacOS/embedded_menu_restore'


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.requested_handoff = False
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = self.directory / 'source/artifacts/cua-driver/embedded-sdk-window'
        self.root.mkdir(parents=True)
        cert = b'unit test certificate bytes, never a real identity'
        (self.root / 'signing-cert0').write_bytes(cert)
        self.manifest = {'source_sha': SHA, 'fixture': fixture.FIXTURE, 'exit_code': 0,
                         'executable': BINARY, 'binary_sha256_before': 'b' * 64,
                         'binary_sha256_after': 'b' * 64,
                         'signing': {'identity': hashlib.sha1(cert).hexdigest().upper(),
                                     'identifier': 'com.trycua.fixture.embedded-sdk-window', 'verified': True}}
        info = plistlib.dumps({'CFBundleIdentifier': 'com.trycua.fixture.embedded-sdk-window',
                               'CFBundleExecutable': 'embedded_menu_restore', 'CFBundlePackageType': 'APPL'})
        (self.root / 'bundle-info.plist').write_bytes(info)
        self.manifest.update(bundle_path=APP, launch_method='LaunchServices', accessibility_handoff=False,
                             bundle_info_sha256_before=hashlib.sha256(info).hexdigest(),
                             bundle_info_sha256_after=hashlib.sha256(info).hexdigest())
        process = {'pid': 11, 'bundle_path': APP, 'bundle_id': 'com.trycua.fixture.embedded-sdk-window', 'parent_pid': 9, 'executable': BINARY,
                   'ax_trusted': True, 'appkit_main_thread': True, 'ancestry': ['12 9 /guest/fixture']}
        self.preflight = {'schema': fixture.SCHEMA, 'source_sha': SHA, 'status': 'preflight', 'process': process}
        before = {'x': 0, 'y': 0, 'width': 400, 'height': 300}
        moved = dict(before, x=25, y=25)
        resized = dict(moved, width=440, height=330)
        cases = [{'id': name, 'status': 'pass', 'pid': 12 if name.startswith('own') else 18,
                  'window_id': 70, 'before': before,
                  'requested': moved if name.endswith('-move') else resized,
                  'observed': moved if name.endswith('-move') else resized}
                 for name in sorted(fixture.GEOMETRY)]
        cases += [
            {'id': 'external-menu-host-restore', 'status': 'pass', 'target_minimized': True,
             'original_key': True, 'distractor_key': False, 'frontmost_pid': 12,
             'focused_window_id': 70, 'original_window_id': 70},
            {'id': 'cancel-before-mainqueue-drain', 'status': 'pass', 'before': before, 'observed': before,
             'public_future_cancelled': True, 'main_queue_drained_after_cancel': True, 'queue_arrival_instrumented': False},
            {'id': 'unavailable-mainqueue-late-drain', 'status': 'pass', 'before': before, 'observed': before,
             'unavailable_error_observed': True, 'main_queue_drained_after_error': True},
            {'id': 'started-cancel-shutdown-correction', 'status': 'pass', 'before': before,
             'initial_write': resized, 'disturbed': dict(resized, x=50), 'requested': resized, 'observed': resized,
             'shutdown_pending_while_main_queue_stalled': True, 'shutdown_completed_after_drain': True},
            {'id': 'closed-wrong-owner-public-auth', 'status': 'pass', 'refusals': [
                {'kind': kind, 'is_error': True, 'text': reason} for kind, reason in [
                    ('closed', 'window_id 70 is closed, stale, or unknown to WindowServer'),
                    ('wrong-owner', 'window_id 70 belongs to pid 12 (Host), not pid 18'),
                    ('public-own-pid', 'Cua Driver refuses operations that target its own authorization process')]]},
            {'id': 'correction-close-replacement', 'status': 'pass', 'initial_write': resized,
             'closed_window_id': 70, 'replacement_window_id': 71, 'replacement_before': before,
             'replacement_after': before, 'action': {'effect': 'unverifiable'}},
        ]
        self.initial = copy.deepcopy(self.preflight)
        self.handoff = {'requested': False, 'needed': False, 'completed': True, 'initial_pid': 11, 'trusted_pid': 11}
        self.report = dict(self.preflight, status='pass', process=dict(process, pid=12), cases=cases,
                           cleanup={'external_process_reaped': True, 'host_windows_closed': True})
        for phase in ('before', 'after'):
            (self.root / f'daemon-{phase}.txt').write_text('permission mode: standard\n')

    def check(self):
        for name, value in [('runner-manifest', self.manifest), ('preflight', self.preflight), ('fixture', self.report), ('preflight-initial', self.initial), ('handoff', self.handoff)]:
            (self.root / (name + '.json')).write_text(json.dumps(value))
        return fixture.evidence_check(self.directory, SHA, self.requested_handoff)

    def test_exact_ten_case_native_contract_passes(self):
        self.assertEqual(self.check(), 10)

    def test_untrusted_process_cannot_borrow_daemon_or_certificate_grants(self):
        self.preflight['process']['ax_trusted'] = False
        with self.assertRaisesRegex(ValueError, 'AX trust'): self.check()

    def test_scope_source_signature_and_cleanup_fail_closed(self):
        mutations = [
            lambda: self.manifest.update(source_sha='f' * 40),
            lambda: self.manifest.update(binary_sha256_after='c' * 64),
            lambda: self.manifest['signing'].update(identifier='com.trycua.driver.local'),
            lambda: self.manifest['signing'].update(identity='D' * 40),
            lambda: self.report.update(status='failed'),
            lambda: self.report['cleanup'].update(external_process_reaped=False),
            lambda: self.report['cases'].append(copy.deepcopy(self.report['cases'][0])),
            lambda: self.report['cases'].pop(),
        ]
        for change in mutations:
            saved = copy.deepcopy((self.manifest, self.report))
            with self.subTest(change=change):
                change()
                with self.assertRaises(ValueError): self.check()
            self.manifest, self.report = saved

    def test_geometry_rejects_noop_wrong_owner_nan_and_false_move_only(self):
        for key, value in [('pid', 999), ('requested', dict(x=0, y=0, width=400, height=300)),
                           ('observed', dict(x=float('nan'), y=25, width=400, height=300)),
                           ('requested', dict(x=25, y=25, width=401, height=300))]:
            case = next(c for c in self.report['cases'] if c['id'] == 'own-move')
            old = copy.deepcopy(case)
            with self.subTest(key=key, value=value):
                case[key] = value
                with self.assertRaises(ValueError): self.check()
            case.clear(); case.update(old)

    def test_native_lifecycle_false_success_is_rejected(self):
        mutations = {
            'cancel-before-mainqueue-drain': {'observed': dict(x=9, y=0, width=400, height=300)},
            'unavailable-mainqueue-late-drain': {'unavailable_error_observed': False},
            'started-cancel-shutdown-correction': {'shutdown_pending_while_main_queue_stalled': False},
            'closed-wrong-owner-public-auth': {'refusals': []},
            'correction-close-replacement': {'action': {'effect': 'confirmed'}},
            'external-menu-host-restore': {'focused_window_id': 999},
        }
        for name, mutation in mutations.items():
            case = next(c for c in self.report['cases'] if c['id'] == name)
            old = copy.deepcopy(case)
            with self.subTest(case=name):
                case.update(mutation)
                with self.assertRaises(ValueError): self.check()
            case.clear(); case.update(old)

    def test_unrelated_errors_cannot_falsify_specific_native_refusals(self):
        case = next(c for c in self.report['cases'] if c['id'] == 'closed-wrong-owner-public-auth')
        original = copy.deepcopy(case['refusals'])
        for selected in ([0], [1], [2], [0, 1, 2]):
            with self.subTest(selected=selected):
                case['refusals'] = copy.deepcopy(original)
                for index in selected:
                    case['refusals'][index]['text'] = 'unrelated internal error'
                with self.assertRaisesRegex(ValueError, 'specific native refusal reason'):
                    self.check()
        case['refusals'] = original
        self.assertEqual(self.check(), 10)

    def test_bundle_launch_and_handoff_falsification(self):
        changes = [lambda: self.manifest.update(launch_method='direct-exec'),
                   lambda: self.manifest.update(bundle_path='/Applications/Terminal.app'),
                   lambda: self.manifest.update(bundle_info_sha256_after='0' * 64),
                   lambda: self.report['process'].update(bundle_id='com.apple.Terminal'),
                   lambda: self.report['process'].update(pid=11),
                   lambda: self.handoff.update(completed=False)]
        for change in changes:
            saved = copy.deepcopy((self.manifest, self.report, self.handoff))
            with self.subTest(change=change):
                change()
                with self.assertRaises(ValueError): self.check()
            self.manifest, self.report, self.handoff = saved

    def test_handoff_requires_opt_in_and_fresh_trusted_process(self):
        self.initial['process']['ax_trusted'] = False
        self.handoff.update(needed=True)
        with self.assertRaisesRegex(ValueError, 'authorized fresh-process'): self.check()
        self.manifest['accessibility_handoff'] = True
        with self.assertRaisesRegex(ValueError, 'authorized host selection'): self.check()
        self.requested_handoff = True
        self.handoff['requested'] = True
        with self.assertRaisesRegex(ValueError, 'authorized fresh-process'): self.check()
        self.preflight['process']['pid'] = 13
        self.handoff['trusted_pid'] = 13
        self.assertEqual(self.check(), 10)

    def test_handoff_flag_is_fixed_mode_only(self):
        with patch.object(app, 'config') as config:
            with self.assertRaises(ValueError):
                app.run(argparse.Namespace(fixture=None, accessibility_handoff=True, standalone_browser=False))
            config.assert_not_called()
        script = app.guest_script('/Users/lume/run', SHA, False, fixture=fixture.FIXTURE, accessibility_handoff=True)
        self.assertIn('bash ' + fixture.ENTRYPOINT + ' --accessibility-handoff', script)
        subprocess.run(['bash', '-n'], input=script.encode(), check=True)

    def test_nonstandard_daemon_cannot_pass(self):
        (self.root / 'daemon-after.txt').write_text('permission mode: unrestricted\n')
        with self.assertRaisesRegex(ValueError, 'standard daemon'): self.check()

    def test_selection_rejected_before_config_credential_or_vm_access(self):
        for selection in [dict(fixture='../evil'), dict(fixture=fixture.FIXTURE, targeted_package='/other'),
                          dict(fixture=fixture.FIXTURE, standalone_browser=True),
                          dict(fixture=fixture.FIXTURE, focus_cell='macos-appkit-ax-tree-ax-not-applicable')]:
            args = dict(fixture=None, targeted_package=None, standalone_browser=False, focus_cell=None)
            args.update(selection)
            with patch.object(app, 'config') as config, patch.object(app, 'retrieve_credential') as credential:
                with self.assertRaises(ValueError): app.run(argparse.Namespace(**args))
                config.assert_not_called(); credential.assert_not_called()

    def test_fixed_dispatch_preserves_original_fifo_and_gui_checks(self):
        script = app.guest_script('/Users/lume/run', SHA, False, fixture=fixture.FIXTURE)
        subprocess.run(['bash', '-n'], input=script.encode(), check=True)
        self.assertIn('bash ' + fixture.ENTRYPOINT, script)
        self.assertIn('IFS= read -r CUA_E2E_SIGNING_KEYCHAIN_PASSWORD < unlock', script)
        self.assertIn('${SSH_CONNECTION:-}', script)
        self.assertNotIn('bash ' + app.RUNNER, script)
        self.assertNotIn('--request-accessibility', script)


if __name__ == '__main__': unittest.main()
