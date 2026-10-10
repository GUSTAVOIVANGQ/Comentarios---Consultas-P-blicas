"""Detección de bloqueos; nunca resolver ni eludir CAPTCHAs automáticamente."""
import re

class PortalBlocked(RuntimeError):
    pass

def check_blocked(html: str, url: str = '') -> None:
    if 'validate.perfdrive.com' in url or re.search(
        r'Radware Bot Manager Captcha|actividad y comportamiento nos hace pensar|'
        r'<title[^>]*>[^<]*(?:Just a moment|Access Denied)|'
        r'Por favor resuelva este CAPTCHA', html, re.I
    ):
        raise PortalBlocked('El portal solicita verificación humana; corrida detenida sin eludir el bloqueo')
