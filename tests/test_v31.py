"""Regresiones encontradas en el Excel enviado por el usuario."""
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock
import hashlib
import pytest
from crt_comments.config import Config
from crt_comments.discovery import CatalogIncomplete, plan_ids
from crt_comments.parsers import parse_detail_html
from crt_comments.scraper import DetailScraper, ScrapeResult
from crt_comments.storage import Store
from crt_comments.google_sync import validate_drive_folder, sync_drive
from crt_comments.models import Attachment, assign_keys
from test_robustness import comment, result

def single_page(total=5, count=5, tail=''):
    cards=''.join(f'<article><b>Autor {i}</b><em>01/10/2026 08:05</em><p>Texto {i}</p></article>' for i in range(count))
    return f'<h1>Consulta 25</h1><section><h2>Comentarios de la Ciudadanía</h2><span>{total} comentarios</span>{cards}{tail}</section>'

def test_five_comments_without_paginator_validate_twice():
    snap=parse_detail_html(single_page())
    assert len(snap.comments)==5 and (snap.page_current,snap.page_total)==(1,1)
    scraper=DetailScraper(Mock(),Config())
    scraper._open=Mock(return_value=snap)
    scraper._click_next=Mock()
    res=scraper.scrape(25)
    assert res.validation_ok and res.pages_visited==1 and len(res.comments)==5
    assert scraper._open.call_count==2
    scraper._click_next.assert_not_called()

@pytest.mark.parametrize('total,count,tail',[(5,4,''),(5,5,'<button>Siguiente</button>'),(5,5,'<span>Cargando comentarios</span>')])
def test_cannot_infer_single_page_with_partial_loading_or_enabled_next(total,count,tail):
    assert parse_detail_html(single_page(total,count,tail)).page_total is None

def test_plural_page_counter():
    snap=parse_detail_html(single_page(tail='<div>Páginas 1 de 1</div>'))
    assert (snap.page_current,snap.page_total)==(1,1)

def test_regular_run_revisits_entire_known_range_and_future_window():
    ids,cursor=plan_ids(Config(probe_batch=8),{24,26},{24,40},100)
    assert set(range(24,49)).issubset(ids)
    assert set(range(100,108)).issubset(ids) and cursor==108
    assert 23 not in ids

def test_historical_scan_does_not_stop_at_gaps_and_includes_closed():
    ids,cursor=plan_ids(Config(),{24,26},set(),24,end_id=100,historical=True)
    assert ids==set(range(1,101)) and cursor is None

def test_runner_keeps_partial_catalog_and_revisits_25(tmp_path,monkeypatch):
    import crt_comments.runner as runner
    cfg=Config(data_dir=tmp_path/'data',log_dir=tmp_path/'logs',sync_drive=False,download_attachments=False,page_delay_s=0,probe_batch=2)
    monkeypatch.setattr(runner,'BrowserSession',lambda _:nullcontext(Mock()))
    def catalog(*_):raise CatalogIncomplete('12 de 20', {24,26,40})
    monkeypatch.setattr(runner,'discover',catalog)
    seen=[]
    def scrape(*args):
        cid=args[-1];seen.append(cid)
        if cid==25:
            snap=parse_detail_html(single_page())
            assign_keys(25,snap.comments)
            return ScrapeResult(25,True,snap,snap.comments,1,1,True)
        return ScrapeResult(cid,False)
    monkeypatch.setattr(runner,'_scrape_with_retries',scrape)
    assert runner.run_once(cfg)==2  # El catálogo parcial sigue visible como advertencia.
    assert seen==list(range(24,43))
    st=Store(cfg.db_path);assert st.count_comments(25)==5;st.close()

def test_drive_rejects_unwritable_folder_before_upload():
    drive=Mock();drive.files.return_value.get.return_value.execute.return_value={
        'mimeType':'application/vnd.google-apps.folder','capabilities':{'canAddChildren':False}}
    with pytest.raises(PermissionError):validate_drive_folder(Config(),drive,Mock())
    drive.files.return_value.create.assert_not_called()

