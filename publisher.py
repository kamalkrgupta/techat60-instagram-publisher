#!/usr/bin/env python3
"""Instagram-only CLI, daily publishing state machine and recovery."""
import argparse,fcntl,json,logging,os,sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime,timedelta
from config import Config,ConfigError,prepare_storage,ROOT
from database import Database,stamp,SEED_ID
from video_source import scan,select,validate_video,prepare_upload,Video,sha
from meta_client import MetaClient,MetaError
from caption_generator import CaptionGenerator
from reel_queue import sync_queue
class Publisher:
 def __init__(self,c,db,meta=None,captions=None):self.c=c;self.db=db;self.meta=meta or MetaClient(c);self.captions=captions or CaptionGenerator(c)
 def reconcile(self,job):
  """READ only at Meta. Never resend media_publish after an uncertain response."""
  if job.get('media_id'):
   media=self.meta.media(job['media_id']);return self.db.confirm(job,media,self.c.zone)
  if not job.get('container_id'):return job
  state=self.meta.container(job['container_id']).get('status_code')
  if state=='PUBLISHED' or job['status'] in ('PUBLISHING','PUBLISH_UNKNOWN'):
   found=self.meta.find_caption(job['caption'])
   earliest=datetime.fromisoformat(job['created_at'])-timedelta(minutes=5)
   found=[r for r in found if r.get('timestamp') and datetime.fromisoformat(r['timestamp'].replace('Z','+00:00'))>=earliest]
   if len(found)==1:
    job=self.db.update(job['id'],'VERIFY_PENDING',media_id=str(found[0]['id']));return self.db.confirm(job,self.meta.media(job['media_id']),self.c.zone)
   self.db.update(job['id'],'PUBLISH_UNKNOWN',error='Publication uncertain; no unique Meta media match. No publish retry will occur.')
   raise RuntimeError('Uncertain publication requires reconciliation; automatic republish is blocked.')
  if state in ('ERROR','EXPIRED'):
   return self.db.update(job['id'],'FAILED' if state=='ERROR' else 'EXPIRED',error='Meta confirmed container '+state)
  if state=='FINISHED':return self.db.update(job['id'],'READY',error=None)
  return job
 def confirm_media(self,job):return self.db.confirm(job,self.meta.media(job['media_id']),self.c.zone)
 def resume(self,job):
  today=self.c.now().date().isoformat()
  if job['status'] in ('PUBLISHING','PUBLISH_UNKNOWN','VERIFY_PENDING'):
   return self.reconcile(job)
  if job['local_day']!=today:
   # Never turn an unfinished previous-day container into an extra catch-up post.
   self.reconcile(job)
   fresh=self.db.job(job['id'])
   if fresh['status']=='PUBLISHED':return fresh
   if not fresh.get('publish_attempt_at'):return self.db.update(job['id'],'EXPIRED',error='Unpublished previous-day attempt retained; no catch-up post.')
   raise RuntimeError('Prior-day publication requires manual reconciliation.')
  if not self.c.due():return {'status':'OUTSIDE_PUBLISH_WINDOW'}
  if self.db.used_day(today):return {'status':'ALREADY_PUBLISHED_TODAY'}
  job=self.prepare_private(job)
  if job['status']!='READY':raise RuntimeError('Unexpected pending state; operator review required.')
  # Recheck the local day and remote account immediately before public mutation.
  if self.c.now().date().isoformat()!=job['local_day'] or not self.c.due():return job
  if self.db.used_day(today) or self.meta.today_reels(today):return {'status':'ALREADY_PUBLISHED_TODAY'}
  self.meta.check_limit()
  if self.meta.container(job['container_id']).get('status_code')!='FINISHED':return self.reconcile(job)
  job=self.db.update(job['id'],'PUBLISHING',publish_attempt_at=stamp())
  try:media_id=self.meta.publish(job['container_id'])
  except Exception:
   self.db.update(job['id'],'PUBLISH_UNKNOWN',error='media_publish response uncertain. Never automatically send publish again.');raise
  job=self.db.update(job['id'],'VERIFY_PENDING',media_id=media_id)
  return self.confirm_media(job)
 def prepare_private(self,job):
  """Create/upload/process a private container only; never calls media_publish."""
  if job['status'] in ('CREATING','CREATE_UNKNOWN','FAILED','EXPIRED'):
   if job['attempts']>=3:raise RuntimeError('Three private-container attempts used today; operator review required.')
   job=self.db.update(job['id'],'CREATING',attempts=job['attempts']+1,error=None)
   try:container,uri=self.meta.create(job['caption'])
   except Exception:
    self.db.update(job['id'],'CREATE_UNKNOWN',error='Container creation outcome unknown. No publication was requested.');raise
   job=self.db.update(job['id'],'CREATED',container_id=container,upload_uri=uri)
  video=Video(Path(job['filename']),0,'',job['sha256'])
  if job['status']=='CREATED':
   # File identity is rechecked before every upload; conversion is project-local.
   upload_file=prepare_upload(video,self.c.root)
   job=self.db.update(job['id'],'UPLOADING')
   try:self.meta.upload(job['upload_uri'],upload_file)
   except Exception:
    self.db.update(job['id'],'UPLOAD_UNKNOWN',error='Binary transfer outcome uncertain. Poll existing container; do not guess offsets.');raise
   job=self.db.update(job['id'],'PROCESSING')
  if job['status'] in ('UPLOADING','UPLOAD_UNKNOWN','PROCESSING'):
   try:state=self.meta.wait_finished(job['container_id'])
   except MetaError as e:
    self.db.update(job['id'],'FAILED' if e.code=='ERROR' else 'EXPIRED' if e.code=='EXPIRED' else job['status'],error=str(e));raise
   if state=='PUBLISHED':return self.reconcile(job)
   job=self.db.update(job['id'],'READY',error=None)
  return job
 def stage_next(self):
  """Cloud-only bounded preflight before 19:00, so ready media can publish on time."""
  if self.c.publish_backend!='github-actions' or os.environ.get('GITHUB_ACTIONS')!='true' or not self.c.active():return {'status':'PRESTAGE_DISABLED'}
  now=self.c.now();target=now.replace(hour=19,minute=0,second=0,microsecond=0)
  if not target-timedelta(minutes=16)<=now<target:return {'status':'OUTSIDE_PRESTAGE_WINDOW'}
  day=now.date().isoformat()
  if self.db.used_day(day):return {'status':'ALREADY_PUBLISHED_TODAY'}
  auth=self.meta.check_auth()
  if not auth.get('expiration_verified'):raise RuntimeError('Token identity/expiration must be verified before staging.')
  self.db.verify_seed(self.meta.media(SEED_ID))
  if self.meta.today_reels(day):return {'status':'ALREADY_PUBLISHED_TODAY_ON_META'}
  pending=self.db.pending()
  for existing in pending:
   if existing['status'] in ('PUBLISHING','PUBLISH_UNKNOWN','VERIFY_PENDING'):return self.reconcile(existing)
   if existing['local_day']!=day:raise RuntimeError('Prior-day private attempt must reconcile before staging a new day.')
  job=self.db.day_job(day)
  if not job:
   video=select(scan(self.c.video_folder),self.db)
   if not video:return {'status':'QUEUE_COMPLETE'}
   validate_video(video);caption,_=self.captions.generate(video,[r['caption'] for r in self.db.history() if r.get('caption')])
   if self.meta.find_caption(caption):raise RuntimeError('Exact caption already exists; manual reconciliation required.')
   job=self.db.reserve(day,video,caption)
  self.meta.check_limit()
  return self.prepare_private(job)
 def execute(self):
  if not self.c.active():raise RuntimeError('Public publishing is disabled pending explicit schedule activation approval.')
  pending=self.db.pending()
  if not self.c.due() and not any(j['status'] in ('PUBLISHING','PUBLISH_UNKNOWN','VERIFY_PENDING') for j in pending):return {'status':'OUTSIDE_PUBLISH_WINDOW'}
  self.c.validate(need_token=True);auth=self.meta.check_auth()
  for warning in auth.get('warnings',[]):logging.warning('%s',warning)
  if not auth.get('expiration_verified'):raise RuntimeError('Token expiration/app identity must be verified before public publication.')
  self.db.verify_seed(self.meta.media(SEED_ID))
  pending=self.db.pending()
  for job in pending:
   # Recovery is prioritized over selecting any new video, even on a new day.
   result=self.resume(job)
   if result.get('status') not in ('PUBLISHED','FAILED','EXPIRED'):return result
  day=self.c.now().date().isoformat()
  if self.db.used_day(day):return {'status':'ALREADY_PUBLISHED_TODAY'}
  if not self.c.due():return {'status':'OUTSIDE_PUBLISH_WINDOW'}
  if self.meta.today_reels(day):return {'status':'ALREADY_PUBLISHED_TODAY_ON_META'}
  videos=scan(self.c.video_folder);video=select(videos,self.db)
  if video is None:return {'status':'QUEUE_COMPLETE'}
  old=self.db.day_job(day)
  if old:
   if old['sha256']!=video.sha256:raise RuntimeError('Today is reserved for another video; no second candidate will be posted.')
   return self.resume(old)
  validate_video(video)
  recent=[r['caption'] for r in self.db.history() if r.get('caption')]
  caption,origin=self.captions.generate(video,recent)
  if self.meta.find_caption(caption):raise RuntimeError('An exact-caption Reel already exists; review rather than posting a duplicate.')
  job=self.db.reserve(day,video,caption);logging.info('Reserved day=%s filename=%s caption_source=%s',day,video.path.name,origin)
  return self.resume(job)

