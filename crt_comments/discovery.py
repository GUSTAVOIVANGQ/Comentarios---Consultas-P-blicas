"""Descubrimiento desde el índice (incluye cerradas) y sondeo progresivo de respaldo."""
import re
import logging
import time
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup
from .safety import check_blocked

log = logging.getLogger(__name__)

DETAIL = re.compile(r'^/ConsultaPublica/Detalle/(\d+)/?$', re.I)

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
    page = session.page
    response = page.goto(cfg.base_url + '/ConsultaPublica', wait_until='domcontentloaded')
    check_blocked(page.content(), page.url)
    if response and response.status >= 400:
        raise RuntimeError(f'Índice HTTP {response.status}')
    log.info("CATALOGO | Abriendo índice %s/ConsultaPublica", cfg.base_url)
    enable_closed(page)
    found, seen = set(), set()
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
                raise RuntimeError('Índice vacío o sin terminar de cargar; usar respaldo y revisar selectores')
            page.wait_for_timeout(300)
        match = re.search(r'Mostrando\s+[\d,]+\s*[-–]\s*[\d,]+\s+de\s+([\d,]+)\s+consultas', text, re.I)
        if match:
            total = int(match.group(1).replace(',', ''))
            if expected is not None and expected != total:
                raise RuntimeError('El catálogo cambió mientras se paginaba')
            expected = total
        signature = tuple(sorted(ids))
        if signature in seen:
            raise RuntimeError('Paginación del índice repetida')
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
                raise RuntimeError(f"Catálogo incompleto: {len(found)} de {expected} consultas")
            return found
        enabled[0].click()
        deadline = time.monotonic() + cfg.load_timeout_s
        while ids_from_html(page.content(), cfg.base_url) == ids:
            check_blocked(page.content(), page.url)
            if time.monotonic() > deadline:
                raise RuntimeError('El índice no avanzó')
            page.wait_for_timeout(300)
    raise RuntimeError('Límite de páginas del índice alcanzado')
