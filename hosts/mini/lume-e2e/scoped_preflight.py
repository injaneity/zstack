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
 'Library/Application Support/Arc')
CHROMIUM_TOKENS = ('chrome','chromium','electron','brave','edge','vivaldi','opera','arc','thorium','iridium','yandex')

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


def chromium_tokens(value):
    import re
    return bool(set(re.split('[^a-z0-9]+',value.lower())) & set(CHROMIUM_TOKENS))

class RuntimeIdentity:
    def __enter__(self):
        import ctypes as c
        self.c=c
        self.appkit=c.CDLL('/System/Library/Frameworks/AppKit.framework/AppKit')
        objc=c.CDLL('/usr/lib/libobjc.A.dylib');self.objc=objc
        objc.objc_getClass.argtypes=[c.c_char_p];objc.objc_getClass.restype=c.c_void_p
        objc.sel_registerName.argtypes=[c.c_char_p];objc.sel_registerName.restype=c.c_void_p
        self.send=c.CFUNCTYPE(c.c_void_p,c.c_void_p,c.c_void_p)(('objc_msgSend',objc))
        self.pid_send=c.CFUNCTYPE(c.c_void_p,c.c_void_p,c.c_void_p,c.c_int32)(('objc_msgSend',objc))
        self.length=c.CFUNCTYPE(c.c_ulong,c.c_void_p,c.c_void_p)(('objc_msgSend',objc))
        self.cls=objc.objc_getClass(b'NSRunningApplication')
        require(bool(self.cls),'runtime_identity_api_unavailable')
        pool=objc.objc_getClass(b'NSAutoreleasePool')
        self.pool=self.send(self.send(pool,self.sel('alloc')),self.sel('init'))
        return self
    def sel(self,name):return self.objc.sel_registerName(name.encode())
    def string(self,value):
        if not value:return None
        require(self.length(value,self.sel('length'))<=512,'runtime_identity_too_long')
        raw=self.send(value,self.sel('UTF8String'))
        require(bool(raw),'runtime_identity_encoding_failed')
        return self.c.string_at(raw).decode('utf-8')
    def lookup(self,pid):
        app=self.pid_send(self.cls,self.sel('runningApplicationWithProcessIdentifier:'),pid)
        if not app:return dict(application_present=False,name=None,bundle_id=None)
        return dict(application_present=True,name=self.string(self.send(app,self.sel('localizedName'))),
                    bundle_id=self.string(self.send(app,self.sel('bundleIdentifier'))))
    def __exit__(self,*args):self.send(self.pool,self.sel('drain'))

def process_observations(output,lookup):
    rows=[]
    for index,line in enumerate(output.splitlines()):
        fields=line.strip().split(None,3)
        if len(fields)!=4 or not (all(v.isdecimal() for v in fields[:2]) and fields[2].lstrip('-').isdecimal()):
            rows.append(dict(line_index=index,classification='malformed_process_row',field_count=len(fields)));continue
        pid,ppid,uid=map(int,fields[:3]);executable=fields[3]
        row=dict(line_index=index,pid=pid,ppid=ppid,uid=uid,executable=executable)
        try:
            identity=lookup(pid)
            require(type(identity) is dict and type(identity.get('application_present')) is bool and all(identity.get(k) is None or type(identity.get(k)) is str for k in ('name','bundle_id')),'invalid_runtime_identity')
            row.update(identity,identity_error=None)
        except Exception as error:row.update(application_present=None,name=None,bundle_id=None,identity_error=type(error).__name__)
        rows.append(row)
    return rows

def relevant_processes(rows):
    matches=[];relevant=set()
    for row in rows:
        if 'pid' not in row:
            matches.append(dict(row));continue
        executable=row['executable'];name=row.get('name') or Path(executable).name
        path_names=' '.join(p[:-4] for p in Path(executable).parts if p.endswith('.app'))
        direct=chromium_tokens(name+' '+(row.get('bundle_id') or ''))
        associated=chromium_tokens(path_names)
        if direct or associated:
            relevant.add(row['pid']);matches.append(dict(row,classification='chromium_runtime_identity' if direct else 'chromium_path_association'))
    changed=True
    while changed:
        changed=False
        for row in rows:
            if 'pid' in row and row['pid'] not in relevant and row['ppid'] in relevant:
                relevant.add(row['pid']);matches.append(dict(row,classification='chromium_parent_association'));changed=True
    return matches

