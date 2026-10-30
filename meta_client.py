"""Official Facebook Login Instagram Graph API and direct binary transport."""
from datetime import datetime,timezone,timedelta
from pathlib import Path
from urllib.parse import urlsplit
import requests,time
class MetaError(RuntimeError):
 def __init__(self,message,uncertain=False,code=None):super().__init__(message);self.uncertain=uncertain;self.code=code
class MetaClient:
 def __init__(self,config,session=None,sleep=time.sleep):
  self.c=config;self.session=session or requests.Session();self.sleep=sleep
  self.base='https://graph.facebook.com/'+config.graph_version
 def request(self,method,path,params=None,data=None,authorization=None):
  if not path or any(c in path for c in ('://','?','..')):raise MetaError('Invalid Graph API path.')
  attempts=3 if method=='GET' else 1
  for attempt in range(attempts):
   try:
    response=self.session.request(method,self.base+'/'+path,params=params,data=data,headers={'Authorization':'Bearer '+(authorization or self.c.token)},timeout=(10,45),allow_redirects=False)
   except requests.RequestException as e:
    if attempt+1<attempts:self.sleep(2**attempt);continue
    raise MetaError('Meta connection failed ('+type(e).__name__+').',uncertain=method!='GET') from None
   try:body=response.json()
   except ValueError:raise MetaError('Meta returned an unreadable response.',uncertain=method!='GET') from None
   if not 200<=response.status_code<300 or 'error' in body:
    error=body.get('error',{});code=error.get('code');sub=error.get('error_subcode')
    if method=='GET' and response.status_code in (429,500,502,503,504) and attempt+1<attempts:self.sleep(2**attempt);continue
    raise MetaError(f'Meta request failed (HTTP {response.status_code}, code {code}, subcode {sub}). No secrets logged.',uncertain=method!='GET',code=code)
   return body
 def get(self,path,**params):return self.request('GET',path,params=params)
 def create(self,caption):
  r=self.request('POST',self.c.account_id+'/media',data={'media_type':'REELS','upload_type':'resumable','caption':caption,'share_to_feed':'true'})
  if not r.get('id') or not r.get('uri'):raise MetaError('Container response lacks id or upload URI.',uncertain=True)
  self.validate_upload_uri(r['uri']);return str(r['id']),r['uri']
 @staticmethod
 def validate_upload_uri(uri):
  u=urlsplit(uri)
  if u.scheme!='https' or u.hostname!='rupload.facebook.com' or u.port not in (None,443) or u.username or u.password or not u.path.startswith('/ig-api-upload/') or u.fragment:raise MetaError('Upload URI is not the official Meta Instagram upload endpoint.')
 def upload(self,uri,path):
  self.validate_upload_uri(uri);path=Path(path)
  try:
   with path.open('rb') as stream:
    r=self.session.post(uri,headers={'Authorization':'OAuth '+self.c.token,'offset':'0','file_size':str(path.stat().st_size),'Content-Type':'application/octet-stream','Content-Length':str(path.stat().st_size)},data=stream,timeout=(15,300),allow_redirects=False)
   body=r.json()
  except (requests.RequestException,ValueError) as e:raise MetaError('Binary upload outcome uncertain ('+type(e).__name__+'); reconcile container before any retry.',uncertain=True) from None
  if not 200<=r.status_code<300 or body.get('success') is not True:raise MetaError('Binary upload was not confirmed. Reconcile the existing container.',uncertain=True)
 def container(self,ident):return self.get(ident,fields='id,status_code,status')
 def wait_finished(self,ident):
  for attempt in range(self.c.poll_attempts):
   value=self.container(ident);status=value.get('status_code')
   if status in ('FINISHED','PUBLISHED'):return status
   if status in ('ERROR','EXPIRED'):raise MetaError('Container reached terminal state '+status+'.',code=status)
   if attempt+1<self.c.poll_attempts:self.sleep(self.c.poll_interval)
  raise MetaError('Processing poll deadline reached; existing container retained.',uncertain=True)
 def publish(self,ident):
  r=self.request('POST',self.c.account_id+'/media_publish',data={'creation_id':ident})
  if not r.get('id'):raise MetaError('Publish outcome uncertain: no media ID returned.',uncertain=True)
  return str(r['id'])
 def media(self,ident):return self.get(ident,fields='id,caption,media_type,media_product_type,permalink,timestamp')
 def recent_media(self,max_pages=20):
  rows=[];after=None
  for _ in range(max_pages):
   params={'fields':'id,caption,media_type,media_product_type,permalink,timestamp','limit':100}
   if after:params['after']=after
   r=self.get(self.c.account_id+'/media',**params)
   if not isinstance(r.get('data'),list):raise MetaError('Media listing response was incomplete; refusing duplicate-risk operation.')
   rows+=r['data']
   if not r.get('paging',{}).get('next'):return rows
   after=r.get('paging',{}).get('cursors',{}).get('after')
   if not after:break
  raise MetaError('Media pagination was incomplete; cannot safely reconcile duplicates.')
 def find_caption(self,caption):return [r for r in self.recent_media() if r.get('caption')==caption and r.get('media_product_type')=='REELS']
 def today_reels(self,day):
  rows=self.recent_media()
  return [r for r in rows if r.get('media_product_type')=='REELS' and r.get('timestamp') and datetime.fromisoformat(r['timestamp'].replace('Z','+00:00')).astimezone(self.c.zone).date().isoformat()==day]
 def check_limit(self):
  values=self.get(self.c.account_id+'/content_publishing_limit',fields='config,quota_usage').get('data',[])
  if not values:raise MetaError('Publishing quota could not be read.')
  config=values[0].get('config',{})
  if not isinstance(config,dict):raise MetaError('Publishing quota format unavailable.')
  maximum=config.get('quota_total');usage=values[0].get('quota_usage')
  if maximum is None or usage is None:raise MetaError('Publishing quota could not be verified.')
  if int(usage)>=int(maximum):raise MetaError('Meta publishing quota is exhausted.')
  return dict(used=usage,total=maximum)
 def inspect_token(self):
  choices=[self.c.token]
  if self.c.app_access_token:choices.append(self.c.app_access_token)
  if self.c.app_secret:choices.append(self.c.app_id+'|'+self.c.app_secret)
  for authorization in dict.fromkeys(choices):
   try:info=self.request('GET','debug_token',params={'input_token':self.c.token},authorization=authorization).get('data',{})
   except MetaError as e:
    if e.code is None:raise
    continue
   if not info.get('is_valid') or str(info.get('app_id'))!=self.c.app_id:raise MetaError('Token is invalid or belongs to a different Meta App.')
   return info
  raise MetaError('Token introspection needs an authorized app credential.',code='INTROSPECTION')
 def check_auth(self):
  self.c.validate(need_token=True)
  report={'account_id':self.c.account_id,'username':'tech_at_60','valid':False,'warnings':[],'expiration_verified':False}
  me=self.get('me',fields='id')
  permissions=self.get('me/permissions').get('data',[]);granted={r['permission'] for r in permissions if r.get('status')=='granted'}
  required={'instagram_basic','instagram_content_publish','pages_show_list','pages_read_engagement','business_management'}
  if not required<=granted:raise MetaError('Required Facebook permissions are missing: '+', '.join(sorted(required-granted)))
  page=self.get(self.c.page_id,fields='id,instagram_business_account{id,username}')
  linked=page.get('instagram_business_account',{})
  if str(linked.get('id'))!=self.c.account_id:raise MetaError('Facebook Page is not linked to the configured Instagram account.')
  account=self.get(self.c.account_id,fields='id,username')
  if account.get('username')!='tech_at_60':raise MetaError('Instagram username mismatch; refusing publication.')
  authorization=self.c.app_access_token or (self.c.app_id+'|'+self.c.app_secret if self.c.app_secret else self.c.token)
  try:
   info=self.inspect_token()
   if not info.get('is_valid') or str(info.get('app_id'))!=self.c.app_id:raise MetaError('Token is invalid or belongs to a different Meta App.')
   report['expiration_verified']=True
   for key in ('expires_at','data_access_expires_at'):
    expires=int(info.get(key) or 0);report[key]=expires
    if expires:
     remaining=expires-datetime.now(timezone.utc).timestamp()
     if remaining<=0:raise MetaError('Meta token or data-access authorization has expired.')
     if remaining<7*86400:report['warnings'].append(key+' is within seven days; renew or reauthorize locally.')
   if info.get('type') not in ('USER','PAGE'):report['warnings'].append('Unexpected token type; verify Facebook Login configuration.')
  except MetaError as e:
   if e.code is None:raise
   report['warnings'].append('Token introspection unavailable. Set META_APP_SECRET or META_APP_ACCESS_TOKEN locally before activation.')
  report['quota']=self.check_limit();report['permissions']=sorted(granted);report['valid']=True
  return report
