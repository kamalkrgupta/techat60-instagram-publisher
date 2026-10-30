"""Durable SQLite ledger. Day/hash reservations precede network mutations."""
import sqlite3,json,uuid,os
from datetime import datetime,timezone
from pathlib import Path
SEED_ID='17984733683898502'
SEED_URL='https://www.instagram.com/reel/DfEp75lDMMK/'
ACTIVE=('CREATING','CREATE_UNKNOWN','CREATED','UPLOADING','UPLOAD_UNKNOWN','PROCESSING','READY','PUBLISHING','PUBLISH_UNKNOWN','VERIFY_PENDING')
def stamp():return datetime.now(timezone.utc).isoformat()
class Database:
 def __init__(self,path):
  self.on_commit=None
  path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
  self.conn=sqlite3.connect(path,timeout=10);self.conn.row_factory=sqlite3.Row
  self.conn.execute('PRAGMA foreign_keys=ON');self.conn.execute('PRAGMA journal_mode=WAL');self.conn.execute('PRAGMA synchronous=FULL');path.chmod(0o600)
  self.conn.executescript('''
  CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,local_day TEXT NOT NULL UNIQUE,filename TEXT NOT NULL,sha256 TEXT NOT NULL,caption TEXT NOT NULL,status TEXT NOT NULL,container_id TEXT,upload_uri TEXT,media_id TEXT UNIQUE,permalink TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,publish_attempt_at TEXT,published_at TEXT,error TEXT,attempts INTEGER NOT NULL DEFAULT 0);
  CREATE UNIQUE INDEX IF NOT EXISTS one_active_hash ON jobs(sha256) WHERE status NOT IN ('FAILED','EXPIRED');
  CREATE TABLE IF NOT EXISTS publications(media_id TEXT PRIMARY KEY,sha256 TEXT UNIQUE,filename TEXT,caption TEXT,permalink TEXT,local_day TEXT NOT NULL,published_at TEXT,verified INTEGER NOT NULL DEFAULT 0,source TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,job_id TEXT,status TEXT NOT NULL,detail TEXT,created_at TEXT NOT NULL);
  ''')
  self.seed()
 def notify(self):
  if self.on_commit:self.on_commit()
 def seed(self):
  with self.conn:
   self.conn.execute("INSERT OR IGNORE INTO settings VALUES('seed_match','pending')")
   self.conn.execute('INSERT OR IGNORE INTO publications(media_id,permalink,local_day,source) VALUES(?,?,?,?)',(SEED_ID,SEED_URL,'2026-10-29','user-provided published test Reel; file match unconfirmed'))
 def setting(self,key,default=None):
  r=self.conn.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone();return r['value'] if r else default
 def set(self,key,value):
  with self.conn:self.conn.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(value)))
  self.notify()
 def seed_match(self,decision,video):
  if decision not in ('same','different'):raise ValueError('Choose same or different.')
  if self.setting('seed_match')!='pending' and self.setting('seed_match')!=decision:raise RuntimeError('Seed decision already recorded; review database before changing it.')
  with self.conn:
   if decision=='same':self.conn.execute('UPDATE publications SET sha256=?,filename=? WHERE media_id=?',(video.sha256,video.path.name,SEED_ID))
   self.conn.execute("UPDATE settings SET value=? WHERE key='seed_match'",(decision,))
  self.notify()
 def published_hashes(self):return {r[0] for r in self.conn.execute('SELECT sha256 FROM publications WHERE sha256 IS NOT NULL')}
 def used_day(self,day):return self.conn.execute('SELECT 1 FROM publications WHERE local_day=? LIMIT 1',(day,)).fetchone() is not None
 def reserve(self,day,video,caption):
  self.conn.execute('BEGIN IMMEDIATE')
  try:
   if self.used_day(day):raise RuntimeError('A Reel is already recorded for this local day.')
   if video.sha256 in self.published_hashes():raise RuntimeError('This content hash was already published.')
   ident=uuid.uuid4().hex;now=stamp()
   self.conn.execute('INSERT INTO jobs(id,local_day,filename,sha256,caption,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(ident,day,str(video.path),video.sha256,caption,'CREATING',now,now))
   self.conn.execute('INSERT INTO events(job_id,status,created_at) VALUES(?,?,?)',(ident,'CREATING',now));self.conn.commit();self.notify();return self.job(ident)
  except BaseException:self.conn.rollback();raise
 def job(self,ident):
  r=self.conn.execute('SELECT * FROM jobs WHERE id=?',(ident,)).fetchone();return dict(r) if r else None
 def pending(self):
  marks=','.join('?' for _ in ACTIVE)
  return [dict(r) for r in self.conn.execute(f'SELECT * FROM jobs WHERE status IN ({marks}) ORDER BY created_at',ACTIVE)]
 def day_job(self,day):
  r=self.conn.execute('SELECT * FROM jobs WHERE local_day=?',(day,)).fetchone();return dict(r) if r else None
 def update(self,ident,status=None,**values):
  allowed={'container_id','upload_uri','media_id','permalink','publish_attempt_at','published_at','error','attempts'}
  if not set(values)<=allowed:raise ValueError('Invalid ledger fields')
  values['updated_at']=stamp()
  if status:values['status']=status
  with self.conn:
   self.conn.execute('UPDATE jobs SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),ident))
   self.conn.execute('INSERT INTO events(job_id,status,detail,created_at) VALUES(?,?,?,?)',(ident,status or 'UPDATE',json.dumps({k:v for k,v in values.items() if k!='upload_uri'}),stamp()))
  self.notify();return self.job(ident)
 def confirm(self,job,media,zone):
  if str(media.get('id'))!=str(job['media_id']) or not media.get('permalink') or not media.get('timestamp'):raise RuntimeError('Publication metadata is not confirmed.')
  from urllib.parse import urlsplit
  if urlsplit(media['permalink']).hostname not in ('www.instagram.com','instagram.com'):raise RuntimeError('Unexpected media permalink host.')
  at=datetime.fromisoformat(media['timestamp'].replace('Z','+00:00'));day=at.astimezone(zone).date().isoformat()
  with self.conn:
   self.conn.execute('INSERT OR IGNORE INTO publications VALUES(?,?,?,?,?,?,?,?,?)',(media['id'],job['sha256'],Path(job['filename']).name,job['caption'],media['permalink'],day,at.isoformat(),1,'Graph API confirmed'))
   row=self.conn.execute('SELECT sha256 FROM publications WHERE media_id=?',(media['id'],)).fetchone()
   if not row or row['sha256']!=job['sha256']:raise RuntimeError('Publication identity conflict; manual reconciliation required.')
   self.conn.execute("UPDATE jobs SET status='PUBLISHED',permalink=?,published_at=?,updated_at=?,error=NULL WHERE id=?",(media['permalink'],at.isoformat(),stamp(),job['id']))
   self.conn.execute('INSERT INTO events(job_id,status,created_at) VALUES(?,?,?)',(job['id'],'PUBLISHED',stamp()))
  self.notify();return self.job(job['id'])
 def verify_seed(self,media):
  if str(media.get('id'))!=SEED_ID or media.get('permalink','').rstrip('/')!=SEED_URL.rstrip('/'):raise RuntimeError('Existing seed Reel identity mismatch.')
  if not media.get('timestamp'):raise RuntimeError('Seed publication timestamp missing.')
  with self.conn:self.conn.execute('UPDATE publications SET verified=1,published_at=? WHERE media_id=?',(media['timestamp'],SEED_ID))
  self.notify()
 def history(self):return [dict(r) for r in self.conn.execute('SELECT * FROM publications ORDER BY local_day,media_id')]
 def jobs(self):return [dict(r) for r in self.conn.execute('SELECT * FROM jobs ORDER BY created_at')]
