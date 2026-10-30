import io,json,unittest
from unittest.mock import patch
from datetime import datetime,timedelta
from test_system import Base
from reel_queue import sync_queue
from publisher import main
class QueueTests(Base):
 def test_queue_refresh_is_idempotent_without_public_jobs(self):
  a=sync_queue(self.config,self.db);b=sync_queue(self.config,self.db)
  self.assertEqual(a['queued'],1);self.assertEqual(b['queued'],1);self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM reel_queue').fetchone()[0],1);self.assertEqual(self.db.jobs(),[])
 def test_new_video_is_detected_and_gets_next_daily_slot(self):
  sync_queue(self.config,self.db);(self.source/'003 - HTML Headings.mp4').write_bytes(b'next-video')
  result=sync_queue(self.config,self.db);self.assertEqual(result['queued'],2)
  rows=json.loads((self.root/'data/queue.json').read_text())['videos'];a=datetime.fromisoformat(rows[0]['planned_at']);b=datetime.fromisoformat(rows[1]['planned_at']);self.assertEqual(b-a,timedelta(days=1));self.assertEqual(a.hour,19)
 def test_published_content_does_not_reenter_queue(self):
  self.db.set('seed_match','pending');self.db.seed_match('same',self.video)
  result=sync_queue(self.config,self.db);self.assertEqual(result['queued'],0);self.assertEqual(result['published'],1)
 def test_source_files_are_not_changed_and_no_openai_request(self):
  before=self.path.read_bytes();self.config.openai_key='unused';self.config.caption_mode='fallback'
  with patch('openai.OpenAI') as client:sync_queue(self.config,self.db)
  client.assert_not_called();self.assertEqual(self.path.read_bytes(),before)
 def test_missing_file_remains_recorded_not_scheduled(self):
  sync_queue(self.config,self.db);self.path.unlink();result=sync_queue(self.config,self.db)
  self.assertEqual(result['queued'],0);row=self.db.conn.execute('SELECT status FROM reel_queue').fetchone();self.assertEqual(row['status'],'UNAVAILABLE')
 def test_publisher_refreshes_queue_outside_publish_window_without_meta(self):
  self.config.now.return_value=self.now.replace(hour=18)
  from config import secure_json
  secure_json(self.config.activation,{'approved':True,'account_id':self.config.account_id})
  with patch('publisher.Config.load',return_value=self.config),patch('sys.argv',['publisher.py','--scheduled']),patch('publisher.MetaClient') as client,patch('sys.stdout',new_callable=io.StringIO) as out:
   self.assertEqual(main(),0)
  client.return_value.check_auth.assert_not_called();client.return_value.publish.assert_not_called();self.assertIn('OUTSIDE_PUBLISH_WINDOW',out.getvalue());self.assertTrue((self.root/'data/queue.csv').exists())
if __name__=='__main__':unittest.main()
