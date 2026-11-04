import unittest
from unittest.mock import patch,Mock
from test_system import Base
from publisher import Publisher
from meta_client import MetaClient,MetaError
class UrlRecoveryTests(Base):
 def test_hosted_recovery_does_not_send_binary_upload(self):
  job=self.ready_job();job=self.db.update(job['id'],'FAILED');meta=self.fake_meta();meta.create_url.return_value='url-container';meta.wait_finished.return_value='FINISHED'
  url='https://github.com/owner/repo/releases/download/reel-inputs/video.mp4'
  with patch('publisher.validate_video',return_value={}):result=Publisher(self.config,self.db,meta,source_url=url).prepare_private(job)
  self.assertEqual(result['status'],'READY');self.assertIsNone(result['upload_uri']);meta.upload.assert_not_called();meta.create.assert_not_called();meta.publish.assert_not_called();meta.create_url.assert_called_once_with(url,job['caption'])
 def test_lost_url_create_response_is_retained_as_uncertain(self):
  job=self.ready_job();job=self.db.update(job['id'],'FAILED');meta=self.fake_meta();meta.create_url.side_effect=MetaError('lost',uncertain=True)
  with patch('publisher.validate_video',return_value={}):
   with self.assertRaises(MetaError):Publisher(self.config,self.db,meta,source_url='https://github.com/o/r/releases/download/v/file.mp4').prepare_private(job)
  self.assertEqual(self.db.job(job['id'])['status'],'CREATE_UNKNOWN');meta.publish.assert_not_called()
 def test_url_create_rejects_other_hosts_before_network(self):
  meta=MetaClient(self.config);meta.request=Mock()
  with self.assertRaises(MetaError):meta.create_url('https://evil.example/file.mp4','caption')
  meta.request.assert_not_called()
 def test_url_create_uses_official_video_url_parameter(self):
  meta=MetaClient(self.config);meta.request=Mock(return_value={'id':'known'})
  url='https://github.com/o/r/releases/download/v/file.mp4';self.assertEqual(meta.create_url(url,'caption'),'known')
  data=meta.request.call_args.kwargs['data'];self.assertEqual(data['video_url'],url);self.assertNotIn('upload_type',data)
if __name__=='__main__':unittest.main()
