import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import Mock
from cloud_watchdog import dispatch_if_due
class WatchdogTests(unittest.TestCase):
 def setUp(self):
  self.c=Mock(publish_backend='github-actions',grace_minutes=180)
  self.c.now.return_value=datetime(2026,11,4,19,5,tzinfo=ZoneInfo('Asia/Kolkata'));self.c.schedule_active.return_value=True
  self.db=Mock();self.db.used_day.return_value=False
  self.api=Mock();self.api.path.side_effect=lambda s:s;self.api.request.return_value={'workflow_runs':[]}
 def execute(self,candidate=True):return dispatch_if_due(self.api,self.c,self.db,candidate)['status']
 def test_dispatch_is_cloud_only_and_obeys_window(self):
  self.assertEqual(self.execute(),'WATCHDOG_DISPATCHED')
  self.api.request.assert_called_with('POST','/actions/workflows/instagram-daily.yml/dispatches',{'ref':'main','inputs':{'mode':'publish'}})
 def test_outside_window_does_not_dispatch(self):
  for hour,minute in [(18,44),(22,1),(8,0)]:
   self.c.now.return_value=self.c.now.return_value.replace(hour=hour,minute=minute);self.assertEqual(self.execute(),'WATCHDOG_OUTSIDE_WINDOW')
  self.api.request.assert_not_called()
 def test_published_day_and_empty_queue_do_not_dispatch(self):
  self.db.used_day.return_value=True;self.assertEqual(self.execute(),'WATCHDOG_ALREADY_PUBLISHED')
  self.db.used_day.return_value=False;self.assertEqual(self.execute(False),'WATCHDOG_QUEUE_EMPTY');self.api.request.assert_not_called()
 def test_active_cloud_run_does_not_queue_another(self):
  self.api.request.return_value={'workflow_runs':[{'status':'queued'}]}
  self.assertEqual(self.execute(),'WATCHDOG_RUN_ACTIVE');self.assertEqual(self.api.request.call_count,1)
 def test_recent_cloud_run_throttles_dispatch(self):
  self.api.request.return_value={'workflow_runs':[{'status':'completed','created_at':'2026-11-04T13:31:00Z'}]}
  self.assertEqual(self.execute(),'WATCHDOG_RECENT_RUN');self.assertEqual(self.api.request.call_count,1)
 def test_local_publishing_backend_cannot_dispatch(self):
  self.c.publish_backend='mac';self.assertEqual(self.execute(),'WATCHDOG_INACTIVE');self.api.request.assert_not_called()
if __name__=='__main__':unittest.main()
