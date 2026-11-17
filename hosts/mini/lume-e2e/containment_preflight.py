import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys

TARGET = 'pr3140-containment-preflight'
EXPECTED_SEED = 'cua-driver-browser-baseline-20260908-chrome152'
CHROME = 'Library/Application Support/Google/Chrome'
OTHER_STORES = ('Library/Application Support/Google/Chrome Beta',
 'Library/Application Support/Google/Chrome Canary','Library/Application Support/Chromium',
 'Library/Application Support/Microsoft Edge','Library/Application Support/BraveSoftware',
 'Library/Application Support/Arc','Library/Application Support/Firefox','Library/Safari')
DOCUMENTS = ('Desktop','Documents','Downloads','Pictures','Movies','Music','Public')
WEB_BROWSERS = ('Google Chrome.app/Contents/','Chromium.app/Contents/',
 'Microsoft Edge.app/Contents/','Brave Browser.app/Contents/','Arc.app/Contents/',
 'Firefox.app/Contents/','Safari.app/Contents/')

class Refusal(ValueError): pass

def require(condition,code):
    if not condition: raise Refusal(code)

def safe_path(path,anchor,uid):
    require(path.is_relative_to(anchor),'path_outside_scope')
    cursor=anchor
    for component in path.relative_to(anchor).parts:
        cursor=cursor/component
        try: info=cursor.lstat()
        except FileNotFoundError: break
        require(not stat.S_ISLNK(info.st_mode),'symlink_in_scope')
        require(info.st_uid in (0,uid),'unexpected_path_owner')
    return path

def metadata_tree(path,uid,limit=20000):
    counts=dict(files=0,directories=0,symlinks=0)
    if not path.exists(): return counts
    for base,dirs,files in os.walk(path,followlinks=False,onerror=lambda e:(_ for _ in ()).throw(Refusal('unreadable_directory'))):
        for name in dirs+files:
            info=(Path(base)/name).lstat()
            require(info.st_uid in (0,uid),'unexpected_path_owner')
            if stat.S_ISLNK(info.st_mode):
                counts['symlinks']+=1
                raise Refusal('symlink_in_scope')
            counts['directories' if stat.S_ISDIR(info.st_mode) else 'files']+=1
            require(sum(counts.values())<=limit,'metadata_budget_exceeded')
    return counts

def load_projected_json(path):
    require(path.name in ('Local State','Preferences'),'unexpected_json_file')
    info=path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_size<=4*1024*1024,'invalid_preferences_file')
    try: value=json.loads(path.read_text())
    except (ValueError,UnicodeError): raise Refusal('invalid_preferences_json') from None
    require(type(value) is dict,'invalid_preferences_shape')
    return value

def get(root,key,default):
    value=root
    for part in key.split('.'):
        require(type(value) is dict,'invalid_account_metadata')
        if part not in value: return default
        value=value[part]
    return value

def account_indicators(local,prefs):
    profiles=get(local,'profile.info_cache',None)
    require(type(profiles) is dict and set(profiles)=={'Default'},'unexpected_profile_index')
    entry=profiles['Default'];require(type(entry) is dict,'invalid_profile_entry')
    names=('user_name','gaia_id','account_id_key')
    strings=[entry.get(key,'') for key in names]
    for key in ('google.services.account_id','google.services.last_gaia_id',
                'google.services.last_username','google.services.last_signed_in_username'):
        strings.append(get(prefs,key,''))
    require(all(type(value) is str for value in strings),'invalid_account_indicator')
    accounts=get(prefs,'account_info',[])
    require(type(accounts) is list,'invalid_account_count')
    bools=[entry.get('is_consented_primary_account'),get(prefs,'google.services.consented_to_sync',False),get(prefs,'sync.has_setup_completed',False)]
    require(all(type(value) is bool for value in bools),'invalid_sync_indicator')
    return dict(profile_count=1,account_count=len(accounts),account_identifier_present=any(strings),sync_or_consent_enabled=any(bools))

def check_accounts(accounts,expected_uid):
    interactive=[]
    for account in accounts:
        if account.pw_name == 'nobody':
            identity=(account.pw_uid,account.pw_gid,account.pw_dir,account.pw_shell)
            require(identity==(4294967294,4294967294,'/var/empty','/usr/bin/false'),'altered_nobody_system_identity')
            continue
        if account.pw_uid>=500 or account.pw_dir.startswith('/Users/'):
            interactive.append(account)
    require(len(interactive)==1 and interactive[0].pw_name=='lume' and interactive[0].pw_uid==expected_uid,'unexpected_interactive_account')
    return len(interactive)

