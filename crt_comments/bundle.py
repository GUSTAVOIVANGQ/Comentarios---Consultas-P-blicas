"""Paquete portable: XLSX, adjuntos y respaldo consistente de SQLite."""
import hashlib
import json
import logging
import sqlite3
import zipfile
from pathlib import Path
from filelock import FileLock
from .excel import export_excel
from .storage import Store, now_iso
from .reporting import local_file_status

log=logging.getLogger(__name__)

def create_bundle(cfg):
    cfg.ensure_dirs()
    output=cfg.data_dir/'paquete_local.zip'
    temporary=output.with_suffix('.zip.tmp')
    backup=cfg.data_dir/'backup_paquete.sqlite3.tmp'
    with FileLock(str(cfg.lock_path),timeout=0):
        store=Store(cfg.db_path)
        try:
            export_excel(store,cfg.xlsx_path,cfg)
            conn=sqlite3.connect(backup)
            try:store.conn.backup(conn)
            finally:conn.close()
            files=[(cfg.xlsx_path,'comentarios_crt.xlsx'),(backup,'crt_comentarios.sqlite3')]
            seen=set();pending=[]
            for row in store.comments_for_export():
                for att in json.loads(row['adjuntos_json']):
                    if local_file_status(att)[0]!='Local verificado':
                        pending.append({'folio':f"{row['folio']:03d}",'archivo':att['nombre']});continue
                    target=Path(att['local']).resolve()
                    if not target.is_relative_to(cfg.attachments_dir.resolve()):
                        raise ValueError('Adjunto fuera del directorio configurado')
                    if target in seen:continue
                    seen.add(target)
                    relative='adjuntos/'+target.relative_to(cfg.attachments_dir.resolve()).as_posix()
                    files.append((target,relative))
                    sidecar=target.with_suffix(target.suffix+'.sha256')
                    files.append((sidecar,relative+'.sha256'))
            manifest={'creado':now_iso(),'comentarios':len(store.comments_for_export()),'adjuntos_incluidos':len(seen),'pendientes':pending,'archivos':[]}
            with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,allowZip64=True) as archive:
                for path,name in files:
                    archive.write(path,name)
                    h=hashlib.sha256()
                    with path.open('rb') as f:
                        for chunk in iter(lambda:f.read(65536),b''):h.update(chunk)
                    manifest['archivos'].append({'ruta':name,'bytes':path.stat().st_size,'sha256':h.hexdigest()})
                archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
                archive.writestr('LEEME.txt','Extraer todo el ZIP en una misma carpeta. Abrir comentarios_crt.xlsx. Los enlaces relativos apuntan a adjuntos/.\nLos documentos pendientes aparecen en manifest.json y deben reintentarse en la instalación original.\nSQLite conserva folios y textos completos. No incluye credenciales.\n')
            temporary.replace(output)
            log.info('PAQUETE | guardado=%s | adjuntos=%s pendientes=%s bytes=%s',output.resolve(),len(seen),len(pending),output.stat().st_size)
            return output
        finally:
            store.close();backup.unlink(missing_ok=True);temporary.unlink(missing_ok=True)
