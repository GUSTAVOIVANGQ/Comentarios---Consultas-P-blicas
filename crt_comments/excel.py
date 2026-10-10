"""Excel local atómico, con enlaces relativos a los adjuntos descargados."""
import logging
import os
from pathlib import Path
from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.utils import get_column_letter
from .config import Config
from .reporting import projection, HEADERS

log = logging.getLogger(__name__)
WIDTHS = {'Resumen':[30,110], 'Comentarios':[12,14,48,30,23,85,13,24,23,25,25,45],
          'Adjuntos':[12,14,43,65,65,68,55,23,45],
          'Consultas':[14,65,22,26,27,27,17,20,16,20,20,18,27,55,65],
          'Ejecuciones':[16,27,27,16,15,15,18,100]}

def _clean(value):
    if not isinstance(value,str): return value
    value = ILLEGAL_CHARACTERS_RE.sub('',value)
    if len(value)>32000: value=value[:31900]+'\n[TRUNCADO: texto completo en SQLite]'
    return value

def export_excel(store, path, cfg=None):
    cfg=cfg or Config(data_dir=path.parent)
    data=projection(store,cfg)
    wb=Workbook();wb.remove(wb.active)
    for name, rows in data.items():
        ws=wb.create_sheet(name); ws.sheet_view.showGridLines=False
        ws.sheet_properties.pageSetUpPr.fitToPage=True
        for row in rows: ws.append([_clean(v) for v in row])
        ws.freeze_panes='B2' if name in ('Comentarios','Adjuntos') else 'A2'
        for i,width in enumerate(WIDTHS[name],1): ws.column_dimensions[get_column_letter(i)].width=width
        for cell in ws[1]:
            cell.fill=PatternFill('solid',fgColor='611232')
            cell.font=Font(name='Arial',size=11,color='FFFFFF',bold=True)
            cell.alignment=Alignment(vertical='center',horizontal='center',wrap_text=True)
        ws.row_dimensions[1].height=40
        ws.auto_filter.ref=ws.dimensions
        if ws.max_row>1:
            table=Table(displayName='CRT'+name,ref=ws.dimensions)
            table.tableStyleInfo=TableStyleInfo(name='TableStyleMedium2',showRowStripes=True)
            ws.add_table(table)
        for row in ws.iter_rows(min_row=2):
            ws.row_dimensions[row[0].row].height=52 if name!='Resumen' else 36
            for cell in row:
                if cell.data_type=='f':cell.data_type='s'
                cell.font=Font(name='Arial',size=11,color='243244')
                cell.alignment=Alignment(wrap_text=True,vertical='top')
            if name in ('Comentarios','Adjuntos'):
                row[0].number_format='@'
            for cell in row:
                if isinstance(cell.value,str) and cell.value.startswith('https://'):
                    cell.hyperlink=cell.value;cell.font=Font(name='Arial',size=11,color='165E93',underline='single')
        if name=='Adjuntos':
            for row in ws.iter_rows(min_row=2):
                if row[4].value and Path(row[4].value).is_file():
                    # Mantener juntos el Excel y adjuntos/ conserva enlaces al trasladar la carpeta.
                    row[4].hyperlink=os.path.relpath(row[4].value,path.parent).replace(os.sep,'/')
                    row[4].font=Font(name='Arial',size=11,color='165E93',underline='single')
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    try:
        wb.save(temp)
        # Leer el ZIP y encabezados antes de publicar. El XLSX anterior sobrevive si falla.
        with temp.open('rb') as f:
            check=load_workbook(f,read_only=True)
            try:
                assert check.sheetnames==list(HEADERS)
            finally: check.close()
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)
    log.info('EXCEL | guardado=%s | comentarios=%s | adjuntos=%s | bytes=%s',path.resolve(),
             len(data['Comentarios'])-1,len(data['Adjuntos'])-1,path.stat().st_size)
    return path
