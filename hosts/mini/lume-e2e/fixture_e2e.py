import hashlib
import json
import math
from pathlib import Path
import plistlib
import re

FIXTURE = 'embedded-sdk-window'
ENTRYPOINT = 'scripts/ci/macos/run-embedded-sdk-window.sh'
SCHEMA = 'cua-driver/embedded-sdk-window@1'
GEOMETRY = {'own-move', 'own-resize', 'external-move', 'external-resize'}
LIFECYCLE = {'cancel-before-mainqueue-drain', 'unavailable-mainqueue-late-drain',
             'started-cancel-shutdown-correction', 'closed-wrong-owner-public-auth',
             'correction-close-replacement'}


def validate_selection(fixture, focus=None, standalone=False, targeted=None, accessibility_handoff=False):
    if accessibility_handoff and fixture != FIXTURE:
        raise ValueError('Accessibility handoff requires the fixed embedded-sdk-window fixture')
    if fixture is None:
        return
    if fixture != FIXTURE or focus or standalone or targeted:
        raise ValueError('select only the fixed embedded-sdk-window fixture')


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def trusted_process(report, sha, executable, require_trust=True):
    require(report.get('schema') == SCHEMA and report.get('source_sha') == sha,
            'fixture source/schema mismatch')
    process = report.get('process', {})
    require(type(process.get('ax_trusted')) is bool and (not require_trust or process['ax_trusted'])
            and process.get('appkit_main_thread') is True,
            'actual fixture process lacks AX trust or the AppKit main thread')
    require(process.get('executable') == executable and type(process.get('pid')) is int
            and process['pid'] > 0 and type(process.get('parent_pid')) is int
            and process['parent_pid'] > 0, 'fixture executable/process identity missing')
    require(isinstance(process.get('ancestry'), list) and bool(process['ancestry']),
            'fixture process ancestry missing')
    require(process.get('bundle_path') == str(Path(executable).parents[2])
            and process.get('bundle_id') == 'com.trycua.fixture.embedded-sdk-window',
            'actual fixture bundle identity mismatch')
    return process['pid']


def evidence_check(directory, sha, accessibility_handoff=False):
    base = directory / 'source/artifacts/cua-driver' / FIXTURE
    manifest = json.loads((base / 'runner-manifest.json').read_text())
    require(manifest.get('source_sha') == sha and manifest.get('fixture') == FIXTURE
            and manifest.get('exit_code') == 0, 'fixture runner did not pass on requested source')
    app = manifest.get('bundle_path', '')
    require(app.endswith('/Applications/Cua Embedded SDK Window Fixture.app')
            and manifest.get('executable') == app + '/Contents/MacOS/embedded_menu_restore'
            and manifest.get('launch_method') == 'LaunchServices', 'fixture must launch its genuine app bundle')
    info_bytes = (base / 'bundle-info.plist').read_bytes()
    info = plistlib.loads(info_bytes)
    require((info.get('CFBundleIdentifier'), info.get('CFBundleExecutable'), info.get('CFBundlePackageType')) == (
        'com.trycua.fixture.embedded-sdk-window', 'embedded_menu_restore', 'APPL'), 'invalid native fixture bundle layout')
    require(hashlib.sha256(info_bytes).hexdigest() == manifest.get('bundle_info_sha256_before')
            == manifest.get('bundle_info_sha256_after'), 'fixture bundle metadata changed during execution')
    signing = manifest.get('signing', {})
    require(re.fullmatch(r'[0-9A-F]{40}', signing.get('identity', '')) is not None
            and signing.get('identifier') == 'com.trycua.fixture.embedded-sdk-window'
            and signing.get('verified') is True, 'stable distinct fixture signature not verified')
    require(re.fullmatch(r'[0-9a-f]{64}', manifest.get('binary_sha256_before', '')) is not None
            and manifest['binary_sha256_before'] == manifest.get('binary_sha256_after'),
            'fixture binary changed during execution')
    certificate = base / 'signing-cert0'
    require(hashlib.sha1(certificate.read_bytes()).hexdigest().upper() == signing['identity'],
            'fixture certificate does not match pinned existing identity')
    for phase in ('before', 'after'):
        require('permission mode: standard' in (base / f'daemon-{phase}.txt').read_text(),
                'standard daemon mode not observed before and after fixture')
    preflight = json.loads((base / 'preflight.json').read_text())
    report = json.loads((base / 'fixture.json').read_text())
    preflight_pid = trusted_process(preflight, sha, manifest['executable'])
    initial = json.loads((base / 'preflight-initial.json').read_text())
    initial_pid = trusted_process(initial, sha, manifest['executable'], require_trust=False)
    handoff = json.loads((base / 'handoff.json').read_text())
    require(type(manifest.get('accessibility_handoff')) is bool
            and manifest['accessibility_handoff'] == accessibility_handoff, 'handoff differs from authorized host selection')
    require(handoff == {
        'requested': manifest['accessibility_handoff'], 'needed': not initial['process']['ax_trusted'],
        'completed': True, 'initial_pid': initial_pid, 'trusted_pid': preflight_pid}, 'incomplete app-only handoff record')
    require(initial.get('status') == 'preflight' and (not handoff['needed'] or (
        handoff['requested'] and initial_pid != preflight_pid)), 'trust changed without authorized fresh-process handoff')
    pid = trusted_process(report, sha, manifest['executable'])
    require(pid != preflight_pid, 'native fixture was not relaunched after preflight')
    require(preflight.get('status') == 'preflight' and report.get('status') == 'pass',
            'fixture preflight or native execution failed')
    cases = report.get('cases', [])
    require(len(cases) == 10 and {case.get('id') for case in cases} == GEOMETRY | LIFECYCLE | {'external-menu-host-restore'},
            'missing, duplicate, or unexpected fixture cases')
    external_pids = set()
    for case in cases:
        require(case.get('status') == 'pass', 'failed fixture case')
        if case['id'] in LIFECYCLE:
            lifecycle_case(case)
            continue
        if case['id'] not in GEOMETRY:
            require(case.get('target_minimized') is True and case.get('original_key') is True
                    and case.get('distractor_key') is False and case.get('frontmost_pid') == pid
                    and case.get('focused_window_id') == case.get('original_window_id')
                    and type(case.get('original_window_id')) is int,
                    'external menu effect/exact host focus not independently observed')
            continue
        require(type(case.get('window_id')) is int and case['window_id'] > 0,
                'geometry lacks exact window identity')
        own = case['id'].startswith('own-')
        require(type(case.get('pid')) is int and case['pid'] > 0
                and (case['pid'] == pid) == own, 'wrong process for frame case')
        if not own:
            external_pids.add(case['pid'])
        for component in ('x', 'y', 'width', 'height'):
            values = [case.get(key, {}).get(component) for key in ('before', 'requested', 'observed')]
            require(all(type(v) in (int, float) and math.isfinite(v) for v in values),
                    'missing or nonfinite independent frame evidence')
            before, requested, observed = values
            require(abs(requested - observed) <= 2, 'independent geometry did not match request')
            if component in ('x', 'y'):
                require(abs(before - requested) > 2, 'move case did not request a move')
            elif case['id'].endswith('-move'):
                require(before == requested, 'move-only case requested a resize')
            else:
                require(abs(before - requested) > 2 and min(values) > 0,
                        'resize case did not request a positive changed size')
    require(len(external_pids) == 1, 'external frame cases did not target one external fixture')
    require(report.get('cleanup') == {'external_process_reaped': True, 'host_windows_closed': True},
            'native fixture cleanup was not verified')
    return len(cases)


