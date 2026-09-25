"""Portal de archivos: demo local o Azure Blob con Managed Identity."""
import io
import os
import re
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from functools import lru_cache
from flask import Flask, jsonify, render_template, request, session, send_file
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

load_dotenv()
LIMIT = 10 * 1024 * 1024
ALLOWED = {'.pdf', '.txt', '.csv', '.png', '.jpg', '.jpeg', '.docx', '.xlsx', '.pptx'}
KEY = re.compile(r'^[0-9a-f]{32}__[A-Za-z0-9_.-]+$')

class StorageError(Exception):
    pass

class LocalStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
    def list(self):
        items = []
        for f in self.root.iterdir():
            if f.is_file() and KEY.fullmatch(f.name):
                s = f.stat()
                items.append(dict(id=f.name, name=f.name.split('__',1)[1], size=s.st_size,
                    modified=datetime.fromtimestamp(s.st_mtime, timezone.utc).isoformat()))
        return sorted(items, key=lambda x:x['modified'], reverse=True)[:1000]
    def upload(self, key, data):
        with (self.root / key).open('xb') as f: f.write(data)
    def download(self, key):
        f = self.root / key
        if not f.exists(): raise FileNotFoundError
        if f.stat().st_size > LIMIT: raise StorageError('El archivo supera los 10 MB permitidos.')
        return f.read_bytes()

class AzureStore:
    def __init__(self, account, container, cloud):
        from azure.identity import AzureCliCredential, ManagedIdentityCredential
        from azure.storage.blob import BlobServiceClient
        if not re.fullmatch(r'[a-z0-9]{3,24}', account):
            raise StorageError('Configurá AZURE_STORAGE_ACCOUNT con el nombre de tu cuenta.')
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])', container):
            raise StorageError('Configurá AZURE_STORAGE_CONTAINER con el nombre del contenedor.')
        # No usa claves de Storage ni secretos de aplicación.
        credential = ManagedIdentityCredential() if cloud else AzureCliCredential()
        self.container = BlobServiceClient(f'https://{account}.blob.core.windows.net',
            credential=credential, connection_timeout=10, read_timeout=20,
            retry_total=1).get_container_client(container)
    def list(self):
        items=[]
        for b in self.container.list_blobs(name_starts_with='archivos/'):
            key=b.name.removeprefix('archivos/')
            if KEY.fullmatch(key):
                items.append(dict(id=key, name=key.split('__',1)[1], size=b.size,
                    modified=b.last_modified.isoformat()))
            if len(items)>=1000: break
        return sorted(items,key=lambda x:x['modified'],reverse=True)
    def upload(self, key, data):
        from azure.storage.blob import ContentSettings
        self.container.upload_blob('archivos/'+key, data, overwrite=False,
            content_settings=ContentSettings(content_type='application/octet-stream'))
    def download(self,key):
        from azure.core.exceptions import ResourceNotFoundError
        try:
            blob=self.container.get_blob_client('archivos/'+key)
            # ETag prevents a changed blob exceeding the validated size.
            from azure.core import MatchConditions
            props=blob.get_blob_properties()
            if props.size > LIMIT: raise StorageError('El archivo supera los 10 MB permitidos.')
            return blob.download_blob(etag=props.etag, match_condition=MatchConditions.IfNotModified).readall()
        except ResourceNotFoundError as e: raise FileNotFoundError from e

