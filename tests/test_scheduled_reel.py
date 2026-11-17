import json,os,unittest
from datetime import timedelta
from unittest.mock import Mock,patch
from test_system import Base
from github_store import CloudStateError
from scheduled_reel import planned_caption,schedule_target,wait_for_preparation
class ScheduledReelTests(Base):
 def setUp(self):
  super().setUp();self.config.now.return_value=self.now.replace(hour=14,minute=0)
  self.target=self.config.now().replace(hour=19,minute=0,second=0,microsecond=0)
  self.plan={'account_id':self.config.account_id,'sha256':self.video.sha256,'publishAt':self.target.isoformat(),'caption':'A useful lesson. Follow @tech_at_60. #HTML #LearnHTML #WebDevelopment'}
  self.db.set('scheduled_reel:'+self.target.date().isoformat(),json.dumps(self.plan))
 def target_for(self,hash=None):return schedule_target(self.config,self.db,self.target.date().isoformat(),hash or self.video.sha256,{'sha256':self.video.sha256})
 def test_same_day_schedule_does_not_bypass_publication_time(self):
  with patch.dict(os.environ,{'GITHUB_EVENT_NAME':'workflow_dispatch'}):self.assertEqual(self.target_for(),self.target)
  self.assertFalse(self.config.due())
 def test_wrong_hash_date_or_nonmanual_event_is_rejected(self):
  with patch.dict(os.environ,{'GITHUB_EVENT_NAME':'workflow_dispatch'}):
   with self.assertRaises(CloudStateError):self.target_for('wrong')
   with self.assertRaises(CloudStateError):schedule_target(self.config,self.db,'2000-01-01',self.video.sha256,{'sha256':self.video.sha256})
  with patch.dict(os.environ,{'GITHUB_EVENT_NAME':'schedule'}):
   with self.assertRaises(CloudStateError):self.target_for()
 def test_pending_job_cannot_be_replaced_by_waiter(self):
  self.ready_job()
  with patch.dict(os.environ,{'GITHUB_EVENT_NAME':'workflow_dispatch'}):
   with self.assertRaises(CloudStateError):self.target_for()
 def test_caption_is_specific_to_date_hash_and_account(self):
  captioner=Mock();captioner.normalize.side_effect=lambda text,video:text
  caption,source=planned_caption(self.config,self.db,self.video,captioner)
  self.assertEqual(caption,self.plan['caption']);self.assertEqual(source,'approved_schedule');captioner.generate.assert_not_called()
  self.plan['sha256']='other';self.db.set('scheduled_reel:'+self.target.date().isoformat(),json.dumps(self.plan))
  with self.assertRaises(CloudStateError):planned_caption(self.config,self.db,self.video,captioner)
 def test_after_today_plan_does_not_override_tomorrow(self):
  self.config.now.return_value+=timedelta(days=1);captioner=Mock();captioner.generate.return_value=('normal','fallback')
  self.assertEqual(planned_caption(self.config,self.db,self.video,captioner),('normal','fallback'))
 def test_waiter_stops_at_preparation_window_without_network(self):
  self.config.now.return_value=self.target-timedelta(minutes=16)
  def advance(seconds):self.config.now.return_value+=timedelta(seconds=seconds)
  sleep=Mock(side_effect=advance);wait_for_preparation(self.config,self.target,sleep)
  self.assertEqual(self.config.now(),self.target-timedelta(minutes=15));self.assertEqual(sum(c.args[0] for c in sleep.call_args_list),60)
 def test_too_early_or_after_slot_is_rejected(self):
  with patch.dict(os.environ,{'GITHUB_EVENT_NAME':'workflow_dispatch'}):
   for hour in [12,20]:
    self.config.now.return_value=self.config.now().replace(hour=hour)
    with self.assertRaises(CloudStateError):self.target_for()
if __name__=='__main__':unittest.main()

class DailyWaiterTests(Base):
 def setUp(self):
  super().setUp();self.config.now.return_value=self.now.replace(hour=13,minute=31)
  self.target=self.config.now().replace(hour=19,minute=0,second=0,microsecond=0)
  self.preflight=Mock(return_value={'meta':{'valid':True,'expiration_verified':True}})
 def execute(self,candidate=True):
  from scheduled_reel import wait_for_daily_slot
  self.sleep=Mock(side_effect=lambda seconds:setattr(self.config.now,'return_value',self.config.now()+timedelta(seconds=seconds)))
  return wait_for_daily_slot(self.config,self.db,{'sha256':self.video.sha256} if candidate else None,self.preflight,sleep=self.sleep)
 def test_afternoon_worker_waits_until_private_preparation_not_publication(self):
  self.assertTrue(self.execute());self.assertEqual(self.config.now(),self.target-timedelta(minutes=15));self.assertFalse(self.config.due())
  plan=json.loads(self.db.setting('scheduled_waiter'));self.assertEqual(plan['status'],'PREPARING');self.assertEqual(plan['sha256'],self.video.sha256);self.preflight.assert_called_once()
 def test_morning_evening_and_overnight_do_not_start_long_wait(self):
  for hour,minute in [(5,39),(13,29),(19,0),(21,0),(23,0)]:
   self.config.now.return_value=self.now.replace(hour=hour,minute=minute);self.assertFalse(self.execute());self.sleep.assert_not_called()
  self.preflight.assert_not_called();self.assertIsNone(self.db.setting('scheduled_waiter'))
 def test_empty_queue_and_already_published_day_do_not_wait(self):
  self.assertFalse(self.execute(False));self.preflight.assert_not_called()
  with patch.object(self.db,'used_day',return_value=True):self.assertFalse(self.execute())
  self.preflight.assert_not_called()
 def test_failed_authentication_or_selection_hold_stops_before_wait(self):
  for result in ({'meta':{'valid':False,'expiration_verified':True}},{'meta':{'valid':True,'expiration_verified':False}},{'meta':{'valid':True,'expiration_verified':True},'selection_hold':'pending'}):
   self.preflight.return_value=result
   with self.assertRaises(CloudStateError):self.execute()
   self.sleep.assert_not_called()
  self.assertIsNone(self.db.setting('scheduled_waiter'))
 def test_clock_crossing_the_allowed_window_is_not_published(self):
  from scheduled_reel import wait_for_daily_slot
  def jump(_):self.config.now.return_value=self.target+timedelta(days=1)
  with self.assertRaises(CloudStateError):wait_for_daily_slot(self.config,self.db,{'sha256':self.video.sha256},self.preflight,sleep=jump)
