"""Parser de la página ``/ConsultaPublica/Detalle/<id>``.

Es una función pura: recibe el HTML ya renderizado (``page.content()``) y
devuelve un ``DetailSnapshot``. No depende del navegador, por lo que se puede
probar con archivos HTML guardados (ver ``tests/``).

Diseño: NO depende de nombres de clases CSS (que cambian con los rediseños).
Se ancla en elementos estables del contenido visible:

* El encabezado "Comentarios de la Ciudadanía" delimita la sección.
* Cada comentario es el bloque más grande que contiene EXACTAMENTE una fecha
  con formato ``dd/mm/aaaa hh:mm``.
* "Página X de Y", "N comentarios", "Periodo de la consulta" se leen del texto.

Si el portal cambia su estructura, este es el único archivo a ajustar.
"""
from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .dates import parse_comment_datetime, parse_long_datetimes
from .models import Attachment, Comment, DetailSnapshot

HEADING_RE = re.compile(r"Comentarios\s+de\s+la\s+Ciudadan[ií]a", re.I)
PAGINATION_RE = re.compile(r"Siguientes|Anteriores", re.I)
DOCS_RE = re.compile(r"Documentos\s+de\s+Referencia", re.I)
# La fecha del comentario va al final del nodo de texto: "09/10/2026 14:29"
TAIL_DATE_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s+(\d{1,2}):(\d{2})\s*$")
ATTACH_LABEL_RE = re.compile(r"^documentos?\s+adjuntos?$", re.I)
ATTACH_PATH_RE = re.compile(r"adjunto-comentario/([^\"'\s<>?#&]+)", re.I)
UI_NOISE_RE = re.compile(r"^(descargar|ver adjunto)$", re.I)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _flat_text(soup: BeautifulSoup) -> str:
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def _is_date_node(s: str) -> bool:
    s = s.strip()
    return 0 < len(s) <= 80 and bool(TAIL_DATE_RE.search(s))


def _has_date_nodes(el: Tag) -> int:
    return len(el.find_all(string=lambda s: s and _is_date_node(s)))


def _contains(el: Tag, regex: "re.Pattern", maxlen: int = 80) -> bool:
    return el.find(string=lambda s: s and len(s.strip()) <= maxlen and regex.search(s)) is not None


# ---------------------------------------------------------------------------
# Localización de la sección y de las tarjetas de comentario
# ---------------------------------------------------------------------------
def _find_section(soup: BeautifulSoup) -> Optional[Tag]:
    """Contenedor de la sección de comentarios (encabezado + lista + paginación)."""
    heading = soup.find(string=lambda s: s and HEADING_RE.search(s))
    if heading is None:
        return None
    first_with_dates: Optional[Tag] = None
    for anc in heading.parents:
        if anc.name in ("body", "html", "[document]"):
            break
        if _contains(anc, PAGINATION_RE, maxlen=20):
            return anc
        # Sin botones de paginación (p.ej. una sola página): se usa el contenedor
        # más pequeño que tenga fechas de comentario, SIN subir hasta el título de
        # la consulta ni a "Documentos de Referencia" (que también traen fechas).
        if anc.find("h1") is not None or _contains(anc, DOCS_RE, maxlen=60):
            break
        if first_with_dates is None and _has_date_nodes(anc):
            first_with_dates = anc
    return first_with_dates or heading.parent


def _card_for(date_node, section: Tag) -> Tag:
    """Sube desde el nodo de fecha hasta el bloque más grande con UNA sola fecha."""
    card = date_node.parent
    for anc in date_node.parents:
        if anc is section or anc.name in ("body", "html", "[document]"):
            break
        if _contains(anc, HEADING_RE, maxlen=80) or _contains(anc, PAGINATION_RE, maxlen=20):
            break
        if _has_date_nodes(anc) > 1:
            break
        card = anc
    return card