def test_drive_failure_is_logged_and_resume_has_no_duplicate(tmp_path,caplog):
    st=Store(tmp_path/'db');path=tmp_path/'001_a.pdf';path.write_bytes(b'%PDF-test')
    path.with_suffix('.pdf.sha256').write_text(hashlib.sha256(path.read_bytes()).hexdigest())
    st.save_validated(24,'https://x',result([comment(attachments=[Attachment('a.pdf','https://portal.crt.gob.mx/adjunto-comentario/a.pdf',str(path))])]),False)
    drive=Mock();drive.files.return_value.generateIds.return_value.execute.return_value={'ids':['stable-id']}
    drive.files.return_value.get.return_value.execute.side_effect=[RuntimeError('sin permiso'),{'id':'stable-id','webViewLink':'https://drive.google.com/file/d/stable-id/view'}]
    assert sync_drive(st,Config(),drive)==['a.pdf']
    assert 'folio=001' in caplog.text and 'sin permiso' in caplog.text
    assert sync_drive(st,Config(),drive)==[]
    assert sync_drive(st,Config(),drive)==[]
    assert drive.files.return_value.generateIds.call_count==1
    assert st.get_meta('last_drive_synced_at')
    st.close()

def test_cli_explicit_commands_and_default_drive(tmp_path,monkeypatch):
    import crt_comments.__main__ as cli
    from dataclasses import replace
    cfg=Config(data_dir=tmp_path/'data',log_dir=tmp_path/'logs',sync_drive=False)
    monkeypatch.setattr(cli.Config,'from_env',lambda:replace(cfg))
    run=Mock(return_value=0);export=Mock(return_value=0)
    monkeypatch.setattr(cli,'run_once',run);monkeypatch.setattr(cli,'export_only',export)
    assert cli.main(['scan-all','--end-id','150'])==0
    assert run.call_args.kwargs=={'historical':True,'end_id':150}
    assert cli.main(['sync-drive'])==0
    conf=export.call_args.args[0]
    assert conf.sync_drive and not conf.sync_sheets
    assert export.call_args.kwargs=={'retry_attachments':True,'cloud':True}
    assert Config().sync_drive

def test_drive_creates_binary_with_reserved_id_and_returns_url(tmp_path):
    import json
    from googleapiclient.errors import HttpError
    st=Store(tmp_path/'db');path=tmp_path/'001_a.pdf';path.write_bytes(b'%PDF-test')
    path.with_suffix('.pdf.sha256').write_text(hashlib.sha256(path.read_bytes()).hexdigest())
    st.save_validated(24,'https://x',result([comment(attachments=[Attachment('a.pdf','https://portal.crt.gob.mx/adjunto-comentario/a.pdf',str(path))])]),False)
    drive=Mock();api=drive.files.return_value
    api.generateIds.return_value.execute.return_value={'ids':['reserved-id']}
    response=Mock(status=404,reason='Not Found')
    api.get.return_value.execute.side_effect=HttpError(response,b'{"error":{"message":"missing"}}')
    def create(**kwargs):
        assert st.conn.execute('SELECT file_id FROM drive_files').fetchone()[0]=='reserved-id'
        assert kwargs['body']['id']=='reserved-id'
        assert kwargs['body']['parents']==[Config().drive_folder_id]
        assert kwargs['media_body'].size()==len(b'%PDF-test')
        request=Mock();request.execute.return_value={'id':'reserved-id','webViewLink':'https://drive.google.com/file/d/reserved-id/view'}
        return request
    api.create.side_effect=create
    assert sync_drive(st,Config(),drive)==[]
    assert sync_drive(st,Config(),drive)==[]
    assert api.create.call_count==1
    assert st.conn.execute('SELECT url FROM drive_files').fetchone()[0].endswith('/reserved-id/view')
    st.close()

def test_service_account_cannot_silently_target_my_drive(monkeypatch):
    import crt_comments.google_sync as google
    class ServiceAccount:pass
    monkeypatch.setattr(google,'ServiceCredentials',ServiceAccount)
    drive=Mock();drive.files.return_value.get.return_value.execute.return_value={
        'mimeType':'application/vnd.google-apps.folder','capabilities':{'canAddChildren':True}}
    with pytest.raises(ValueError,match='OAuth'):
        validate_drive_folder(Config(),drive,ServiceAccount())
    drive.files.return_value.create.assert_not_called()
