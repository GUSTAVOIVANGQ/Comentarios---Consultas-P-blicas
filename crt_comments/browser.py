"""Sesión de Chromium headless (Playwright) y utilidades de diagnóstico."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from .config import MX_TZ, Config

log = logging.getLogger(__name__)

_BLOCKED = {"image", "font", "media"}  # el texto no los necesita; acelera cada página


class BrowserSession:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._pw = None
        self._browser: Browser | None = None
        self._ctx: BrowserContext | None = None
        self.page: Page | None = None

    def __enter__(self) -> "BrowserSession":
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(
                headless=self.cfg.headless,
                chromium_sandbox=self.cfg.browser_sandbox,
                executable_path=self.cfg.browser_executable or None,
                args=["--disable-dev-shm-usage"],  # evita crashes por /dev/shm chico en servidores
            )
            kwargs: Dict[str, Any] = dict(locale="es-MX", timezone_id=str(MX_TZ.key),
                                          viewport={"width": 1366, "height": 900})
            if self.cfg.user_agent:
                kwargs["user_agent"] = self.cfg.user_agent
            self._ctx = self._browser.new_context(**kwargs)
            self._ctx.set_default_timeout(self.cfg.nav_timeout_ms)
            self._ctx.route(
                "**/*",
                lambda route: route.abort() if route.request.resource_type in _BLOCKED else route.continue_(),
            )
            self.page = self._ctx.new_page()
        except Exception:
            self.__exit__()
            raise
        return self

    def reset_page(self) -> None:
        """Pestaña nueva (útil tras un error para no arrastrar estado roto)."""
        try:
            if self.page:
                self.page.close()
        except Exception:  # noqa: BLE001
            pass
        self.page = self._ctx.new_page()

    def __exit__(self, *exc) -> None:
        for closer in (lambda: self._ctx and self._ctx.close(),
                       lambda: self._browser and self._browser.close(),
                       lambda: self._pw and self._pw.stop()):
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass


def discover_api(cfg: Config, consulta_id: int) -> None:
    """Registra las llamadas XHR/fetch que hace la página de detalle.

    Úselo UNA vez para ver qué endpoints JSON consume el portal. Si existe una
    API paginada, se puede reemplazar el navegador por ``requests`` (mucho más
    rápido y ligero). Genera ``data/discover_<id>.json``.
    """
    found: List[Dict[str, Any]] = []

    with BrowserSession(cfg) as s:
        page = s.page

        def on_response(resp):
            req = resp.request
            if req.resource_type not in ("xhr", "fetch"):
                return
            entry: Dict[str, Any] = {
                "method": req.method, "url": resp.url, "status": resp.status,
                "content_type": resp.headers.get("content-type", ""),
                "post_data": req.post_data,
            }
            try:
                entry["body_sample"] = resp.text()[:1500]
            except Exception:  # noqa: BLE001
                entry["body_sample"] = None
            found.append(entry)

        page.on("response", on_response)
        page.goto(f"{cfg.base_url}/ConsultaPublica/Detalle/{consulta_id}", wait_until="networkidle")
        from .safety import check_blocked
        check_blocked(page.content(), page.url)
        page.wait_for_timeout(2000)
        try:  # provoca la segunda página para ver cómo se pagina
            btn = page.locator("button:has-text('Siguientes'), a:has-text('Siguientes')").first
            if btn.is_visible() and not btn.is_disabled():
                btn.click()
                page.wait_for_timeout(2500)
        except Exception as exc:  # noqa: BLE001
            log.info("No se pudo hacer click en Siguientes durante discover: %s", exc)

    cfg.ensure_dirs()
    out = cfg.data_dir / f"discover_{consulta_id}.json"
    out.write_text(json.dumps({"capturado": datetime.now(MX_TZ).isoformat(), "llamadas": found},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nLlamadas XHR/fetch detectadas: {len(found)}  ->  {out}\n")
    for e in found:
        print(f"[{e['status']}] {e['method']} {e['url']}  ({e['content_type']})")
