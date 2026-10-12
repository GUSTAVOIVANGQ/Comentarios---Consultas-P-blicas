"""Panel local sin dependencias web. Operaciones en procesos y bloqueo compartido.

Publicar solo en loopback o mediante túnel SSH. No exponer directamente a Internet.
"""
from __future__ import annotations
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
from dataclasses import asdict
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, quote
from filelock import FileLock, Timeout
from .config import Config
from .storage import Store, now_iso

log = logging.getLogger(__name__)
WEB = Path(__file__).with_name('web')
COMMANDS = {'run': ['run'], 'scan-all': ['scan-all'], 'sync-drive': ['sync-drive'], 'export': ['export'], 'retry': ['retry-downloads'], 'sync': ['sync'], 'bundle': ['bundle']}


def locked(cfg):
    lock=FileLock(str(cfg.lock_path),timeout=0)
    try:
        lock.acquire();lock.release()
        return False
    except Timeout:
        return True


def tail(path, lines=200):
    if not path.is_file(): return ''
    with path.open('rb') as f:
        f.seek(max(0,path.stat().st_size-256000))
        return '\n'.join(f.read().decode('utf-8',errors='replace').splitlines()[-lines:])


class Jobs:
    def __init__(self,cfg):
        self.cfg=cfg;self.mutex=threading.Lock();self.process=None
        store=Store(cfg.db_path)
        store.conn.execute('CREATE TABLE IF NOT EXISTS ui_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT, inicio TEXT, fin TEXT, status TEXT, code INTEGER)')
        store.conn.execute("UPDATE ui_jobs SET status='Interrumpida', fin=? WHERE status='En ejecución'",(now_iso(),))
        store.conn.commit();store.close()

    def start(self,action,ids=None):
        if action not in COMMANDS: raise ValueError('Acción desconocida')
        with self.mutex:
            if self.process is not None and self.process.poll() is None or locked(self.cfg):
                raise BlockingIOError('Ya existe una operación en curso. Esperar su finalización.')
            command=[sys.executable,'-m','crt_comments']+COMMANDS[action]
            if ids:
                if action!='run': raise ValueError('Solo extracción admite IDs')
                parts=ids.split(',')
                if len(parts)>100 or any(not re.fullmatch(r'[1-9]\d{0,8}',part.strip()) for part in parts):
                    raise ValueError('IDs inválidos. Usar 24,25,26 o dejar vacío para todas.')
                command+=['--only',','.join(str(int(part)) for part in parts)]
            environment=os.environ.copy()
            for name,value in asdict(self.cfg).items():
                if isinstance(value,bool):value='true' if value else 'false'
                if isinstance(value,Path):value=str(value.resolve())
                environment['CRT_'+name.upper()]=str(value)
            package_root=Path(__file__).resolve().parents[1]
            environment['PYTHONPATH']=str(package_root)+os.pathsep+environment.get('PYTHONPATH','')
            store=Store(self.cfg.db_path)
            try:
                cursor=store.conn.execute('INSERT INTO ui_jobs(action,inicio,status) VALUES (?,?,?)',(action,now_iso(),'En ejecución'))
                job_id=cursor.lastrowid;store.conn.commit()
            finally:store.close()
            try:
                self.process=subprocess.Popen(command,cwd=package_root,env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            except Exception:
                self._finish(job_id,-1);raise
            threading.Thread(target=self._wait,args=(self.process,job_id),daemon=True).start()
            log.info('UI | trabajo=%s | acción=%s | iniciado',job_id,action)
            return job_id

    def _wait(self,process,job_id):self._finish(job_id,process.wait())

    def _finish(self,job_id,code):
        store=Store(self.cfg.db_path)
        try:
            store.conn.execute('UPDATE ui_jobs SET fin=?,status=?,code=? WHERE id=?',
                               (now_iso(),'Completada' if code==0 else 'Revisar logs',code,job_id))
            store.conn.commit()
        finally:store.close()
        log.info('UI | trabajo=%s | finalizado | código=%s',job_id,code)

    def busy(self):return (self.process is not None and self.process.poll() is None) or locked(self.cfg)


def overview(cfg,jobs):
    store=Store(cfg.db_path)
    try:
        comments=store.conn.execute('SELECT COUNT(*) FROM comentarios').fetchone()[0]
        attachments=pending=0
        for row in store.conn.execute('SELECT adjuntos_json FROM comentarios'):
            for a in json.loads(row[0]):
                attachments+=1
                p=Path(a.get('local') or '')
                if not p.is_file() or not p.with_suffix(p.suffix+'.sha256').is_file():pending+=1
        return {'busy':jobs.busy(),'comments':comments,'attachments':attachments,'pending':pending,
                'consultas':[dict(r) for r in store.consultas_for_export()],
                'runs':[dict(r) for r in store.runs_for_export(15)],
                'jobs':[dict(r) for r in store.conn.execute('SELECT * FROM ui_jobs ORDER BY id DESC LIMIT 10')],
                'excel_ready':cfg.xlsx_path.is_file(),'bundle_ready':(cfg.data_dir/'paquete_local.zip').is_file(),
                'last_sync':store.get_meta('last_synced_at','Sin sincronizaciones'),
                'last_drive_sync':store.get_meta('last_drive_synced_at','Sin sincronizaciones'),
                'settings':{'sync_sheets':cfg.sync_sheets,'sync_drive':cfg.sync_drive,'interval_hours':cfg.interval_hours},
                'data_dir':str(cfg.data_dir.resolve())}
    finally:store.close()


class UIHandler(BaseHTTPRequestHandler):
    server_version='CRTPanel/3.0'
    def log_message(self,fmt,*args):log.debug('HTTP | '+fmt,*args)

    def allowed_host(self):
        try:
            host=urlsplit('http://'+self.headers.get('Host','')).hostname
            return host in ('localhost','127.0.0.1','::1')
        except ValueError:return False

    def session_valid(self):
        cookie=SimpleCookie()
        try:cookie.load(self.headers.get('Cookie',''))
        except Exception:return False
        return 'crt_session' in cookie and secrets.compare_digest(cookie['crt_session'].value,self.server.session)

    def send(self,status,data,ctype='application/json; charset=utf-8',cookie=False):
        if isinstance(data,(dict,list)):data=json.dumps(data,ensure_ascii=False).encode()
        elif isinstance(data,str):data=data.encode()
        self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'")
        if cookie:self.send_header('Set-Cookie',f'crt_session={self.server.session}; HttpOnly; SameSite=Strict; Path=/')
        self.end_headers();self.wfile.write(data)

    def send_file(self,path,name=None):
        if not path.is_file():return self.send(404,{'error':'El archivo todavía no está disponible'})
        name=name or path.name
        ctype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' if path.suffix=='.xlsx' else 'application/octet-stream'
        # Abrir antes de stat: una sustitución atómica no produce Content-Length inconsistente.
        with path.open('rb') as f:
            self.send_response(200);self.send_header('Content-Type',ctype)
            self.send_header('Content-Length',str(os.fstat(f.fileno()).st_size))
            self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(name,safe=''))
            self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
            shutil.copyfileobj(f,self.wfile)

    def do_GET(self):
        if not self.allowed_host():return self.send(403,{'error':'Abrir desde localhost o túnel SSH'})
        parsed=urlsplit(self.path);path=parsed.path;cfg=self.server.cfg;params=parse_qs(parsed.query)
        if path=='/':return self.send(200,(WEB/'index.html').read_bytes(),'text/html; charset=utf-8',cookie=True)
        if not self.session_valid():return self.send(401,{'error':'Abrir primero la página principal'})
        if path in ('/app.js','/style.css'):
            return self.send(200,(WEB/path[1:]).read_bytes(),'text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
        try:
            if path=='/api/state':return self.send(200,overview(cfg,self.server.jobs))
            if path=='/api/token':return self.send(200,{'token':self.server.csrf})
            if path=='/api/logs':
                return self.send(200,{'text':tail(cfg.log_dir/'crt_comments.log',min(1000,max(1,int(params.get('lines',['200'])[0]))))})
            if path=='/api/comments':
                page=max(1,int(params.get('page',['1'])[0]));size=30
                query=params.get('q',[''])[0][:200];cid=params.get('consulta',[''])[0]
                filters=['(autor LIKE ? OR comentario LIKE ? OR CAST(folio AS TEXT)=?)'];values=['%'+query+'%','%'+query+'%',query.lstrip('0') or '0']
                if cid:filters.append('consulta_id=?');values.append(int(cid))
                store=Store(cfg.db_path)
                try:
                    where=' AND '.join(filters)
                    total=store.conn.execute('SELECT COUNT(*) FROM comentarios WHERE '+where,values).fetchone()[0]
                    rows=[dict(r) for r in store.conn.execute('SELECT * FROM comentarios WHERE '+where+' ORDER BY folio LIMIT ? OFFSET ?',values+[size,(page-1)*size])]
                    for r in rows:r['folio_label']=f"{r['folio']:03d}";r['adjuntos']=json.loads(r.pop('adjuntos_json'))
                    return self.send(200,{'rows':rows,'total':total,'page':page,'pages':max(1,(total+size-1)//size)})
                finally:store.close()
            if path=='/download/excel':return self.send_file(cfg.xlsx_path)
            if path=='/download/bundle':return self.send_file(cfg.data_dir/'paquete_local.zip')
            match=re.fullmatch(r'/download/attachment/(\d+)/(\d+)',path)
            if match:
                store=Store(cfg.db_path)
                try:
                    row=store.conn.execute('SELECT adjuntos_json FROM comentarios WHERE folio=?',(int(match[1]),)).fetchone()
                    if not row:return self.send(404,{'error':'Comentario no encontrado'})
                    attachments=json.loads(row[0]);index=int(match[2])
                    if index>=len(attachments):return self.send(404,{'error':'Adjunto no encontrado'})
                    local=attachments[index].get('local','')
                    if not local:return self.send(404,{'error':'Adjunto pendiente de descarga'})
                    target=Path(local).resolve()
                    if not target.is_relative_to(cfg.attachments_dir.resolve()):return self.send(403,{'error':'Ruta fuera de adjuntos'})
                    return self.send_file(target)
                finally:store.close()
            return self.send(404,{'error':'Ruta desconocida'})
        except (ValueError,IndexError):return self.send(400,{'error':'Parámetros inválidos'})
        except (BrokenPipeError,ConnectionResetError):return
        except Exception:
            log.exception('UI | error consultando datos');return self.send(500,{'error':'No fue posible consultar; revisar logs'})

    def do_POST(self):
        if not self.allowed_host() or not self.session_valid():return self.send(403,{'error':'Sesión local requerida'})
        token=self.headers.get('X-CSRF-Token','')
        if not secrets.compare_digest(token,self.server.csrf):return self.send(403,{'error':'Actualizar la página y reintentar'})
        origin=self.headers.get('Origin')
        if origin and urlsplit(origin).netloc!=self.headers.get('Host'):return self.send(403,{'error':'Origen no permitido'})
        if self.path!='/api/jobs':return self.send(404,{'error':'Ruta desconocida'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=4096:raise ValueError('Solicitud demasiado grande o vacía')
            data=json.loads(self.rfile.read(size))
            if not isinstance(data,dict):raise ValueError('Solicitud inválida')
            job_id=self.server.jobs.start(data.get('action'),data.get('ids'))
            return self.send(202,{'id':job_id,'message':'Operación iniciada; revisar progreso en los logs'})
        except BlockingIOError as exc:return self.send(409,{'error':str(exc)})
        except (ValueError,TypeError,AttributeError) as exc:return self.send(400,{'error':str(exc)})
        except Exception:
            log.exception('UI | no se pudo iniciar operación');return self.send(500,{'error':'No se pudo iniciar. Revisar logs.'})


def make_server(cfg,host='127.0.0.1',port=8765):
    cfg.ensure_dirs()
    server=ThreadingHTTPServer((host,port),UIHandler)
    server.daemon_threads=True;server.cfg=cfg;server.jobs=Jobs(cfg)
    server.session=secrets.token_urlsafe(32);server.csrf=secrets.token_urlsafe(32)
    return server


def serve(cfg,host='127.0.0.1',port=8765):
    server=make_server(cfg,host,port)
    print(f'Panel CRT: http://127.0.0.1:{server.server_port}',flush=True)
    log.info('UI | lista | host=%s puerto=%s',host,server.server_port)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