def create_app(config=None):
    app=Flask(__name__)
    cloud=bool(os.getenv('WEBSITE_INSTANCE_ID') or os.getenv('WEBSITE_HOSTNAME'))
    app.config.update(CLOUD=cloud, STORAGE_MODE=os.getenv('STORAGE_MODE','azure'),
        SECRET_KEY=os.getenv('FLASK_SECRET_KEY') or secrets.token_hex(32),
        SECRET_CONFIGURED=bool(os.getenv('FLASK_SECRET_KEY')),
        ACCOUNT=os.getenv('AZURE_STORAGE_ACCOUNT',''),
        CONTAINER=os.getenv('AZURE_STORAGE_CONTAINER','documentos'),
        DATA_DIR=os.getenv('LOCAL_DATA_DIR',str(Path(__file__).parent / '.data')),
        MAX_CONTENT_LENGTH=LIMIT+1024*1024,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=cloud)
    if config: app.config.update(config)
    @lru_cache(maxsize=1)
    def store():
        if app.config['STORAGE_MODE']=='demo':
            return LocalStore(app.config['DATA_DIR'])
        if app.config['STORAGE_MODE']!='azure': raise StorageError('STORAGE_MODE debe ser azure o demo.')
        return AzureStore(app.config['ACCOUNT'],app.config['CONTAINER'],app.config['CLOUD'])
    def err(message, status): return jsonify(error=message),status
    @app.before_request
    def protect():
        if request.endpoint=='static': return
        if app.config['CLOUD']:
            if app.config['STORAGE_MODE']!='azure':
                return render_template('blocked.html', message='El modo demo es solo para uso local. Configurá STORAGE_MODE=azure.'),503
            if not app.config['SECRET_CONFIGURED']:
                return render_template('blocked.html', message='Falta configurar FLASK_SECRET_KEY en App Service.'),503
            if not request.headers.get('X-MS-CLIENT-PRINCIPAL-ID'):
                if request.path.startswith('/api/'):
                    return err('Iniciá sesión para acceder a los archivos.',401)
                return render_template('blocked.html', message='Iniciá sesión con tu cuenta autorizada. Si todavía no configuraste Authentication en App Service, completá ese paso primero.'),401
        elif request.remote_addr not in ('127.0.0.1','::1'):
            return err('La ejecución local solo permite conexiones desde este equipo.',403)
        if request.method=='POST':
            token=request.headers.get('X-CSRF-Token','')
            if not token or not secrets.compare_digest(token, session.get('csrf','')):
                return err('La sesión cambió. Actualizá la página e intentá de nuevo.',403)
    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='same-origin'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        response.headers['Cache-Control']='no-store'
        if app.config['CLOUD']: response.headers['Strict-Transport-Security']='max-age=31536000'
        return response
    @app.get('/')
    def index():
        session.setdefault('csrf',secrets.token_hex(32))
        return render_template('index.html', csrf=session['csrf'], demo=app.config['STORAGE_MODE']=='demo',
            cloud=app.config['CLOUD'], user=request.headers.get('X-MS-CLIENT-PRINCIPAL-NAME','Sesión local'))
    @app.get('/api/files')
    def files():
        return jsonify(files=store().list(), demo=app.config['STORAGE_MODE']=='demo')
    @app.post('/api/files')
    def upload():
        f=request.files.get('file')
        if not f or not f.filename: return err('Seleccioná un archivo.',400)
        name=secure_filename(f.filename)
        if not name or len(name)>160 or Path(name).suffix.lower() not in ALLOWED:
            return err('Usá PDF, TXT, CSV, PNG, JPG, DOCX, XLSX o PPTX (nombre de hasta 160 caracteres).',400)
        data=f.stream.read(LIMIT+1)
        if not data: return err('El archivo está vacío.',400)
        if len(data)>LIMIT: return err('El tamaño máximo por archivo es 10 MB.',413)
        key=uuid.uuid4().hex+'__'+name
        store().upload(key,data)
        return jsonify(message='Archivo guardado correctamente.',name=name),201
    @app.get('/api/files/<key>/download')
    def download(key):
        if not KEY.fullmatch(key): return err('Archivo no válido.',400)
        data=store().download(key)
        return send_file(io.BytesIO(data),mimetype='application/octet-stream',as_attachment=True,
            download_name=key.split('__',1)[1],max_age=0)
    @app.errorhandler(413)
    def large(e): return err('El tamaño máximo por archivo es 10 MB.',413)
    @app.errorhandler(FileNotFoundError)
    def missing(e): return err('El archivo ya no está disponible.',404)
    @app.errorhandler(StorageError)
    def setup(e): return err(str(e),503)
    @app.errorhandler(Exception)
    def failure(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e,HTTPException):return err(e.name,e.code)
        from azure.core.exceptions import HttpResponseError, ServiceRequestError, ClientAuthenticationError
        app.logger.exception('Error de operación de archivos')
        if isinstance(e,ClientAuthenticationError):return err('No se pudo identificar la aplicación. Revisá la identidad o el inicio de sesión local.',503)
        if isinstance(e,HttpResponseError) and e.status_code in (401,403):
            return err('Storage rechazó el acceso. Revisá permisos, identidad y restricciones de red.',403)
        if isinstance(e,HttpResponseError) and e.status_code==404:return err('No se encontró el contenedor. Revisá su nombre y que esté creado.',503)
        if isinstance(e,ServiceRequestError):return err('No se pudo conectar con Storage. Revisá la red y la resolución DNS.',503)
        return err('No se pudo completar la operación. Revisá los registros del servidor.',503)
    return app

app=create_app()
if __name__=='__main__': app.run(host='127.0.0.1',port=5000,debug=False)
