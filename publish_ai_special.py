"""Explicitly approved extra Reel; shares durable hash history, never a daily job."""
import json,fcntl,uuid,tempfile,sys,os,base64
from pathlib import Path
from datetime import datetime,timedelta
from urllib.parse import urlsplit
from config import Config,ROOT,secure_json
from database import Database,stamp
from github_deploy import deployment,auth,read_key
from github_store import GitHub,StateStore
from meta_client import MetaClient
from publisher import Publisher
from video_source import Video,sha,validate_video
SOURCE=Path(os.environ.get('SPECIAL_PROJECT_ROOT',str(ROOT.parent/'Documents/YT Short/special-shorts/ai-friend-or-devil')))
DIGEST='7ee826575558054136673106341b66036da624e6863f76f4b5c36871a5bbb811'
CAPTION="""AI: friend or devil? It depends on what you trust it with.

Use it to learn, practise and get a first draft. Check important claims, verify unexpected requests independently, and pause before sharing private information.

Where would you draw the line? Tell us in the comments. Follow @tech_at_60 for more beginner-friendly tech lessons.

Special · AI Friend or Devil · TECH AT 60

#ArtificialIntelligence #AIAwareness #AISafety #TechAt60 #TechForBeginners"""
class SpecialLedger(Database):
 def __init__(self,path):
  super().__init__(path)
  self.conn.execute('''CREATE TABLE IF NOT EXISTS special_jobs(id TEXT PRIMARY KEY,local_day TEXT NOT NULL,filename TEXT NOT NULL,sha256 TEXT NOT NULL UNIQUE,caption TEXT NOT NULL,status TEXT NOT NULL,container_id TEXT,upload_uri TEXT,media_id TEXT,permalink TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,publish_attempt_at TEXT,published_at TEXT,error TEXT,attempts INTEGER NOT NULL DEFAULT 0)''');self.conn.commit()
 def job(self,ident):
  r=self.conn.execute('SELECT * FROM special_jobs WHERE id=?',(ident,)).fetchone();return dict(r) if r else None
 def for_hash(self,digest):
  r=self.conn.execute('SELECT * FROM special_jobs WHERE sha256=?',(digest,)).fetchone();return dict(r) if r else None
 def reserve_special(self,day,video,caption):
  if video.sha256 in self.published_hashes():raise RuntimeError('Content already published.')
  ident=uuid.uuid4().hex;now=stamp()
  with self.conn:self.conn.execute('INSERT INTO special_jobs(id,local_day,filename,sha256,caption,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(ident,day,str(video.path),video.sha256,caption,'CREATING',now,now))
  self.notify();return self.job(ident)
 def bind_source(self,job,video):
  if job['sha256']!=video.sha256 or sha(video.path)!=video.sha256:raise RuntimeError('Special source hash changed; no path rebinding allowed.')
  if job['filename']!=str(video.path):
   with self.conn:self.conn.execute('UPDATE special_jobs SET filename=?,updated_at=? WHERE id=? AND sha256=?',(str(video.path),stamp(),job['id'],video.sha256))
   self.notify()
  return self.job(job['id'])
 def update(self,ident,status=None,**values):
  if not set(values)<={'container_id','upload_uri','media_id','permalink','publish_attempt_at','published_at','error','attempts'}:raise ValueError('Invalid special job field')
  values['updated_at']=stamp()
  if status:values['status']=status
  with self.conn:
   self.conn.execute('UPDATE special_jobs SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),ident))
   self.conn.execute('INSERT INTO events(job_id,status,detail,created_at) VALUES(?,?,?,?)',(ident,'SPECIAL_'+(status or 'UPDATE'),json.dumps({k:v for k,v in values.items() if k!='upload_uri'}),stamp()))
  self.notify();return self.job(ident)
 def confirm(self,job,media,zone):
  if str(media.get('id'))!=str(job['media_id']) or not media.get('timestamp') or urlsplit(media.get('permalink','')).hostname not in ('www.instagram.com','instagram.com'):raise RuntimeError('Special publication metadata not confirmed.')
  at=datetime.fromisoformat(media['timestamp'].replace('Z','+00:00'))
  with self.conn:
   self.conn.execute('INSERT OR IGNORE INTO publications VALUES(?,?,?,?,?,?,?,?,?)',(media['id'],job['sha256'],Path(job['filename']).name,job['caption'],media['permalink'],at.astimezone(zone).date().isoformat(),at.isoformat(),1,'Explicitly user-approved extra Reel; separate from daily queue'))
   row=self.conn.execute('SELECT sha256 FROM publications WHERE media_id=?',(media['id'],)).fetchone()
   if not row or row['sha256']!=job['sha256']:raise RuntimeError('Special publication identity conflict.')
   self.conn.execute("UPDATE special_jobs SET status='PUBLISHED',permalink=?,published_at=?,updated_at=?,error=NULL WHERE id=?",(media['permalink'],at.isoformat(),stamp(),job['id']))
  self.notify();return self.job(job['id'])
def cloud_inputs():
 if os.environ.get('GITHUB_ACTIONS')!='true':return None
 if os.environ.get('GITHUB_EVENT_NAME')!='workflow_dispatch' or os.environ.get('SPECIAL_APPROVED_SHA')!=DIGEST or os.environ.get('INSTAGRAM_CLOUD_ENABLED')!='true':raise RuntimeError('Explicit enabled manual special-publication dispatch and reviewed hash are required.')
 from cloud_runner import download_video
 api=GitHub(os.environ['GITHUB_REPOSITORY'],os.environ['GITHUB_TOKEN'])
 manifest=json.loads((ROOT/'cloud/special-ai-friend.json').read_text())
 if manifest['repository']!=api.repo or manifest['original']['sha256']!=DIGEST:raise RuntimeError('Special source manifest mismatch.')
 download_video(manifest['original'],SOURCE/'out',api.repo)
 download_video(manifest['transport'],ROOT/'data/prepared',api.repo)
 os.environ['VIDEO_FOLDER']=str(SOURCE/'out')
 secure_json(SOURCE/'instagram-approval.json',{'approved':True,'sha256':DIGEST,'account_id':'17841416969437440','authorization':'Explicit user approval; manual workflow dispatch bound to reviewed video hash'})
 key=base64.b64decode(os.environ['INSTAGRAM_STATE_KEY'],validate=True)
 if len(key)!=32:raise RuntimeError('Invalid state key.')
 return api,key

def main():
 remote=cloud_inputs()
 c=Config.load();video=Video(SOURCE/'out/AI-Friend-or-Devil-REVIEW.mp4',0,'AI Friend or Devil',DIGEST)
 approval=json.loads((SOURCE/'instagram-approval.json').read_text())
 if not approval.get('approved') or approval.get('sha256')!=DIGEST or approval.get('account_id')!=c.account_id:raise RuntimeError('Explicit Instagram approval is missing.')
 if sha(video.path)!=DIGEST:raise RuntimeError('Approved version 1 changed.')
 validate_video(video);m=MetaClient(c)
 def diagnostic(response,*args,**kwargs):
  if urlsplit(response.url).hostname!='rupload.facebook.com' or response.status_code<400:return response
  try:
   import re
   body=response.json();debug=body.get('debug_info',{});message=debug.get('message','') if isinstance(debug,dict) else ''
   try:message=json.loads(message).get('error',{}).get('message',message)
   except (ValueError,AttributeError,TypeError):pass
   message=str(message)
   for secret in (c.token,c.app_secret,c.app_access_token,c.openai_key):
    if secret:message=message.replace(secret,'[REDACTED]')
   message=re.sub(r'https?://[^\s]+','[URL REDACTED]',message)[:600]
   secure_json(SOURCE/'instagram-upload-diagnostics.json',{'http_status':response.status_code,'debug_type':debug.get('type') if isinstance(debug,dict) else None,'message':message})
  except Exception:pass
  return response
 m.session.hooks['response'].append(diagnostic);a=m.check_auth()
 if not a.get('valid') or not a.get('expiration_verified'):raise RuntimeError('Meta authentication is not verified.')
 if remote:api,key=remote
 else:api,key=GitHub(deployment()['repository'],auth()),read_key()
 # Cloud executions share the existing workflow concurrency group. Local
 # operator runs additionally check for an active cloud writer.
 if not remote:
  active=api.request('GET',api.path('/actions/runs?status=in_progress&per_page=20')).get('workflow_runs',[])
  if active:raise RuntimeError('Wait for active cloud workflows before this operator publication.')
 with (ROOT/'data/special-publisher.lock').open('a') as lock,tempfile.TemporaryDirectory(dir=ROOT/'data',prefix='special-') as tmp:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);path=Path(tmp)/'state.sqlite3';store=StateStore(api,key);store.load(path);db=SpecialLedger(path);db.on_commit=lambda:store.persist(db)
  if db.pending():raise RuntimeError('Daily publication needs reconciliation before an extra post.')
  previous=next((r for r in db.history() if r.get('sha256')==DIGEST),None)
  if previous:
   confirmed=m.media(previous['media_id']);print(json.dumps(confirmed,indent=2));return
  job=db.for_hash(DIGEST);p=Publisher(c,db,m)
  if job:job=db.bind_source(job,video)
  if not job:
   if m.find_caption(CAPTION):raise RuntimeError('Matching caption already exists; reconcile before publishing.')
   job=db.reserve_special(c.now().date().isoformat(),video,CAPTION)
  if job['status'] in ('PUBLISHING','PUBLISH_UNKNOWN','VERIFY_PENDING'):job=p.reconcile(job)
  elif job['status']!='PUBLISHED':
   m.check_limit();job=p.prepare_private(job)
   if job['status']=='READY':
    if m.find_caption(job['caption']):raise RuntimeError('Matching publication appeared; reconcile, do not publish again.')
    m.check_limit();job=db.update(job['id'],'PUBLISHING',publish_attempt_at=stamp())
    try:ident=m.publish(job['container_id'])
    except Exception:
     db.update(job['id'],'PUBLISH_UNKNOWN',error='Public response uncertain; read-only reconciliation required.');raise
    job=db.update(job['id'],'VERIFY_PENDING',media_id=ident);job=p.confirm_media(job)
  if job['status']!='PUBLISHED':raise RuntimeError('Special Reel has not been confirmed.')
  confirmed=m.media(job['media_id']);secure_json(SOURCE/'instagram-publication.json',{'sha256':DIGEST,'media':confirmed,'publication_type':'explicitly approved extra; daily queue unchanged','confirmed_at':c.now().isoformat()});db.conn.close();print(json.dumps(confirmed,indent=2))
if __name__=='__main__':
 try:main()
 except Exception as e:
  print(json.dumps({'status':'STOPPED','error':str(e) if type(e).__module__ in ('meta_client','github_store','builtins') and isinstance(e,RuntimeError) else type(e).__name__}));raise SystemExit(1)
