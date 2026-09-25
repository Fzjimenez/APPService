import io
import re
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import create_app, LIMIT

def client(tmp_path, **cfg):
    app=create_app(dict(TESTING=True,CLOUD=False,STORAGE_MODE='demo',DATA_DIR=str(tmp_path),**cfg))
    c=app.test_client()
    c.get('/')
    with c.session_transaction() as s: token=s['csrf']
    return c,token

def test_upload_list_download_duplicate_and_filename(tmp_path):
    c,t=client(tmp_path)
    for _ in range(2):
        r=c.post('/api/files',data={'file':(io.BytesIO(b'contenido'), '../../prueba.txt')},headers={'X-CSRF-Token':t})
        assert r.status_code==201
    rows=c.get('/api/files').json['files']
    assert len(rows)==2 and rows[0]['id']!=rows[1]['id']
    assert rows[0]['name']=='prueba.txt'
    r=c.get('/api/files/'+rows[0]['id']+'/download')
    assert r.data==b'contenido' and 'attachment' in r.headers['Content-Disposition']

def test_reject_csrf_extension_size_empty(tmp_path):
    c,t=client(tmp_path)
    assert c.post('/api/files',data={'file':(io.BytesIO(b'a'),'x.txt')}).status_code==403
    for name,data,code in [('a.exe',b'a',400),('a.txt',b'',400),('a.txt',b'a'*(LIMIT+1),413)]:
        assert c.post('/api/files',data={'file':(io.BytesIO(data),name)},headers={'X-CSRF-Token':t}).status_code==code
    assert c.get('/api/files').json['files']==[]
    assert c.get('/api/files/invalid/download').status_code==400

def test_cloud_blocks_anonymous_and_demo():
    a=create_app(dict(TESTING=True,CLOUD=True,STORAGE_MODE='azure',SECRET_CONFIGURED=True))
    assert a.test_client().get('/api/files').status_code==401
    a=create_app(dict(TESTING=True,CLOUD=True,STORAGE_MODE='demo',SECRET_CONFIGURED=True))
    assert a.test_client().get('/').status_code==503

def test_missing_storage_shows_configuration_message(tmp_path):
    a=create_app(dict(TESTING=True,CLOUD=False,STORAGE_MODE='azure',ACCOUNT=''))
    r=a.test_client().get('/api/files')
    assert r.status_code==503 and 'AZURE_STORAGE_ACCOUNT' in r.json['error']

def test_azure_adapter_uses_prefix_and_no_overwrite(monkeypatch):
    from app import AzureStore
    from types import SimpleNamespace
    class Container:
        def list_blobs(self, **kw):
            assert kw['name_starts_with']=='archivos/'
            from datetime import datetime,timezone
            return [SimpleNamespace(name='archivos/'+'a'*32+'__test.txt',size=3,last_modified=datetime.now(timezone.utc))]
        def upload_blob(self,*args,**kw):
            assert args==('archivos/'+'b'*32+'__test.txt',b'abc')
            assert kw['overwrite'] is False
    s=AzureStore.__new__(AzureStore);s.container=Container()
    assert s.list()[0]['name']=='test.txt'
    s.upload('b'*32+'__test.txt',b'abc')
