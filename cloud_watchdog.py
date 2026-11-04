"""Mac-online backup trigger. Only GitHub Actions may write to Meta."""
from datetime import datetime, timedelta

def dispatch_if_due(api, config, db, has_candidate):
 if config.publish_backend!='github-actions' or not config.schedule_active():return {'status':'WATCHDOG_INACTIVE'}
 now=config.now();target=now.replace(hour=19,minute=0,second=0,microsecond=0)
 if not target-timedelta(minutes=15)<=now<=target+timedelta(minutes=config.grace_minutes):return {'status':'WATCHDOG_OUTSIDE_WINDOW'}
 if db.used_day(now.date().isoformat()):return {'status':'WATCHDOG_ALREADY_PUBLISHED'}
 if not has_candidate:return {'status':'WATCHDOG_QUEUE_EMPTY'}
 runs=api.request('GET',api.path('/actions/workflows/instagram-daily.yml/runs?per_page=20')).get('workflow_runs',[])
 if any(r.get('status')!='completed' for r in runs):return {'status':'WATCHDOG_RUN_ACTIVE'}
 for run in runs:
  created=run.get('created_at')
  if created and datetime.fromisoformat(created.replace('Z','+00:00'))>=now-timedelta(minutes=10):return {'status':'WATCHDOG_RECENT_RUN'}
 api.request('POST',api.path('/actions/workflows/instagram-daily.yml/dispatches'),{'ref':'main','inputs':{'mode':'publish'}})
 return {'status':'WATCHDOG_DISPATCHED','local_slot':target.isoformat()}
