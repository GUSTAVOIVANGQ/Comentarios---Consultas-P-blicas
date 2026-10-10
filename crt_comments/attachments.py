"""Descargas locales atómicas, limitadas y verificadas por SHA-256."""
from __future__ import annotations
import hashlib
import logging
import re
from pathlib import Path
from urllib.parse import urlsplit
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from .safety import check_blocked

log = logging.getLogger(__name__)

def make_session(user_agent=''):
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=('GET',))
    s.mount('https://', HTTPAdapter(max_retries=retry))
    s.mount('http://', HTTPAdapter(max_retries=retry))
    s.headers['User-Agent'] = user_agent or 'CRT-Comments/3.0 (+public consultation archive)'
    return s

def _safe_name(name):
    return re.sub(r'[^\w.\-]', '_', name)[:150].strip('.') or 'adjunto.bin'

def _digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()

def download_missing(session, consulta_id, comments, dest_root, max_mb, base_url='https://portal.crt.gob.mx'):
    failures = 0
    dest_dir = dest_root / str(consulta_id)
    origin = urlsplit(base_url)
    for c in comments:
        for index, att in enumerate(c.attachments, 1):
            ident = hashlib.sha256(att.url.encode()).hexdigest()[:12]
            prefix = f'{c.folio:03d}_{index:02d}_' if c.folio else ''
            dest = dest_dir / (prefix + ident + '_' + _safe_name(att.name))
            if att.local_path and Path(att.local_path).is_file():
                previous = Path(att.local_path).resolve()
                if previous.is_relative_to(dest_root.resolve()):
                    dest = previous
            sidecar = dest.with_suffix(dest.suffix + '.sha256')
            part = dest.with_suffix(dest.suffix + '.part')
            try:
                u = urlsplit(att.url)
                if (u.scheme, u.netloc) != (origin.scheme, origin.netloc) or not u.path.startswith('/adjunto-comentario/'):
                    raise ValueError('Adjunto fuera del origen/ruta autorizados')
                if dest.is_file() and sidecar.is_file() and dest.stat().st_size and _digest(dest) == sidecar.read_text():
                    att.local_path = str(dest.resolve())
                    log.info('ADJUNTO | consulta=%s folio=%03d | ya verificado | %s', consulta_id, c.folio, dest)
                    continue
                log.info('ADJUNTO | consulta=%s folio=%03d | descargando %s', consulta_id, c.folio, att.url)
                dest_dir.mkdir(parents=True, exist_ok=True)
                size = 0
                # No seguir redirecciones a otros hosts ni descargar páginas CAPTCHA como PDF.
                with session.get(att.url, stream=True, timeout=(15, 60), allow_redirects=False) as response:
                    if response.status_code != 200:
                        raise ValueError(f'HTTP {response.status_code}')
                    if 'text/html' in response.headers.get('Content-Type', ''):
                        check_blocked(response.text[:100000], response.url)
                        raise ValueError('El adjunto devolvió HTML')
                    with part.open('wb') as fh:
                        for chunk in response.iter_content(65536):
                            size += len(chunk)
                            if size > max_mb * 1024 * 1024:
                                raise ValueError(f'Adjunto excede {max_mb} MB')
                            fh.write(chunk)
                if not size:
                    raise ValueError('Adjunto vacío')
                if dest.suffix.lower() == '.pdf':
                    with part.open('rb') as fh:
                        if not fh.read(1024).lstrip().startswith(b'%PDF-'):
                            raise ValueError('Firma PDF inválida')
                checksum = _digest(part)
                part.replace(dest)
                sidecar.write_text(checksum)
                att.local_path = str(dest.resolve())
                log.info('ADJUNTO | consulta=%s folio=%03d | guardado=%s | bytes=%s | sha256=%s', consulta_id, c.folio, dest, size, checksum)
            except Exception as exc:
                from .safety import PortalBlocked
                if isinstance(exc, PortalBlocked):
                    raise
                failures += 1
                att.local_path = ''
                log.warning('ADJUNTO | consulta=%s folio=%03d | pendiente=%s | causa=%s', consulta_id, c.folio, att.name, exc)
            finally:
                part.unlink(missing_ok=True)
    return failures
