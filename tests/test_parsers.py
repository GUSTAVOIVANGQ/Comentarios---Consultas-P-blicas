import copy
from datetime import datetime
from pathlib import Path

from crt_comments.models import assign_keys, dedupe
from crt_comments.parsers import parse_detail_html

FX = Path(__file__).parent / "fixtures"


def load(name):
    return parse_detail_html((FX / name).read_text(encoding="utf-8"))


def test_metadata_and_pagination():
    s = load("detalle_pagina2.html")
    assert s.titulo.startswith("Acuerdo mediante el cual")
    assert s.estado == "Consulta Activa"
    assert s.fecha_publicacion_texto == "10 de septiembre de 2026"
    assert s.total_reportado == 32
    assert s.participantes_unicos == 27
    assert (s.page_current, s.page_total) == (2, 4)
    assert not s.loading
    # periodo: 12:00 a. m. -> 00:00 ; 11:59 p. m. -> 23:59
    assert s.periodo_inicio.replace(tzinfo=None) == datetime(2026, 9, 11, 0, 0)
    assert s.periodo_fin.replace(tzinfo=None) == datetime(2026, 10, 9, 23, 59)
    assert s.periodo_fin.tzinfo is not None


def test_comments_extracted_and_no_false_positives():
    s = load("detalle_pagina2.html")
    # Las fechas de "Objetivo", "Documentos de referencia" y el <script> NO son comentarios
    assert [c.autor for c in s.comments] == [
        "Luis Alberto Arce Gómez", "JORGE RÚBEN VILCHIS HERNANDEZ",
        "Juan Pablo Ramos salido Sánchez", "Luis Alberto Gamboa Moreno",
    ]
    c0 = s.comments[0]
    assert c0.fecha_texto == "09/10/2026 14:29"
    assert c0.fecha == datetime(2026, 10, 9, 14, 29)
    assert c0.texto.startswith("Se envían observaciones al proyecto de Acuerdo")
    assert "Descargar" not in c0.texto and "DOCUMENTO ADJUNTO" not in c0.texto
    assert ".pdf" not in c0.texto


def test_attachments_from_href_and_from_onclick():
    s = load("detalle_pagina2.html")
    assert s.comments[0].attachments[0].url == \
        "https://portal.crt.gob.mx/adjunto-comentario/341dcc4e-37fe-4b3e-81c9-96e80689886f.pdf"
    assert s.comments[1].attachments[0].url == \
        "https://portal.crt.gob.mx/adjunto-comentario/b0dec5fa-55d6-402a-8c27-e06c0f30ce0f.pdf"
    assert s.comments[1].attachments[0].name == "b0dec5fa-55d6-402a-8c27-e06c0f30ce0f.pdf"
    assert s.comments[2].attachments == []
    assert s.comments[2].texto == "No estoy de acuerdo"


def test_single_comment_does_not_swallow_page():
    s = load("detalle_un_comentario.html")
    assert len(s.comments) == 1
    c = s.comments[0]
    assert c.autor == "Ana Pérez"
    assert c.texto == "Comentario único sin adjunto"
    assert s.total_reportado == 1 and (s.page_current, s.page_total) == (1, 1)


def test_loading_state():
    s = load("detalle_cargando.html")
    assert s.loading and s.comments == []


def test_not_a_detail_page():
    assert load("no_existe.html") is None


def test_keys_stable_and_duplicates_distinguished():
    a = load("detalle_pagina2.html").comments
    b = load("detalle_pagina2.html").comments
    assign_keys(24, a); assign_keys(24, b)
    assert [c.key for c in a] == [c.key for c in b]          # estables entre corridas
    assert len({c.key for c in a}) == 4
    dup = a + [copy.deepcopy(a[2])]                                         # comentario idéntico repetido
    assign_keys(24, dup)
    assert len({c.key for c in dup}) == 5                    # no se pierde ninguno
    assert len(dedupe(dup)) == 4                             # y se puede quitar si es corrimiento
