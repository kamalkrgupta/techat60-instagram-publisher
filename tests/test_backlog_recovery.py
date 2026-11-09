import tempfile,unittest
from pathlib import Path
from zoneinfo import ZoneInfo
from publish_ai_special import SpecialLedger
from video_source import Video
from recover_november_backlog import PLAN,SOURCE
class RecoveryTests(unittest.TestCase):
 def test_batch_confirm_keeps_real_day_and_regular_slot_available(self):
  with tempfile.TemporaryDirectory() as tmp:
   db=SpecialLedger(Path(tmp)/'ledger.db');db.publication_source=SOURCE
   for i,(missed,number,digest) in enumerate(PLAN):
    video=Video(Path(tmp)/str(number),number,'topic',digest)
    job=db.reserve_special('2026-11-09',video,'caption '+str(number))
    job=db.update(job['id'],'VERIFY_PENDING',media_id=str(number))
    media={'id':str(number),'timestamp':'2026-11-09T10:00:00+00:00','permalink':'https://www.instagram.com/reel/test'+str(number)+'/'}
    db.confirm(job,media,ZoneInfo('Asia/Kolkata'))
    self.assertIn(digest,db.published_hashes());self.assertFalse(db.used_day('2026-11-09'))
   self.assertEqual(db.catchup_media_ids(),{'6','7','8'})
   self.assertTrue(all(v['local_day']=='2026-11-09' for v in db.history() if v['sha256'] in {p[2] for p in PLAN}))
   with db.conn:db.conn.execute("INSERT INTO publications(media_id,local_day,source) VALUES('9','2026-11-09','Graph API confirmed')")
   self.assertTrue(db.used_day('2026-11-09'));db.conn.close()
