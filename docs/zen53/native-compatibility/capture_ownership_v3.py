"""Bounded read-only capture of two pinned Codex sources; no namespace/mount creation."""
import os,sys,json,hashlib,stat,time,datetime,re,struct,ctypes,signal,errno,posixpath,select
SOURCES={
'/opt/codex/bin/codex':('/root/.codex/packages/standalone/releases/0.162.1-x86_64-unknown-linux-musl/bin/codex',294415048,'628e35487d888a28f0427fc37501da529dd3d81fcbad7a2c3421c1e8670d92e1'),
'/opt/codex/codex-package.json':('/root/.codex/packages/standalone/releases/0.162.1-x86_64-unknown-linux-musl/codex-package.json',205,'f05ebff6db98bd613dd5654461985090c6416b41ad67902243d83a53b305ac05')}
FLAGS=os.O_PATH|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC
libc=ctypes.CDLL(None,use_errno=True)
libc.statx.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_uint,ctypes.c_void_p];libc.statx.restype=ctypes.c_int
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def digest(b):return hashlib.sha256(b).hexdigest()
def limited(path,limit=1048576):
 fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC)
 try:
  data=os.read(fd,limit+1)
  if len(data)>limit:raise ValueError('METADATA_LIMIT:'+path)
  return data.decode('ascii')
 finally:os.close(fd)
def error(e):return {'type':type(e).__name__,'errno':getattr(e,'errno',None),'message':str(e)}
def meta(s):return {'device':s.st_dev,'inode':s.st_ino,'uid':s.st_uid,'gid':s.st_gid,'mode':oct(stat.S_IMODE(s.st_mode)),'size':s.st_size,'nlink':s.st_nlink,'atime_ns':s.st_atime_ns,'mtime_ns':s.st_mtime_ns,'ctime_ns':s.st_ctime_ns}
def context():
 p={n:os.readlink('/proc/'+n) for n in ['self/ns/user','self/ns/mnt','self/ns/pid','1/ns/user','1/ns/mnt','1/ns/pid']}
 return {'observed_at_utc':now(),'pid':os.getpid(),'uid':os.getuid(),'gid':os.getgid(),'namespace_links':p,'uid_map_raw':limited('/proc/self/uid_map',4096),'gid_map_raw':limited('/proc/self/gid_map',4096),'overflow_uid_raw':limited('/proc/sys/kernel/overflowuid',128),'overflow_gid_raw':limited('/proc/sys/kernel/overflowgid',128),'boot_id_raw':limited('/proc/sys/kernel/random/boot_id',128),'kernel_release_raw':limited('/proc/sys/kernel/osrelease',256),'machine':os.uname().machine}
def unescape(s):return re.sub(r'\\([0-7]{3})',lambda m:chr(int(m[1],8)),s)
def mounts():
 raw=limited('/proc/self/mountinfo')
 rows=[]
 for line in raw.splitlines():
  a,b=line.split(' - ',1);v=a.split();z=b.split()
  rows.append({'id':int(v[0]),'parent':int(v[1]),'device':v[2],'root':unescape(v[3]),'mountpoint':unescape(v[4]),'options':v[5].split(','),'optional':v[6:],'fstype':z[0],'source':unescape(z[1]),'super_options':z[2].split(','),'raw':line})
 return {'raw':raw,'sha256':digest(raw.encode()),'rows':rows}
