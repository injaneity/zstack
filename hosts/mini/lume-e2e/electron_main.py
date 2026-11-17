import hashlib
import json
import re
from urllib.parse import urlparse

TARGET = 'pr4384-electron-main'
IDENTITY = 'ee7a0f341e06cdee7c1ec63929079f77d566adfc'
ASSETS = {'guest-electron-main.py', 'endpoint-scenario.mjs', 'run-inspector.mjs',
          'host-readiness.mjs', 'inspector-completion.mjs', 'action-envelope.mjs',
          'target-selection.mjs', 'socket-oracle.c', 'process-argv.c',
          'source-manifest.json', 'frozen-host.zip', 'frozen-host-files.json'}

def require(condition, message):
    if not condition:
        raise ValueError(message)

def read(base, name):
    return json.loads((base / name).read_text())

def observe(base, sha):
    path = base / 'targeted-result.json'
    if not path.exists():
        return {'e2e_executed': False, 'full_matrix': False}
    result = read(base, path.name)
    require((result.get('source_sha'), result.get('mode')) == (sha, TARGET), 'Electron source/scope mismatch')
    return {'e2e_executed': result.get('e2e_executed') is True, 'full_matrix': False,
            'scenario_outcomes': result.get('scenarios', [])}

def evidence_check(base, sha, manifest):
    result = read(base, 'targeted-result.json')
    require((result.get('source_sha'), result.get('mode'), result.get('status'), result.get('e2e_executed')) ==
            (sha, TARGET, 'pass', True), 'Electron outcome incomplete')
    require(result.get('host_unchanged_after') is True, 'host changed')
    for entry, mode in [(result.get('loader_validation', {}), 'loader-validation'),
                        *((s, 'isolated-inherited') for s in result.get('scenarios', []))]:
        require((entry.get('mode'), entry.get('status'), entry.get('host_exited')) == (mode, 'pass', True)
                and entry.get('owned_group_empty') is True and entry.get('host_cleanup', {}).get('remaining') == []
                and not entry.get('cleanup_errors'), 'Electron host cleanup incomplete')
    require(len(result.get('scenarios', [])) == 1, 'exactly one inherited scenario required')
    owned_cleanup = read(base, 'owned-browser-cleanup.json')
    require(owned_cleanup == result['scenarios'][0].get('owned_cleanup') and owned_cleanup.get('status') == 'pass'
            and owned_cleanup.get('remaining') == [] and all(p['closed'] is True for p in owned_cleanup.get('ports', [])), 'separate browser cleanup incomplete')
    events = read(base / 'isolated-inherited', 'events.json')
    require(not any(e['kind'] in ('failure', 'cleanup-failure') for e in events), 'raw scenario failure')
    def one(kind):
        items = [e['data'] for e in events if e['kind'] == kind]
        require(len(items) == 1, 'missing/duplicate '+kind)
        return items[0]
    require(events[-1]['kind'] == 'result' and one('result') ==
            {'status':'pass','mode':'isolated-inherited','sourceSha':sha}, 'raw final outcome mismatch')
    host = one('host'); loaded = one('loaded-native-library'); build = read(base, 'build-identity.json')
    require(host['sourceSha'] == sha == build['source_sha'] and host['pid'] > 0, 'serving source mismatch')
    require(loaded['sha256'] == host['nativeSha256'] == build['native_sha256'] and
            loaded['sdkEntrySha256'] == build['sdk_entry_sha256'] and loaded['path'] == build['native'] and
            re.fullmatch('[0-9a-f]{64}', loaded['sha256']), 'loaded image mismatch')
    for mode in ('loader-validation', 'isolated-inherited'):
        loader = read(base, mode+'-loader-identity.json')
        require(loader['processType'] == 'browser' and loader['fixtureSha256'] == manifest['files']['endpoint-scenario.mjs'], 'wrong Electron fixture')
        client = read(base, mode+'-client.json')
        protocol = read(base, mode+'-inspector-result.json')
        require(client['returncode'] == 0 and protocol['result']['result']['value']['status'] == 'pass', 'inspector execution failed')
        if mode == 'isolated-inherited':
            require(loader['pid'] == host['pid'] and protocol['result']['result']['value']['pid'] == host['pid'], 'wrong inspector host')
    permissions = one('observed-permissions')['source']
    require(permissions['attribution'] == 'host' and permissions['direct_runtime'] is True and
            permissions['pid'] == host['pid'] and permissions['disclaim_env'] is False, 'not direct Electron runtime')
    inheritance = one('inheritance'); child = inheritance['childListeners']; before = one('host-listeners-before')
    require(child['pid'] == inheritance['ownedPid'] != host['pid'] and 0 < inheritance['hostPort'] < inheritance['port'] < 65536, 'nonadversarial listener order')
    require(inheritance['port'] in child['ports'], 'file port not owned')
    require(any(c['port'] == h['port'] == inheritance['hostPort'] and c['address'] == h['address'] and
                c['socket'] == h['socket'] and int(h['socket'],16) != 0 and
                c['generation'] == h['generation'] and int(h['generation'],16) != 0
                for c in child['sockets'] for h in before['sockets']), 'socket inheritance not proved')
    require(inheritance['wsPath'].startswith('/devtools/browser/'), 'invalid profile endpoint')
    tools = [e['data'] for e in events if e['kind'] == 'tool']
    require(all(t['error'] is False for t in tools), 'raw tool error')
    prepared = [t['data'] for t in tools if t['name'] == 'browser_prepare']
    require(len(prepared) == 1 and prepared[0]['prepared'] is True and prepared[0]['prepared_pid'] == child['pid'] and
            prepared[0]['endpoint_ownership']['owner_pid'] == child['pid'], 'prepare selected wrong PID')
    binds = [t for t in tools if t['name'] == 'get_browser_state' and t['data'].get('mode') == 'bind']
    require(len(binds) == 1 and binds[0]['args']['pid'] == child['pid'] and
            binds[0]['data']['binding_quality'] == 'exact' and binds[0]['data']['endpoint_transport'] == 'dev_tools_active_port', 'wrong native binding')
    selected = one('selected-tab')
    require(len(selected['initialPages']) == 1 and selected['tab']['url'] == selected['initialPages'][0]['url'] and
            urlparse(selected['initialPages'][0]['webSocketDebuggerUrl']).port == inheritance['port'], 'wrong selected endpoint')
    binding = binds[0]['data']; session = binds[0]['args']['session']
    target = binding['target_id']; tab = selected['tab']['tab_id']
    require(prepared[0]['prepared'] and all(t['args'].get('session') == session for t in tools if t['name'] in
            ('start_session','browser_prepare','get_browser_state','browser_navigate','browser_click','end_session')), 'session coherence mismatch')
    navigations = [t for t in tools if t['name'] == 'browser_navigate']
    require(len(navigations) == 1, 'one nonce navigation required')
    url = navigations[0]['args']['url']
    for t in tools:
        if t['name'] in ('browser_navigate','browser_click') or t['name'] == 'get_browser_state' and t['data'].get('mode') == 'snapshot':
            require(t['args'].get('target_id') == target and t['args'].get('tab_id') == tab, 'action target mismatch')
    snapshots = [t['data'] for t in tools if t['name'] == 'get_browser_state' and t['data'].get('mode') == 'snapshot']
    require(len(snapshots) == 2 and all(s['target_id'] == target and s['tab_id'] == tab and s['url'] == url for s in snapshots), 'snapshot target mismatch')
    refs = {r['ref'] for r in snapshots[0]['refs']}
    clicks = [t for t in tools if t['name'] == 'browser_click']
    require(len(clicks) == 4 and clicks[0]['data'].get('error',{}).get('code') == 'browser_input_trust_unavailable' and
            all(t['args']['input_route'] == 'dom_event' and t['data']['effect'] != 'refused' for t in clicks[1:]), 'action sequence mismatch')
    require(all(t['args'].get('ref') in refs for t in clicks), 'click ref mismatch')
    repeats = [e['data'] for e in events if e['kind'] == 'repeat-action']
    require(all(e['target'] == {'session':session,'target_id':target,'tab_id':tab} and e['expected'] == e['observed'] for e in repeats), 'repeat target mismatch')
    require([e['data']['observed'] for e in events if e['kind'] == 'repeat-action'] == [2,3], 'repeat counter mismatch')
    counter = one('page-counter'); require(counter['viewport']['count'] == 3 and counter['viewport']['nonce'] == counter['nonce'], 'independent counter mismatch')
    require(counter['url'] == url and urlparse(url).path == '/'+counter['nonce'] and urlparse(url).hostname == '127.0.0.1', 'nonce URL mismatch')
    require(one('host-sentinel')['text'] == 'HOST_SENTINEL', 'host page changed')
    pixels = one('capture-pixels'); image = base/'isolated-inherited/capture-0.png'
    require(pixels['matching'] > pixels['total']*.2 and hashlib.sha256(image.read_bytes()).hexdigest() == pixels['sha256'], 'capture identity mismatch')
    behavior = one('behavior')
    require(all(behavior[k] is True for k in ('navigate','evaluate','click','capture','standaloneTrustedRefusalChecked')) and behavior['repeatedClicks'] == 3, 'behavior incomplete')
    cleanup = one('cleanup')
    require(cleanup['ownedPid'] == child['pid'] and all(cleanup[k] is True for k in ('ownedBrowserGone','ownedProfileGone','hostAlive')), 'owned cleanup incomplete')
    return 1
