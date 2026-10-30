"""GitHub-only deployment, encrypted state bootstrap, asset sync and safe handoff."""
import argparse,base64,fcntl,json,os,plistlib,shutil,sqlite3,subprocess,sys,tempfile,time
from pathlib import Path
from urllib.parse import quote
import requests
from config import Config,ROOT,secure_json
from database import Database
from github_store import GitHub,StateStore,CloudStateError,encode_state,decode_state,snapshot
from video_source import scan
from maintenance import write_env
NAME='techat60-instagram-publisher'
DESCRIPTION='Automated Instagram Reels publisher for TECH AT 60; free GitHub Actions'
STATE_FILE=ROOT/'.github-deployment.json';KEY_FILE=ROOT/'.github-state-key';GH=ROOT/'.tools/gh'
PUBLIC_FILES=['github_deploy.py','maintenance.py','config.py','database.py','meta_client.py','caption_generator.py','video_source.py','publisher.py','reel_queue.py','scheduler.py','github_store.py','cloud_runner.py','requirements-cloud.txt','.github/workflows/instagram-daily.yml','tests/test_system.py','tests/test_queue.py','tests/test_cloud.py']

def command(args,input=None,check=True):
 r=subprocess.run(list(map(str,args)),input=input,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,cwd=ROOT)
 if check and r.returncode:raise RuntimeError('GitHub deployment command failed: '+(' '.join(str(a) for a in args[:3]))+' (no secrets logged).')
 return r

def auth():
 r=command([GH,'auth','token']);token=r.stdout.strip()
 if not token:raise RuntimeError('Complete GitHub device authorization first.')
 return token

def deployment():
 if not STATE_FILE.exists():raise RuntimeError('Run prepare first.')
 return json.loads(STATE_FILE.read_text())
def read_key():
 if not KEY_FILE.exists():raise RuntimeError('Missing local state-encryption key; stop rather than create a new history.')
 if KEY_FILE.stat().st_mode&0o077:raise RuntimeError('State key permissions must be 600.')
 key=base64.b64decode(KEY_FILE.read_text(),validate=True)
 if len(key)!=32:raise RuntimeError('Invalid local state key.')
 return key

def save_secret(repo,name,value):
 # Secret travels through stdin to gh, never a command-line argument or printed output.
 command([GH,'secret','set',name,'--repo',repo],input=value)

def put_source(api,files):
 ref=api.request('GET',api.path('/git/ref/heads/main'));head=ref['object']['sha'];commit=api.request('GET',api.path('/git/commits/'+head))
 tree=api.request('POST',api.path('/git/trees'),{'base_tree':commit['tree']['sha'],'tree':[{'path':name,'mode':'100644','type':'blob','content':content} for name,content in files.items()]})
 if tree['sha']==commit['tree']['sha']:return head
 new=api.request('POST',api.path('/git/commits'),{'message':'Deploy free Instagram cloud publisher','tree':tree['sha'],'parents':[head]})
 api.request('PATCH',api.path('/git/refs/heads/main'),{'sha':new['sha'],'force':False});return new['sha']

def release(api):
 value=api.request('GET',api.path('/releases/tags/reel-inputs'),allowed=(200,404))
 if value:return value
 return api.request('POST',api.path('/releases'),{'tag_name':'reel-inputs','name':'TECH AT 60 publisher video inputs','body':'Video inputs for this Instagram publishing automation. These Shorts have already been published by their owner on YouTube.','draft':False,'prerelease':False})

def sync_assets(api,c):
 value=release(api);assets=[];page=1
 while True:
  chunk=api.request('GET',api.path(f'/releases/{value["id"]}/assets?per_page=100&page={page}'));assets+=chunk
  if len(chunk)<100:break
  page+=1
 existing={a['name']:a for a in assets};manifest=[]
 for video in scan(c.video_folder):
  name=f'{video.number:03d}-{video.sha256}.mp4';asset=existing.get(name)
  if not asset:
   url=f'https://uploads.github.com/repos/{api.repo}/releases/{value["id"]}/assets'
   with video.path.open('rb') as stream:
    try:r=api.session.post(url,params={'name':name},headers={'Authorization':'Bearer '+api.token,'Content-Type':'video/mp4','Content-Length':str(video.path.stat().st_size),'Accept':'application/vnd.github+json'},data=stream,timeout=(15,240),allow_redirects=False)
    except requests.RequestException:raise RuntimeError('Asset upload response uncertain. Next sync will list existing assets before retrying.') from None
   if r.status_code!=201:raise RuntimeError('GitHub video asset upload failed (HTTP '+str(r.status_code)+').')
   asset=r.json();print('Synced video',video.number,video.path.name,flush=True)
  if asset.get('size')!=video.path.stat().st_size:raise RuntimeError('Hosted asset size mismatch; preserve existing asset for review.')
  manifest.append(dict(number=video.number,filename=video.path.name,topic=video.topic,sha256=video.sha256,bytes=video.path.stat().st_size,download_url=asset['browser_download_url']))
 data=json.dumps({'repository':api.repo,'videos':manifest},indent=2)+'\n'
 current=api.contents('cloud/videos.json','main')
 if not current or base64.b64decode(current['content']).decode()!=data:
  api.put_contents('cloud/videos.json',data.encode(),'main',current['sha'] if current else None,'Sync published YouTube Short inputs for Instagram')
 return len(manifest)

