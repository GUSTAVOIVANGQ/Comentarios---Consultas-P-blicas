from pathlib import Path
from unittest.mock import Mock
import pytest
from crt_comments.config import Config
from crt_comments.discovery import ids_from_html
from crt_comments.models import Comment, DetailSnapshot, Attachment, assign_keys
from crt_comments.scraper import DetailScraper, ScrapeResult
from crt_comments.safety import check_blocked, PortalBlocked
from crt_comments.storage import Store
from crt_comments.attachments import download_missing
from crt_comments.google_sync import projection, sync_sheets, sync_drive


def comment(text='Texto', attachments=None):
    return Comment('Ana', '01/10/2026 12:00', None, text, attachments or [])

def result(comments):
    assign_keys(24, comments)
    snap = DetailSnapshot('Prueba', total_reportado=len(comments), page_current=1, page_total=1)
    return ScrapeResult(24, True, snap, comments, 1, 1, True)

def test_discovery_handles_id_gaps_and_foreign_links():
    html = '<a href="/ConsultaPublica/Detalle/24">A</a><a href="/ConsultaPublica/Detalle/1000">B</a><a href="https://evil.test/ConsultaPublica/Detalle/99">C</a>'
    assert ids_from_html(html, 'https://portal.crt.gob.mx') == {24, 1000}

@pytest.mark.parametrize('html,url', [('<title>Radware Bot Manager Captcha</title>', ''), ('', 'https://validate.perfdrive.com/test')])
def test_block_is_not_missing_consulta(html, url):
    with pytest.raises(PortalBlocked): check_blocked(html, url)

def test_validation_does_not_delete_legitimate_duplicates():
    scraper = DetailScraper(None, Config())
    raw = [comment(), comment()]
    res = result(raw)
    scraper._validate(res, raw)
    assert res.validation_ok and len(set(c.key for c in raw)) == 2
    res.snapshot.total_reportado = 1
    scraper._validate(res, raw)
    assert not res.validation_ok

def test_two_passes_detect_changed_contents_even_with_equal_count():
    scraper = DetailScraper(None, Config())
    scraper._scan = Mock(side_effect=[result([comment('A')]), result([comment('B')])])
    assert not scraper.scrape(24).validation_ok

def test_snapshot_keeps_history_and_marks_removed(tmp_path):
    store = Store(tmp_path/'db')
    assert store.save_validated(24, 'https://x', result([comment('A'), comment('B')]), False) == 2
    assert store.save_validated(24, 'https://x', result([comment('B')]), False) == 0
    row = store.consultas_for_export()[0]
    assert row['total_extraido'] == 1 and row['total_historico'] == 2
    store.close()

def test_snapshot_rollback(tmp_path, monkeypatch):
    store = Store(tmp_path/'db')
    store.save_validated(24, 'https://x', result([comment('A')]), False)
    monkeypatch.setattr(store, 'upsert_consulta', Mock(side_effect=RuntimeError('fallo')))
    with pytest.raises(RuntimeError): store.save_validated(24, 'https://x', result([comment('B')]), False)
    assert store.count_comments(24) == 1
    assert store.comments_for_export()[0]['presente'] == 1
    store.close()

class Response:
    status_code = 200
    headers = {'Content-Type':'application/pdf'}
    url = 'https://portal.crt.gob.mx/adjunto-comentario/a.pdf'
    def __init__(self, data): self.data=data
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_content(self, *args, **kwargs): yield self.data

def test_attachment_integrity_and_retry(tmp_path):
    att = Attachment('a.pdf', Response.url)
    c = comment(attachments=[att])
    session = Mock()
    session.get.return_value = Response(b'<html>not pdf</html>')
    assert download_missing(session,24,[c],tmp_path,1) == 1
    assert not list(tmp_path.rglob('*.pdf'))
    session.get.return_value = Response(b'%PDF-1.4 test')
    assert download_missing(session,24,[c],tmp_path,1) == 0
    session.get.reset_mock()
    assert download_missing(session,24,[c],tmp_path,1) == 0
    session.get.assert_not_called()
    Path(att.local_path).write_bytes(b'broken')
    assert download_missing(session,24,[c],tmp_path,1) == 0
    session.get.assert_called_once()

def test_attachment_outside_origin_is_not_requested(tmp_path):
    c=comment(attachments=[Attachment('a.pdf','http://127.0.0.1/secret')])
    session=Mock()
    assert download_missing(session,24,[c],tmp_path,1) == 1
    session.get.assert_not_called()

