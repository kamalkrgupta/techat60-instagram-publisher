import io,json,logging,sqlite3,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta
from unittest.mock import Mock,patch
import requests
from config import Config,ConfigError,secure_json
from database import Database,SEED_ID,SEED_URL
from video_source import Video,scan,select,sha
from caption_generator import CaptionGenerator
from meta_client import MetaClient,MetaError
from publisher import Publisher,dry_run,Redactor
from scheduler import definition,LABEL

class Base(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve();self.source=self.root/'source';self.source.mkdir();self.config=Config(root=self.root,video_folder=self.source,token='SECRET_TOKEN',openai_key='')
  self.now=datetime(2026,10,30,19,0,tzinfo=self.config.zone);self.config.now=Mock(return_value=self.now)
  self.db=Database(self.root/'data/test.db');self.db.set('seed_match','different')
  self.path=self.source/'002 - HTML Tags - abcdefghijk.mp4';self.path.write_bytes(b'fake-mp4-test');self.video=Video(self.path,2,'HTML Tags',sha(self.path))
 def tearDown(self):self.db.conn.close();self.temp.cleanup()
 def ready_job(self):
  j=self.db.reserve(self.now.date().isoformat(),self.video,'A unique HTML caption #HTML #Coding #LearnToCode\nLesson 002 · TECH AT 60')
  return self.db.update(j['id'],'READY',container_id='container',upload_uri='https://rupload.facebook.com/ig-api-upload/v26.0/container')
 def fake_meta(self):
  m=Mock();m.check_auth.return_value={'valid':True,'expiration_verified':True};m.today_reels.return_value=[];m.container.return_value={'status_code':'FINISHED'};m.find_caption.return_value=[];m.publish.return_value='published-id';m.media.side_effect=lambda ident: {'id':SEED_ID,'permalink':SEED_URL,'timestamp':'2026-10-29T12:00:00+0000'} if ident==SEED_ID else {'id':ident,'permalink':'https://www.instagram.com/reel/REAL/','timestamp':'2026-10-30T13:30:00+0000'}
  return m
class DatabaseTests(Base):
 def test_seed_is_recorded_but_hash_match_is_not_assumed(self):
  fresh=Database(self.root/'data/fresh.db');self.assertEqual(fresh.setting('seed_match'),'pending');self.assertEqual(fresh.history()[0]['media_id'],SEED_ID);self.assertIsNone(fresh.history()[0]['sha256']);self.assertTrue(fresh.used_day('2026-10-29'));fresh.conn.close()
 def test_one_candidate_per_calendar_day(self):
  self.db.reserve('2026-10-30',self.video,'caption')
  other=Video(self.path,3,'other','otherhash')
  with self.assertRaises(sqlite3.IntegrityError):self.db.reserve('2026-10-30',other,'second')
 def test_hash_cannot_have_two_pending_attempts(self):
  self.db.reserve('2026-10-30',self.video,'caption')
  with self.assertRaises(sqlite3.IntegrityError):self.db.reserve('2026-10-31',self.video,'second')
 def test_confirm_is_idempotent(self):
  j=self.ready_job();j=self.db.update(j['id'],'VERIFY_PENDING',media_id='m')
  media={'id':'m','permalink':'https://www.instagram.com/reel/M/','timestamp':'2026-10-30T13:30:00+0000'}
  self.db.confirm(j,media,self.config.zone);self.db.confirm(j,media,self.config.zone);self.assertEqual(len(self.db.history()),2);self.assertEqual(self.db.job(j['id'])['status'],'PUBLISHED')
 def test_unconfirmed_media_not_marked_published(self):
  j=self.ready_job();j=self.db.update(j['id'],'VERIFY_PENDING',media_id='m')
  with self.assertRaises(RuntimeError):self.db.confirm(j,{'id':'m'},self.config.zone)
  self.assertEqual(self.db.job(j['id'])['status'],'VERIFY_PENDING')
 def test_database_survives_reopen(self):
  j=self.ready_job();other=Database(self.root/'data/test.db');self.assertEqual(other.job(j['id'])['container_id'],'container');other.conn.close()
 def test_confirmed_seed_only_skips_its_matching_hash(self):
  self.db.set('seed_match','pending');self.db.seed_match('same',self.video);self.assertIn(self.video.sha256,self.db.published_hashes())
 def test_permissions_private(self):self.assertEqual((self.root/'data/test.db').stat().st_mode&0o777,0o600)
class SelectionTests(Base):
 def test_numbers_are_numeric_and_duplicates_choose_first(self):
  (self.source/'010 - Last.mp4').write_bytes(b'last');(self.source/'001 - First.mp4').write_bytes(b'fake-mp4-test')
  result=scan(self.source);self.assertEqual([v.number for v in result],[1,10])
 def test_ambiguous_first_video_holds_the_queue(self):
  (self.source/'001 - HTML intro.mp4').write_bytes(b'intro');self.db.set('seed_match','pending')
  with self.assertRaises(RuntimeError):select(scan(self.source),self.db)
 def test_published_hash_is_skipped_after_filename_change(self):
  self.db.set('seed_match','pending');self.db.seed_match('same',self.video);self.assertIsNone(select(scan(self.source),self.db))
 def test_dry_run_never_makes_network_calls(self):
  with patch('publisher.validate_video',return_value={}),patch('requests.Session.request') as request,patch('requests.Session.post') as post:
   result=dry_run(self.config,self.db)
  self.assertFalse(result['upload']);self.assertFalse(result['publish']);request.assert_not_called();post.assert_not_called();self.assertEqual(self.db.jobs(),[])
class CaptionTests(Base):
 def test_fallback_is_topic_specific_and_has_relevant_hashtags(self):
  g=CaptionGenerator(self.config);text=g.fallback(self.video);self.assertIn('HTML tags',text);self.assertIn('@tech_at_60',text);self.assertEqual(g.normalize(text,self.video),text)
 def test_missing_key_has_deterministic_fallback(self):
  g=CaptionGenerator(self.config);self.assertEqual(g.generate(self.video),g.generate(self.video));self.assertEqual(g.generate(self.video)[1],'fallback')
 def test_ai_failure_falls_back(self):
  self.config.openai_key='fake-key';client=Mock();client.responses.create.side_effect=RuntimeError('outage');g=CaptionGenerator(self.config,client);self.assertEqual(g.generate(self.video)[1],'fallback')
 def test_explicit_free_mode_never_calls_openai_even_with_key(self):
  self.config.openai_key='unused-key';self.config.caption_mode='fallback';client=Mock()
  _,origin=CaptionGenerator(self.config,client).generate(self.video);self.assertEqual(origin,'fallback');client.responses.create.assert_not_called()
 def test_valid_ai_output_is_used_and_bounded(self):
  self.config.openai_key='fake';client=Mock();client.responses.create.return_value.output_text='HTML tags give content structure. Try one example. Save, share, comment and follow @tech_at_60. #HTML #Coding #LearnToCode'
  result,origin=CaptionGenerator(self.config,client).generate(self.video);self.assertEqual(origin,'openai');self.assertIn('Lesson 002',result);self.assertEqual(client.responses.create.call_args.kwargs['max_output_tokens'],500);self.assertFalse(client.responses.create.call_args.kwargs['store'])
class TransportTests(Base):
 def test_create_uses_official_resumable_fields(self):
  client=MetaClient(self.config);client.request=Mock(return_value={'id':'c','uri':'https://rupload.facebook.com/ig-api-upload/v26.0/c'});client.create('caption');data=client.request.call_args.kwargs['data'];self.assertEqual(data['upload_type'],'resumable');self.assertEqual(data['media_type'],'REELS');self.assertNotIn('video_url',data)
 def test_binary_stream_uses_documented_headers(self):
  session=Mock();session.post.return_value.status_code=200;session.post.return_value.json.return_value={'success':True};client=MetaClient(self.config,session)
  client.upload('https://rupload.facebook.com/ig-api-upload/v26.0/c',self.path);kw=session.post.call_args.kwargs;self.assertEqual(kw['headers']['Authorization'],'OAuth SECRET_TOKEN');self.assertEqual(kw['headers']['offset'],'0');self.assertEqual(kw['headers']['file_size'],str(self.path.stat().st_size));self.assertFalse(kw['allow_redirects']);self.assertTrue(hasattr(kw['data'],'read'))
 def test_rejects_upload_to_other_hosts_or_redirect_hosts(self):
  for uri in ['http://rupload.facebook.com/ig-api-upload/x','https://evil.test/ig-api-upload/x','https://rupload.facebook.com.evil.test/ig-api-upload/x','https://x@rupload.facebook.com/ig-api-upload/x']:
   with self.assertRaises(MetaError):MetaClient.validate_upload_uri(uri)
 def test_mutating_post_is_never_automatically_retried(self):
  session=Mock();session.request.side_effect=requests.Timeout('secret-url');client=MetaClient(self.config,session,sleep=lambda _:None)
  with self.assertRaises(MetaError) as error:client.publish('c')
  self.assertEqual(session.request.call_count,1);self.assertNotIn('secret-url',str(error.exception));self.assertTrue(error.exception.uncertain)
 def test_reads_have_bounded_network_retries(self):
  session=Mock();session.request.side_effect=requests.Timeout();client=MetaClient(self.config,session,sleep=lambda _:None)
  with self.assertRaises(MetaError):client.container('c')
  self.assertEqual(session.request.call_count,3)
 def test_poll_timeout_retains_uncertain_state(self):
  client=MetaClient(self.config,sleep=lambda _:None);self.config.poll_attempts=3;client.container=Mock(return_value={'status_code':'IN_PROGRESS'})
  with self.assertRaises(MetaError) as error:client.wait_finished('c')
  self.assertEqual(client.container.call_count,3);self.assertTrue(error.exception.uncertain)
 def test_terminal_container_stops_polling(self):
  client=MetaClient(self.config,sleep=lambda _:None);client.container=Mock(return_value={'status_code':'ERROR'})
  with self.assertRaises(MetaError) as error:client.wait_finished('c')
  self.assertEqual(error.exception.code,'ERROR');self.assertEqual(client.container.call_count,1)
class RecoveryTests(Base):
 def test_publish_request_is_durable_before_network_call(self):
  j=self.ready_job();m=self.fake_meta()
  def publish(_):self.assertEqual(self.db.job(j['id'])['status'],'PUBLISHING');raise MetaError('timeout',uncertain=True)
  m.publish.side_effect=publish;p=Publisher(self.config,self.db,m)
  with self.assertRaises(MetaError):p.resume(j)
  self.assertEqual(self.db.job(j['id'])['status'],'PUBLISH_UNKNOWN')
 def test_uncertain_publish_is_never_reissued(self):
  j=self.ready_job();j=self.db.update(j['id'],'PUBLISH_UNKNOWN',publish_attempt_at='2026-10-30T13:30:00Z');m=self.fake_meta();p=Publisher(self.config,self.db,m)
  with self.assertRaises(RuntimeError):p.reconcile(j)
  m.publish.assert_not_called();self.assertEqual(self.db.job(j['id'])['status'],'PUBLISH_UNKNOWN')
 def test_recovery_matches_meta_before_confirming(self):
  j=self.ready_job();j=self.db.update(j['id'],'PUBLISH_UNKNOWN');m=self.fake_meta();m.container.return_value={'status_code':'PUBLISHED'};m.find_caption.return_value=[{'id':'recovered','timestamp':'2026-10-30T13:30:00+0000'}]
  result=Publisher(self.config,self.db,m).reconcile(j);self.assertEqual(result['status'],'PUBLISHED');self.assertEqual(result['media_id'],'recovered');m.publish.assert_not_called()
 def test_media_id_response_crash_only_reads_on_recovery(self):
  j=self.ready_job();j=self.db.update(j['id'],'VERIFY_PENDING',media_id='known');m=self.fake_meta();Publisher(self.config,self.db,m).reconcile(j);m.publish.assert_not_called();self.assertEqual(self.db.job(j['id'])['status'],'PUBLISHED')
 def test_no_publication_without_activation(self):
  m=self.fake_meta()
  with self.assertRaises(RuntimeError):Publisher(self.config,self.db,m).execute()
  m.check_auth.assert_not_called();m.publish.assert_not_called()
 def test_remote_reel_today_blocks_publish(self):
  j=self.ready_job();m=self.fake_meta();m.today_reels.return_value=[{'id':'external'}];result=Publisher(self.config,self.db,m).resume(j);self.assertEqual(result['status'],'ALREADY_PUBLISHED_TODAY');m.publish.assert_not_called()
 def test_unpublished_prior_day_is_not_a_catchup_post(self):
  j=self.ready_job();self.config.now.return_value=self.now+timedelta(days=1);m=self.fake_meta();result=Publisher(self.config,self.db,m).resume(j);self.assertEqual(result['status'],'EXPIRED');m.publish.assert_not_called()
class SchedulerTests(Base):
 def test_time_guard_and_missed_run_window(self):
  self.assertFalse(self.config.due(self.now.replace(hour=18)));self.assertTrue(self.config.due(self.now));self.assertTrue(self.config.due(self.now.replace(hour=21)));self.assertFalse(self.config.due(self.now.replace(hour=23)))
 def test_plist_uses_venv_and_is_disabled_by_default(self):
  p=definition(self.config);self.assertEqual(p['Label'],LABEL);self.assertTrue(p['Disabled']);self.assertFalse(p['RunAtLoad']);self.assertEqual(p['StartCalendarInterval'],{'Hour':19,'Minute':0});self.assertEqual(p['ProgramArguments'][0],str(self.root/'.venv/bin/python'));self.assertNotIn('youtube',str(p).lower())
 def test_config_repr_does_not_expose_keys(self):self.assertNotIn('SECRET_TOKEN',repr(self.config))
 def test_redactor_masks_secrets(self):
  record=logging.LogRecord('x',logging.ERROR,'x',1,'Token %s',('SECRET_TOKEN',),None);Redactor(['SECRET_TOKEN']).filter(record);self.assertNotIn('SECRET_TOKEN',record.getMessage())
if __name__=='__main__':unittest.main()
