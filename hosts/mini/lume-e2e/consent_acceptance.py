import json
from pathlib import Path

TARGET = 'pr3140-consent-allow'
LOCALES = ('en', 'zh-Hans')
UNTESTED = ('native_cancel', 'reconnect', 'competing_sheets', 'setup_rollback')

def structured(raw):
    if raw.get('error') or raw.get('result', {}).get('isError'):
        raise ValueError('tool response failed')
    return raw['result']['structuredContent']

def selected_locales(locales):
    if type(locales) not in (list, tuple) or tuple(locales) not in (('en',), LOCALES):
        raise ValueError('unsupported native consent locale scope')
    return tuple(locales)

def check_rows(base, locales=LOCALES):
    locales = selected_locales(locales)
    for locale in locales:
        row = json.loads((base / ('native-consent-allow-' + locale + '.json')).read_text())
        if row['locale'] != locale or row['fixture_counter'] != 'counter=1' or row.get('fixture_state',{}).get('lbl-counter',{}).get('text') != 'counter=1':
            raise ValueError('wrong locale/external effect')
        if any(row.get(key) != 'not_executed' for key in UNTESTED):
            raise ValueError('native scope overclaimed')
        if not row.get('profile_removed') or not row.get('listener_closed'):
            raise ValueError('fixture cleanup incomplete')
        before = structured(row['before'])
        if before.get('truncated') is not False or before.get('degraded'):
            raise ValueError('native observation incomplete')
        if before['pid'] != row['pid'] or before['window_id'] != row['window_id']:
            raise ValueError('wrong observation target')
        prompt = 'Allow remote debugging?' if locale == 'en' else '要允许远程调试吗？'
        elements = before['elements']
        sheets = [e for e in elements if e.get('role') == 'AXSheet' and e.get('label') == prompt]
        if len(sheets) != 1:
            raise ValueError('wrong native sheet')
        sheet = sheets[0]
        parents = [e for e in elements if e.get('element_index') == sheet.get('parent_index')]
        if len(parents) != 1 or parents[0].get('role') != 'AXWindow' or parents[0].get('depth') != 0 or parents[0].get('parent_index') is not None or sheet.get('depth') != 1:
            raise ValueError('native sheet parent not proven')
        start = elements.index(sheet)
        subtree = [sheet]
        for element in elements[start+1:]:
            if element.get('depth', 0) <= 1: break
            subtree.append(element)
        if any(e.get('role') in ('AXWebArea','AXWebContent') or e.get('in_web_content') is True for e in [parents[0],*subtree]):
            raise ValueError('web-backed consent evidence')
        warning = ('An external app wants full control over this Chrome session to debug it. This includes access to your saved data, cookies and site data, and the ability to navigate to any URL.' if locale == 'en' else '为进行调试，一款外部应用请求完全控制此 Chrome 会话，包括访问您保存的数据、Cookie 和网站数据，以及前往任意网址。')
        if not any(e.get('parent_index') == sheet['element_index'] and e.get('value') == warning and e.get('role') == 'AXStaticText' for e in elements):
            raise ValueError('native consent purpose not proven')
        buttons = [e for e in elements if e.get('parent_index') == sheet['element_index'] and e.get('role') == 'AXButton']
        for label in (('Cancel', 'Allow') if locale == 'en' else ('取消', '允许', '在“设置”中关闭')):
            matches = [e for e in buttons if e.get('label') == label]
            if len(matches) != 1 or 'AXPress' not in matches[0].get('actions', []) or matches[0].get('enabled') is not True or matches[0].get('depth') != 2:
                raise ValueError('positive native controls not proven')
        prepared = structured(row['prepared'])
        effects = prepared['side_effects']
        if prepared.get('status') != 'ok' or effects.get('displayed_consent_prompt') is not True or effects.get('opened_setup_page') is not False or effects.get('enabled_remote_debugging') is not False:
            raise ValueError('wrong prepare route')
        binding = structured(row['binding'])
        request = row['binding_request']
        active = [tab for tab in binding['tabs'] if tab.get('active') is True]
        if (binding.get('binding_quality') != 'exact' or binding.get('binding_route') != 'native_cdp_window'
                or request.get('pid') != row['pid'] or request.get('window_id') != row['window_id']
                or prepared.get('prepared_pid') != row['pid'] or prepared.get('endpoint_ownership',{}).get('owner_pid') != row['pid'] or len(active) != 1
                or active[0].get('url') != row['fixture_url']):
            raise ValueError('binding not exact for owned native window/URL')
        journal = [json.loads(line) for line in (base/('native-consent-allow-'+locale+'-journal.jsonl')).read_text().splitlines()]
        stages = [j['stage'] for j in journal]
        for first,second in [('prepare-request','native-snapshot'),('prepare-response','binding-request'),('binding-response','click-response')]:
            if first not in stages or second not in stages or stages.index(first) >= stages.index(second):
                raise ValueError('missing ordered raw observations')
        requests=[j['value'] for j in journal if j['stage']=='prepare-request']
        responses=[j['value'] for j in journal if j['stage']=='prepare-response']
        snapshots=[j['value'] for j in journal if j['stage']=='native-snapshot' and j['value'].get('raw')==row['before']]
        if len(requests)!=1 or len(responses)!=1 or len(snapshots)!=1:
            raise ValueError('single prepare/concurrent observation not proven')
        req,resp,snap=requests[0],responses[0],snapshots[0]
        times=[req.get('dispatched_us'),snap.get('started_us'),snap.get('completed_us'),resp.get('completed_us')]
        if any(type(t) is not int or t<0 for t in times) or not times[0]<=times[1]<=times[2]<times[3]:
            raise ValueError('native observation did not precede actual prepare completion')
        if resp.get('raw')!=row['prepared'] or req.get('raw')!={'pid':row['pid'],'window_id':row['window_id'],'session':request['session'],'strategy':{'kind':'existing_profile'}}:
            raise ValueError('prepare request/response receipt mismatch')
        identity=json.loads((base/'chrome-identity.json').read_text())
        provenance=row.get('provenance',{})
        signature=identity.get('signature',{})
        if (identity.get('version')!='152.0.7977.83' or identity.get('verification',{}).get('returncode')!=0 or signature.get('returncode')!=0
            or 'Identifier=com.google.Chrome' not in signature.get('stderr','') or 'TeamIdentifier=EQHXZ8M8AV' not in signature.get('stderr','')):
            raise ValueError('verified Chrome identity missing')
        if (provenance.get('method')!='verified_binary_owned_launch' or provenance.get('browser')!=identity['version']
            or provenance.get('executable_sha256')!=identity.get('executable_sha256') or len(provenance.get('executable_sha256',''))!=64
            or provenance.get('executable')!='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
            or provenance.get('pid')!=row['pid'] or provenance.get('window_id')!=row['window_id']
            or provenance.get('protocol_version') is not None or provenance.get('user_agent') is not None):
            raise ValueError('owned binary provenance mismatch')
        if [j['value'] for j in journal if j['stage']=='browser-provenance'] != [provenance]:
            raise ValueError('provenance journal mismatch')
        if next(j['value'] for j in journal if j['stage']=='binding-request') != request:
            raise ValueError('binding request receipt mismatch')
        windows = structured(row['after'])['windows']
        if any(w.get('title') in ('Allow remote debugging?', '要允许远程调试吗？') for w in windows):
            raise ValueError('consent CG surface retained')
        if not any(w.get('window_id') == row['window_id'] for w in windows):
            raise ValueError('fixture closed before session ended')
    results = [json.loads(line) for line in (base/'results.jsonl').read_text().splitlines()]
    expected = {'macos-chrome-standalone-native-consent-allow-'+locale for locale in locales}
    if len(results) != len(locales) or {r['cell_id'] for r in results} != expected:
        raise ValueError('missing/duplicate/wrong native rows')
    if any(r.get('test_status') != 'pass' or r.get('observed_behavior') != 'delivered' for r in results):
        raise ValueError('failed/refused native row')
    provenance_rows=[json.loads(line) for line in (base/'browser-provenance.jsonl').read_text().splitlines()]
    expected_provenance=[json.loads((base/('native-consent-allow-'+locale+'.json')).read_text())['provenance'] for locale in locales]
    if provenance_rows!=expected_provenance:raise ValueError('browser provenance sidecar mismatch')
    return len(locales)