def immediate_metadata(path,anchor,uid):
    safe_path(path,anchor,uid)
    entries=[]
    if not path.exists(): return entries
    with os.scandir(path) as children:
        for child in children:
            require(len(entries)<2000,'immediate_metadata_budget_exceeded')
            info=child.stat(follow_symlinks=False)
            kind=('symlink' if stat.S_ISLNK(info.st_mode) else
                  'directory' if stat.S_ISDIR(info.st_mode) else
                  'file' if stat.S_ISREG(info.st_mode) else 'other')
            entries.append(dict(name=child.name,type=kind,uid=info.st_uid,
                                gid=info.st_gid,size_bytes=info.st_size))
    return entries

def inventory(home,run_root,report,errors):
    uid=os.getuid()
    require(home==Path('/Users/lume') and pwd.getpwuid(uid).pw_name=='lume','unexpected_execution_account')
    require(home.lstat().st_uid==uid and not home.is_symlink(),'unexpected_home_owner')
    check_accounts(pwd.getpwall(),uid)
    require(run_root.parent==home and run_root.name.startswith('cua-e2e-'),'not_owned_run')
    safe_path(run_root,home,uid)
    report.update(dict(expected_account=True,expected_home_owner=True,interactive_account_count=1,
                database_contents_inspected=False,forensic_absence_claim=False,documents={},browser_stores={},scopes={}))
    def collect(label,operation):
        try:
            operation()
            report['scopes'][label]='complete'
        except Refusal as error:
            errors.append(dict(scope=label,code=str(error)))
            report['scopes'][label]='refused'
        except Exception as error:
            errors.append(dict(scope=label,code='unreadable_or_ambiguous_metadata:'+type(error).__name__))
            report['scopes'][label]='blocked'
    def homes():
        entries=[p for p in home.parent.iterdir() if p.name not in ('.localized','.DS_Store','lume','Shared')]
        require(not entries,'unexpected_user_home')
    collect('homes',homes)
    def document(label,path):
        entries=immediate_metadata(path,home.parent,uid)
        report['documents'][label]=dict(immediate_entries=entries)
        require(all(e['type']!='symlink' for e in entries),'symlink_in_scope')
        require(all(e['uid'] in (0,uid) for e in entries),'unexpected_path_owner')
        unexplained=[e for e in entries if e['name'] not in ('.localized','.DS_Store')]
        if label=='Public' and len(unexplained)==1 and unexplained[0]['name']=='Drop Box':
            require(unexplained[0]['type']=='directory','invalid_drop_box_type')
            unexplained=immediate_metadata(path/'Drop Box',home.parent,uid)
            report['documents'][label]['drop_box_entries']=unexplained
        report['documents'][label]['non_os_entries']=len(unexplained)
        require(not unexplained,'unexplained_document_occupancy:'+label)
    for label,path in [(n,home/n) for n in DOCUMENTS]+[('Shared',home.parent/'Shared')]:
        collect('documents:'+label,lambda label=label,path=path:document(label,path))
    def other_store(relative):
        path=safe_path(home/relative,home,uid)
        count=0
        if path.exists():
            with os.scandir(path) as children:
                for _ in children:
                    count+=1
                    require(count<=2000,'immediate_metadata_budget_exceeded')
        report['browser_stores'][relative]=dict(entries=count)
        require(count==0,'unexpected_browser_store')
    for relative in OTHER_STORES:
        collect('browser_store:'+relative,lambda relative=relative:other_store(relative))
    def chrome_store():
        chrome=safe_path(home/CHROME,home,uid)
        report['chrome_metadata']=metadata_tree(chrome,uid)
        require(chrome.is_dir() and chrome.lstat().st_uid==uid,'documented_chrome_store_missing_or_wrong_owner')
        indexed=[]
        for path in chrome.iterdir():
            if path.is_dir() and (path.name in ('Default','System Profile','Guest Profile') or path.name.startswith('Profile ') or (path/'Preferences').exists()): indexed.append(path.name)
        require(set(indexed) <= {'Default','System Profile'} and 'Default' in indexed,'unexpected_browser_profile')
        local=load_projected_json(safe_path(chrome/'Local State',home,uid))
        prefs=load_projected_json(safe_path(chrome/'Default/Preferences',home,uid))
        indicators=account_indicators(local,prefs)
        report['account_indicators']=indicators
        require(indicators['account_count']==0 and not indicators['account_identifier_present'] and not indicators['sync_or_consent_enabled'],'signed_in_or_prior_account_indicator')
        if 'System Profile' in indexed:
            system_prefs=load_projected_json(safe_path(chrome/'System Profile/Preferences',home,uid))
            system=account_indicators(local,system_prefs)
            report['system_account_indicators']=system
            require(system['account_count']==0 and not system['account_identifier_present'] and not system['sync_or_consent_enabled'],'system_profile_account_indicator')
        report['profile_classification']='documented_initialized_signed_out_test_Default'
    collect('chrome',chrome_store)
    def processes():
        output=subprocess.run(['/bin/ps','-axo','uid=,comm='],capture_output=True,text=True,timeout=5,check=True).stdout
        report['running_browser_process_count']=sum(any(browser in line for browser in WEB_BROWSERS) for line in output.splitlines())
        require(report['running_browser_process_count']==0,'browser_already_running')
    collect('processes',processes)
    def mounts():
        output=subprocess.run(['/sbin/mount'],capture_output=True,text=True,timeout=5,check=True).stdout
        report['shared_or_network_mount_count']=sum(any(kind in line.lower() for kind in ('virtiofs','smbfs','webdav',' (nfs',' (9p','osxfuse','macfuse')) for line in output.splitlines())
        require(report['shared_or_network_mount_count']==0,'unexpected_shared_or_network_mount')
    collect('mounts',mounts)
    return report

