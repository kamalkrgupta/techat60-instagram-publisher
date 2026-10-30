"""Configuration and private storage. No writes are made to VIDEO_FOLDER."""
from dataclasses import dataclass,field
from pathlib import Path
from datetime import datetime,time,timedelta
from zoneinfo import ZoneInfo
import json,os,tempfile
from dotenv import dotenv_values
ROOT=Path(__file__).resolve().parent
EXPECTED_ACCOUNT='17841416969437440'
EXPECTED_PAGE='1303934949479949'
EXPECTED_APP='2087957475177708'
class ConfigError(RuntimeError):pass

def secure_json(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.'+path.name)
 try:
  os.fchmod(fd,0o600)
  with os.fdopen(fd,'w') as f:json.dump(value,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
  os.replace(tmp,path)
 finally:Path(tmp).unlink(missing_ok=True)

@dataclass
class Config:
 root:Path=ROOT
 account_id:str=EXPECTED_ACCOUNT
 page_id:str=EXPECTED_PAGE
 app_id:str=EXPECTED_APP
 token:str=field(default='',repr=False)
 openai_key:str=field(default='',repr=False)
 app_secret:str=field(default='',repr=False)
 app_access_token:str=field(default='',repr=False)
 video_folder:Path=Path('/Users/impulsiveweb/Documents/YT Short/Published Shorts')
 timezone:str='Asia/Kolkata'
 publish_time:str='19:00'
 graph_version:str='v26.0'
 caption_model:str='gpt-5.6-luna'
 caption_mode:str='auto'
 publish_backend:str='mac'
 grace_minutes:int=180
 poll_interval:int=15
 poll_attempts:int=40
 @property
 def zone(self):return ZoneInfo(self.timezone)
 @property
 def db_path(self):
  if self.publish_backend=='github-actions' and os.environ.get('GITHUB_ACTIONS')!='true':
   p=self.root/'data/cloud-mirror.sqlite3'
   if not p.exists():raise ConfigError('Cloud history mirror is missing. Run github_deploy.py sync before viewing local status.')
   return p
  return self.root/'data/instagram.sqlite3'
 @property
 def activation(self):return self.root/'activation.json'
 def now(self):return datetime.now(self.zone)
 def due(self,now=None):
  now=(now or self.now()).astimezone(self.zone);h,m=map(int,self.publish_time.split(':'));target=now.replace(hour=h,minute=m,second=0,microsecond=0)
  return target<=now<=target+timedelta(minutes=self.grace_minutes)
 def next_slot(self,now=None,used_today=False):
  now=(now or self.now()).astimezone(self.zone);h,m=map(int,self.publish_time.split(':'));at=now.replace(hour=h,minute=m,second=0,microsecond=0)
  return at+timedelta(days=1) if used_today or now>=at else at
 def daily_approved(self):
  p=self.root/'publication-approval.json'
  if not p.exists():return False
  value=json.loads(p.read_text());return value.get('approved') is True and value.get('account_id')==self.account_id
 def schedule_active(self):
  if self.publish_backend=='github-actions' and os.environ.get('GITHUB_ACTIONS')!='true':
   p=self.root/'.github-deployment.json'
   return p.exists() and json.loads(p.read_text()).get('phase')=='active'
  return self.active()
 def active(self):
  if self.publish_backend=='github-actions' and os.environ.get('GITHUB_ACTIONS')!='true':return False
  if not self.activation.exists():return False
  d=json.loads(self.activation.read_text());return d.get('approved') is True and d.get('account_id')==self.account_id
 def validate(self,need_token=False):
  if self.account_id!=EXPECTED_ACCOUNT or self.page_id!=EXPECTED_PAGE or self.app_id!=EXPECTED_APP:raise ConfigError('Configured Meta identity does not match the authorized Techat60 account.')
  if self.timezone!='Asia/Kolkata' or self.publish_time!='19:00':raise ConfigError('This deployment is configured for 19:00 Asia/Kolkata only.')
  if self.graph_version!='v26.0':raise ConfigError('Expected requested Graph API v26.0.')
  if not self.video_folder.is_dir():raise ConfigError('VIDEO_FOLDER is missing or unreadable.')
  if self.root.resolve()==self.video_folder.resolve() or self.root.resolve().is_relative_to(self.video_folder.resolve()):raise ConfigError('Project storage must be separate from source videos.')
  if need_token and not self.token:raise ConfigError('META_ACCESS_TOKEN is empty. Enter it in the protected local .env file, not chat.')
  if self.publish_backend not in ('mac','github-actions'):raise ConfigError('PUBLISH_BACKEND must be mac or github-actions.')
  if self.caption_mode not in ('auto','fallback'):raise ConfigError('CAPTION_MODE must be auto or fallback.')
  if self.grace_minutes<1 or self.grace_minutes>240:raise ConfigError('Missed-run grace must be 1–240 minutes.')
  return self
 @classmethod
 def load(cls,root=ROOT):
  root=Path(root).resolve();envfile=root/'.env'
  if envfile.exists() and envfile.stat().st_mode&0o077:raise ConfigError('.env permissions must be 600; run chmod 600 .env.')
  e=dict(dotenv_values(envfile))
  for k in list(e)+['META_APP_SECRET','META_APP_ACCESS_TOKEN','OPENAI_MODEL','MISSED_RUN_GRACE_MINUTES','CAPTION_MODE','PUBLISH_BACKEND','INSTAGRAM_ACCOUNT_ID','FACEBOOK_PAGE_ID','META_APP_ID','META_ACCESS_TOKEN','OPENAI_API_KEY','VIDEO_FOLDER','TIMEZONE','PUBLISH_TIME','GRAPH_API_VERSION']:
   if k in os.environ:e[k]=os.environ[k]
  c=cls(root=root,account_id=e.get('INSTAGRAM_ACCOUNT_ID') or EXPECTED_ACCOUNT,page_id=e.get('FACEBOOK_PAGE_ID') or EXPECTED_PAGE,app_id=e.get('META_APP_ID') or EXPECTED_APP,token=e.get('META_ACCESS_TOKEN') or '',openai_key=e.get('OPENAI_API_KEY') or '',app_secret=e.get('META_APP_SECRET') or '',app_access_token=e.get('META_APP_ACCESS_TOKEN') or '',video_folder=Path(e.get('VIDEO_FOLDER') or cls.video_folder).expanduser(),timezone=e.get('TIMEZONE') or 'Asia/Kolkata',publish_time=e.get('PUBLISH_TIME') or '19:00',graph_version=e.get('GRAPH_API_VERSION') or 'v26.0',caption_model=e.get('OPENAI_MODEL') or 'gpt-5.6-luna',caption_mode=e.get('CAPTION_MODE') or 'auto',publish_backend=e.get('PUBLISH_BACKEND') or 'mac',grace_minutes=int(e.get('MISSED_RUN_GRACE_MINUTES') or 180))
  return c.validate()

def prepare_storage(c):
 for name in ('data','logs'):
  p=c.root/name;p.mkdir(exist_ok=True,mode=0o700);p.chmod(0o700)
 os.umask(0o077)
