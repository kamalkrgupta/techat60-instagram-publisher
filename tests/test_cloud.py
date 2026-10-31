import base64,hashlib,json,os,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from test_system import Base
from github_store import StateStore,CloudStateError,encode_state,decode_state,snapshot
from cloud_runner import download_video,authorize_catchup
from publisher import Publisher
class MemoryGitHub:
 repo='owner/reels'
 def __init__(self,data):self.data=data;self.sha='initial';self.puts=0
 def path(self,s):return '/repos/'+self.repo+s
 def request(self,*args,**kwargs):return {'object':{'sha':'head'}}
 def contents(self,*args):return {'sha':self.sha,'content':base64.b64encode(self.data).decode(),'encoding':'base64'}
 def put_contents(self,path,content,branch,sha,message):
  if sha!=self.sha:raise CloudStateError('Concurrent modification')
  self.data=content;self.sha=hashlib.sha1(content).hexdigest();self.puts+=1;return {'content':{'sha':self.sha}}
class CloudTests(Base):
 def setUp(self):
  super().setUp();self.key=os.urandom(32);self.aad=b'owner/reels:instagram-state:state.enc';self.api=MemoryGitHub(encode_state(snapshot(self.db.conn),self.key,self.aad))
 def test_encrypted_state_hides_private_ledger_and_authenticates(self):
  plain=snapshot(self.db.conn);encrypted=encode_state(plain,self.key,self.aad);self.assertNotIn(b'SQLite format',encrypted);self.assertEqual(decode_state(encrypted,self.key,self.aad),plain)
  with self.assertRaises(CloudStateError):decode_state(encrypted,os.urandom(32),self.aad)
 def test_tampered_or_wrong_repository_state_is_rejected(self):
  value=self.api.data
  with self.assertRaises(CloudStateError):decode_state(value[:-1]+bytes([value[-1]^1]),self.key,self.aad)
  with self.assertRaises(CloudStateError):decode_state(value,self.key,b'other/repo:instagram-state:state.enc')
 def test_wal_commits_survive_snapshot_and_restore(self):
  j=self.ready_job();self.api.data=encode_state(snapshot(self.db.conn),self.key,self.aad);store=StateStore(self.api,self.key);path=self.root/'restored.sqlite3';store.load(path);con=sqlite3.connect(path)
  self.assertEqual(con.execute('SELECT status FROM jobs WHERE id=?',(j['id'],)).fetchone()[0],'READY');con.close()
 def test_critical_phase_commits_are_remote_before_publish(self):
  j=self.ready_job();store=StateStore(self.api,self.key);store.sha=self.api.sha;self.db.on_commit=lambda:store.persist(self.db);m=self.fake_meta()
  def publish(_):
   raw=decode_state(self.api.data,self.key,self.aad);p=self.root/'remote-view.sqlite3';p.write_bytes(raw);con=sqlite3.connect(p)
   self.assertEqual(con.execute('SELECT status FROM jobs WHERE id=?',(j['id'],)).fetchone()[0],'PUBLISHING');con.close();return 'published-id'
  m.publish.side_effect=publish;result=Publisher(self.config,self.db,m).resume(j);self.assertEqual(result['status'],'PUBLISHED');self.assertGreaterEqual(self.api.puts,3)
 def test_failed_checkpoint_stops_before_meta_publish(self):
  j=self.ready_job();self.db.on_commit=Mock(side_effect=CloudStateError('Storage unavailable'));m=self.fake_meta()
  with self.assertRaises(CloudStateError):Publisher(self.config,self.db,m).resume(j)
  m.publish.assert_not_called()
 def test_stale_state_revision_cannot_overwrite_newer_history(self):
  store=StateStore(self.api,self.key);store.sha='stale';before=self.api.data
  with self.assertRaises(CloudStateError):store.persist(self.db)
  self.assertEqual(self.api.data,before)
 def test_missing_remote_state_is_not_silently_recreated(self):
  self.api.request=lambda *a,**kw:None
  with self.assertRaises(CloudStateError):StateStore(self.api,self.key).load(self.root/'none.sqlite3')
  self.assertFalse((self.root/'none.sqlite3').exists())
 def test_cloud_backend_disables_local_publication_even_with_approval_marker(self):
  from config import secure_json
  secure_json(self.config.activation,{'approved':True,'account_id':self.config.account_id});self.config.publish_backend='github-actions'
  with patch.dict(os.environ,{'GITHUB_ACTIONS':'false'}):self.assertFalse(self.config.active())
 def test_download_rejects_foreign_asset_host_before_network(self):
  entry={'filename':'002.mp4','sha256':'abc','download_url':'https://evil.example/video.mp4'};session=Mock()
  with self.assertRaises(CloudStateError):download_video(entry,self.source,'owner/reels',session)
  session.get.assert_not_called()
 def test_cloud_prestage_prepares_container_without_publication(self):
  from config import secure_json
  self.config.publish_backend='github-actions';self.config.now.return_value=self.now.replace(hour=18,minute=45);secure_json(self.config.activation,{'approved':True,'account_id':self.config.account_id})
  m=self.fake_meta();m.create.return_value=('staged','https://rupload.facebook.com/ig-api-upload/v26.0/staged');m.wait_finished.return_value='FINISHED'
  with patch.dict(os.environ,{'GITHUB_ACTIONS':'true'}),patch('publisher.validate_video',return_value={}),patch('publisher.prepare_upload',return_value=self.path):result=Publisher(self.config,self.db,m).stage_next()
  self.assertEqual(result['status'],'READY');m.upload.assert_called_once();m.publish.assert_not_called();self.assertEqual(len(self.db.jobs()),1)
 def test_prestage_does_not_run_outside_early_window(self):
  from config import secure_json
  self.config.publish_backend='github-actions';self.config.now.return_value=self.now.replace(hour=17);secure_json(self.config.activation,{'approved':True,'account_id':self.config.account_id});m=self.fake_meta()
  with patch.dict(os.environ,{'GITHUB_ACTIONS':'true'}):result=Publisher(self.config,self.db,m).stage_next()
  self.assertEqual(result['status'],'OUTSIDE_PRESTAGE_WINDOW');m.create.assert_not_called();m.publish.assert_not_called()
 def test_local_publisher_cannot_use_cloud_prestaging(self):
  m=self.fake_meta();result=Publisher(self.config,self.db,m).stage_next();self.assertEqual(result['status'],'PRESTAGE_DISABLED');m.create.assert_not_called()
 def test_explicit_catchup_uses_real_today_and_retains_daily_limit(self):
  self.config.now.return_value=self.now.replace(day=31,hour=10)
  with patch.dict(os.environ,{'GITHUB_EVENT_NAME':'workflow_dispatch'}):authorize_catchup(self.config,self.db,'2026-10-30',self.video.sha256,{'sha256':self.video.sha256})
  self.assertTrue(self.config.due());self.assertEqual(self.config.now().day,31)
  self.assertIn('2026-10-30',self.db.setting('last_catchup_authorization'))
 def test_scheduled_runs_cannot_authorize_catchup(self):
  with patch.dict(os.environ,{'GITHUB_EVENT_NAME':'schedule'}):
   with self.assertRaises(CloudStateError):authorize_catchup(self.config,self.db,'2026-10-29',self.video.sha256,{'sha256':self.video.sha256})
 def test_catchup_rejects_wrong_hash_published_date_and_pending_jobs(self):
  self.config.now.return_value=self.now.replace(day=31,hour=10)
  with patch.dict(os.environ,{'GITHUB_EVENT_NAME':'workflow_dispatch'}):
   with self.assertRaises(CloudStateError):authorize_catchup(self.config,self.db,'2026-10-30','wrong',{'sha256':self.video.sha256})
   with self.assertRaises(CloudStateError):authorize_catchup(self.config,self.db,'2026-10-29',self.video.sha256,{'sha256':self.video.sha256})
   self.ready_job()
   with self.assertRaises(CloudStateError):authorize_catchup(self.config,self.db,'2026-10-30',self.video.sha256,{'sha256':self.video.sha256})
 def test_public_export_never_includes_credentials_plain_database_or_key(self):
  from github_deploy import PUBLIC_FILES
  self.assertNotIn('.env',PUBLIC_FILES);self.assertFalse(any('sqlite3' in f or 'state-key' in f for f in PUBLIC_FILES));self.assertIn('.github/workflows/instagram-daily.yml',PUBLIC_FILES)
if __name__=='__main__':unittest.main()