def bootstrap_state(api,c,key):
 existing=api.request('GET',api.path('/git/ref/heads/instagram-state'),allowed=(200,404))
 if existing:
  # Existing cloud state is authoritative; never replace it during a redeploy.
  store=StateStore(api,key)
  with tempfile.TemporaryDirectory() as tmp:store.load(Path(tmp)/'check.sqlite3')
  return
 db=Database(c.db_path)
 if db.pending():raise RuntimeError('Local in-progress publication must finish/reconcile before cloud state bootstrap.')
 encrypted=encode_state(snapshot(db.conn),key,(api.repo+':instagram-state:state.enc').encode());db.conn.close()
 blob=api.request('POST',api.path('/git/blobs'),{'content':base64.b64encode(encrypted).decode(),'encoding':'base64'})
 tree=api.request('POST',api.path('/git/trees'),{'tree':[{'path':'state.enc','mode':'100644','type':'blob','sha':blob['sha']}]})
 commit=api.request('POST',api.path('/git/commits'),{'message':'Initialize encrypted Instagram ledger','tree':tree['sha'],'parents':[]})
 api.request('POST',api.path('/git/refs'),{'ref':'refs/heads/instagram-state','sha':commit['sha']})
 print('Encrypted Instagram publication history initialized.',flush=True)

def refresh_mirror(api,c,key):
 with tempfile.TemporaryDirectory() as tmp:
  restored=StateStore(api,key).load(Path(tmp)/'remote.sqlite3');source=sqlite3.connect(restored)
  target=c.root/'data/cloud-mirror.sqlite3';target.parent.mkdir(exist_ok=True,mode=0o700);dest=sqlite3.connect(target,timeout=30)
  try:source.backup(dest)
  finally:dest.close();source.close()
  target.chmod(0o600)
 if c.publish_backend=='github-actions':
  from reel_queue import sync_queue
  db=Database(target);sync_queue(c,db);db.conn.close()

def export_files():
 files={name:(ROOT/name).read_text() for name in PUBLIC_FILES}
 files['README.md']=(ROOT/'README-CLOUD.md').read_text()
 files['.gitignore']='.env\n.venv/\n__pycache__/\n*.pyc\n.instagram-runtime/\n*.sqlite3*\ndata/\nlogs/\n.tools/\n.github-state-key\n.github-deployment.json\nactivation.json\npublication-approval.json\n'
 c=Config.load()
 secrets=[c.token,c.app_secret,c.openai_key,c.app_access_token]
 if KEY_FILE.exists():secrets.append(KEY_FILE.read_text().strip())
 for text in files.values():
  if any(len(secret)>=8 and secret in text for secret in secrets if secret):raise RuntimeError('Secret found in public source export. Deployment blocked.')
 return files

def prepare():
 c=Config.load();token=auth();api=GitHub('placeholder/'+NAME,token);user=api.request('GET','/user');repo=user['login']+'/'+NAME;api=GitHub(repo,token)
 existing=api.request('GET',api.path(''),allowed=(200,404))
 if existing:
  if existing.get('private') or existing.get('description')!=DESCRIPTION:raise RuntimeError('Existing repository is private or not this managed deployment; no changes made.')
 else:
  api.request('POST','/user/repos',{'name':NAME,'description':DESCRIPTION,'private':False,'auto_init':True,'has_issues':False,'has_wiki':False,'default_branch':'main'})
 if STATE_FILE.exists():
  state=deployment()
  if state['repository']!=repo:raise RuntimeError('Saved deployment belongs to a different repository.')
 else:state={'repository':repo,'phase':'preparing'}
 secure_json(STATE_FILE,state)
 if not KEY_FILE.exists():
  if api.request('GET',api.path('/git/ref/heads/instagram-state'),allowed=(200,404)):raise RuntimeError('Remote ledger exists but local decryption key is missing. Do not replace it.')
  fd=os.open(KEY_FILE,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'w') as f:f.write(base64.b64encode(os.urandom(32)).decode())
 key=read_key()
 # Only first deployment disables cloud. Redeploys must not reset an active backend.
 if state.get('phase')!='active':save_secret(repo,'INSTAGRAM_CLOUD_ENABLED','false')
 save_secret(repo,'META_ACCESS_TOKEN',c.token);save_secret(repo,'INSTAGRAM_STATE_KEY',base64.b64encode(key).decode())
 commit=put_source(api,export_files());count=sync_assets(api,c);bootstrap_state(api,c,key);refresh_mirror(api,c,key)
 state.update(source_commit=commit,hosted_videos=count,phase='active' if state.get('phase')=='active' else 'prepared',url='https://github.com/'+repo);secure_json(STATE_FILE,state)
 print(json.dumps({'repository':repo,'hosted_videos':count,'phase':state['phase'],'source_commit':commit},indent=2))