def evidence_check(base, sha, locales=LOCALES):
    locales = selected_locales(locales)
    base = Path(base)
    result = json.loads((base/'targeted-result.json').read_text())
    if (result.get('source_sha'),result.get('mode'),result.get('status'),result.get('scope')) != (sha,TARGET,'pass','native_allow_only'):
        raise ValueError('source/scope/run mismatch')
    if result.get('e2e_executed') is not True or result.get('full_matrix') is not False or result.get('report_exit') != 0:
        raise ValueError('native execution/report incomplete')
    if result.get('daemon_mode_before') != 'standard' or result.get('daemon_mode_after') != 'standard':
        raise ValueError('daemon mode changed')
    if result.get('cleanup_errors') != [] or result.get('row_exits') != [0] * len(locales):
        raise ValueError('native rows/cleanup failed')
    for locale in locales:
        row=json.loads((base/('native-consent-allow-'+locale+'.json')).read_text())
        if row.get('provenance',{}).get('source_sha')!=sha:raise ValueError('provenance source mismatch')
    build=json.loads((base/'build-identity.json').read_text())
    config=json.loads((base/'daemon-config.json').read_text())
    if build.get('source_sha') != sha or not build.get('installed_sha256') or build.get('installed_sha256') != build.get('staged_sha256') or config.get('response',{}).get('source_sha') != sha:
        raise ValueError('built/serving source mismatch')
    after=json.loads((base/'controller-after.json').read_text())
    final=json.loads((base/'controller-final.json').read_text())
    if after.get('pid') != final.get('pid') or after.get('binary_sha256') != build['installed_sha256'] or final.get('binary_sha256') != build['installed_sha256']:
        raise ValueError('serving identity changed')
    teardown=json.loads((base/'teardown.json').read_text())
    if teardown.get('daemon_exited') is not True or teardown.get('profiles_removed') is not True or teardown.get('owned_processes_remaining') != [] or teardown.get('errors') != []:
        raise ValueError('runtime teardown incomplete')
    preflight=json.loads((base/'privacy-preflight.json').read_text())
    if preflight.get('policy') != 'chromium-grant-isolation-v1' or preflight.get('errors') != [] or preflight.get('scopes') != dict.fromkeys(('chromium_profiles','processes','mounts'),'complete'):
        raise ValueError('scoped isolation policy/coverage mismatch')
    if preflight.get('passed') is not True or preflight.get('expected_account') is not True or preflight.get('database_contents_inspected') is not False or preflight.get('forensic_absence_claim') is not False:
        raise ValueError('scoped account or evidence boundary mismatch')
    if type(preflight.get('processes')) is not list or type(preflight.get('mounts')) is not list:
        raise ValueError('process/mount evidence absent or ambiguous')
    from scoped_preflight import relevant_processes, validate_process_observations
    observations=preflight.get('process_observations')
    if preflight.get('runtime_acquisition_complete') is not True:
        raise ValueError('runtime acquisition incomplete')
    validate_process_observations(observations)
    if relevant_processes(observations) or preflight['processes'] != []:
        raise ValueError('unresolved Chromium process identity')
    observations=preflight.get('mount_observations')
    if type(observations) is not list or any(type(o) is not dict or o.get('parse_complete') is not True for o in observations):
        raise ValueError('missing or ambiguous mount observations')
    relevant_types={'virtiofs','smbfs','webdav','nfs','9p','osxfuse','macfuse'}
    if any(type(o.get('line_index')) is not int or o['line_index'] != i or type(o.get('type')) is not str for i,o in enumerate(observations)):
        raise ValueError('mount observation identity incomplete')
    expected_indices=[o['line_index'] for o in observations if o['type'] in relevant_types]
    if [m.get('line_index') for m in preflight['mounts'] if type(m) is dict] != expected_indices:
        raise ValueError('mount candidate coverage incomplete')
    for mount in preflight['mounts']:
        if type(mount) is not dict or mount.get('type') != 'virtiofs' or mount.get('source_tag') != 'com.apple.virtio-fs.automount' or mount.get('source_class') != 'virtio_tag' or mount.get('classification') != 'reviewed_lume_empty_readonly_placeholder' or mount.get('placeholder_empty') is not True or mount.get('placeholder_readonly') is not True or type(mount.get('root_entry_count')) is not int or mount['root_entry_count'] != 1:
            raise ValueError('unresolved mount exposure')
        if type(mount.get('destination')) is not str or Path(mount['destination']).parent != Path('/Volumes') or type(mount.get('readonly')) is not bool or type(mount.get('options')) is not list or not all(type(v) is str for v in mount['options']):
            raise ValueError('mount identity/options incomplete')
    projections=preflight.get('account_projections')
    expected=dict(profile_count=1,account_count=0,account_identifier_present=False,sync_or_consent_enabled=False)
    if type(projections) is not dict or 'Default' not in projections or not set(projections)<={'Default','System Profile'} or type(preflight.get('profile_count')) is not int or preflight['profile_count'] != len(projections):
        raise ValueError('Chromium profile coverage incomplete')
    for projection in projections.values():
        if type(projection) is not dict or {k:(type(v),v) for k,v in projection.items()} != {k:(type(v),v) for k,v in expected.items()}:
            raise ValueError('Chromium account indicators not clear')
    stores=preflight.get('other_chromium_stores')
    required={'Library/Application Support/'+p for p in ('Google/Chrome Beta','Google/Chrome Canary','Chromium','Microsoft Edge','BraveSoftware','Arc')}
    if type(stores) is not dict or set(stores)!=required or not all(v is False for v in stores.values()):
        raise ValueError('foreign Chromium store evidence incomplete')
    metadata=preflight.get('chrome_metadata')
    if type(metadata) is not dict or type(metadata.get('files')) is not int or metadata['files']<2 or type(metadata.get('directories')) is not int or metadata['directories']<1 or type(metadata.get('symlinks')) is not int or metadata['symlinks']!=0:
        raise ValueError('Chromium metadata incomplete')
    return check_rows(base, locales)