def _parse_card(card: Tag, base_url: str) -> Optional[Comment]:
    lines = [ln.strip() for ln in card.get_text("\n", strip=True).split("\n") if ln.strip()]

    # 1) línea de la fecha ---------------------------------------------------
    date_idx = next((i for i, ln in enumerate(lines) if TAIL_DATE_RE.search(ln)), None)
    if date_idx is None:
        return None
    m = TAIL_DATE_RE.search(lines[date_idx])
    fecha_texto = f"{m.group(1)}/{m.group(2)}/{m.group(3)} {m.group(4)}:{m.group(5)}"
    fecha = parse_comment_datetime(*m.groups())

    # 2) autor: lo que va antes de la fecha ---------------------------------
    author_parts = lines[:date_idx]
    prefix = lines[date_idx][: m.start()].strip()
    if prefix:
        author_parts.append(prefix)
    autor = re.sub(r"\s+", " ", " ".join(author_parts)).strip()

    # 3) adjuntos ------------------------------------------------------------
    body = lines[date_idx + 1:]
    attachments: List[Attachment] = []
    filename_lines = set()
    for i, ln in enumerate(body):
        if ATTACH_LABEL_RE.match(ln):
            filename_lines.add(i)           # la etiqueta
            if i > 0:
                filename_lines.add(i - 1)   # el nombre del archivo va justo antes

    urls: List[str] = []
    for a in card.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("javascript", "#", "mailto")):
            continue
        if ATTACH_PATH_RE.search(href):
            urls.append(urljoin(base_url + "/", href))
    for name in ATTACH_PATH_RE.findall(str(card)):
        urls.append(f"{base_url}/adjunto-comentario/{name}")
    if not urls:
        for i in sorted(filename_lines):
            cand = body[i]
            if "." in cand and " " not in cand:
                urls.append(f"{base_url}/adjunto-comentario/{cand}")
    seen = set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        attachments.append(Attachment(name=u.rstrip("/").rsplit("/", 1)[-1], url=u))

    # 4) texto del comentario ----------------------------------------------
    text_lines = [
        ln for i, ln in enumerate(body)
        if i not in filename_lines and not UI_NOISE_RE.match(ln)
    ]
    texto = "\n".join(text_lines).strip()

    return Comment(autor=autor, fecha_texto=fecha_texto, fecha=fecha, texto=texto, attachments=attachments)


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
def parse_detail_html(html: str, base_url: str = "https://portal.crt.gob.mx") -> Optional[DetailSnapshot]:
    """Devuelve el snapshot o ``None`` si el HTML no es una página de detalle válida."""
    base_url = base_url.rstrip("/")
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()

    for t in list(soup.select('[hidden], [aria-hidden="true"], .d-none')):
        t.decompose()
    for t in list(soup.find_all(style=re.compile(r'display\s*:\s*none', re.I))):
        t.decompose()
    h1 = soup.find("h1")
    titulo = re.sub(r"\s+", " ", h1.get_text(" ", strip=True)) if h1 else ""
    text = _flat_text(soup)
    if not titulo or not HEADING_RE.search(text):
        return None

    snap = DetailSnapshot(titulo=titulo)

    if (m := re.search(r"\bConsulta\s+(Activa|Finalizada|Cerrada|Concluida)\b", text, re.I)):
        snap.estado = f"Consulta {m.group(1).capitalize()}"

    if (m := re.search(r"Fecha de publicaci[oó]n:\s*(\d{1,2} de [a-záéíóú]+ de \d{4})", text, re.I)):
        snap.fecha_publicacion_texto = m.group(1)

    if (m := re.search(r"Periodo de la consulta:\s*(.{0,220})", text, re.I)):
        seg = m.group(1)
        dts = parse_long_datetimes(seg)
        if dts:
            snap.periodo_inicio = dts[0]
        if len(dts) > 1:
            snap.periodo_fin = dts[1]
        snap.periodo_texto = seg.split(" Objetivo")[0].strip()

    # Total reportado por el portal (el chip "32 comentarios" junto al título)
    if (m := re.search(r"Comentarios de la Ciudadan[ií]a\s+(\d+)\s+comentarios?", text, re.I)):
        snap.total_reportado = int(m.group(1))
    elif (m := re.search(r"Indicadores de participaci[oó]n\s+Comentarios\s+(\d+)", text, re.I)):
        snap.total_reportado = int(m.group(1))

    if (m := re.search(r"Participantes\s+[uú]nicos\s+(\d+)", text, re.I)):
        snap.participantes_unicos = int(m.group(1))

    if (m := re.search(r"P[aá]gina\s+(\d+)(?:\s+de\s+(\d+))?", text)):
        snap.page_current = int(m.group(1))
        snap.page_total = int(m.group(2)) if m.group(2) else None

    if snap.total_reportado == 0 and re.search(r'No (?:hay|se han (?:publicado|registrado)) comentarios', text, re.I):
        snap.page_current = snap.page_current or 1
        snap.page_total = snap.page_total or 1
    snap.loading = bool(re.search(r"Cargando comentarios", text, re.I))

    # Comentarios ----------------------------------------------------------
    section = _find_section(soup)
    if section is not None:
        date_nodes = section.find_all(string=lambda s: s and _is_date_node(s))
        seen_cards = set()
        for dn in date_nodes:
            card = _card_for(dn, section)
            if id(card) in seen_cards:
                continue
            seen_cards.add(id(card))
            c = _parse_card(card, base_url)
            if c:
                snap.comments.append(c)
    return snap
