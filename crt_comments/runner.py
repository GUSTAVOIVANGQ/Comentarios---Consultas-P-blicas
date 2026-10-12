"""Coordinación de corrida, validación, persistencia y salidas recuperables."""
from __future__ import annotations
import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from filelock import FileLock, Timeout
from playwright.sync_api import Error as PlaywrightError
from .attachments import download_missing, make_session
from .browser import BrowserSession
from .config import MX_TZ
from .discovery import discover, CatalogIncomplete, plan_ids
from .excel import export_excel
from .scraper import DetailScraper, TransientError
from .safety import PortalBlocked
from .storage import Store
from .models import Attachment, Comment
from .reporting import local_file_status

log = logging.getLogger(__name__)
EXIT_OK, EXIT_WARNINGS, EXIT_LOCKED = 0, 2, 3

def _scrape_with_retries(scraper, session, cfg, consulta_id):
    last = None
    for attempt in range(max(cfg.scrape_retries, cfg.validation_retries + 1)):
        try:
            last = scraper.scrape(consulta_id)
            if not last.exists or last.validation_ok:
                return last
            log.warning('VALIDACION | consulta=%s intento=%s | %s',consulta_id,attempt+1,'; '.join(last.notes))
        except (TransientError, PlaywrightError) as exc:
            log.warning('Consulta %s: intento %s: %s', consulta_id, attempt + 1, type(exc).__name__)
        session.reset_page()
        if attempt + 1 < max(cfg.scrape_retries, cfg.validation_retries + 1):
            time.sleep(min(2 ** attempt, 10))
    if last is not None:
        return last
    raise TransientError('Agotados los intentos de extracción')

def _is_finalized(res, now, grace_min):
    snap = res.snapshot
    if not (res.validation_ok and snap):
        return False
    if snap.periodo_fin:
        return now > snap.periodo_fin + timedelta(minutes=grace_min)
    return snap.estado.lower().endswith(('finalizada', 'cerrada', 'concluida'))

def run_once(cfg, only_ids=None, force=False, historical=False, end_id=None):
    # --force se conserva por compatibilidad; todas las conocidas se revisan siempre.
    cfg.ensure_dirs()
    lock = FileLock(str(cfg.lock_path), timeout=0)
    try:
        lock.acquire()
    except Timeout:
        log.warning('CORRIDA | omitida: otra ejecución tiene el bloqueo %s', cfg.lock_path)
        return EXIT_LOCKED
    store = None
    http = None
    blocked = False
    started = time.monotonic()
    revised = new = errors = warnings = 0
    notes = []
    try:
        store = Store(cfg.db_path)
        run_id = store.start_run()
        log.info('CORRIDA %s | INICIO | datos=%s | descarga_local=%s | sheets=%s | drive=%s',run_id,cfg.data_dir.resolve(),cfg.download_attachments,cfg.sync_sheets,cfg.sync_drive)
        known = store.known_consultas()

        with BrowserSession(cfg) as session:
            ids = set(only_ids or [])
            probe_end = None
            if only_ids is None:
                if cfg.discover_index:
                    try:
                        ids.update(discover(session, cfg))
                    except PortalBlocked:
                        raise
                    except Exception as exc:
                        if isinstance(exc, CatalogIncomplete):
                            ids.update(exc.ids)
                            log.warning('CATALOGO | Se conservan IDs parciales: %s', sorted(exc.ids))
                        warnings += 1
                        notes.append('Índice no validado: ' + str(exc))
                        log.warning(notes[-1])
                        session.reset_page()
                cursor = int(store.get_meta('probe_cursor', cfg.start_id))
                ids, probe_end = plan_ids(cfg, known, ids, cursor, end_id, historical)
                log.info('ALCANCE | modo=%s | inicio=%s fin=%s | IDs=%s | activas y finalizadas; sin cortar por huecos',
                         'histórico' if historical else 'normal', min(ids), max(ids), len(ids))
            scraper = DetailScraper(session, cfg)
            consecutive_errors = 0
            probe_ok = True
            log.info('CORRIDA %s | Consultas programadas: %s',run_id,sorted(ids))
            for cid in sorted(ids):
                log.info('CONSULTA %s | INICIO',cid)
                try:
                    result = _scrape_with_retries(scraper, session, cfg, cid)
                    if not result.exists:
                        log.info('CONSULTA %s | no encontrada (HTTP 404/410)',cid)
                        consecutive_errors = 0
                        time.sleep(cfg.page_delay_s)
                        if cid in known:
                            warnings += 1
                            notes.append(f'#{cid}: conocida, ahora no encontrada; se conserva el histórico')
                            store.conn.execute('UPDATE consultas SET validacion_ok=0, notas=? WHERE id=?', (notes[-1], cid))
                            store.conn.commit()
                        continue
                    revised += 1
                    if not result.validation_ok:
                        warnings += 1
                        notes.append(f'#{cid}: ' + '; '.join(result.notes))
                        log.warning('CONSULTA %s | REVISAR | %s',cid,'; '.join(result.notes))
                        target = cfg.debug_dir / f'{run_id}_{cid}'
                        target.with_suffix('.html').write_text(result.last_html, encoding='utf-8')
                        target.with_suffix('.json').write_text(json.dumps(asdict(result), default=str, ensure_ascii=False, indent=2), encoding='utf-8')
                        if cid not in known and result.snapshot:
                            store.upsert_consulta(cid, f'{cfg.base_url}/ConsultaPublica/Detalle/{cid}',
                                                  result.snapshot, False, False, notes[-1])
                        else:
                            store.conn.execute('UPDATE consultas SET validacion_ok=0, notas=? WHERE id=?', (notes[-1], cid))
                            store.conn.commit()
                        continue
                    added = store.save_validated(cid, f'{cfg.base_url}/ConsultaPublica/Detalle/{cid}', result,
                                                 _is_finalized(result, datetime.now(MX_TZ), cfg.finalize_grace_minutes))
                    new += added
                    log.info('Consulta %s: %s/%s comentarios; %s/%s páginas; %s nuevos', cid,
                             len(result.comments), result.snapshot.total_reportado,
                             result.pages_visited, result.pages_expected, added)
                    consecutive_errors = 0
                    time.sleep(cfg.page_delay_s)
                except PortalBlocked:
                    raise
                except Exception as exc:
                    errors += 1
                    probe_ok = False
                    consecutive_errors += 1
                    notes.append(f'#{cid}: {type(exc).__name__}: {exc}')
                    log.exception('Error en consulta %s', cid)
                    store.conn.execute('UPDATE consultas SET validacion_ok=0, notas=? WHERE id=?', (notes[-1], cid))
                    store.conn.commit()
                    session.reset_page()
                    if consecutive_errors >= cfg.max_consecutive_errors:
                        errors += 1
                        notes.append(f'Barrido incompleto: interrupción en ID {cid}; quedan IDs sin revisar')
                        break
            if probe_end is not None and probe_ok:
                store.set_meta('probe_cursor', probe_end)
    except Exception as exc:
        blocked = isinstance(exc, PortalBlocked)
        errors += 1
        notes.append(f'{type(exc).__name__}: {exc}')
        log.error('Corrida interrumpida: %s', exc)
    finally:
        if store is not None:
            try:
                if cfg.download_attachments and not blocked:
                    try:
                        failed=download_all(store,cfg)
                        if failed:
                            warnings+=1
                            notes.append(f'{failed} adjuntos pendientes; no publicar nube')
                    except Exception as exc:
                        errors+=1
                        notes.append('Descargas interrumpidas: '+str(exc))
                        log.error('DESCARGAS | interrumpidas | %s',exc)
                store.finish_run(run_id,revised,new,errors,warnings,' | '.join(notes))
                export_excel(store,cfg.xlsx_path,cfg)
                if cfg.sync_drive or cfg.sync_sheets:
                    try:
                        publish_cloud(store,cfg)
                    except Exception as exc:
                        errors+=1
                        notes.append('Nube pendiente: '+str(exc))
                        log.error('NUBE | pendiente | causa=%s',exc)
                        store.finish_run(run_id,revised,new,errors,warnings,' | '.join(notes))
                        export_excel(store,cfg.xlsx_path,cfg)
            except Exception as exc:
                errors+=1
                log.exception('SALIDA | No se pudo terminar la descarga/exportación')
                if 'run_id' in locals():
                    store.finish_run(run_id,revised,new,errors,warnings,' | '.join(notes+[str(exc)]))
            finally:
                store.close()
        lock.release()
    log.info('CORRIDA | FIN | consultas=%s nuevos=%s errores=%s advertencias=%s duración=%.1fs',
             revised,new,errors,warnings,time.monotonic()-started)
    return EXIT_WARNINGS if errors or warnings else EXIT_OK