def dry_run(c,db):
 videos=scan(c.video_folder);held=None;candidate=None
 try:candidate=select(videos,db)
 except RuntimeError as e:held=str(e)
 result={'mode':'dry-run','network_calls':0,'upload':False,'publish':False,'source_count':len(videos),'seed_match':db.setting('seed_match'),'held_reason':held,'used_today':db.used_day(c.now().date().isoformat()),'active':c.active()}
 if candidate:
  validate_video(candidate);caption,origin=CaptionGenerator(c).generate(candidate,allow_api=False)
  result.update(filename=candidate.path.name,sha256=candidate.sha256,topic=candidate.topic,caption=caption,caption_source=origin)
 elif videos and held:result['held_filename']=videos[0].path.name
 return result

def check(c,db):
 videos=scan(c.video_folder);report={'source_count':len(videos),'timezone':c.timezone,'publish_time':c.publish_time,'active':c.active(),'credentials':{'meta':'configured' if c.token else 'missing','openai':'configured' if c.openai_key else 'missing (deterministic fallback available)'},'seed_match':db.setting('seed_match'),'caption_mode':c.caption_mode}
 try:
  candidate=select(videos,db);report['next_video']=candidate.path.name if candidate else None
  if candidate:report['video_preflight']=validate_video(candidate)
 except RuntimeError as e:report['selection_hold']=str(e)
 if c.token:
  try:
   client=MetaClient(c);report['meta']=client.check_auth();db.verify_seed(client.media(SEED_ID));report['existing_reel_verified']=True
  except (MetaError,ConfigError,RuntimeError) as e:report['meta']={'valid':False,'error':str(e)}
 else:report['meta']={'valid':False,'error':'META_ACCESS_TOKEN is empty.'}
 if c.openai_key and c.caption_mode!='fallback':
  try:
   from openai import OpenAI
   OpenAI(api_key=c.openai_key,timeout=20,max_retries=0).models.retrieve(c.caption_model);report['openai']={'available':True,'model':c.caption_model}
  except Exception as e:report['openai']={'available':False,'error':type(e).__name__,'fallback':True}
 else:report['openai']={'available':False,'fallback':True}
 return report

