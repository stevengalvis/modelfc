"""Publication ACL ordering/isolation plus real Linux cross-UID acceptance."""
import ctypes
import ctypes.util
from datetime import date
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from modelfc import corner_refresh as refresh
from modelfc.corner_data import CornerDataConfig
from tests.team_intelligence_data import HEADER, TODAY

def cross_uid_available():
    try:
        return all(any(int(start) <= 65533 and int(start)+int(length) > 65534
                       for start, _, length in (line.split() for line in Path(path).read_text().splitlines()))
                   for path in ('/proc/self/uid_map', '/proc/self/gid_map'))
    except OSError:
        return False


PAYLOAD=(HEADER+'E1,01/09/2026,Cardiff,Portsmouth,0,0,D,7,5\n').encode()


class PublicHistoryAclTests(unittest.TestCase):
    def test_acl_before_replace_and_only_e1(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);config=CornerDataConfig(root,('E1','SP1'),14)
            seen=[]
            def acl(staging,user):
                self.assertTrue(staging.exists())
                canonical=root/('E1_2627.csv' if staging.name.startswith('.E1_') else 'SP1_2627.csv')
                self.assertFalse(canonical.exists())
                seen.append((staging.name,user))
            with patch.object(refresh,'download_csv',side_effect=[PAYLOAD,PAYLOAD.replace(b'E1,',b'SP1,')]),patch.object(refresh,'prepare_validator_read',side_effect=acl):
                result=refresh.refresh_data(config,TODAY,validator_read_user='modelfc-validator',public_history_read_user='modelfc-api')
            self.assertTrue(all(r['status']=='updated' for r in result['results']))
            self.assertEqual([user for _,user in seen],['modelfc-validator','modelfc-api','modelfc-validator'])
            self.assertEqual(seen[0][0],seen[1][0])
            self.assertEqual(len([name for name,user in seen if user=='modelfc-api' and name.startswith('.E1_')]),1)

    def test_api_acl_failure_preserves_old_inode(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);target=root/'E1_2627.csv';target.write_bytes(PAYLOAD)
            before=target.stat().st_ino
            with patch.object(refresh,'prepare_validator_read',side_effect=[None,ValueError('ACL unavailable')]):
                with self.assertRaises(ValueError):refresh.atomic_write(target,PAYLOAD+b'\n',validator_read_user='modelfc-validator',public_history_read_user='modelfc-api')
            self.assertEqual(target.stat().st_ino,before)
            self.assertEqual(target.read_bytes(),PAYLOAD)
            self.assertEqual(list(root.iterdir()),[target])

    def test_invalid_acl_target_fails_closed(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):refresh.atomic_write(Path(tmp)/'SP1_2627.csv',PAYLOAD,public_history_read_user='modelfc-api')
            with self.assertRaises(ValueError):refresh.atomic_write(Path(tmp)/'E1_2627.csv',PAYLOAD,public_history_read_user='someone')

    def test_prior_season_read_acl_removed_without_listing(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'E1_2526.csv').write_bytes(b'old')
            with patch.object(refresh,'download_csv',return_value=PAYLOAD),patch.object(refresh.pwd,'getpwnam',return_value=type('User',(),{'pw_uid':1234})()),patch.object(refresh.subprocess,'run') as run,patch.object(refresh,'prepare_validator_read'):
                refresh.refresh_data(CornerDataConfig(root,('E1',),14),TODAY,public_history_read_user='modelfc-api')
            self.assertEqual(run.call_args.args[0],['/usr/bin/setfacl','-x','u:1234','--',str(root/'E1_2526.csv')])

    @unittest.skipUnless(os.geteuid()==0 and ctypes.util.find_library('acl') and cross_uid_available(), 'cross-UID ACL acceptance requires mapped UIDs, root and libacl')
    def test_real_atomic_replacement_cross_uid_isolation(self):
        acl=ctypes.CDLL(ctypes.util.find_library('acl'),use_errno=True)
        acl.acl_from_text.argtypes=[ctypes.c_char_p];acl.acl_from_text.restype=ctypes.c_void_p
        acl.acl_set_file.argtypes=[ctypes.c_char_p,ctypes.c_int,ctypes.c_void_p];acl.acl_set_file.restype=ctypes.c_int
        acl.acl_free.argtypes=[ctypes.c_void_p]
        def set_acl(path,text):
            value=acl.acl_from_text(text.encode())
            self.assertTrue(value)
            try:self.assertEqual(acl.acl_set_file(os.fsencode(path),0x8000,value),0,os.strerror(ctypes.get_errno()))
            finally:acl.acl_free(value)
        with TemporaryDirectory() as tmp:
            root=Path(tmp);set_acl(root,'u::rwx,u:65534:--x,u:65533:--x,g::---,m::--x,o::---')
            target=root/'E1_2627.csv';target.write_bytes(PAYLOAD);target.chmod(0o600)
            for name in ('SP1_2627.csv','E1_2526.csv','status.json','refresh-run.lock'):
                path=root/name;path.write_bytes(b'private');path.chmod(0o600)
            users=[]
            def real_acl(path,user):
                users.append(user)
                text='u::rw-,u:65533:r--,g::---,m::r--,o::---'
                if user=='modelfc-api':text='u::rw-,u:65533:r--,u:65534:r--,g::---,m::r--,o::---'
                set_acl(path,text)
            before=target.stat().st_ino
            with patch.object(refresh,'prepare_validator_read',side_effect=real_acl):
                refresh.atomic_write(target,PAYLOAD,validator_read_user='modelfc-validator',public_history_read_user='modelfc-api')
            self.assertNotEqual(target.stat().st_ino,before)
            self.assertEqual(users,['modelfc-validator','modelfc-api'])
            code='''import os,sys,json
root=sys.argv[1]; result={}
for name in ['E1_2627.csv','SP1_2627.csv','E1_2526.csv','status.json','refresh-run.lock']:
 try:
  with open(root+'/'+name,'rb') as f:f.read()
  result[name]=True
 except PermissionError:result[name]=False
try:os.listdir(root);result['listing']=True
except PermissionError:result['listing']=False
try:
 with open(root+'/E1_2627.csv','ab') as f:f.write(b'x')
 result['write']=True
except PermissionError:result['write']=False
print(json.dumps(result))'''
            actual=subprocess.run(['/usr/bin/python3','-c',code,tmp],user=65534,group=65534,extra_groups=[],capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(actual.stdout),{'E1_2627.csv':True,'SP1_2627.csv':False,'E1_2526.csv':False,'status.json':False,'refresh-run.lock':False,'listing':False,'write':False})