def fdinfo(fd):return limited('/proc/self/fdinfo/'+str(fd),16384)
def mount_observation(fd,table):
 raw=fdinfo(fd);m=re.search(r'^mnt_id:\s*(\d+)$',raw,re.M)
 if not m:raise ValueError('MISSING_FD_MOUNT_ID')
 old=int(m[1]);row=[r for r in table['rows'] if r['id']==old]
 if len(row)!=1:raise ValueError('FD_MOUNT_NOT_IN_TABLE')
 s=os.fstat(fd)
 if row[0]['device']!=str(os.major(s.st_dev))+':'+str(os.minor(s.st_dev)):raise ValueError('FD_MOUNT_DEVICE_MISMATCH')
 out={'fdinfo_raw':raw,'mount_id':old,'mountinfo_row':row[0],'statx':{},'statmount':{},'idmapped':None}
 buf=ctypes.create_string_buffer(256);ctypes.set_errno(0)
 rc=libc.statx(fd,b'',0x1000|0x100|0x4000,0x4000,ctypes.byref(buf))
 out['statx']={'return_code':rc,'errno':ctypes.get_errno(),'request_mask':0x4000,'flags':0x1000|0x100|0x4000}
 if rc!=0:return out
 mask=struct.unpack_from('<I',buf.raw,0)[0];unique=struct.unpack_from('<Q',buf.raw,144)[0]
 out['statx'].update(returned_mask=mask,unique_mount_id=unique,raw_hex=buf.raw.hex())
 if not mask&0x4000:return out
 req=ctypes.create_string_buffer(struct.pack('<IIQQ',24,0,unique,2));sb=ctypes.create_string_buffer(512);ctypes.set_errno(0)
 rc=libc.syscall(ctypes.c_long(457),ctypes.byref(req),ctypes.byref(sb),ctypes.c_size_t(512),ctypes.c_uint(0))
 out['statmount']={'syscall':457,'request_hex':req.raw[:24].hex(),'buffer_bytes':512,'flags':0,'return_code':rc,'errno':ctypes.get_errno()}
 if rc!=0:return out
 returned=struct.unpack_from('<Q',sb.raw,8)[0];mid=struct.unpack_from('<Q',sb.raw,40)[0];old_returned=struct.unpack_from('<I',sb.raw,56)[0];attrs=struct.unpack_from('<Q',sb.raw,64)[0]
 out['statmount'].update(returned_mask=returned,unique_mount_id=mid,mount_id_old=old_returned,mount_attributes=attrs,raw_hex=sb.raw.hex())
 if returned&2 and mid==unique and old_returned==old:out['idmapped']=bool(attrs&0x100000)
 return out
def walk(path):
 parts=path.split('/')[1:];d=os.open('/',FLAGS);held=[('/',d)];prefix=''
 for n in parts[:-1]:
  prefix+='/'+n;d=os.open(n,FLAGS,dir_fd=d);held.append((prefix,d))
 f=os.open(parts[-1],os.O_PATH|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=d)
 if not stat.S_ISREG(os.fstat(f).st_mode):raise ValueError('NOT_REGULAR:'+path)
 return held,f
def binding(path,held,fd):
 named=os.stat(path,follow_symlinks=False);f=os.fstat(fd)
 if meta(named)!=meta(f):raise ValueError('FILE_PATH_BINDING_CHANGED:'+path)
 ancestors=[]
 for p,h in held:
  a=os.fstat(h);n=os.stat(p,follow_symlinks=False)
  keys=('st_dev','st_ino','st_mode','st_uid','st_gid')
  if any(getattr(a,k)!=getattr(n,k) for k in keys):raise ValueError('ANCESTOR_BINDING_CHANGED:'+p)
  ancestors.append({'path':p,'metadata':meta(a),'fdinfo_raw':fdinfo(h)})
 return {'descriptor':meta(f),'named':meta(named),'ancestors':ancestors}
def contains(base,p):return p==base or p.startswith(base.rstrip('/')+'/')
def aliases(path,fd,mount,table):
 selected=mount['mountinfo_row']
 if not contains(selected['mountpoint'],path):raise ValueError('MOUNTPOINT_DOES_NOT_CONTAIN_SOURCE')
 relative=posixpath.relpath(path,selected['mountpoint']);fs_path=posixpath.normpath(posixpath.join(selected['root'],relative))
 result=[];confirmed=0;unknown=0
 for row in table['rows']:
  if row['id']==selected['id'] or row['device']!=selected['device'] or not contains(row['root'],fs_path):continue
  candidate=posixpath.normpath(posixpath.join(row['mountpoint'],posixpath.relpath(fs_path,row['root'])))
  record={'candidate_mount_row':row,'derived_path':candidate}
  if candidate==path:
   record['state']='SAME_PATH_OBSCURED_BY_SELECTED_MOUNT'
  else:
   hs=[];af=None
   try:
    hs,af=walk(candidate);a=os.fstat(af);same=(a.st_dev,a.st_ino)==(os.fstat(fd).st_dev,os.fstat(fd).st_ino)
    am=mount_observation(af,table);record.update(observed_metadata=meta(a),observed_mount_id=am['mount_id'])
    if same:record['state']='CONFIRMED_ADDITIONAL_VISIBLE_ALIAS';confirmed+=1
    else:record['state']='OBSCURED_DIFFERENT_OBJECT'
   except OSError as e:
    record.update(state='UNRESOLVED_ALIAS_ROUTE',error=error(e));unknown+=1
   finally:
    if af is not None:os.close(af)
    for _,h in reversed(hs):os.close(h)
  result.append(record)
 return {'underlying_fs_path':fs_path,'candidate_routes':result,'confirmed_alias_count':confirmed,'unresolved_route_count':unknown,'alias_count':confirmed if unknown==0 else None,'scope':'Additional visible mount routes in this observed mount namespace; not a global all-process path claim. Same-path covered mount routes are recorded separately.'}