class Redactor(logging.Filter):
 def __init__(self,secrets):super().__init__();self.secrets=[s for s in secrets if s]
 def filter(self,record):
  text=record.getMessage()
  for secret in self.secrets:text=text.replace(secret,'[REDACTED]')
  record.msg=text;record.args=();return True

def main():
 parser=argparse.ArgumentParser(description='Instagram-only Techat60 Reels publisher')
 mode=parser.add_mutually_exclusive_group(required=True)
 for name in ('check','dry-run','status','publish-next','history','scheduled','caption-test','queue'):mode.add_argument('--'+name,action='store_true')
 args=parser.parse_args();c=Config.load();prepare_storage(c)
 handler=RotatingFileHandler(c.root/'logs/publisher.log',maxBytes=2_000_000,backupCount=5);handler.addFilter(Redactor([c.token,c.openai_key,c.app_secret,c.app_access_token]));handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'));logging.basicConfig(level=logging.INFO,handlers=[handler]);(c.root/'logs/publisher.log').chmod(0o600)
 db=Database(c.db_path)
 try:
  queue_summary=sync_queue(c,db) if not args.dry_run else None
  if args.queue:result=queue_summary
  elif args.check:result=check(c,db)
  elif args.dry_run:result=dry_run(c,db)
  elif args.history:result={'publications':db.history(),'attempts':[{k:v for k,v in r.items() if k!='upload_uri'} for r in db.jobs()]}
  elif args.status:result={'active':c.schedule_active(),'backend':c.publish_backend,'local_execution_enabled':c.active(),'daily_publication_approved':c.daily_approved(),'seed_match':db.setting('seed_match'),'recorded_publications':len(db.history()),'pending_jobs':[{k:v for k,v in r.items() if k not in ('caption','upload_uri')} for r in db.pending()],'next_schedule_IST':c.next_slot(used_today=db.used_day(c.now().date().isoformat())).isoformat(),'queued_videos':queue_summary['queued'],'next_filename':queue_summary['next_filename']}
  elif args.caption_test:
   video=select(scan(c.video_folder),db)
   if not video:raise RuntimeError('No eligible video.')
   caption,origin=CaptionGenerator(c).generate(video,[r['caption'] for r in db.history() if r.get('caption')]);result={'filename':video.path.name,'caption':caption,'source':origin,'uploaded':False,'published':False}
  else:
   if args.scheduled and not c.active():result={'status':'DISABLED'}
   else:
    with (c.root/'data/publisher.lock').open('a') as lock:
     try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
     except BlockingIOError:print(json.dumps({'status':'ANOTHER_INSTANCE_RUNNING'}));return 0
     result=Publisher(c,db).execute()
   if isinstance(result,dict):result={k:v for k,v in result.items() if k!='upload_uri'}
  if args.publish_next or args.scheduled:sync_queue(c,db)
  logging.info('Command completed: %s',next(k for k,v in vars(args).items() if v));print(json.dumps(result,indent=2,default=str))
  if args.check and (not result.get('meta',{}).get('valid') or result.get('selection_hold')):return 2
  return 0
 except Exception as e:
  safe=str(e) if isinstance(e,(ConfigError,MetaError,RuntimeError)) else type(e).__name__
  for secret in (c.token,c.openai_key,c.app_secret,c.app_access_token):
   if secret:safe=safe.replace(secret,'[REDACTED]')
  logging.error('%s',safe);print(json.dumps({'status':'ERROR','error':safe}));return 1
if __name__=='__main__':
 try:raise SystemExit(main())
 except ConfigError as e:print(json.dumps({'status':'CONFIG_ERROR','error':str(e)}));raise SystemExit(1)
