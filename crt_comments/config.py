"""Configuración centralizada.

Todos los valores tienen un default razonable y se pueden sobreescribir con
variables de entorno con prefijo ``CRT_`` (ver ``Config.from_env``) o con
argumentos de línea de comandos (ver ``__main__.py``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

# El portal publica fechas en hora del centro de México (sin horario de verano
# desde 2022). Se usa para interpretar "Periodo de la consulta" y comentarios.
MX_TZ = ZoneInfo("America/Mexico_City")


@dataclass
class Config:
    base_url: str = "https://portal.crt.gob.mx"

    # --- Descubrimiento de consultas -------------------------------------
    # Catálogo + IDs conocidos + ventana progresiva de respaldo.
    start_id: int = 24
    max_consecutive_misses: int = 5  # legado; ya no se detiene ante huecos
    max_id_span: int = 500

    # --- Rutas -----------------------------------------------------------
    data_dir: Path = Path("data")
    log_dir: Path = Path("logs")

    # --- Adjuntos --------------------------------------------------------
    download_attachments: bool = True
    max_attachment_mb: int = 50

    # --- Navegador / red -------------------------------------------------
    headless: bool = True
    nav_timeout_ms: int = 45_000
    load_timeout_s: int = 30       # espera a que terminen de cargar comentarios
    page_delay_s: float = 0.5      # pausa cortés entre clicks/páginas
    user_agent: str = ""           # vacío = el de Chromium

    # --- Robustez --------------------------------------------------------
    scrape_retries: int = 3        # reintentos ante errores transitorios
    validation_retries: int = 2    # re-scrapes si el total no coincide
    max_pages_per_consulta: int = 500
    max_consecutive_errors: int = 3  # aborta la corrida si el sitio está caído
    # Etiqueta de cierre; no excluye consultas de futuras revisiones.
    finalize_grace_minutes: int = 30

    # --- Modo daemon -----------------------------------------------------
    interval_hours: float = 8.0
    discover_index: bool = True
    probe_batch: int = 8
    spreadsheet_id: str = "115cwia3lnqSTNIy8ndqlp7woN1tWnepjpMIZTxgn-28"
    google_credentials: str = ""
    sync_sheets: bool = False
    sync_drive: bool = True
    drive_folder_id: str = "11zSH8drBUGh6yjwpmPjBayrKm7doNwcT"
    browser_executable: str = ""
    browser_sandbox: bool = True


    # ---------------------------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.data_dir / "crt_comentarios.sqlite3"

    @property
    def xlsx_path(self) -> Path:
        return self.data_dir / "comentarios_crt.xlsx"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "adjuntos"

    @property
    def debug_dir(self) -> Path:
        return self.data_dir / "debug"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / ".crt_comments.lock"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.log_dir, self.attachments_dir, self.debug_dir):
            d.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "Config":
        cfg = cls()

        def env(name: str):
            return os.environ.get(f"CRT_{name}")

        if env("BASE_URL"):
            cfg.base_url = env("BASE_URL").rstrip("/")
        if env("START_ID"):
            cfg.start_id = int(env("START_ID"))
        if env("MAX_CONSECUTIVE_MISSES"):
            cfg.max_consecutive_misses = int(env("MAX_CONSECUTIVE_MISSES"))
        if env("DATA_DIR"):
            cfg.data_dir = Path(env("DATA_DIR"))
        if env("LOG_DIR"):
            cfg.log_dir = Path(env("LOG_DIR"))
        if env("DOWNLOAD_ATTACHMENTS"):
            cfg.download_attachments = env("DOWNLOAD_ATTACHMENTS").lower() in ("1", "true", "yes", "si")
        if env("HEADLESS"):
            cfg.headless = env("HEADLESS").lower() in ("1", "true", "yes", "si")
        if env("INTERVAL_HOURS"):
            cfg.interval_hours = float(env("INTERVAL_HOURS"))
        if env("USER_AGENT"):
            cfg.user_agent = env("USER_AGENT")
        from dataclasses import fields
        # Todos los campos admiten CRT_<NOMBRE>; secretos solo como ruta a JSON.
        for field in fields(cfg):
            value = env(field.name.upper())
            if value is None:
                continue
            current = getattr(cfg, field.name)
            if isinstance(current, bool):
                if value.lower() not in ("1", "0", "true", "false", "yes", "no", "si"):
                    raise ValueError(f"CRT_{field.name.upper()}: booleano inválido")
                value = value.lower() in ("1", "true", "yes", "si")
            elif isinstance(current, Path):
                value = Path(value)
            elif isinstance(current, int):
                value = int(value)
            elif isinstance(current, float):
                value = float(value)
            setattr(cfg, field.name, value)
        cfg.base_url = cfg.base_url.rstrip("/")
        for name in ("start_id", "probe_batch", "max_id_span", "nav_timeout_ms", "load_timeout_s", "scrape_retries",
                     "max_pages_per_consulta", "max_attachment_mb", "interval_hours"):
            if getattr(cfg, name) <= 0:
                raise ValueError(f"{name} debe ser mayor que cero")
        return cfg
