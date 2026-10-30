"""Only the Instagram LaunchAgent is managed; all YouTube jobs are untouched."""
import argparse,json,os,plistlib,subprocess,sys
from pathlib import Path
from datetime import datetime,timedelta
from config import Config,ROOT,prepare_storage,secure_json
from database import Database
LABEL='com.techat60.instagram-reels'
def system_timezone():
 try:return str(Path('/etc/localtime').resolve()).split('/zoneinfo/')[-1]
 except OSError:return 'unknown'
def definition(c,enabled=False):
 return dict(Label=LABEL,ProgramArguments=[str(c.root/'.venv/bin/python'),'-u',str(c.root/'publisher.py'),'--scheduled'],WorkingDirectory=str(c.root),StartCalendarInterval=dict(Hour=19,Minute=0),StartInterval=900,RunAtLoad=False,Disabled=not enabled,ProcessType='Background',StandardOutPath=str(c.root/'logs/launchd.stdout.log'),StandardErrorPath=str(c.root/'logs/launchd.stderr.log'),EnvironmentVariables=dict(PATH='/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',TZ='Asia/Kolkata'))
def run(args,check=True):return subprocess.run(args,check=check,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','install','activate','stop','start','status','uninstall']);p.add_argument('--approve-daily-publication',action='store_true');args=p.parse_args()
 c=Config.load();prepare_storage(c);source=c.root/'launchd'/f'{LABEL}.plist';target=Path.home()/'Library/LaunchAgents'/source.name;domain=f'gui/{os.getuid()}'
 source.parent.mkdir(exist_ok=True)
 if args.action in ('prepare','install','activate'):
  # TZ in EnvironmentVariables affects Python, not launchd's calendar trigger.
  if system_timezone() not in ('Asia/Kolkata','Asia/Calcutta'):raise RuntimeError('Mac timezone differs from IST. Set it to Asia/Kolkata and reinstall; no system setting is changed automatically.')
  if target.exists():
   existing=plistlib.loads(target.read_bytes())
   if existing.get('Label')!=LABEL or existing.get('WorkingDirectory')!=str(c.root):raise RuntimeError('Different existing Instagram job; inspect before replacing.')
  if args.action=='activate':
   if c.publish_backend!='mac':raise RuntimeError('Cloud backend selected. Disable cloud publishing before any local reactivation.')
   if not args.approve_daily_publication and not c.daily_approved():raise RuntimeError('Activation requires explicit approval: --approve-daily-publication')
   from publisher import check
   db=Database(c.db_path);report=check(c,db)
   if not report.get('meta',{}).get('valid') or not report.get('meta',{}).get('expiration_verified') or not report.get('existing_reel_verified'):raise RuntimeError('Meta authentication, token expiration and seed Reel verification must pass before activation.')
   next_slot=c.next_slot(used_today=db.used_day(c.now().date().isoformat()))
   for field in ('expires_at','data_access_expires_at'):
    expiry=report['meta'].get(field,0)
    if expiry and datetime.fromtimestamp(expiry,c.zone)<=next_slot+timedelta(minutes=c.grace_minutes):raise RuntimeError('Meta authorization expires before the first publishing window ends. Save a longer-lived token before activation.')
   if db.setting('seed_match')=='pending':raise RuntimeError('Resolve the existing Reel/file 001 match before activation.')
   if report.get('selection_hold'):raise RuntimeError(report['selection_hold'])
  enabled=args.action=='activate';source.write_bytes(plistlib.dumps(definition(c,enabled)));source.chmod(0o600);run(['/usr/bin/plutil','-lint',str(source)])
  if args.action in ('install','activate'):
   target.parent.mkdir(exist_ok=True)
   if args.action=='install' and c.active():raise RuntimeError('Already activated. Use stop before installing disabled settings.')
   target.write_bytes(source.read_bytes());target.chmod(0o600)
  if enabled:
   secure_json(c.activation,{'approved':True,'account_id':c.account_id,'approved_at':c.now().isoformat(),'schedule':'daily 19:00 Asia/Kolkata','missed_run_grace_minutes':c.grace_minutes})
   try:
    run(['/bin/launchctl','enable',domain+'/'+LABEL]);run(['/bin/launchctl','bootstrap',domain,str(target)])
   except subprocess.CalledProcessError:
    c.activation.unlink(missing_ok=True);raise RuntimeError('LaunchAgent activation failed; publishing approval marker removed.') from None
  print(json.dumps({'action':args.action,'installed':target.exists(),'active':c.active(),'loaded':run(['/bin/launchctl','print',domain+'/'+LABEL],False).returncode==0,'system_timezone':system_timezone(),'plist':str(source),'next_schedule_IST':c.next_slot(used_today=Database(c.db_path).used_day(c.now().date().isoformat())).isoformat()},indent=2));return
 if args.action=='status':
  result=run(['/bin/launchctl','print',domain+'/'+LABEL],False);print(json.dumps({'installed':target.exists(),'loaded':result.returncode==0,'approved':c.daily_approved() or c.active(),'active':c.active(),'timezone':system_timezone()},indent=2));return
 if args.action=='stop':
  c.activation.unlink(missing_ok=True);run(['/bin/launchctl','bootout',domain+'/'+LABEL],False);run(['/bin/launchctl','disable',domain+'/'+LABEL],False);print('Instagram schedule stopped.');return
 if args.action=='start':
  if not c.active():raise RuntimeError('Not approved/active. Use activate with explicit publishing approval first.')
  run(['/bin/launchctl','kickstart',domain+'/'+LABEL]);print('Instagram run requested; daily/time guards still apply.');return
 if args.action=='uninstall':
  c.activation.unlink(missing_ok=True);run(['/bin/launchctl','bootout',domain+'/'+LABEL],False)
  if target.exists():
   data=plistlib.loads(target.read_bytes())
   if data.get('Label')!=LABEL or data.get('WorkingDirectory')!=str(c.root):raise RuntimeError('Refusing to remove a different job.')
   target.unlink()
  print('Instagram LaunchAgent removed; videos and database preserved.')
if __name__=='__main__':
 try:main()
 except Exception as e:print(json.dumps({'error':str(e) if isinstance(e,RuntimeError) else type(e).__name__}));raise SystemExit(1)
