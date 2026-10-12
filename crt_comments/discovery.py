"""Descubrimiento desde el índice (incluye cerradas) y sondeo progresivo de respaldo."""
import re
import logging
import time
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup
from .safety import check_blocked, PortalBlocked

log = logging.getLogger(__name__)

DETAIL = re.compile(r'^/ConsultaPublica/Detalle/(\d+)/?$', re.I)

class CatalogIncomplete(RuntimeError):
    """Retiene los IDs útiles aunque el catálogo no se pueda validar completo."""
    def __init__(self, message, ids):
        super().__init__(message)
        self.ids = set(ids)

def ids_from_html(html, base_url):
    out = set()
    for a in BeautifulSoup(html, 'html.parser').select('a[href]'):
        u = urlsplit(urljoin(base_url, a['href']))
        m = DETAIL.match(u.path)
        if u.netloc == urlsplit(base_url).netloc and m:
            out.add(int(m[1]))
    return out

def enable_closed(page):
    """Checkbox visual personalizado: preferir etiqueta y comprobar estado real."""
    toggle = page.locator('#toggleVencidas')
    if not toggle.count():
        log.warning('CATALOGO | No se encontró el selector de consultas finalizadas')
        return
    if toggle.is_checked():
        return
    label = page.locator('label[for="toggleVencidas"]')
    if toggle.is_visible():
        toggle.check(timeout=5000)
    elif label.count() and label.first.is_visible():
        label.first.click(timeout=5000)
    else:
        # El input observado en el log existe, pero CSS lo oculta. click nativo
        # activa su evento y la lógica del portal sin modificar checked a mano.
        toggle.evaluate('(element) => element.click()')
    if not toggle.is_checked():
        raise RuntimeError('No se pudo activar Mostrar consultas finalizadas')
    log.info('CATALOGO | Consultas finalizadas activadas')


def discover(session, cfg):
    found = set()
    try:
        return _discover(session, cfg, found)
    except (PortalBlocked, CatalogIncomplete):
        raise
    except Exception as exc:
        raise CatalogIncomplete(str(exc), found) from exc


def _discover(session, cfg, found):
    page = session.page
    response = page.goto(cfg.base_url + '/ConsultaPublica', wait_until='domcontentloaded')
    check_blocked(page.content(), page.url)
    if response and response.status >= 400:
        raise RuntimeError(f'Índice HTTP {response.status}')
    log.info("CATALOGO | Abriendo índice %s/ConsultaPublica", cfg.base_url)
    enable_closed(page)
    seen = set()
    expected = None
    for _ in range(cfg.max_pages_per_consulta):
        deadline = time.monotonic() + cfg.load_timeout_s
        while True:
            html = page.content()
            check_blocked(html, page.url)
            text = BeautifulSoup(html, 'html.parser').get_text(' ', strip=True)
            ids = ids_from_html(html, cfg.base_url)
            # No validar un índice vacío inicial como catálogo completo.
            if ids and not page.get_by_text(re.compile(r'^Cargando consultas')).first.is_visible():
                break
            if time.monotonic() > deadline:
                raise CatalogIncomplete('Índice vacío o sin terminar de cargar; usar respaldo y revisar selectores', found)
            page.wait_for_timeout(300)
        match = re.search(r'Mostrando\s+[\d,]+\s*[-–]\s*[\d,]+\s+de\s+([\d,]+)\s+consultas', text, re.I)
        if match:
            total = int(match.group(1).replace(',', ''))
            if expected is not None and expected != total:
                raise CatalogIncomplete('El catálogo cambió mientras se paginaba', found | ids)
            expected = total
        signature = tuple(sorted(ids))
        if signature in seen:
            raise CatalogIncomplete('Paginación del índice repetida', found | ids)
        seen.add(signature)
        found.update(ids)
        log.info("CATALOGO | Página %s: IDs %s; acumulados=%s; reportados=%s", len(seen), sorted(ids), len(found), expected)
        buttons = page.get_by_role('button', name=re.compile(r'^Siguientes?$|^Siguiente página$', re.I))
        links = page.get_by_role('link', name=re.compile(r'^Siguientes?$|^Siguiente página$', re.I))
        next_button = buttons.or_(links)
        enabled = [b for b in next_button.all() if b.is_visible() and b.is_enabled()
                   and b.get_attribute('aria-disabled') != 'true'
                   and 'disabled' not in (b.get_attribute('class') or '')]
        if not enabled:
            if expected is not None and len(found) != expected:
                raise CatalogIncomplete(f"Catálogo incompleto: {len(found)} de {expected} consultas", found)
            return found
        enabled[0].click()
        deadline = time.monotonic() + cfg.load_timeout_s
        while ids_from_html(page.content(), cfg.base_url) == ids:
            check_blocked(page.content(), page.url)
            if time.monotonic() > deadline:
                raise CatalogIncomplete('El índice no avanzó', found)
            page.wait_for_timeout(300)
    raise CatalogIncomplete('Límite de páginas del índice alcanzado', found)


def plan_ids(cfg, known, catalog, cursor, end_id=None, historical=False):
    """Rango continuo conocido + sondeo de respaldo; nunca cortar por huecos.

    La web no publica un ID máximo garantizado. El barrido histórico usa un
    límite explícito o un horizonte de max_id_span más allá del mayor conocido.
    """
    start = 1 if historical else cfg.start_id
    discovered = set(known) | set(catalog)
    highest = max([start, *discovered])
    if end_id is not None:
        if end_id < start:
            raise ValueError('El ID final debe ser mayor o igual al inicial')
        return set(range(start, end_id + 1)), None
    if historical:
        return set(range(1, highest + cfg.max_id_span + 1)), None
    ceiling = highest + cfg.max_id_span
    cursor = max(start, cursor)
    if cursor > ceiling:
        cursor = start
    probe_end = min(cursor + cfg.probe_batch, ceiling + 1)
    ids = set(range(start, highest + cfg.probe_batch + 1))
    ids.update(range(cursor, probe_end))
    return ids, probe_end