def sync():
 c=Config.load();state=deployment();api=GitHub(state['repository'],auth());count=sync_assets(api,c);refresh_mirror(api,c,read_key());state['hosted_videos']=count;secure_json(STATE_FILE,state);print('Cloud assets/history synchronized:',count,'videos.',flush=True)

def activate():
 c=Config.load();state=deployment()
 if state.get('phase')=='active':print('Cloud already active; local Meta publisher remains disabled.');return
 if not c.daily_approved():raise RuntimeError('Daily public posting approval is missing.')
 repo=state['repository'];api=GitHub(repo,auth());key=read_key()
 runs=api.request('GET',api.path('/actions/workflows/instagram-daily.yml/runs?event=workflow_dispatch&per_page=10')).get('workflow_runs',[])
 main_sha=api.request('GET',api.path('/git/ref/heads/main'))['object']['sha']
 success=[r for r in runs if r.get('head_sha')==main_sha and r.get('conclusion')=='success']
 if not success:raise RuntimeError('First run the read-only Actions check successfully on the current default branch.')
 with (c.root/'data/publisher.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  # Block concurrent local publication before the final fresh state transfer.
  local=Database(c.db_path)
  if local.pending():raise RuntimeError('Reconcile local publication before handoff.')
  store=StateStore(api,key)
  with tempfile.TemporaryDirectory() as tmp:store.load(Path(tmp)/'state.sqlite3')
  store.persist(local);local.conn.close();refresh_mirror(api,c,key)
  command([sys.executable,ROOT/'scheduler.py','stop'])
  write_env(c.root,{'PUBLISH_BACKEND':'github-actions','CAPTION_MODE':'fallback'})
  # If this secret update has an uncertain outcome, leave local disabled, never race two publishers.
  save_secret(repo,'INSTAGRAM_CLOUD_ENABLED','true')
  state.update(phase='active',read_only_test_run=success[0]['html_url'],activated_at=c.now().isoformat());secure_json(STATE_FILE,state)
 print('Cloud daily publisher enabled; local Meta publisher disabled.',flush=True)
 install_sync()

def install_sync():
 label='com.techat60.instagram-github-sync';target=Path.home()/'Library/LaunchAgents'/f'{label}.plist';source=ROOT/'launchd'/target.name
 body=dict(Label=label,ProgramArguments=[str(ROOT/'.venv/bin/python'),'-u',str(ROOT/'github_deploy.py'),'sync'],WorkingDirectory=str(ROOT),StartInterval=900,RunAtLoad=True,ProcessType='Background',StandardOutPath=str(ROOT/'logs/github-sync.stdout.log'),StandardErrorPath=str(ROOT/'logs/github-sync.stderr.log'),EnvironmentVariables=dict(PATH='/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',TZ='Asia/Kolkata'))
 if target.exists() and plistlib.loads(target.read_bytes()).get('WorkingDirectory')!=str(ROOT):raise RuntimeError('Different sync agent already exists.')
 source.write_bytes(plistlib.dumps(body));source.chmod(0o600);target.write_bytes(source.read_bytes());target.chmod(0o600)
 domain=f'gui/{os.getuid()}'
 if command(['/bin/launchctl','print',domain+'/'+label],check=False).returncode:command(['/bin/launchctl','bootstrap',domain,str(target)])
 print('Local asset-only sync enabled; no local Meta publishing.',flush=True)

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','sync','check-run','activate','status']);args=p.parse_args()
 if args.action=='status':print(json.dumps(deployment(),indent=2));return
 if args.action=='check-run':
  state=deployment();command([GH,'workflow','run','instagram-daily.yml','--repo',state['repository'],'--ref','main','-f','mode=check']);print('Read-only Actions validation requested.');return
 (ROOT/'data').mkdir(exist_ok=True)
 with (ROOT/'data/github-sync.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  {'prepare':prepare,'sync':sync,'activate':activate}[args.action]()
if __name__=='__main__':
 try:main()
 except Exception as e:print(json.dumps({'status':'GITHUB_SETUP_ERROR','error':str(e) if isinstance(e,(RuntimeError,CloudStateError)) else type(e).__name__}));raise SystemExit(1)
