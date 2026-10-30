"""Encrypted, compare-and-swap durable state on a dedicated GitHub branch."""
import base64,hashlib,json,os,sqlite3,tempfile
from pathlib import Path
from urllib.parse import quote,urlsplit
import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
class CloudStateError(RuntimeError):pass
class GitHub:
 def __init__(self,repo,token,session=None):
  if not __import__('re').fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo):raise CloudStateError('Invalid repository name.')
  self.repo=repo;self.token=token;self.session=session or requests.Session()
 def request(self,method,path,body=None,allowed=(200,201,204)):
  if not path.startswith('/') or '://' in path or '..' in path:raise CloudStateError('Invalid GitHub API path.')
  try:r=self.session.request(method,'https://api.github.com'+path,json=body,headers={'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','Cache-Control':'no-cache'},timeout=(10,60),allow_redirects=False)
  except requests.RequestException as e:raise CloudStateError('GitHub network outcome uncertain ('+type(e).__name__+'). Publishing stopped.') from None
  if r.status_code not in allowed:raise CloudStateError(f'GitHub HTTP {r.status_code}; publishing stopped without overwriting state.')
  if r.status_code in (204,404):return None
  try:return r.json()
  except ValueError:raise CloudStateError('GitHub returned an unreadable response.') from None
 def path(self,suffix):return '/repos/'+self.repo+suffix
 def contents(self,path,ref):return self.request('GET',self.path('/contents/'+path+'?ref='+quote(ref,safe='')),allowed=(200,404))
 def put_contents(self,path,content,branch,sha=None,message='Update Instagram automation'):
  body={'message':message,'content':base64.b64encode(content).decode(),'branch':branch}
  if sha:body['sha']=sha
  return self.request('PUT',self.path('/contents/'+path),body)

def encode_state(data,key,aad):
 if len(key)!=32:raise CloudStateError('State encryption key must be 32 bytes.')
 nonce=os.urandom(12);return b'IGSTATE1'+nonce+AESGCM(key).encrypt(nonce,data,aad)
def decode_state(data,key,aad):
 if not data.startswith(b'IGSTATE1') or len(data)<36:raise CloudStateError('Encrypted state header is invalid.')
 try:plain=AESGCM(key).decrypt(data[8:20],data[20:],aad)
 except Exception:raise CloudStateError('State decryption/authentication failed. Never initialize an empty replacement ledger.') from None
 if not plain.startswith(b'SQLite format 3\x00'):raise CloudStateError('State is not a SQLite ledger.')
 return plain

def snapshot(conn):
 fd,name=tempfile.mkstemp(suffix='.sqlite3');os.close(fd)
 try:
  backup=sqlite3.connect(name);conn.backup(backup);backup.close();return Path(name).read_bytes()
 finally:Path(name).unlink(missing_ok=True)

class StateStore:
 branch='instagram-state';filename='state.enc'
 def __init__(self,api,key):
  self.api=api;self.key=key;self.sha=None;self.aad=(api.repo+':'+self.branch+':'+self.filename).encode()
 def load(self,path):
  # Pin the content read to the branch head; never restore a stale cached artifact.
  ref=self.api.request('GET',self.api.path('/git/ref/heads/'+self.branch))
  if not ref or not ref.get('object',{}).get('sha'):raise CloudStateError('Durable state branch is missing. No empty ledger will be created.')
  head=ref['object']['sha']
  content=self.api.contents(self.filename,head)
  if not content or content.get('encoding')!='base64':raise CloudStateError('Durable ledger is missing. Refusing a fresh publishing history.')
  data=decode_state(base64.b64decode(content['content']),self.key,self.aad)
  path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
  fd,tmp=tempfile.mkstemp(dir=path.parent)
  try:
   os.fchmod(fd,0o600)
   with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
   check=sqlite3.connect(tmp)
   try:
    if check.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise CloudStateError('Ledger integrity check failed.')
   finally:check.close()
   os.replace(tmp,path)
  finally:Path(tmp).unlink(missing_ok=True)
  self.sha=content['sha'];return path
 def persist(self,db):
  if self.sha is None:raise CloudStateError('No known remote state revision. Refusing overwrite.')
  data=encode_state(snapshot(db.conn),self.key,self.aad)
  result=self.api.put_contents(self.filename,data,self.branch,self.sha,'Checkpoint Instagram publishing ledger')
  try:self.sha=result['content']['sha']
  except (KeyError,TypeError):raise CloudStateError('Checkpoint response was uncertain. Stop before any public mutation.') from None
