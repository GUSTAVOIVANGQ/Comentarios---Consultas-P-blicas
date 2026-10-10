"""Parseo de las fechas que muestra el portal (en español)."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import List, Optional

from .config import MX_TZ

MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

# "11 de septiembre de 2026, a las 12:00 a. m."  (tolera "a.m.", "AM", espacios raros)
_LONG_DT_RE = re.compile(
    r"(?P<d>\d{1,2})\s+de\s+(?P<mo>[a-záéíóú]+)\s+de\s+(?P<y>\d{4}),?\s*"
    r"(?:a\s+las\s+)?(?P<h>\d{1,2}):(?P<mi>\d{2})\s*(?P<ap>[ap])\.?\s*m\.?",
    re.IGNORECASE,
)
_LONG_DATE_RE = re.compile(
    r"(?P<d>\d{1,2})\s+de\s+(?P<mo>[a-záéíóú]+)\s+de\s+(?P<y>\d{4})", re.IGNORECASE
)


def parse_long_datetimes(text: str) -> List[datetime]:
    """Devuelve todas las fechas-hora largas halladas, con tz de Ciudad de México."""
    out: List[datetime] = []
    for m in _LONG_DT_RE.finditer(text):
        month = MONTHS.get(m.group("mo").lower())
        if not month:
            continue
        hour = int(m.group("h")) % 12
        if m.group("ap").lower() == "p":
            hour += 12
        out.append(
            datetime(int(m.group("y")), month, int(m.group("d")), hour, int(m.group("mi")), tzinfo=MX_TZ)
        )
    return out


def parse_long_date(text: str) -> Optional[date]:
    m = _LONG_DATE_RE.search(text)
    if not m:
        return None
    month = MONTHS.get(m.group("mo").lower())
    if not month:
        return None
    return date(int(m.group("y")), month, int(m.group("d")))


def parse_comment_datetime(dd: str, mm: str, yyyy: str, hh: str, mi: str) -> Optional[datetime]:
    """'09','10','2026','14','29' -> datetime naive (hora de Ciudad de México)."""
    try:
        return datetime(int(yyyy), int(mm), int(dd), int(hh), int(mi))
    except ValueError:
        return None
