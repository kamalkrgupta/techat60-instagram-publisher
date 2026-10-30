"""Persistent local Instagram queue; no Meta uploads or source-folder writes."""
import csv,json,os,tempfile
from datetime import timedelta
from pathlib import Path
from config import secure_json
from database import ACTIVE,stamp
from video_source import scan
from caption_generator import CaptionGenerator

def first_slot(c,db):
 now=c.now();h,m=map(int,c.publish_time.split(':'));target=now.replace(hour=h,minute=m,second=0,microsecond=0)
 if db.used_day(now.date().isoformat()) or now>target+timedelta(minutes=c.grace_minutes):target+=timedelta(days=1)
 return target

def sync_queue(c,db):
 videos=scan(c.video_folder);now=stamp();publications={r['sha256']:r for r in db.history() if r.get('sha256')};jobs={r['sha256']:r for r in db.jobs() if r['status'] in ACTIVE};hold=any(r['status'] in ('PUBLISHING','PUBLISH_UNKNOWN','VERIFY_PENDING') for r in jobs.values())
 db.conn.execute('''CREATE TABLE IF NOT EXISTS reel_queue(sha256 TEXT PRIMARY KEY,number INTEGER NOT NULL,filename TEXT NOT NULL,topic TEXT NOT NULL,caption TEXT NOT NULL,status TEXT NOT NULL,available INTEGER NOT NULL DEFAULT 1,planned_at TEXT,media_id TEXT,permalink TEXT,added_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
 captioner=CaptionGenerator(c)
 with db.conn:
  db.conn.execute('UPDATE reel_queue SET available=0')
  for v in videos:
   caption,_=captioner.generate(v,allow_api=False)
   published=publications.get(v.sha256);state='PUBLISHED' if published else 'IN_PROGRESS' if v.sha256 in jobs else 'QUEUED'
   if not published and (hold or v.number==1 and db.setting('seed_match')=='pending'):state='ON_HOLD'
   db.conn.execute('''INSERT INTO reel_queue(sha256,number,filename,topic,caption,status,available,media_id,permalink,added_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?,?,?) ON CONFLICT(sha256) DO UPDATE SET number=excluded.number,filename=excluded.filename,topic=excluded.topic,caption=excluded.caption,status=excluded.status,available=1,media_id=excluded.media_id,permalink=excluded.permalink,updated_at=excluded.updated_at''',(v.sha256,v.number,v.path.name,v.topic,caption,state,published.get('media_id') if published else None,published.get('permalink') if published else None,now,now))
  # Keep publication history after a file is removed; never silently re-queue it.
  for digest,pub in publications.items():db.conn.execute("UPDATE reel_queue SET status='PUBLISHED',media_id=?,permalink=?,planned_at=NULL WHERE sha256=?",(pub['media_id'],pub.get('permalink'),digest))
  db.conn.execute("UPDATE reel_queue SET status='UNAVAILABLE',planned_at=NULL WHERE available=0 AND status!='PUBLISHED'")
  db.conn.execute('UPDATE reel_queue SET planned_at=NULL')
  at=first_slot(c,db)
  rows=db.conn.execute("SELECT sha256 FROM reel_queue WHERE available=1 AND status IN ('QUEUED','IN_PROGRESS','ON_HOLD') ORDER BY number,filename").fetchall()
  for r in rows:
   db.conn.execute('UPDATE reel_queue SET planned_at=? WHERE sha256=?',(at.isoformat(),r['sha256']));at+=timedelta(days=1)
 rows=[dict(r) for r in db.conn.execute('SELECT * FROM reel_queue ORDER BY number,filename')];upcoming=[r for r in rows if r['status'] in ('QUEUED','IN_PROGRESS','ON_HOLD') and r['available']]
 summary={'updated_at':c.now().isoformat(),'queue_type':'local automation schedule; not pre-uploaded Meta containers','caption_mode':'free deterministic fallback','active':c.schedule_active(),'backend':c.publish_backend,'total_files':len(videos),'published':sum(r['status']=='PUBLISHED' for r in rows),'queued':len(upcoming),'next':upcoming[0]['planned_at'] if upcoming else None,'last_planned':upcoming[-1]['planned_at'] if upcoming else None,'next_filename':upcoming[0]['filename'] if upcoming else None,'queue_file':str(c.root/'data/queue.csv'),'videos':rows}
 secure_json(c.root/'data/queue.json',summary)
 fd,temp=tempfile.mkstemp(dir=c.root/'data',prefix='.queue-',suffix='.csv')
 try:
  os.fchmod(fd,0o600)
  with os.fdopen(fd,'w',newline='') as f:
   writer=csv.DictWriter(f,fieldnames=['number','filename','topic','status','planned_at','caption','sha256','media_id','permalink','available']);writer.writeheader();writer.writerows({k:r.get(k) for k in writer.fieldnames} for r in rows)
  os.replace(temp,c.root/'data/queue.csv')
 finally:Path(temp).unlink(missing_ok=True)
 text='# Instagram daily queue\n\nOne Reel per day at 19:00 IST. Local schedule: upload/publish occurs when the Mac runs the approved job. New source MP4s are discovered automatically; published hashes are excluded. Dates are planned slots, not confirmed future Meta publications.\n\n| Lesson | Video | Status | Planned release (IST) |\n|---|---|---|---|\n'
 for r in rows:text+=f'| {r["number"]:03d} | {r["topic"].replace("|"," / ")} | {r["status"]} | {r["planned_at"] or ""} |\n'
 p=c.root/'data/queue.md';p.write_text(text);p.chmod(0o600)
 return {k:v for k,v in summary.items() if k!='videos'}
