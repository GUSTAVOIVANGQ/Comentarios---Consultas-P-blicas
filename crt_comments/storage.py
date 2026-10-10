"""Persistencia en SQLite.

SQLite es la fuente de verdad (idempotente, sin duplicados, con historial).
El Excel se REGENERA desde aquí en cada corrida (ver excel.py), lo que evita
corrupción por escrituras parciales y permite filtrar/ordenar sin perder datos.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from filelock import FileLock

from .models import Attachment, Comment, DetailSnapshot

log=logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS consultas (
    id INTEGER PRIMARY KEY,
    titulo TEXT, url TEXT, estado TEXT,
    fecha_publicacion TEXT,
    periodo_inicio TEXT, periodo_fin TEXT, periodo_texto TEXT,
    total_reportado INTEGER, participantes_unicos INTEGER,
    validacion_ok INTEGER NOT NULL DEFAULT 0,
    finalizada INTEGER NOT NULL DEFAULT 0,
    notas TEXT,
    primera_deteccion TEXT, ultima_revision TEXT
);
CREATE TABLE IF NOT EXISTS comentarios (
    hash TEXT PRIMARY KEY,
    consulta_id INTEGER NOT NULL,
    autor TEXT, fecha TEXT, fecha_texto TEXT, comentario TEXT,
    adjuntos_json TEXT NOT NULL DEFAULT '[]',
    primera_deteccion TEXT, ultima_vez_visto TEXT
);
CREATE INDEX IF NOT EXISTS ix_comentarios_consulta ON comentarios(consulta_id);
CREATE TABLE IF NOT EXISTS ejecuciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inicio TEXT, fin TEXT,
    consultas_revisadas INTEGER DEFAULT 0,
    comentarios_nuevos INTEGER DEFAULT 0,
    errores INTEGER DEFAULT 0,
    advertencias INTEGER DEFAULT 0,
    resumen TEXT
);
"""


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") if dt else None


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        with FileLock(str(path.with_suffix(path.suffix+".schema.lock")),timeout=30):
            self.conn.executescript(SCHEMA)
            self.conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS drive_files (key TEXT PRIMARY KEY, file_id TEXT, url TEXT, checksum TEXT)")
            columns = {r[1] for r in self.conn.execute("PRAGMA table_info(comentarios)")}
            if 'presente' not in columns:
                self.conn.execute("ALTER TABLE comentarios ADD COLUMN presente INTEGER NOT NULL DEFAULT 1")
            # Folio global permanente. Migración determinista de bases de la versión 2.
            if 'folio' not in columns:
                self.conn.execute("ALTER TABLE comentarios ADD COLUMN folio INTEGER")
            self.conn.execute("CREATE TABLE IF NOT EXISTS folios (id INTEGER PRIMARY KEY AUTOINCREMENT, comment_hash TEXT NOT NULL UNIQUE)")
            for row in self.conn.execute("SELECT hash FROM comentarios WHERE folio IS NULL ORDER BY primera_deteccion, consulta_id, fecha, hash").fetchall():
                self.conn.execute("INSERT OR IGNORE INTO folios(comment_hash) VALUES (?)", (row[0],))
                self.conn.execute("UPDATE comentarios SET folio=(SELECT id FROM folios WHERE comment_hash=?) WHERE hash=?", (row[0], row[0]))
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_comentarios_folio ON comentarios(folio)")
            columns = {r[1] for r in self.conn.execute("PRAGMA table_info(consultas)")}
            for name in ('paginas_visitadas', 'paginas_esperadas'):
                if name not in columns:
                    self.conn.execute(f"ALTER TABLE consultas ADD COLUMN {name} INTEGER")
            self.conn.commit()

    def get_meta(self, key, default=None):
        row = self.conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key, value):
        self.conn.execute("INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        self.conn.commit()

    def save_validated(self, cid, url, result, finalized):
        # Una sola transacción publica el snapshot completo; nunca mezclar un parcial.
        self.conn.execute("SAVEPOINT snapshot")
        try:
            self.conn.execute("UPDATE comentarios SET presente=0 WHERE consulta_id=?", (cid,))
            new = self.upsert_comments(cid, result.comments, commit=False)
            self.upsert_consulta(cid, url, result.snapshot, True, finalized, '; '.join(result.notes), commit=False)
            self.conn.execute("UPDATE consultas SET paginas_visitadas=?, paginas_esperadas=? WHERE id=?",
                              (result.pages_visited, result.pages_expected, cid))
            self.conn.execute("RELEASE snapshot")
            return new
        except Exception:
            self.conn.execute("ROLLBACK TO snapshot")
            self.conn.execute("RELEASE snapshot")
            raise

    def close(self) -> None:
        self.conn.close()

    # ---- ejecuciones ------------------------------------------------------
    def start_run(self) -> int:
        cur = self.conn.execute("INSERT INTO ejecuciones (inicio) VALUES (?)", (now_iso(),))
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, revisadas: int, nuevos: int, errores: int,
                   advertencias: int, resumen: str) -> None:
        self.conn.execute(
            "UPDATE ejecuciones SET fin=?, consultas_revisadas=?, comentarios_nuevos=?, "
            "errores=?, advertencias=?, resumen=? WHERE id=?",
            (now_iso(), revisadas, nuevos, errores, advertencias, resumen, run_id),
        )
        self.conn.commit()

    # ---- consultas --------------------------------------------------------
    def known_consultas(self) -> Dict[int, sqlite3.Row]:
        return {r["id"]: r for r in self.conn.execute("SELECT * FROM consultas")}

    def upsert_consulta(self, consulta_id: int, url: str, snap: DetailSnapshot,
                        validacion_ok: bool, finalizada: bool, notas: str, commit=True) -> None:
        ts = now_iso()
        self.conn.execute(
            """
            INSERT INTO consultas (id, titulo, url, estado, fecha_publicacion, periodo_inicio,
                periodo_fin, periodo_texto, total_reportado, participantes_unicos,
                validacion_ok, finalizada, notas, primera_deteccion, ultima_revision)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                titulo=excluded.titulo, url=excluded.url, estado=excluded.estado,
                fecha_publicacion=excluded.fecha_publicacion,
                periodo_inicio=excluded.periodo_inicio, periodo_fin=excluded.periodo_fin,
                periodo_texto=excluded.periodo_texto, total_reportado=excluded.total_reportado,
                participantes_unicos=excluded.participantes_unicos,
                validacion_ok=excluded.validacion_ok, finalizada=excluded.finalizada,
                notas=excluded.notas, ultima_revision=excluded.ultima_revision
            """,
            (consulta_id, snap.titulo, url, snap.estado, snap.fecha_publicacion_texto,
             _iso(snap.periodo_inicio), _iso(snap.periodo_fin), snap.periodo_texto,
             snap.total_reportado, snap.participantes_unicos,
             int(validacion_ok), int(finalizada), notas, ts, ts),
        )
        if commit:
            self.conn.commit()

    def count_comments(self, consulta_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM comentarios WHERE consulta_id=?", (consulta_id,)
        ).fetchone()[0]

    # ---- comentarios ------------------------------------------------------
    def upsert_comments(self, consulta_id: int, comments: List[Comment], commit=True) -> int:
        """Inserta nuevos y marca los ya existentes como vistos. Devuelve cuántos son nuevos."""
        ts = now_iso()
        existing = {r[0] for r in self.conn.execute(
            "SELECT hash FROM comentarios WHERE consulta_id=?", (consulta_id,))}
        new = 0
        for c in comments:
            allocated = self.conn.execute("SELECT id FROM folios WHERE comment_hash=?", (c.key,)).fetchone()
            if allocated is None:
                c.folio = self.conn.execute("INSERT INTO folios(comment_hash) VALUES (?)", (c.key,)).lastrowid
            else:
                c.folio = allocated[0]
            previous = self.conn.execute("SELECT adjuntos_json FROM comentarios WHERE hash=?", (c.key,)).fetchone()
            previous_paths = {a['url']: a.get('local', '') for a in json.loads(previous[0])} if previous else {}
            for a in c.attachments:
                if not a.local_path and previous_paths.get(a.url):
                    a.local_path = previous_paths[a.url]
            adj = json.dumps(
                [{"nombre": a.name, "url": a.url, "local": a.local_path} for a in c.attachments],
                ensure_ascii=False,
            )
            if c.key in existing:
                self.conn.execute(
                    "UPDATE comentarios SET ultima_vez_visto=?, adjuntos_json=?, presente=1 WHERE hash=?",
                    (ts, adj, c.key),
                )
            else:
                new += 1
                log.info('COMENTARIO | folio=%03d | consulta=%s | nuevo | autor=%s | fecha=%s | adjuntos=%s',
                         c.folio,consulta_id,c.autor.replace('\n',' '),c.fecha_texto,len(c.attachments))
                self.conn.execute(
                    "INSERT INTO comentarios (hash, consulta_id, autor, fecha, fecha_texto, comentario, "
                    "adjuntos_json, primera_deteccion, ultima_vez_visto, folio) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (c.key, consulta_id, c.autor, _iso(c.fecha), c.fecha_texto, c.texto, adj, ts, ts, c.folio),
                )
        if commit:
            self.conn.commit()
        return new

    # ---- consultas para el Excel -----------------------------------------
    def comments_for_export(self) -> List[sqlite3.Row]:
        return list(self.conn.execute(
            """SELECT c.*, k.titulo AS consulta_titulo
               FROM comentarios c LEFT JOIN consultas k ON k.id = c.consulta_id
               ORDER BY c.folio ASC"""
        ))

    def consultas_for_export(self) -> List[sqlite3.Row]:
        return list(self.conn.execute(
            """SELECT k.*, (SELECT COUNT(*) FROM comentarios c WHERE c.consulta_id = k.id) AS total_historico,
               (SELECT COUNT(*) FROM comentarios c WHERE c.consulta_id=k.id AND c.presente=1) AS total_extraido
               FROM consultas k ORDER BY k.id"""
        ))

    def runs_for_export(self, limit: int = 200) -> List[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM ejecuciones ORDER BY id DESC LIMIT ?", (limit,)))
