"""Esquema único del Excel local. Los folios son texto para conservar ceros."""
import hashlib
import json
from pathlib import Path
from .attachments import _digest

HEADERS = {
    'Resumen': ['Indicador', 'Valor'],
    'Comentarios': ['Folio', 'ID consulta', 'Consulta', 'Autor', 'Fecha local México', 'Comentario',
                    'Adjuntos', 'Estado local', 'Visible último barrido', 'Primera detección', 'Última vez visto', 'ID técnico'],
    'Adjuntos': ['Folio', 'ID consulta', 'Archivo', 'URL original', 'Ruta local', 'SHA-256', 'URL Drive', 'Estado', 'ID técnico'],
    'Consultas': ['ID consulta', 'Título', 'Estado portal', 'Publicación', 'Inicio', 'Cierre', 'Reportados',
                  'Visibles capturados', 'Histórico', 'Páginas visitadas', 'Páginas esperadas', 'Validación', 'Revisión', 'Notas', 'URL'],
    'Ejecuciones': ['ID ejecución', 'Inicio', 'Fin', 'Consultas', 'Nuevos', 'Errores', 'Advertencias', 'Detalle'],
}

def local_file_status(att):
    path = Path(att.get('local') or '')
    if not path.is_file():
        return 'Pendiente', ''
    sidecar = path.with_suffix(path.suffix + '.sha256')
    checksum = _digest(path)
    if not sidecar.is_file() or sidecar.read_text().strip() != checksum:
        return 'Verificar', checksum
    return 'Local verificado', checksum

def projection(store, cfg):
    data = {name: [header.copy()] for name, header in HEADERS.items()}
    pending = 0
    for row in store.comments_for_export():
        folio = f"{row['folio']:03d}"
        attachments = json.loads(row['adjuntos_json'])
        ready = 0
        for att in attachments:
            status, checksum = local_file_status(att)
            ready += status == 'Local verificado'
            pending += status != 'Local verificado'
            key = hashlib.sha256((cfg.drive_folder_id+'|'+att['url']+'|'+checksum).encode()).hexdigest()
            saved = store.conn.execute('SELECT url FROM drive_files WHERE key=?',(key,)).fetchone()
            url = saved[0] if saved else ''
            if url and status == 'Local verificado':
                status = 'Local + Drive'
            data['Adjuntos'].append([folio, row['consulta_id'], att['nombre'], att['url'], att.get('local',''),
                                      checksum, url, status, row['hash']])
        state = 'Sin adjuntos' if not attachments else ('Completo' if ready==len(attachments) else f'Pendiente ({ready}/{len(attachments)})')
        data['Comentarios'].append([folio,row['consulta_id'],row['consulta_titulo'],row['autor'],row['fecha_texto'],
                                    row['comentario'],len(attachments),state,'Sí' if row['presente'] else 'No / archivado',
                                    row['primera_deteccion'],row['ultima_vez_visto'],row['hash']])
    for row in store.consultas_for_export():
        ok = row['validacion_ok'] and row['total_reportado']==row['total_extraido']
        data['Consultas'].append([row['id'],row['titulo'],row['estado'],row['fecha_publicacion'],row['periodo_inicio'],
                                  row['periodo_fin'],row['total_reportado'],row['total_extraido'],row['total_historico'],
                                  row['paginas_visitadas'],row['paginas_esperadas'],'OK' if ok else 'REVISAR',
                                  row['ultima_revision'],row['notas'],row['url']])
    for row in store.runs_for_export():
        data['Ejecuciones'].append([row[k] for k in ('id','inicio','fin','consultas_revisadas','comentarios_nuevos','errores','advertencias','resumen')])
    data['Resumen'] += [
        ['Fuente',cfg.base_url+'/ConsultaPublica'],['Zona horaria','America/Mexico_City'],
        ['Comentarios históricos',len(data['Comentarios'])-1],['Adjuntos registrados',len(data['Adjuntos'])-1],
        ['Adjuntos pendientes',pending],['Consultas registradas',len(data['Consultas'])-1],
        ['Última ejecución',data['Ejecuciones'][1][1] if len(data['Ejecuciones'])>1 else 'Sin ejecuciones'],
        ['Numeración','Folio global permanente: 001, 002… 1000. No depende de la posición de la fila.'],
        ['Flujo','Descarga local → Excel local → Google Sheets. Drive es opcional.'],
        ['Estado del archivo','Sin comentarios capturados' if len(data['Comentarios'])==1 else 'Generado desde el registro local'],
        ['Uso','El programa regenera este libro. Crear otra hoja en la nube para anotaciones humanas.'],
        ['Texto extenso','Los textos que exceden el límite de Excel se identifican. SQLite conserva el contenido completo.'],
    ]
    return data
