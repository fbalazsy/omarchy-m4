#!/usr/bin/python3 -B
"""Passive, bounded touchpad trace. No grab, reset, keyboard capture or movement cue."""
import fcntl,hashlib,json,os,platform,select,signal,stat,struct,sys,time,uuid
from pathlib import Path
EVENT=struct.Struct('=qqHHi');ABS=struct.Struct('=iiiiii')
NAMES={'Apple MTP multi-touch','Apple DockChannel Multi-touch'}
AXES={'x':0,'y':1,'slot':0x2f,'mt_x':0x35,'mt_y':0x36,'tracking_id':0x39}
TOUCH_KEYS={0x140,0x141,0x142,0x143,0x144,0x145,0x146,0x147,0x148,0x149,0x14a,0x14d,0x14e,0x14f}
MAX_EVENTS=65536;DURATION=60
STOP_REQUESTED=False
def stop_requested(signum,frame):
 global STOP_REQUESTED
 STOP_REQUESTED=True

def ioc(read,number,size):return ((2 if read else 1)<<30)|(size<<16)|(ord('E')<<8)|number

def get_abs(fd,axis):
 raw=bytearray(ABS.size);fcntl.ioctl(fd,ioc(True,0x40+axis,len(raw)),raw,True)
 return dict(zip(('value','minimum','maximum','fuzz','flat','resolution'),ABS.unpack(raw)))

def snapshot(fd):
 result={'atomic':False,'axes':{},'slot_state':{},'errors':[]}
 for name,code in AXES.items():
  try:result['axes'][name]=get_abs(fd,code)
  except OSError as e:result['errors'].append(name+': '+str(e))
 slot=result['axes'].get('slot',{})
 count=slot.get('maximum',-1)+1
 if slot.get('minimum')!=0 or not 1<=count<=32:result['errors'].append('slot range unsupported');return result
 for name in ('tracking_id','mt_x','mt_y'):
  raw=bytearray(struct.pack('='+str(count+1)+'i',AXES[name],*([0]*count)))
  try:
   fcntl.ioctl(fd,ioc(True,0x0a,len(raw)),raw,True)
   result['slot_state'][name]=list(struct.unpack('='+str(count+1)+'i',raw)[1:])
  except OSError as e:result['errors'].append(name+' slots: '+str(e))
 return result


def filtered(chunk):
 if len(chunk)%EVENT.size:raise ValueError('partial input_event record')
 records=[]
 for sec,usec,kind,code,value in EVENT.iter_unpack(chunk):
  if sec<0 or not 0<=usec<1000000:raise ValueError('invalid monotonic input timestamp')
  if kind==0 or (kind==3 and code<=0x3f) or (kind==1 and code in TOUCH_KEYS) or (kind==4 and code==5):
   records.append((sec,usec,kind,code,value))
 return records


def summary(raw):
 if len(raw)%EVENT.size:raise ValueError('partial trace')
 rows=list(EVENT.iter_unpack(raw));frames=[s*1000000+u for s,u,k,c,v in rows if k==0 and c==0]
 deltas=[b-a for a,b in zip(frames,frames[1:])]
 return {'records':len(rows),'syn_reports':len(frames),
         'client_syn_dropped':sum(k==0 and c==3 for s,u,k,c,v in rows),
         'frame_interval_us':{'count':len(deltas),'minimum':min(deltas) if deltas else None,
                              'maximum':max(deltas) if deltas else None,
                              'nonpositive':sum(v<=0 for v in deltas),'under_1ms':sum(0<v<1000 for v in deltas)},
         'msc_timestamp_records':sum(k==4 and c==5 for s,u,k,c,v in rows),
         'movement_or_transport_quality_pass':None}