def stable_mount(a,b):
 return a['mount_id']==b['mount_id'] and a['mountinfo_row']==b['mountinfo_row'] and a['statx']==b['statx'] and a['statmount']==b['statmount'] and a['idmapped']==b['idmapped']
def save(root,name,x):
 b=(json.dumps(x,indent=2,sort_keys=True)+'\n').encode();f=os.open(root+'/'+name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(f,'wb') as o:o.write(b);o.flush();os.fsync(o.fileno())
 return digest(b)
def wait_finish(fd=0, max_seconds=180, clock_ns=None, select_fn=None,
                read_fn=None, get_blocking=None, set_blocking=None):
 """Bound exact finish line on a nonblocking descriptor and one monotonic clock.

 Return fixed codes only; never emit received bytes. A separately reviewed
 sender must prove restricted capture completed before it sent finish.
 """
 clock_ns = clock_ns or time.monotonic_ns
 select_fn = select_fn or select.select
 read_fn = read_fn or os.read
 get_blocking = get_blocking or os.get_blocking
 set_blocking = set_blocking or os.set_blocking
 if type(max_seconds) is not int or not 0 < max_seconds <= 180:
  raise ValueError('FINISH_WAIT_LIMIT')
 before = get_blocking(fd)
 start = clock_ns()
 deadline = start + max_seconds * 1000000000
 data = bytearray()
 set_blocking(fd, False)
 try:
  while True:
   remaining = deadline - clock_ns()
   if remaining <= 0:
    return {'code':'FINISH_PARTIAL_DEADLINE' if data else 'FINISH_DEADLINE',
            'wait_started_monotonic_ns':start}
   try:
    ready = select_fn([fd], [], [], remaining / 1000000000)[0]
   except InterruptedError:
    continue
   except OSError:
    return {'code':'FINISH_SELECT_ERROR','wait_started_monotonic_ns':start}
   if not ready:
    return {'code':'FINISH_PARTIAL_NO_READY' if data else 'FINISH_NO_READY',
            'wait_started_monotonic_ns':start}
   try:
    chunk = read_fn(fd, 16 - len(data))
   except (BlockingIOError, InterruptedError):
    continue
   except OSError:
    return {'code':'FINISH_READ_ERROR','wait_started_monotonic_ns':start}
   if type(chunk) is not bytes:
    return {'code':'FINISH_READ_TYPE','wait_started_monotonic_ns':start}
   observed = clock_ns()
   if observed >= deadline:
    return {'code':'FINISH_LATE_INPUT' if chunk else
            ('FINISH_PARTIAL_DEADLINE' if data else 'FINISH_DEADLINE'),
            'wait_started_monotonic_ns':start}
   if not chunk:
    return {'code':'FINISH_PARTIAL_EOF' if data else 'FINISH_EOF',
            'wait_started_monotonic_ns':start}
   data.extend(chunk)
   if b'\n' in data:
    if bytes(data) == b'finish\n':
     return {'code':'FINISH_ACK','wait_started_monotonic_ns':start,
             'finish_received_monotonic_ns':observed}
    return {'code':'FINISH_NONMATCHING','wait_started_monotonic_ns':start}
   if len(data) >= 16:
    return {'code':'FINISH_OVERLONG','wait_started_monotonic_ns':start}
 finally:
  set_blocking(fd, before)

def timeout_handler(signum,frame):raise TimeoutError('SOURCE_READ_DEADLINE_30_SECONDS')
signal.signal(signal.SIGALRM,timeout_handler)
label=sys.argv[1];output={'version':1,'context_label':label,'started_at_utc':now(),'collector_path':os.path.abspath(__file__),'context_before':context(),'mountinfo_before':mounts(),'files':{},'blockers':[],'historical_observation_context':'UNKNOWN'}
held_files=[]
try:
 for target,(path,size,pin) in SOURCES.items():
  held,fd=walk(path);held_files.append((target,path,held,fd,None))
  before=binding(path,held,fd);mo=mount_observation(fd,output['mountinfo_before']);record={'source':path,'expected_size':size,'expected_sha256':pin,'binding_before':before,'mount_before':mo,'read_limit_bytes':size+1,'read_deadline_seconds':30,'read_started_at_utc':now()}
  output['files'][target]=record
  if before['descriptor']['size']!=size:raise ValueError('SOURCE_SIZE_MISMATCH:'+target)
  if before['descriptor']['nlink']!=1:raise ValueError('SOURCE_LINK_COUNT:'+target)
  content_fd=None
  try:
   try:
    content_fd=os.open(path.rsplit('/',1)[1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC|os.O_NOATIME,dir_fd=held[-1][1])
    record['read_strategy']='O_NOATIME'
   except OSError as e:
    record['noatime_open_error']=error(e)
    readonly=mo['statmount'].get('returned_mask',0)&2 and mo['statmount'].get('mount_attributes',0)&1 and 'ro' in mo['mountinfo_row']['options']
    if e.errno!=errno.EPERM or not readonly:raise
    content_fd=os.open(path.rsplit('/',1)[1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=held[-1][1])
    record['read_strategy']='READ_ONLY_KERNEL_MOUNT_CONFIRMED_BEFORE_AND_AFTER'
   held_files[-1]=(target,path,held,fd,content_fd)
   if meta(os.fstat(content_fd))!=before['descriptor']:raise ValueError('CONTENT_DESCRIPTOR_BINDING')
   h=hashlib.sha256();count=0;signal.alarm(30)
   while count<=size:
    b=os.read(content_fd,min(1048576,size+1-count))
    if not b:break
    h.update(b);count+=len(b)
   signal.alarm(0);record.update(sha256=h.hexdigest(),bytes_hashed=count,read_completed_at_utc=now())
   if count!=size or h.hexdigest()!=pin:raise ValueError('SOURCE_HASH:'+target)
  except Exception as e:
   signal.alarm(0);record['read_error']=error(e);output['blockers'].append({'target':target,'gate':'BOUNDED_READ','error':error(e)})
  record['aliases']=aliases(path,fd,mo,output['mountinfo_before'])
 if label=='host-held':
  root=sys.argv[2];save(root,'HOST_CAPTURE_BEFORE.json',output)
  output['host_ready_monotonic_ns']=time.monotonic_ns()
  print(json.dumps({'event':'HOST_DESCRIPTORS_HELD','pid':os.getpid(),'files':{t:v['binding_before']['descriptor'] for t,v in output['files'].items()},'max_hold_seconds':180,'host_ready_monotonic_ns':output['host_ready_monotonic_ns']}),flush=True)
  output['finish_control']=wait_finish()
  if output['finish_control']['code']=='FINISH_ACK':
   print(json.dumps({'event':'HOST_FINISH_ACK','finish_received_monotonic_ns':output['finish_control']['finish_received_monotonic_ns']}),flush=True)
  else:
   output['blockers'].append({'gate':'HOST_BRACKET','error':output['finish_control']['code']})
 output['context_after']=context();output['mountinfo_after']=mounts()
 for target,path,held,fd,content_fd in held_files:
  record=output['files'][target];record['binding_after']=binding(path,held,fd);record['mount_after']=mount_observation(fd,output['mountinfo_after'])
  record['stable_descriptor_and_named_metadata']=record['binding_before']['descriptor']==record['binding_after']['descriptor'] and (content_fd is None or meta(os.fstat(content_fd))==record['binding_after']['descriptor'])
  record['stable_mount_observation']=stable_mount(record['mount_before'],record['mount_after'])
  if not record['stable_descriptor_and_named_metadata'] or not record['stable_mount_observation']:output['blockers'].append({'target':target,'gate':'SOURCE_OR_MOUNT_CHANGED'})
  if record['mount_after']['idmapped'] is not False:output['blockers'].append({'target':target,'gate':'IDMAP_UNKNOWN_OR_TRUE'})
  if record['aliases']['alias_count']!=0:output['blockers'].append({'target':target,'gate':'MOUNT_ALIAS_OR_UNKNOWN'})
 if output['mountinfo_before']['sha256']!=output['mountinfo_after']['sha256']:output['blockers'].append({'gate':'MOUNTINFO_CHANGED_DURING_CAPTURE'})
 keys=['namespace_links','uid_map_raw','gid_map_raw','overflow_uid_raw','overflow_gid_raw','boot_id_raw']
 if any(output['context_before'][k]!=output['context_after'][k] for k in keys):output['blockers'].append({'gate':'NAMESPACE_OR_BOOT_CHANGED'})
except Exception as e:output['blockers'].append({'gate':'CAPTURE_EXCEPTION','error':error(e)})
finally:
 for _,_,held,fd,content_fd in held_files:
  if content_fd is not None:os.close(content_fd)
  os.close(fd)
  for _,h in reversed(held):os.close(h)
if label=='host-held':output['held_descriptors_closed_monotonic_ns']=time.monotonic_ns()
output['completed_at_utc']=now()
if label=='host-held':
 sh=save(sys.argv[2],'HOST_CAPTURE.json',output);print(json.dumps({'event':'HOST_CAPTURE_COMPLETE','sha256':sh,'blockers':output['blockers'],'completed_at_utc':output['completed_at_utc']}),flush=True)
else:print(json.dumps(output,sort_keys=True),flush=True)
sys.exit(0 if not output['blockers'] else 2)
