"""Read-only filename selection, content hashing and optional source metadata."""
from dataclasses import dataclass
from pathlib import Path
import hashlib,json,re,shutil,subprocess
@dataclass(frozen=True)
class Video:
 path:Path
 number:int
 topic:str
 sha256:str
 context:str=''
def sha(path):
 with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def scan(folder):
 result=[];seen=set()
 for p in sorted(Path(folder).glob('*.mp4'),key=lambda p:(int(re.match(r'^(\d+)',p.name)[1]) if re.match(r'^(\d+)',p.name) else 10**9,p.name)):
  match=re.match(r'^(\d+)\s*[-_ ]+(.+)\.mp4$',p.name,re.I)
  if not match:continue
  topic=match[2];topic=re.sub(r'\s+-\s+[\w-]{11}$','',topic);topic=re.split(r'\s+(?:Web Development in 60 Seconds|HTML for Beginners)\b',topic,flags=re.I)[0].strip(' -')
  digest=sha(p);context=''
  # This is a read-only Instagram input; never write to the YouTube archive.
  for metadata in (Path(folder)/'records').glob('*/youtube-metadata.json') if (Path(folder)/'records').exists() else []:
   pub=metadata.with_name('publication.json')
   try:
    if pub.exists() and json.loads(pub.read_text()).get('sha256')==digest:
     context=json.loads(metadata.read_text()).get('description','').split('Try it:')[0][:1100];break
   except (OSError,ValueError):continue
  if digest not in seen:result.append(Video(p.resolve(),int(match[1]),topic,digest,context));seen.add(digest)
 return sorted(result,key=lambda v:(v.number,v.path.name))
def select(videos,db):
 used=db.published_hashes()
 for video in videos:
  if video.sha256 in used:continue
  if video.number==1 and db.setting('seed_match')=='pending':raise RuntimeError('Video 001 is held: confirm whether it matches the existing HTML Reel using maintenance.py resolve-seed same|different.')
  return video
 return None

def validate_video(video):
 if sha(video.path)!=video.sha256:raise RuntimeError('Source video changed since selection.')
 if video.path.stat().st_size<=0 or video.path.stat().st_size>1_000_000_000:raise RuntimeError('MP4 must be nonempty and at most 1GB.')
 ffprobe=shutil.which('ffprobe')
 if not ffprobe:
  candidate=Path('/opt/homebrew/bin/ffprobe');ffprobe=str(candidate) if candidate.exists() else None
 if not ffprobe:raise RuntimeError('ffprobe is required for upload preflight but is unavailable.')
 try:r=subprocess.run([ffprobe,'-v','error','-show_streams','-show_format','-of','json',str(video.path)],capture_output=True,text=True,check=True,timeout=30);info=json.loads(r.stdout)
 except Exception:raise RuntimeError('Unable to validate the local MP4.') from None
 v=next((s for s in info['streams'] if s['codec_type']=='video'),None);a=next((s for s in info['streams'] if s['codec_type']=='audio'),None)
 if not v or v['codec_name'] not in ('h264','hevc') or not a or a['codec_name']!='aac':raise RuntimeError('Meta upload requires H264/HEVC video and AAC audio.')
 n,d=map(int,v['r_frame_rate'].split('/'));fps=n/d;duration=float(info['format']['duration'])
 if not 23<=fps<=60 or not 3<=duration<=900 or v['width']>1920 or not .01<=v['width']/v['height']<=10:raise RuntimeError('MP4 dimensions, frame rate or duration exceed documented Reels limits.')
 if int(a.get('sample_rate',48000))>48000 or int(a.get('channels',2))>2:raise RuntimeError('AAC sample rate/channels exceed documented Reels limits.')
 # Meta requests fast-start MP4 (moov before mdat), without edit lists.
 with video.path.open('rb') as f:
  position=0;atoms=[]
  while position<video.path.stat().st_size:
   f.seek(position);head=f.read(8)
   if len(head)!=8:break
   size=int.from_bytes(head[:4],'big');kind=head[4:8]
   if size==1:size=int.from_bytes(f.read(8),'big')
   if not size:break
   atoms.append(kind);position+=size
  if b'moov' not in atoms or b'mdat' not in atoms or atoms.index(b'moov')>atoms.index(b'mdat'):raise RuntimeError('MP4 must have its moov atom before mdat. Source will not be modified.')
 return {'duration':duration,'fps':fps,'width':v['width'],'height':v['height'],'bytes':video.path.stat().st_size,'audio_bitrate':a.get('bit_rate'),'video_bitrate':v.get('bit_rate')}

def prepare_upload(video,root):
 """Create a Meta-compatible derivative inside Instagram project only."""
 import os,tempfile
 validate_video(video)
 folder=Path(root)/'data/prepared';folder.mkdir(parents=True,exist_ok=True,mode=0o700);target=folder/(video.sha256+'.mp4')
 if target.exists():return target
 ffmpeg=shutil.which('ffmpeg') or ('/opt/homebrew/bin/ffmpeg' if Path('/opt/homebrew/bin/ffmpeg').exists() else None)
 if not ffmpeg:raise RuntimeError('ffmpeg is needed to prepare AAC 128k/no-edit-list upload media. Original files remain unchanged.')
 fd,name=tempfile.mkstemp(suffix='.mp4',dir=folder);os.close(fd)
 try:
  r=subprocess.run([ffmpeg,'-y','-v','error','-i',str(video.path),'-map','0:v:0','-map','0:a:0','-c:v','copy','-c:a','aac','-b:a','128k','-ar','48000','-ac','2','-movflags','+faststart','-use_editlist','0',name],capture_output=True,timeout=180)
  if r.returncode:raise RuntimeError('Instagram-local MP4 preparation failed; original unchanged.')
  os.chmod(name,0o600);os.replace(name,target)
 finally:Path(name).unlink(missing_ok=True)
 return target
