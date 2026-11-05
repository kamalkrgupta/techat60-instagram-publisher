"""Explicit same-day scheduling and captions in the encrypted ledger."""
import json,os,time
from datetime import date,datetime,timedelta
from github_store import CloudStateError

def planned_caption(c,db,video,captioner,recent=(),strict=True):
 value=db.setting('scheduled_reel:'+c.now().date().isoformat())
 if not value:return captioner.generate(video,recent)
 plan=json.loads(value)
 if plan.get('account_id')!=c.account_id:raise CloudStateError('Scheduled Reel account mismatch.')
 if plan.get('sha256')!=video.sha256:
  if strict:raise CloudStateError('Selected content differs from the scheduled Reel.')
  return captioner.generate(video,recent)
 at=datetime.fromisoformat(plan['publishAt']);target=c.now().replace(hour=19,minute=0,second=0,microsecond=0)
 if at!=target:raise CloudStateError('Scheduled Reel time differs from the approved daily slot.')
 return captioner.normalize(plan['caption'],video),'approved_schedule'

def schedule_target(c,db,publish_date,expected_sha256,candidate):
 if os.environ.get('GITHUB_EVENT_NAME')!='workflow_dispatch':raise CloudStateError('Same-day scheduling requires a manual dispatch.')
 now=c.now();target=now.replace(hour=19,minute=0,second=0,microsecond=0)
 if publish_date!=now.date().isoformat():raise CloudStateError('The scheduled date must be today in Asia/Kolkata.')
 if not 0<(target-now).total_seconds()<=5.5*3600:raise CloudStateError('Scheduling must start within 5.5 hours before 19:00 IST.')
 if db.used_day(publish_date):raise CloudStateError('Today already has a confirmed Reel.')
 if db.pending():raise CloudStateError('Reconcile pending jobs before starting a scheduled waiter.')
 if not candidate or candidate['sha256']!=expected_sha256:raise CloudStateError('Scheduled video differs from the expected hash.')
 plan=json.loads(db.setting('scheduled_reel:'+publish_date,'{}'))
 if plan.get('sha256')!=expected_sha256 or plan.get('account_id')!=c.account_id or datetime.fromisoformat(plan.get('publishAt','2000-01-01T00:00:00+00:00'))!=target:raise CloudStateError('The durable scheduled Reel record is missing or mismatched.')
 return target

def wait_for_preparation(c,target,sleep=time.sleep):
 while (target-c.now()).total_seconds()>15*60:
  sleep(min(45,(target-c.now()).total_seconds()-15*60))
 if c.now().date()!=target.date() or c.now()>target+timedelta(minutes=c.grace_minutes):raise CloudStateError('The requested scheduled window was missed.')