def download_all(store,cfg):
    """Cola duradera: reintenta adjuntos aunque no se pueda volver a extraer su consulta."""
    errors=total=0
    with make_session(cfg.user_agent) as session:
        for row in store.comments_for_export():
            atts=[Attachment(a['nombre'],a['url'],a.get('local','')) for a in json.loads(row['adjuntos_json'])]
            if not atts: continue
            total+=len(atts)
            c=Comment(row['autor'],row['fecha_texto'],None,row['comentario'],atts,key=row['hash'],folio=row['folio'])
            try:
                errors+=download_missing(session,row['consulta_id'],[c],cfg.attachments_dir,cfg.max_attachment_mb,cfg.base_url)
            finally:
                store.conn.execute('UPDATE comentarios SET adjuntos_json=? WHERE hash=?',
                    (json.dumps([{'nombre':a.name,'url':a.url,'local':a.local_path} for a in atts],ensure_ascii=False),row['hash']))
                store.conn.commit()
    log.info('DESCARGAS | total=%s verificados=%s pendientes=%s | directorio=%s',total,total-errors,errors,cfg.attachments_dir.resolve())
    return errors


def publish_cloud(store,cfg):
    pending=[]
    for row in store.comments_for_export():
        for att in json.loads(row['adjuntos_json']):
            if local_file_status(att)[0]!='Local verificado': pending.append(f"{row['folio']:03d}")
    if pending:
        raise RuntimeError(f'{len(pending)} adjuntos sin copia local verificada; folios: '+', '.join(sorted(set(pending))))
    from .google_sync import synchronize
    synchronize(store,cfg)


def export_only(cfg,retry_attachments=False,cloud=False):
    cfg.ensure_dirs()
    with FileLock(str(cfg.lock_path),timeout=0):
        store=Store(cfg.db_path)
        try:
            failed=download_all(store,cfg) if retry_attachments else 0
            export_excel(store,cfg.xlsx_path,cfg)
            if cloud: publish_cloud(store,cfg)
            return EXIT_WARNINGS if failed else EXIT_OK
        finally:
            store.close()