def validate_process_observations(rows):
    require(type(rows) is list and 0<len(rows)<=4096,'process_observation_budget')
    seen=set()
    for i,row in enumerate(rows):
        require(type(row) is dict and row.get('line_index')==i,'process_observation_order')
        require(all(type(row.get(k)) is int for k in ('pid','ppid','uid')) and row['ppid']>=0 and -(2**31)<=row['uid']<2**32 and row['pid']>0 and row['pid'] not in seen,'process_observation_identity')
        seen.add(row['pid'])
        require(type(row.get('executable')) is str and bool(row['executable']),'process_executable_missing')
        require(type(row.get('application_present')) is bool and row.get('identity_error') is None,'runtime_acquisition_failed')
        require(all(k in row and (row[k] is None or type(row[k]) is str) for k in ('name','bundle_id')),'runtime_identity_shape')
        require(row['application_present'] or (row['name'] is None and row['bundle_id'] is None),'runtime_identity_contradiction')

def process_projection(output):
    with RuntimeIdentity() as runtime:return relevant_processes(process_observations(output,runtime.lookup))

def safe_mount_observations(output):
    import re
    rows=[]
    for index,line in enumerate(output.splitlines()):
        source,sep,remainder=line.partition(' on ')
        destination,options_sep,tail=remainder.rpartition(' (')
        closed=tail.endswith(')');fields=tail[:-1].split(', ') if closed else []
        kind=fields[0] if fields else None
        tag=source if kind=='virtiofs' and re.fullmatch(r'[A-Za-z0-9._-]{1,128}',source) else None
        complete=bool(sep and options_sep and closed and destination.startswith('/') and kind and re.fullmatch(r'[A-Za-z0-9_-]+',kind))
        rows.append(dict(line_index=index,source_class='virtio_tag' if tag else 'device' if source.startswith('/dev/') else 'remote_or_opaque',
            source_tag=tag,destination=destination if complete else None,
            type=kind if kind and re.fullmatch(r'[A-Za-z0-9_-]+',kind) else None,
            readonly='read-only' in fields[1:],options=[v for v in fields[1:] if v in ('read-only','local','nodev','nosuid','noexec','nobrowse')],
            parse_complete=complete,parse_reason=None if complete else 'unrecognized_mount_structure'))
    return rows

def mount_projection(output):
    return [dict(row,classification='unresolved_exposure' if row['parse_complete'] else 'malformed_mount_row')
            for row in safe_mount_observations(output)
            if not row['parse_complete'] or row['type'] in ('virtiofs','smbfs','webdav','nfs','9p','osxfuse','macfuse')]

def inspect_internal_mount(row):
    require(row['type']=='virtiofs' and row['source_tag']=='com.apple.virtio-fs.automount','unresolved_mount_exposure')
    path=Path(row['destination'])
    require(path.parent==Path('/Volumes'),'unexpected_internal_mount_destination')
    require(not path.is_symlink() and path.is_dir(),'unsafe_internal_mount')
    children=list(path.iterdir())
    row['root_entry_count']=len(children)
    require(len(children)==1 and children[0].name=='.lume-live-share','unexpected_internal_mount_entries')
    child=children[0];info=child.lstat()
    require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode),'unsafe_internal_placeholder')
    row['placeholder_empty']=not any(child.iterdir())
    row['placeholder_readonly']=bool(os.statvfs(child).f_flag & os.ST_RDONLY)
    require(row['placeholder_empty'] and row['placeholder_readonly'],'internal_placeholder_not_empty_readonly')
    row['classification']='reviewed_lume_empty_readonly_placeholder'

