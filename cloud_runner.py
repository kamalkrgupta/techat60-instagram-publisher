"""GitHub-hosted publisher; no Mac access and no ephemeral-only post state."""
import argparse,base64,hashlib,json,logging,os,time
from pathlib import Path
from datetime import timedelta
from urllib.parse import quote,urlsplit
import requests
from config import Config,prepare_storage,secure_json
from database import Database
from github_store import GitHub,StateStore,CloudStateError
from publisher import Publisher,check,Redactor
from video_source import sha,scan,select,validate_video
from caption_generator import CaptionGenerator

def download_video(entry,folder,repo,session=None):
 name=Path(entry['filename']).name
 if name!=entry['filename'] or not name.lower().endswith('.mp4'):raise CloudStateError('Manifest filename is invalid.')
 url=entry['download_url'];u=urlsplit(url)
 if u.scheme!='https' or u.hostname!='github.com' or not u.path.startswith('/'+repo+'/releases/download/') or u.username or u.password:raise CloudStateError('Video URL is not an approved repository Release asset.')
 folder=Path(folder);folder.mkdir(parents=True,exist_ok=True,mode=0o700);target=folder/name
 if target.exists() and sha(target)==entry['sha256']:return target
 temporary=folder/('.'+entry['sha256']+'.part');s=session or requests.Session()
 try:
  # Public assets need no credential. No Meta/GitHub authorization is sent on redirects.
  with s.get(url,stream=True,timeout=(15,180),headers={'User-Agent':'Techat60-Instagram'},allow_redirects=True) as r:
   r.raise_for_status()
   if urlsplit(r.url).hostname not in ('github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'):raise CloudStateError('Unexpected Release download host.')
   total=0;digest=hashlib.sha256()
   with temporary.open('wb') as out:
    for chunk in r.iter_content(1024*1024):
     if chunk:
      total+=len(chunk)
      if total>1_000_000_000:raise CloudStateError('Video exceeds approved size.')
      out.write(chunk);digest.update(chunk)
   if digest.hexdigest()!=entry['sha256']:raise CloudStateError('Hosted video checksum mismatch; do not publish.')
   temporary.chmod(0o600);os.replace(temporary,target)
 finally:temporary.unlink(missing_ok=True)
 return target

def main():
 p=argparse.ArgumentParser();p.add_argument('--mode',choices=['check','publish'],default='check');args=p.parse_args()
 if os.environ.get('GITHUB_ACTIONS')!='true':raise CloudStateError('Cloud runner is only enabled inside GitHub Actions.')
 repo=os.environ['GITHUB_REPOSITORY'];token=os.environ['GITHUB_TOKEN'];key=base64.b64decode(os.environ['INSTAGRAM_STATE_KEY'],validate=True)
 if len(key)!=32:raise CloudStateError('Invalid state encryption key.')
 root=Path('.instagram-runtime').resolve();videos=root/'videos';videos.mkdir(parents=True,exist_ok=True)
 os.environ.update(VIDEO_FOLDER=str(videos),TIMEZONE='Asia/Kolkata',PUBLISH_TIME='19:00',CAPTION_MODE='fallback',OPENAI_API_KEY='',PUBLISH_BACKEND='github-actions')
 c=Config.load(root);prepare_storage(c)
 h=logging.StreamHandler();h.addFilter(Redactor([c.token,c.app_secret,token,os.environ['INSTAGRAM_STATE_KEY']]));logging.basicConfig(level=logging.INFO,handlers=[h])
 api=GitHub(repo,token);store=StateStore(api,key);store.load(c.db_path);db=Database(c.db_path)
 # A cold runner must restore the existing history; it never starts an empty ledger.
 if db.setting('seed_match')!='same':raise CloudStateError('Original Reel match is not present in the migrated ledger.')
 manifest=json.loads(Path('cloud/videos.json').read_text());entries=manifest['videos']
 if manifest.get('repository')!=repo:raise CloudStateError('Wrong asset repository.')
 if len({v['sha256'] for v in entries})!=len(entries):raise CloudStateError('Duplicate hashes in asset manifest.')
 entries=sorted(entries,key=lambda e:(e['number'],e['filename']));by_hash={v['sha256']:v for v in entries}
 pending=db.pending();needed={j['sha256'] for j in pending}
 candidate=next((v for v in entries if v['sha256'] not in db.published_hashes()),None)
 if candidate:needed.add(candidate['sha256'])
 for digest in needed:
  if digest not in by_hash:raise CloudStateError('Pending/eligible video is not available in cloud assets.')
  download_video(by_hash[digest],videos,repo)
 # Paths are local to each runner, but identity stays bound to the original content hash.
 with db.conn:
  for job in pending:db.conn.execute('UPDATE jobs SET filename=? WHERE id=? AND sha256=?',(str(videos/by_hash[job['sha256']]['filename']),job['id'],job['sha256']))
 enabled=os.environ.get('INSTAGRAM_CLOUD_ENABLED')=='true'
 if enabled:
  secure_json(c.activation,{'approved':True,'account_id':c.account_id,'backend':'github-actions'})
  secure_json(c.root/'publication-approval.json',{'approved':True,'account_id':c.account_id})
 if args.mode=='check':
  result=check(c,db)
  result['hosted_video_count']=len(entries);result['durable_state_restored']=True;result['publications']=len(db.history());result['meta_mutations']=0
  print(json.dumps(result,indent=2));
  if not result.get('meta',{}).get('valid') or not result.get('meta',{}).get('expiration_verified') or result.get('selection_hold'):return 2
  return 0
 if not enabled:
  print(json.dumps({'status':'CLOUD_PUBLISH_DISABLED','hosted_video_count':len(entries)}));return 0
 # Every critical ledger commit is encrypted and persisted BEFORE control returns to Meta calls.
 db.on_commit=lambda:store.persist(db)
 store.persist(db)
 # Start early and wait toward 19:00, allowing scheduler/setup overhead; no exact-time guarantee.
 now=c.now();target=now.replace(hour=19,minute=0,second=0,microsecond=0);wait=(target-now).total_seconds()
 publisher=Publisher(c,db)
 if 0<wait<=16*60:
  staged=publisher.stage_next()
  print('Cloud preflight result:',staged.get('status'),'— waiting toward 19:00 IST.',flush=True)
  wait=(target-c.now()).total_seconds()
  if wait>0:time.sleep(wait)
 result=publisher.execute();store.persist(db)
 # Report fields cannot include the authenticated resumable upload URI.
 print(json.dumps({k:v for k,v in result.items() if k!='upload_uri'},indent=2));return 0
if __name__=='__main__':
 try:raise SystemExit(main())
 except Exception as e:
  # Never print raw HTTP exceptions, secret-bearing URLs or decrypted state.
  print(json.dumps({'status':'CLOUD_ERROR','error':str(e) if isinstance(e,CloudStateError) else type(e).__name__}));raise SystemExit(1)