def expected_scopes():
    return {'homes','chrome','processes','mounts'} | {'documents:'+n for n in (*DOCUMENTS,'Shared')} | {'browser_store:'+n for n in OTHER_STORES}

def evidence_check(base,sha):
    result=json.loads((Path(base)/'inventory.json').read_text())
    require(result.get('source_sha')==sha and result.get('target')==TARGET,'inventory_identity_mismatch')
    require(result.get('scope')=='metadata_only' and result.get('e2e_executed') is False,'inventory_scope_mismatch')
    require(result.get('status')=='pass' and result.get('errors')==[],'inventory_refused_or_incomplete')
    report=result['inventory']
    require(report.get('scopes')==dict.fromkeys(expected_scopes(),'complete'),'scope_coverage_incomplete')
    require(report.get('database_contents_inspected') is False and report.get('forensic_absence_claim') is False,'overclaim')
    require(report.get('running_browser_process_count')==0 and report.get('shared_or_network_mount_count')==0,'live_surface_not_isolated')
    indicators=report['account_indicators']
    expected=dict(profile_count=1,account_count=0,account_identifier_present=False,sync_or_consent_enabled=False)
    require({k:(type(v),v) for k,v in indicators.items()}=={k:(type(v),v) for k,v in expected.items()},'account_indicators_not_clear')
    metadata=report.get('chrome_metadata',{})
    require(type(metadata.get('files')) is int and metadata['files']>=2 and type(metadata.get('directories')) is int and metadata['directories']>=1 and metadata.get('symlinks')==0,'chrome_metadata_incomplete')
    require(report.get('profile_classification')=='documented_initialized_signed_out_test_Default','profile_classification_missing')
    require(all(v.get('non_os_entries')==0 for v in report['documents'].values()) and set(report['documents'])==set(DOCUMENTS)|{'Shared'},'document_coverage_incomplete')
    require(set(report['browser_stores'])==set(OTHER_STORES) and all(v.get('entries')==0 for v in report['browser_stores'].values()),'browser_coverage_incomplete')
    require(report.get('expected_account') is True and report.get('expected_home_owner') is True and report.get('interactive_account_count')==1,'account_coverage_incomplete')
    return 1

def main(root):
    manifest=json.loads((root/'harness/targeted-package.json').read_text())
    out=root/'source/artifacts/cua-driver/targeted'/TARGET
    out.mkdir(parents=True,exist_ok=False)
    result=dict(target=TARGET,source_sha=manifest['source_sha'],scope='metadata_only',e2e_executed=False,status='refused',errors=[])
    try:
        require(manifest.get('expected_seed')==EXPECTED_SEED,'unexpected_seed_contract')
        require((root/'source/.cua-e2e-source-sha').read_text().strip()==manifest['source_sha'],'source_identity_mismatch')
        result['inventory']={}
        inventory(Path('/Users/lume'),root,result['inventory'],result['errors'])
        if not result['errors']: result['status']='pass'
    except Refusal as error: result['errors']=[str(error)]
    except Exception as error: result['errors']=['unreadable_or_ambiguous_metadata:'+type(error).__name__]
    (out/'inventory.json').write_text(json.dumps(result,indent=2)+'\n')
    return 0 if result['status']=='pass' else 1

if __name__=='__main__':sys.exit(main(Path(sys.argv[1]).resolve()))
