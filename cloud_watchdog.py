"""Mac-online backup trigger. Only GitHub Actions may write to Meta."""
from datetime import datetime, timedelta

def dispatch_if_due(api, config, db, has_candidate):
 if config.publish_backend!='github-actions' or not config.schedule_active():return {'status':'WATCHDOG_INACTIVE'}
 now=config.now();target=now.replace(hour=19,minute=0,second=0,microsecond=0)
 if not target-timedelta(hours=5.5)<=now<=target+timedelta(minutes=config.grace_minutes):return {'status':'WATCHDOG_OUTSIDE_WINDOW'}
 if db.used_day(now.date().isoformat()):return {'status':'WATCHDOG_ALREADY_PUBLISHED'}
 if not has_candidate:return {'status':'WATCHDOG_QUEUE_EMPTY'}
 runs=api.request('GET',api.path('/actions/workflows/instagram-daily.yml/runs?per_page=20')).get('workflow_runs',[])
 for run in runs:
  if run.get('status')=='completed':continue
  created=run.get('created_at')
  # Old schedule events can remain queued for days. A genuinely running job,
  # a recent queued job, or an unknown timestamp still blocks another dispatch.
  stale_queued=run.get('status')=='queued' and created and datetime.fromisoformat(created.replace('Z','+00:00'))<now-timedelta(minutes=30)
  if not stale_queued:return {'status':'WATCHDOG_RUN_ACTIVE'}
 for run in runs:
  created=run.get('created_at')
  if created and datetime.fromisoformat(created.replace('Z','+00:00'))>=now-timedelta(minutes=10):return {'status':'WATCHDOG_RECENT_RUN'}
 api.request('POST',api.path('/actions/workflows/instagram-daily.yml/dispatches'),{'ref':'main','inputs':{'mode':'publish'}})
 return {'status':'WATCHDOG_DISPATCHED','local_slot':target.isoformat()}
