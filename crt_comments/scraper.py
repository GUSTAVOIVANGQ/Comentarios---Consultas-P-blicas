"""Extrae TODOS los comentarios de una consulta (todas las páginas) y valida el total."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from playwright.sync_api import Error as PlaywrightError

from .browser import BrowserSession
from .config import Config
from .models import Comment, DetailSnapshot, assign_keys
from .safety import check_blocked
from .parsers import parse_detail_html

log = logging.getLogger(__name__)


class TransientError(Exception):
    """Error recuperable (timeout, 5xx, contenido que no terminó de cargar)."""


@dataclass
class ScrapeResult:
    consulta_id: int
    exists: bool
    snapshot: Optional[DetailSnapshot] = None      # metadatos (de la primera página)
    comments: List[Comment] = field(default_factory=list)
    pages_visited: int = 0
    pages_expected: Optional[int] = None
    validation_ok: bool = False
    notes: List[str] = field(default_factory=list)
    last_html: str = ""                            # para volcado de depuración


class DetailScraper:
    def __init__(self, session: BrowserSession, cfg: Config):
        self.session = session
        self.cfg = cfg
        self._last_html = ""

    # ------------------------------------------------------------------
    @property
    def page(self):
        return self.session.page

    def _snapshot(self) -> Optional[DetailSnapshot]:
        html = self.page.content()
        check_blocked(html, self.page.url)
        self._last_html = html
        return parse_detail_html(html, self.cfg.base_url)

    def _poll(self, predicate: Callable[[Optional[DetailSnapshot]], bool], timeout_s: float,
              what: str) -> DetailSnapshot:
        deadline = time.monotonic() + timeout_s
        snap = None
        while True:
            try:
                snap = self._snapshot()
            except PlaywrightError:
                snap = None  # la página está navegando; se reintenta
            if predicate(snap):
                return snap  # type: ignore[return-value]
            if time.monotonic() > deadline:
                raise TransientError(f"Timeout esperando {what}")
            self.page.wait_for_timeout(300)

    # ------------------------------------------------------------------
    def _open(self, consulta_id: int) -> Optional[DetailSnapshot]:
        """Abre Detalle/<id>. Devuelve None si la consulta NO existe."""
        url = f"{self.cfg.base_url}/ConsultaPublica/Detalle/{consulta_id}"
        try:
            resp = self.page.goto(url, wait_until="domcontentloaded")
        except PlaywrightError as exc:
            raise TransientError(f"Error de navegación: {exc}") from exc

        check_blocked(self.page.content(), self.page.url)
        if resp is not None:
            if resp.status in (404, 410):
                return None
            if resp.status >= 400:
                raise TransientError(f"HTTP {resp.status}")
        if f"/Detalle/{consulta_id}" not in self.page.url:   # redirigió a otra parte
            raise TransientError("Redirección inesperada; no se declara inexistente")

        # Un HTML inesperado es un error de estructura, nunca prueba de inexistencia.
        snap = self._snapshot()
        if snap is None:
            self.page.wait_for_timeout(1000)
            snap = self._snapshot()
        if snap is None:
            raise TransientError("HTML no reconocido; posible cambio de estructura")

        def loaded(s: Optional[DetailSnapshot]) -> bool:
            return s is not None and not s.loading and (bool(s.comments) or (s.total_reportado == 0 and s.page_total is not None))

        try:
            return self._poll(loaded, self.cfg.load_timeout_s, "carga de comentarios")
        except TransientError:
            raise

    def _click_next(self) -> bool:
        name = re.compile(r'^Siguientes?(?: página)?$', re.I)
        loc = self.page.get_by_role('button', name=name).or_(self.page.get_by_role('link', name=name))
        for i in range(loc.count()):
            btn = loc.nth(i)
            if not btn.is_visible():
                continue
            cls = (btn.get_attribute("class") or "").lower()
            if btn.is_disabled() or "disabled" in cls or btn.get_attribute("aria-disabled") == "true":
                return False
            btn.scroll_into_view_if_needed()
            btn.click()
            return True
        return False

    def _wait_next_page(self, prev: DetailSnapshot) -> DetailSnapshot:
        def changed(s: Optional[DetailSnapshot]) -> bool:
            if s is None or s.loading or not s.comments:
                return False
            if s.page_current is not None and prev.page_current is not None:
                return (s.page_current == prev.page_current + 1 and
                        tuple(c.fingerprint() for c in s.comments) !=
                        tuple(c.fingerprint() for c in prev.comments))
            return s.signature() != prev.signature()

        return self._poll(changed, self.cfg.load_timeout_s, "cambio de página")

    # ------------------------------------------------------------------
    def scrape(self, consulta_id: int) -> ScrapeResult:
        # Dos barridos coincidentes reducen omisiones por inserciones durante la paginación.
        log.info('EXTRACCION | consulta=%s | barrido=1/2', consulta_id)
        first = self._scan(consulta_id)
        if not first.exists or not first.validation_ok:
            return first
        log.info('EXTRACCION | consulta=%s | barrido=2/2', consulta_id)
        second = self._scan(consulta_id)
        if (not second.exists or not second.validation_ok or
            sorted(c.fingerprint() for c in first.comments) !=
            sorted(c.fingerprint() for c in second.comments)):
            first.validation_ok = False
            first.notes.append("Los dos barridos no coinciden; reintentar sin publicar el parcial")
            return first
        return second

    def _scan(self, consulta_id: int) -> ScrapeResult:
        first = self._open(consulta_id)
        if first is None:
            return ScrapeResult(consulta_id=consulta_id, exists=False)

        result = ScrapeResult(consulta_id=consulta_id, exists=True, snapshot=first,
                              pages_expected=first.page_total)
        raw: List[Comment] = []
        snap = first
        seen_pages = set()
        while True:
            fingerprint = (snap.page_current, tuple(c.fingerprint() for c in snap.comments))
            if fingerprint in seen_pages:
                result.notes.append("Página repetida")
                break
            seen_pages.add(fingerprint)
            if snap.total_reportado != first.total_reportado or snap.page_total != first.page_total:
                result.notes.append("El total cambió durante la paginación")
            if snap.page_current != result.pages_visited + 1:
                result.notes.append("Secuencia de páginas inesperada")
            result.pages_visited += 1
            raw.extend(snap.comments)
            log.info("PAGINA | consulta=%s | página=%s/%s | comentarios=%d",
                      consulta_id, snap.page_current, snap.page_total, len(snap.comments))

            at_last = snap.page_total is not None and (snap.page_current or result.pages_visited) >= snap.page_total
            if at_last or result.pages_visited >= self.cfg.max_pages_per_consulta:
                break
            if not self._click_next():
                break
            self.page.wait_for_timeout(int(self.cfg.page_delay_s * 1000))
            snap = self._wait_next_page(snap)

        result.last_html = self._last_html
        self._validate(result, raw)
        return result

    # ------------------------------------------------------------------
    def _validate(self, result: ScrapeResult, raw: List[Comment]) -> None:
        total = result.snapshot.total_reportado if result.snapshot else None
        comments = raw

        assign_keys(result.consulta_id, comments)
        result.comments = comments

        problems = list(result.notes)
        if result.pages_expected is None:
            problems.append("el portal no reportó total de páginas")
        if total is None:
            problems.append("el portal no reportó total de comentarios")
        elif len(comments) != total:
            problems.append(f"extraídos {len(comments)} != reportados {total}")
        if result.pages_expected is not None and result.pages_visited != result.pages_expected:
            problems.append(f"páginas visitadas {result.pages_visited} != esperadas {result.pages_expected}")
        result.validation_ok = not problems
        result.notes = list(dict.fromkeys(problems))
