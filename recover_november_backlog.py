"""One-time, explicitly approved recovery of November 6–8 missed Reels."""
import base64,json,os
from pathlib import Path
from config import Config,prepare_storage
from database import stamp
from github_store import GitHub,StateStore,CloudStateError
from cloud_runner import download_video,validated_source_url
from publish_ai_special import SpecialLedger
from publisher import Publisher
from meta_client import MetaClient
from video_source import Video,validate_video
from caption_generator import CaptionGenerator
PLAN=(
 ('2026-11-06',6,'bc15870e5a2a9e696bfefda81561eadb189aa4e42aed4e3a8800f231c37d3cb6'),
 ('2026-11-07',7,'104ef9b0f5efd9585d53487560b5b3429f061fbea81d22af3ad7f089fbb41e5f'),
 ('2026-11-08',8,'4cccd6582168f98ad85ad75aa72ac1f8aebd89f7d284ace498dea3a9bbb54695'))
SOURCE='Explicit missed-slot catch-up approved 2026-11-09'
def main():
 if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('GITHUB_EVENT_NAME')!='workflow_dispatch' or os.environ.get('INSTAGRAM_CLOUD_ENABLED')!='true':raise CloudStateError('Enabled manual cloud dispatch required.')
 repo=os.environ['GITHUB_REPOSITORY']
 if repo!='kamalkrgupta/techat60-instagram-publisher':raise CloudStateError('Wrong repository.')
 root=Path('.backlog-runtime').resolve();videos=root/'videos';videos.mkdir(parents=True,exist_ok=True)
 os.environ.update(VIDEO_FOLDER=str(videos),PUBLISH_BACKEND='github-actions',CAPTION_MODE='fallback',OPENAI_API_KEY='')
 c=Config.load(root);prepare_storage(c)
 if c.now().date().isoformat()!='2026-11-09':raise CloudStateError('This one-time recovery is authorized only for November 9.')
 store=StateStore(GitHub(repo,os.environ['GITHUB_TOKEN']),base64.b64decode(os.environ['INSTAGRAM_STATE_KEY'],validate=True))
 path=root/'data/recovery.sqlite3';store.load(path);db=SpecialLedger(path);db.publication_source=SOURCE;db.on_commit=lambda:store.persist(db)
 if db.setting('seed_match')!='same':raise CloudStateError('Verified original history is required.')
 if db.pending():raise CloudStateError('Daily jobs require reconciliation before recovery.')
 targets={p[2] for p in PLAN}
 unfinished=db.conn.execute("SELECT sha256 FROM special_jobs WHERE status NOT IN ('PUBLISHED','FAILED','EXPIRED')").fetchall()
 if any(row[0] not in targets for row in unfinished):raise CloudStateError('Unrelated extra publication needs reconciliation.')
 entries=json.loads(Path('cloud/videos.json').read_text())['videos'];meta=MetaClient(c)
 auth=meta.check_auth()
 if not auth.get('valid') or not auth.get('expiration_verified'):raise CloudStateError('Authentication not verified.')
 for missed,number,digest in PLAN:
  previous=next((v for v in db.history() if v.get('sha256')==digest),None)
  if previous:
   media=meta.media(previous['media_id']);print(json.dumps({'status':'ALREADY_CONFIRMED','lesson':number,'permalink':media['permalink']}),flush=True);continue
  if db.used_day(missed):raise CloudStateError('Missed date already has a regular publication.')
  entry=next(v for v in entries if v['number']==number and v['sha256']==digest)
  source_url=validated_source_url(repo,entry,digest,[])
  video=Video(download_video(entry,videos,repo),number,entry['topic'],digest);validate_video(video)
  publisher=Publisher(c,db,meta,source_url=source_url);job=db.for_hash(digest)
  if job:job=db.bind_source(job,video)
  else:
   caption,_=CaptionGenerator(c).generate(video,[v['caption'] for v in db.history() if v.get('caption')],allow_api=False)
   if meta.find_caption(caption):raise CloudStateError('Matching caption exists; manual reconciliation required.')
   job=db.reserve_special(c.now().date().isoformat(),video,caption)
  if job['status'] in ('PUBLISHING','PUBLISH_UNKNOWN','VERIFY_PENDING'):job=publisher.reconcile(job)
  elif job['status']!='PUBLISHED':
   meta.check_limit();job=publisher.prepare_private(job)
   if job['status']=='READY':
    if meta.find_caption(job['caption']):raise CloudStateError('Matching publication appeared; reconciliation required.')
    if c.now().date().isoformat()!='2026-11-09':raise CloudStateError('Recovery date expired.')
    meta.check_limit();job=db.update(job['id'],'PUBLISHING',publish_attempt_at=stamp())
    try:ident=meta.publish(job['container_id'])
    except Exception:
     db.update(job['id'],'PUBLISH_UNKNOWN',error='Public response uncertain; reconcile without another publish request.');raise
    job=db.update(job['id'],'VERIFY_PENDING',media_id=ident);job=publisher.confirm_media(job)
  if job['status']!='PUBLISHED':raise CloudStateError('Publication not confirmed; stopping batch.')
  print(json.dumps({'status':'PUBLISHED','lesson':number,'missed_date':missed,'permalink':job['permalink'],'published_at':job['published_at']}),flush=True)
 # Bind today's normal slot after the three confirmed catch-up publications.
 entry=next(v for v in entries if v['number']==9)
 video=Video(videos/entry['filename'],9,entry['topic'],entry['sha256'])
 caption,_=CaptionGenerator(c).generate(video,allow_api=False)
 target=c.now().replace(hour=19,minute=0,second=0,microsecond=0)
 db.set('scheduled_reel:2026-11-09',json.dumps({'account_id':c.account_id,'sha256':entry['sha256'],'publishAt':target.isoformat(),'caption':caption}))
 print(json.dumps({'status':'DAILY_SLOT_PLANNED','lesson':9,'publishAt':target.isoformat(),'sha256':entry['sha256']}),flush=True)
 db.conn.close()
if __name__=='__main__':
 try:main()
 except Exception as e:
  print(json.dumps({'status':'STOPPED','error':str(e) if isinstance(e,RuntimeError) else type(e).__name__}),flush=True);raise SystemExit(1)
