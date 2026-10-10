"""Modelos de datos (sin dependencias de navegador ni de base de datos)."""
from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class Attachment:
    name: str                 # p.ej. 341dcc4e-37fe-4b3e-81c9-96e80689886f.pdf
    url: str                  # https://portal.crt.gob.mx/adjunto-comentario/<name>
    local_path: str = ""      # se llena al descargar


@dataclass
class Comment:
    autor: str
    fecha_texto: str                  # tal cual la muestra el portal: 09/10/2026 14:29
    fecha: Optional[datetime]         # naive, hora de Ciudad de México
    texto: str
    attachments: List[Attachment] = field(default_factory=list)
    key: str = ""                     # identificador estable (se asigna con assign_keys)

    folio: int = 0                   # número global persistente, representación 001/002/...

    def fingerprint(self) -> str:
        att = ",".join(a.name for a in self.attachments)
        return "|".join([self.autor, self.fecha_texto, self.texto, att])


@dataclass
class DetailSnapshot:
    """Lo que se ve en UNA página de resultados de Detalle/<id>."""
    titulo: str
    estado: str = ""
    fecha_publicacion_texto: str = ""
    periodo_texto: str = ""
    periodo_inicio: Optional[datetime] = None   # con tzinfo America/Mexico_City
    periodo_fin: Optional[datetime] = None      # con tzinfo America/Mexico_City
    total_reportado: Optional[int] = None       # "32 comentarios"
    participantes_unicos: Optional[int] = None
    page_current: Optional[int] = None          # "Página 2 de 4" -> 2
    page_total: Optional[int] = None            # "Página 2 de 4" -> 4
    loading: bool = False                       # aún dice "Cargando comentarios..."
    comments: List[Comment] = field(default_factory=list)

    def signature(self) -> tuple:
        """Huella de la página para detectar que el contenido cambió al paginar."""
        first = self.comments[0] if self.comments else None
        return (
            self.page_current,
            len(self.comments),
            first.autor if first else "",
            first.fecha_texto if first else "",
            first.texto[:60] if first else "",
        )


def assign_keys(consulta_id: int, comments: List[Comment]) -> None:
    """Asigna a cada comentario un ``key`` estable (sha1).

    El portal no expone un id de comentario, así que se calcula a partir del
    contenido. Si hay dos comentarios idénticos (mismo autor, minuto y texto)
    se distinguen con un índice de ocurrencia, de modo que ninguno se pierda.
    """
    seen: Counter = Counter()
    for c in comments:
        fp = c.fingerprint()
        occ = seen[fp]
        seen[fp] += 1
        c.key = hashlib.sha1(f"{consulta_id}|{fp}|{occ}".encode("utf-8")).hexdigest()


def dedupe(comments: List[Comment]) -> List[Comment]:
    """Quita repetidos exactos conservando el orden (se usa solo si el total no cuadra)."""
    seen = set()
    out = []
    for c in comments:
        fp = c.fingerprint()
        if fp in seen:
            continue
        seen.add(fp)
        out.append(c)
    return out