def read_stream(fd,duration=DURATION):
 start=time.monotonic_ns();end=start+int(duration*1000000000)
 data=bytearray();reads=[];stop='deadline';buffer=b''
 while time.monotonic_ns()<end and not STOP_REQUESTED:
  if not select.select([fd],[],[],min(1,max(0,(end-time.monotonic_ns())/1e9)))[0]:continue
  try:chunk=os.read(fd,EVENT.size*256)
  except BlockingIOError:continue
  if not chunk:stop='device_closed';break
  received=time.monotonic_ns();buffer+=chunk
  usable=len(buffer)//EVENT.size*EVENT.size
  records=filtered(buffer[:usable]);buffer=buffer[usable:]
  available=MAX_EVENTS-len(data)//EVENT.size
  if len(records)>available:records=records[:available];stop='record_limit'
  reads.append([received,len(data)//EVENT.size,len(records)])
  for record in records:data.extend(EVENT.pack(*record))
  if stop=='record_limit' or len(data)==MAX_EVENTS*EVENT.size:stop='record_limit';break
  if len(reads)>=8192:stop='read_limit';break
 finish=time.monotonic_ns()
 if STOP_REQUESTED:stop='stopped'
 if buffer:stop='partial_event'
 return bytes(data),reads,start,finish,stop


def capture():
 sys.path.insert(0,'/usr/lib/m4');import m4host;m4host.require()
 if os.geteuid()!=0 or platform.system()!='Linux' or platform.machine()!='aarch64':raise ValueError('native collector only')
 boot=str(uuid.UUID(Path('/proc/sys/kernel/random/boot_id').read_text().strip()))
 candidates=[]
 for node in Path('/sys/class/input').glob('event*'):
  try:
   if (node/'device/name').read_text().strip() in NAMES:candidates.append(node)
  except FileNotFoundError:continue
 if len(candidates)!=1:raise ValueError('exactly one named Apple MTP touchpad required')
 node=candidates[0];path=Path('/dev/input')/node.name
 major,minor=map(int,(node/'dev').read_text().split(':'))
 fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_CLOEXEC|os.O_NOFOLLOW)
 output=Path('/run')/('m4-trackpad-trace-'+boot)
 try:
  st=os.fstat(fd)
  if not stat.S_ISCHR(st.st_mode) or st.st_rdev!=os.makedev(major,minor):raise ValueError('evdev identity changed')
  name=bytearray(256);fcntl.ioctl(fd,ioc(True,6,len(name)),name,True)
  if name.split(b'\0',1)[0].decode()!= (node/'device/name').read_text().strip():raise ValueError('descriptor name changed')
  ident=bytearray(8);fcntl.ioctl(fd,ioc(True,2,len(ident)),ident,True)
  # This ioctl changes only this descriptor's event clock, not hardware or other readers.
  fcntl.ioctl(fd,ioc(False,0xa0,4),struct.pack('=i',time.CLOCK_MONOTONIC))
  output.mkdir(mode=0o700)
  initial=snapshot(fd)
  raw,reads,start,finish,stop=read_stream(fd)
  stats=summary(raw)
  complete=stop=='deadline' and not initial['errors'] and stats['client_syn_dropped']==0
  metadata={'schema':1,'boot_id':boot,'device':path.name,'name':name.split(b'\0',1)[0].decode(),
            'input_id':list(struct.unpack('=HHHH',ident)),'start_monotonic_ns':start,'end_monotonic_ns':finish,
            'event_clock':'CLOCK_MONOTONIC','initial_snapshot':initial,'reads':reads,'stop_reason':stop,
            'complete_capture':complete,'trace_bytes':len(raw),'trace_sha256':hashlib.sha256(raw).hexdigest(),
            'event_layout':'little-endian signed64 seconds,signed64 microseconds,u16 type,u16 code,s32 value',
            'keyboard_captured':False,'exclusive_grab':False,'reset_attempted':False,**stats}
  (output/'touchpad.events').write_bytes(raw)
  (output/'touchpad.json').write_text(json.dumps(metadata,indent=2)+'\n')
  return {'status':('CAPTURE_COMPLETE' if stats['syn_reports'] else 'NO_TOUCHPAD_FRAMES') if complete else 'CAPTURE_INCOMPLETE','directory':str(output),
          'boot_id':boot,'trace_bytes':len(raw),'trace_sha256':metadata['trace_sha256'],
          'metadata_sha256':hashlib.sha256((output/'touchpad.json').read_bytes()).hexdigest(),
          'stop_reason':stop,**stats,
          'scope':'Passive touchpad events during ordinary use. No movement instruction or quality pass. SYN_DROPPED concerns this reader only; firmware timestamps absent unless explicit MSC_TIMESTAMP is captured.'}
 finally:os.close(fd)

if __name__=='__main__':
 signal.signal(signal.SIGTERM,stop_requested)
 try:
  if sys.argv[1:]:raise ValueError('no arguments; collector owns the fixed bounded path')
  result=capture()
 except (OSError,ValueError) as e:result={'status':'CAPTURE_REFUSED','error':str(e)}
 print(json.dumps(result));raise SystemExit(result['status']=='CAPTURE_REFUSED')