def frame(value):
    require(isinstance(value, dict), 'missing native frame')
    numbers = [value.get(key) for key in ('x', 'y', 'width', 'height')]
    require(all(type(n) in (int, float) and math.isfinite(n) for n in numbers)
            and numbers[2] > 0 and numbers[3] > 0, 'invalid native frame')
    return numbers


def lifecycle_case(case):
    name = case['id']
    if name in ('cancel-before-mainqueue-drain', 'unavailable-mainqueue-late-drain'):
        require(frame(case.get('before')) == frame(case.get('observed')), 'late main-queue write observed')
        keys = ('public_future_cancelled', 'main_queue_drained_after_cancel') if name.startswith('cancel-') else (            'unavailable_error_observed', 'main_queue_drained_after_error')
        require(all(case.get(key) is True for key in keys), 'cancellation/timeout drain not exercised')
        if name.startswith('cancel-'):
            require(case.get('queue_arrival_instrumented') is False,
                    'native cancellation case must not claim instrumentation it lacks')
    elif name == 'started-cancel-shutdown-correction':
        before, initial, disturbed, requested, observed = [frame(case.get(key)) for key in (            'before', 'initial_write', 'disturbed', 'requested', 'observed')]
        require(abs(initial[0] - before[0]) > 2 and abs(disturbed[0] - requested[0]) > 2,
                'native start or forced correction was not observed')
        require(all(abs(a - b) <= 2 for a, b in zip(requested, observed)), 'native correction did not converge')
        require(case.get('shutdown_pending_while_main_queue_stalled') is True
                and case.get('shutdown_completed_after_drain') is True,
                'started cancellation did not retain shutdown ownership')
    elif name == 'closed-wrong-owner-public-auth':
        refusals = case.get('refusals', [])
        reasons = {'closed': 'closed', 'wrong-owner': 'belongs to pid',
                   'public-own-pid': 'own authorization process'}
        require(len(refusals) == 3 and {item.get('kind') for item in refusals} == set(reasons)
                and all(item.get('is_error') is True for item in refusals),
                'closed/owner/public-authority refusals incomplete')
        require(all(isinstance(item.get('text'), str) and reasons[item['kind']] in item['text']
                    for item in refusals), 'specific native refusal reason missing')
    elif name == 'correction-close-replacement':
        frame(case.get('initial_write'))
        require(type(case.get('closed_window_id')) is int and type(case.get('replacement_window_id')) is int
                and case['closed_window_id'] > 0 and case['replacement_window_id'] > 0
                and case['closed_window_id'] != case['replacement_window_id'], 'replacement window identity missing')
        require(frame(case.get('replacement_before')) == frame(case.get('replacement_after')),
                'correction changed replacement window')
        require(case.get('action', {}).get('effect') == 'unverifiable', 'correction-close lost truthful uncertain outcome')
