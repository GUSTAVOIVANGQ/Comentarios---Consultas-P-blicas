"""CLI.

    python -m crt_comments run                 # una corrida (lo normal, con systemd timer)
    python -m crt_comments scan-all --end-id 100  # barrido histórico 1..100
    python -m crt_comments run --only 24,25    # solo esas consultas
    python -m crt_comments daemon              # corre cada 8 h en un bucle (alternativa a systemd)
    python -m crt_comments export              # regenera el Excel desde SQLite, sin tocar la web
    python -m crt_comments discover 24         # muestra qué API interna usa la página
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from logging.handlers import RotatingFileHandler

from .config import Config, MX_TZ
from datetime import datetime
from .runner import EXIT_OK, export_only, run_once


def _setup_logging(cfg: Config, verbose: bool, ui=False) -> None:
    cfg.ensure_dirs()
    fmt = logging.Formatter("%(asctime)s (MX) %(levelname)-7s %(name)s: %(message)s")
    fmt.converter=lambda timestamp: datetime.fromtimestamp(timestamp,MX_TZ).timetuple()
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers.clear()
    stream = logging.StreamHandler(sys.stdout)       # journald lo captura
    stream.setFormatter(fmt)
    root.addHandler(stream)
    fileh = RotatingFileHandler(cfg.log_dir / ("crt_ui.log" if ui else "crt_comments.log"), maxBytes=5_000_000,
                                backupCount=5, encoding="utf-8")
    fileh.setFormatter(fmt)
    root.addHandler(fileh)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="crt_comments", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--start-id", type=int, help="primer id a revisar (default 24)")
        sp.add_argument("--only", help="lista de ids separados por coma, p.ej. 24,25")
        sp.add_argument("--force", action="store_true", help="compatibilidad: las consultas conocidas ya se revisan siempre")
        sp.add_argument("--no-attachments", action="store_true", help="no descargar PDFs")
        sp.add_argument("--headed", action="store_true", help="mostrar el navegador (depuración)")

    common(sub.add_parser("run", help="una corrida"))
    scan = sub.add_parser("scan-all", help="revisar desde ID 1: activas y finalizadas, sin cortar por huecos")
    scan.add_argument("--end-id", type=int, help="último ID inclusive; sin él: mayor conocido + CRT_MAX_ID_SPAN")
    scan.add_argument("--headed", action="store_true")
    scan.add_argument("--no-attachments", action="store_true")
    d = sub.add_parser("daemon", help="corrida periódica en bucle")
    common(d)
    d.add_argument("--interval-hours", type=float)
    sub.add_parser("export", help="regenerar Excel desde SQLite")
    sub.add_parser("retry-downloads", help="reintentar adjuntos locales y regenerar Excel")
    sub.add_parser("bundle", help="crear ZIP portable con Excel y adjuntos locales")
    sub.add_parser("sync", help="generar Excel local y sincronizarlo con Google Sheets")
    sub.add_parser("sync-drive", help="reintentar descargas, subir adjuntos a Drive y actualizar Excel")
    ui = sub.add_parser("ui", help="interfaz local de operación")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8765)
    disc = sub.add_parser("discover", help="detectar endpoints XHR/fetch de la página")
    disc.add_argument("consulta_id", type=int)

    args = p.parse_args(argv)
    if getattr(args, 'end_id', None) is not None and args.end_id < 1:
        p.error('--end-id debe ser positivo')
    if getattr(args, 'start_id', None) is not None and args.start_id < 1:
        p.error('--start-id debe ser positivo')
    cfg = Config.from_env()
    if getattr(args, "start_id", None) is not None:
        cfg.start_id = args.start_id
    if getattr(args, "no_attachments", False):
        cfg.download_attachments = False
    if getattr(args, "headed", False):
        cfg.headless = False
    if getattr(args, "interval_hours", None):
        cfg.interval_hours = args.interval_hours
    _setup_logging(cfg, args.verbose, ui=args.cmd=="ui")

    only = [int(x) for x in args.only.split(",")] if getattr(args, "only", None) else None

    if args.cmd == "ui":
        from .ui import serve
        serve(cfg,args.host,args.port)
        return EXIT_OK
    if args.cmd=="bundle":
        try:
            from .bundle import create_bundle
            create_bundle(cfg)
            return 0
        except Exception as exc:
            logging.getLogger(__name__).error("PAQUETE | falló | %s",exc,exc_info=args.verbose)
            return 2
    if args.cmd in ("export","retry-downloads","sync","sync-drive"):
        if args.cmd=="sync": cfg.sync_sheets=cfg.sync_drive=True
        if args.cmd=="sync-drive": cfg.sync_drive=True;cfg.sync_sheets=False
        try:
            return export_only(cfg,retry_attachments=args.cmd in ('retry-downloads','sync-drive','sync'),cloud=args.cmd in ('sync','sync-drive'))
        except Exception as exc:
            logging.getLogger(__name__).error("OPERACIÓN %s | falló | %s",args.cmd,exc,exc_info=args.verbose)
            return 2
    if args.cmd == "discover":
        from .browser import discover_api  # import perezoso: no exige Playwright para `export`
        discover_api(cfg, args.consulta_id)
        return EXIT_OK
    if args.cmd == "run":
        return run_once(cfg, only_ids=only, force=args.force)
    if args.cmd == 'scan-all':
        return run_once(cfg, historical=True, end_id=args.end_id)

    # daemon ---------------------------------------------------------------
    log = logging.getLogger("daemon")
    interval = cfg.interval_hours * 3600
    try:
        while True:
            t0 = time.monotonic()
            try:
                run_once(cfg, only_ids=only, force=args.force)
            except Exception:  # noqa: BLE001 - el daemon nunca debe morir por una corrida fallida
                log.exception("Corrida fallida")
            sleep_s = max(60.0, interval - (time.monotonic() - t0))
            log.info("Próxima corrida en %.1f h", sleep_s / 3600)
            time.sleep(sleep_s)
    except KeyboardInterrupt:
        log.info("Detenido por el usuario")
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