def check(root,out):
    home=Path('/Users/lume');uid=os.getuid()
    report=dict(policy='chromium-grant-isolation-v1',passed=False,errors=[],scopes={},
                database_contents_inspected=False,forensic_absence_claim=False)
    def scope(name,operation):
        try:operation();report['scopes'][name]='complete'
        except Refusal as error:
            report['errors'].append(dict(scope=name,code=str(error)));report['scopes'][name]='refused'
        except Exception as error:
            report['errors'].append(dict(scope=name,code='unreadable_metadata:'+type(error).__name__));report['scopes'][name]='blocked'
    try:
        require(root.parent==home and root.name.startswith('cua-e2e-'),'not_owned_run')
        require(pwd.getpwuid(uid).pw_name=='lume' and home.lstat().st_uid==uid and not home.is_symlink(),'unexpected_execution_account')
        check_accounts(pwd.getpwall(),uid);safe_path(root,home,uid)
        report['expected_account']=True
        def profiles():
            stores={};report['other_chromium_stores']=stores
            for relative in OTHER_STORES:
                p=safe_path(home/relative,home,uid)
                stores[relative]=bool(p.exists() and any(p.iterdir()))
            require(not any(stores.values()),'foreign_chromium_store')
            chrome=safe_path(home/CHROME,home,uid)
            report['chrome_metadata']=metadata_tree(chrome,uid)
            require(chrome.is_dir() and chrome.lstat().st_uid==uid,'chrome_owner_or_store_mismatch')
            indexed=[p.name for p in chrome.iterdir() if p.is_dir() and (p.name in ('Default','System Profile','Guest Profile') or p.name.startswith('Profile ') or (p/'Preferences').exists())]
            report['profile_count']=len(indexed)
            require(set(indexed)<={'Default','System Profile'} and 'Default' in indexed,'foreign_chromium_profile')
            local=load_projected_json(safe_path(chrome/'Local State',home,uid))
            projections={};report['account_projections']=projections
            for name in indexed:
                projections[name]=account_indicators(local,load_projected_json(safe_path(chrome/name/'Preferences',home,uid)))
            require(all(p['account_count']==0 and not p['account_identifier_present'] and not p['sync_or_consent_enabled'] for p in projections.values()),'chromium_account_indicator')
        scope('chromium_profiles',profiles)
        def processes():
            output=subprocess.run(['/bin/ps','-axww','-o','pid=,ppid=,uid=,comm='],capture_output=True,text=True,timeout=5,check=True).stdout
            def unqueried(pid):raise Refusal('runtime_not_acquired')
            report['process_observations']=process_observations(output,unqueried)
            report['runtime_acquisition_complete']=False
            with RuntimeIdentity() as runtime:
                observations=process_observations(output,runtime.lookup)
            report['process_observations']=observations
            report['runtime_acquisition_complete']=True
            validate_process_observations(observations)
            rows=relevant_processes(observations);report['processes']=rows
            require(not rows,'preexisting_chromium_process_or_unknown_identity')
        scope('processes',processes)
        def mounts():
            output=subprocess.run(['/sbin/mount'],capture_output=True,text=True,timeout=5,check=True).stdout
            report['mount_observations']=safe_mount_observations(output)
            rows=mount_projection(output);report['mounts']=rows
            require(len(report['mount_observations'])<=256,'mount_budget_exceeded')
            failures=[]
            for row in rows:
                try:
                    require(row['parse_complete'],'ambiguous_mount_row')
                    inspect_internal_mount(row)
                except Refusal as error:row['refusal']=str(error);failures.append(str(error))
            require(not failures,'unresolved_mount_exposure')
        scope('mounts',mounts)
        report['passed']=not report['errors'] and report['scopes']==dict.fromkeys(('chromium_profiles','processes','mounts'),'complete')
    except Refusal as error:report['errors'].append(dict(scope='identity',code=str(error)))
    except Exception as error:report['errors'].append(dict(scope='identity',code='unreadable_metadata:'+type(error).__name__))
    output=out/'privacy-preflight.json';output.write_text(json.dumps(report,indent=2)+'\n');output.chmod(0o600)
    require(report['passed'],'scoped_chromium_isolation_unverified')
    return report
