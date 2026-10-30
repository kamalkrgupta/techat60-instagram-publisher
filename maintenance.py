"""Local secure credential entry, seed resolution and token exchange."""
import argparse,getpass,json,os,sys,tempfile
from pathlib import Path
from dotenv import dotenv_values
from config import Config,ROOT,prepare_storage,secure_json
from database import Database
from video_source import scan

def write_env(root,updates):
 p=Path(root)/'.env';values=dict(dotenv_values(p))
 for k,v in updates.items():values[k]=v
 fd,tmp=tempfile.mkstemp(dir=root,prefix='.env.')
 try:
  os.fchmod(fd,0o600)
  with os.fdopen(fd,'w') as f:
   for k,v in values.items():
    if v is not None:f.write(k+'='+json.dumps(str(v))+'\n')
   f.flush();os.fsync(f.fileno())
  os.replace(tmp,p)
 finally:Path(tmp).unlink(missing_ok=True)
def main():
 p=argparse.ArgumentParser();s=p.add_subparsers(dest='command',required=True);s.add_parser('credentials');r=s.add_parser('resolve-seed');r.add_argument('decision',choices=['same','different']);s.add_parser('exchange-token');args=p.parse_args()
 c=Config.load();prepare_storage(c)
 if args.command=='credentials':
  if not sys.stdin.isatty():raise RuntimeError('Run this command in your local Terminal; credentials are entered using hidden prompts.')
  values={}
  for name in ('META_ACCESS_TOKEN','OPENAI_API_KEY','META_APP_SECRET'):
   value=getpass.getpass(name+' (hidden; Enter keeps current value): ').strip()
   if value:values[name]=value
  write_env(c.root,values);print('Credential file updated with permissions 600. No credential values printed.');return
 if args.command=='resolve-seed':
  videos=scan(c.video_folder);first=next((v for v in videos if v.number==1),None)
  if not first:raise RuntimeError('Numbered video 001 was not found.')
  Database(c.db_path).seed_match(args.decision,first);print('Seed match recorded:',args.decision,'; content hash:',first.sha256);return
 if args.command=='exchange-token':
  if not c.app_secret or not c.token:raise RuntimeError('Set META_APP_SECRET and the authorized short-lived META_ACCESS_TOKEN locally first.')
  from meta_client import MetaClient
  result=MetaClient(c).request('GET','oauth/access_token',params={'grant_type':'fb_exchange_token','client_id':c.app_id,'client_secret':c.app_secret,'fb_exchange_token':c.token})
  if not result.get('access_token'):raise RuntimeError('Token exchange did not return a token.')
  # Verify identity and expiry before replacing the original protected token.
  from dataclasses import replace
  check=MetaClient(replace(c,token=result['access_token'])).check_auth()
  if not check.get('valid') or not check.get('expiration_verified'):raise RuntimeError('Exchanged token was not verified; original token preserved.')
  write_env(c.root,{'META_ACCESS_TOKEN':result['access_token']});print('Verified long-lived user token saved privately. Facebook reauthorization may still be required after expiration/revocation.')
if __name__=='__main__':
 try:main()
 except Exception as e:print(json.dumps({'error':str(e) if isinstance(e,RuntimeError) else type(e).__name__}));raise SystemExit(1)