def test_sheets_uses_raw_and_only_managed_tabs(tmp_path):
    store=Store(tmp_path/'db')
    store.save_validated(24,'https://x',result([comment('=IMPORTXML("https://evil.test")')]),False)
    cfg=Config(data_dir=tmp_path)
    data=projection(store,cfg)
    from crt_comments.excel import export_excel
    export_excel(store,cfg.xlsx_path,cfg)
    sheets=Mock(); api=sheets.spreadsheets.return_value
    api.get.return_value.execute.return_value={'sheets':[{'properties':{'title':'CRT_'+n,'sheetId':i,'gridProperties':{'rowCount':1000}}} for i,n in enumerate(data)]}
    api.values.return_value.get.return_value.execute.side_effect=[{'values':[rows[0]]} for rows in data.values()]
    sync_sheets(store,cfg,sheets)
    calls=api.values.return_value.update.call_args_list
    assert len(calls)==5
    assert all(c.kwargs['valueInputOption']=='RAW' and c.kwargs['range'].startswith("'CRT_") for c in calls)
    assert data['Comentarios'][1][5].startswith('=IMPORTXML')
    store.close()

def test_drive_retries_use_persisted_id(tmp_path):
    store=Store(tmp_path/'db')
    p=tmp_path/'a.pdf';p.write_bytes(b'%PDF-test')
    import hashlib
    p.with_suffix('.pdf.sha256').write_text(hashlib.sha256(p.read_bytes()).hexdigest())
    store.save_validated(24,'https://x',result([comment(attachments=[Attachment('a.pdf',Response.url,str(p))])]),False)
    drive=Mock()
    drive.files.return_value.generateIds.return_value.execute.return_value={'ids':['same-id']}
    drive.files.return_value.get.return_value.execute.side_effect=[RuntimeError('timeout'),{'id':'same-id','webViewLink':'https://drive.google.com/example'}]
    assert len(sync_drive(store,Config(),drive)) == 1
    assert sync_drive(store,Config(),drive) == []
    assert drive.files.return_value.generateIds.call_count == 1
    assert store.conn.execute('SELECT file_id FROM drive_files').fetchone()[0]=='same-id'
    store.close()

@pytest.mark.parametrize('broken', ['none', 'count', 'order', 'total'])
def test_pagination_contract(broken):
    from types import SimpleNamespace
    pages=[]
    for i, texts in enumerate([['A','B'], ['C','D'], ['E']], 1):
        pages.append(DetailSnapshot('Consulta', total_reportado=5, page_current=i, page_total=3,
                                    comments=[comment(t) for t in texts]))
    if broken=='count': pages[-1].comments=[]
    if broken=='order': pages[1].page_current=3
    if broken=='total': pages[-1].total_reportado=6
    scraper=DetailScraper(SimpleNamespace(page=Mock()),Config(page_delay_s=0))
    scraper._open=Mock(return_value=pages[0])
    scraper._click_next=Mock(return_value=True)
    scraper._wait_next_page=Mock(side_effect=pages[1:])
    res=scraper._scan(24)
    assert res.validation_ok == (broken=='none')
    if broken=='none': assert res.pages_visited==3 and len(res.comments)==5

def test_runner_additions_closed_consultas_and_idempotency(tmp_path, monkeypatch):
    import crt_comments.runner as runner
    from contextlib import nullcontext
    from datetime import datetime
    from crt_comments.config import MX_TZ
    cfg=Config(data_dir=tmp_path/'data',log_dir=tmp_path/'logs',download_attachments=False,page_delay_s=0,sync_drive=False)
    session=Mock()
    monkeypatch.setattr(runner,'BrowserSession',lambda cfg: nullcontext(session))
    items=[comment('A')]
    def scrape(*args):
        res=result(items.copy())
        res.snapshot.periodo_fin=datetime(2020,1,1,tzinfo=MX_TZ)
        return res
    monkeypatch.setattr(runner,'_scrape_with_retries',scrape)
    assert runner.run_once(cfg,only_ids=[24])==0
    items.append(comment('B'))
    assert runner.run_once(cfg,only_ids=[24])==0
    assert runner.run_once(cfg,only_ids=[24])==0
    store=Store(cfg.db_path)
    assert store.count_comments(24)==2
    assert store.runs_for_export()[0]['comentarios_nuevos']==0
    assert store.known_consultas()[24]['finalizada']==1
    store.close()

def test_runner_quarantines_partial_and_reports_failure(tmp_path, monkeypatch):
    import crt_comments.runner as runner
    from contextlib import nullcontext
    cfg=Config(data_dir=tmp_path/'data',log_dir=tmp_path/'logs',download_attachments=False,page_delay_s=0,sync_drive=False)
    monkeypatch.setattr(runner,'BrowserSession',lambda cfg: nullcontext(Mock()))
    partial=result([comment('A')]);partial.validation_ok=False;partial.notes=['Falta página']
    monkeypatch.setattr(runner,'_scrape_with_retries',lambda *args: partial)
    assert runner.run_once(cfg,only_ids=[24])==2
    store=Store(cfg.db_path)
    assert store.count_comments(24)==0 and store.runs_for_export()[0]['advertencias']==1
    assert list(cfg.debug_dir.glob('*.json'))
    store.close()
