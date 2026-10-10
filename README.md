# CRT Comments 3.0

Proyecto Python para capturar comentarios públicos de la CRT, descargar sus
adjuntos **localmente**, generar un Excel y después publicar sus celdas en Google
Sheets. Incluye interfaz web local y ejecución cada ocho horas en Red Hat.

## Qué se completó

- Folios globales permanentes: **001, 002, 003… 1000**. No se renumeran al ordenar
  filas, recibir comentarios nuevos o retirar comentarios del portal.
- Corrección del checkbox oculto del catálogo detectado en el log adjunto.
- Captura validada de todas las páginas, con dos barridos coincidentes.
- Cola persistente de adjuntos, descargas atómicas, firma PDF y SHA-256.
- Excel local con cinco hojas, filtros, columnas legibles y enlaces relativos.
- Google Sheets recibe las celdas del **Excel previamente generado**.
- La nube no se publica si falta una copia local verificada de algún adjunto.
- UI para iniciar captura, buscar folios, descargar documentos y consultar logs.
- ZIP portable con Excel, adjuntos, checksums, manifiesto y respaldo de SQLite.
- Migración automática de las bases del borrador y de la versión 2.

El log proporcionado muestra 33 comentarios y 4 páginas en la consulta 24, y una
segunda corrida sin duplicados. El archivo no contiene los textos de esos
comentarios ni sus documentos. Por eso `templates/comentarios_crt.xlsx` es un
**Excel inicial vacío**, preparado para capturar datos reales, y no una supuesta
extracción de esos 33 comentarios. La versión 3 usa tu base existente para
conservarlos y asignarles folios. No se reutilizaron las credenciales del ZIP.

## Actualizar una instalación existente

Detener el timer/daemon y la UI, y respaldar la instalación antes de actualizar.
Reemplazar código, scripts y dependencias. **Conservar `data/`, `logs/` y las
credenciales fuera del proyecto**. No reemplazar la base por el Excel inicial.
La primera apertura de SQLite asigna folios a los registros existentes con un
orden determinista. Los nuevos reciben el siguiente número disponible.

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m crt_comments export
.venv/bin/python -m crt_comments ui
```

`export` solo regenera el libro local. Revisar los comentarios antes de usar
`retry-downloads` o `sync`. Los nombres de adjuntos antiguos se conservan cuando
sus copias son válidas. Las nuevas descargas llevan el folio, por ejemplo
`001_01_<huella>_<archivo>.pdf`. Los folios son globales en toda la instalación.

## Instalar y abrir la UI

Python 3.11 o 3.12. Linux compatible con Playwright, Windows o contenedor Podman.

### Linux

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m crt_comments ui
```

### Windows

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
.venv\Scripts\python -m crt_comments ui
```

Abrir **http://127.0.0.1:8765**. Después de instalar se puede usar
`start-ui.bat` en Windows o `./start-ui.sh` en Linux. La UI no necesita conexión
a Google para operar localmente. Configurar Google se hace mediante el JSON y
variables de entorno; el panel no muestra ni pide secretos.

| Vista o acción              | Resultado                                                              |
| ---------------------------- | ---------------------------------------------------------------------- |
| Resumen                      | Cantidades, pendientes, Excel disponible y última sincronización     |
| Iniciar captura              | Consulta el portal, valida páginas, descarga y genera el libro        |
| Comentarios                  | Busca por autor/texto/folio y permite descargar documentos locales     |
| Consultas                    | Compara reportados, visibles, histórico, páginas y última revisión |
| Actividad y logs             | Progreso, advertencias, errores y estado de operaciones manuales       |
| Regenerar Excel              | Reconstituye el libro desde SQLite sin consultar el portal             |
| Reintentar adjuntos          | Procesa la cola local y regenera el Excel                              |
| Crear paquete ZIP            | Agrupa XLSX, documentos y respaldo SQLite sin credenciales             |
| Sincronizar Excel con Sheets | Genera el libro local y después publica sus celdas                    |

Las acciones se ejecutan en segundo plano. La UI permanece disponible y no
permite lanzar otra acción mientras el mismo archivo de bloqueo esté ocupado.
El timer comparte ese bloqueo, incluso cuando corre en otro contenedor.

El panel es **local**: sirve en loopback. Para acceso al servidor, usar un túnel
SSH. El servicio Podman publica únicamente en `127.0.0.1`. Incluye comprobación de
Host, cookie de sesión, protección CSRF y escape de contenido mediante `textContent`.
No exponer el panel directamente a Internet; no incluye usuarios multiusuario.

## Flujo y archivos locales

1. Descubre consultas del índice, incluyendo finalizadas, y une IDs ya conocidos.
2. Sondea una ventana progresiva de IDs como respaldo, sin detenerse ante huecos.
3. Revisa también consultas cerradas para captar publicaciones tardías.
4. Recorre páginas consecutivas y exige igual número de comentarios y páginas
   que el reportado. Repite el barrido y exige contenido coincidente.
5. Guarda una captura válida en una transacción SQLite y asigna folios.
6. Descarga la cola de adjuntos de todos los comentarios guardados, incluidos
   históricos. Verifica origen, firma, tamaño y checksum antes de publicar el archivo.
7. Genera el Excel local. Los archivos pendientes aparecen identificados en él.
8. Si está activada la nube y todas las copias locales están verificadas, publica
   el contenido de ese XLSX en las pestañas CRT_* de Google Sheets.

| Archivo                          | Uso                                                     |
| -------------------------------- | ------------------------------------------------------- |
| `data/crt_comentarios.sqlite3` | Fuente principal, folios, histórico y cola de adjuntos |
| `data/comentarios_crt.xlsx`    | Excel local generado automáticamente                   |
| `data/adjuntos/<consulta>/`    | Documentos locales y checksums`.sha256`               |
| `data/paquete_local.zip`       | Paquete descargable con enlaces relativos funcionales   |
| `data/debug/`                  | HTML/JSON de capturas parciales para diagnóstico       |
| `logs/crt_comments.log`        | Operaciones de captura, descargas, Excel y nube         |
| `logs/crt_ui.log`              | Operaciones de la interfaz y errores HTTP               |

El ZIP debe **extraerse completo** en una carpeta. El XLSX apunta a
`adjuntos/<consulta>/...` con enlaces relativos. El Excel contiene enlaces al
archivo, no el PDF incrustado como objeto. La URL original y el checksum también
quedan en la hoja `Adjuntos`. Al llevar el contenido a Sheets, la ruta local
sigue siendo un dato de auditoría: Google no puede abrir una ruta de tu disco.
Para abrir documentos desde la nube, activar la subida opcional a Drive.

## Estructura del Excel y Sheets

| Hoja local  | Pestaña Google | Datos                                                              |
| ----------- | --------------- | ------------------------------------------------------------------ |
| Resumen     | CRT_Resumen     | Fuente, cantidades, pendientes y uso                               |
| Comentarios | CRT_Comentarios | Folio, consulta, autor, fecha, texto, presencia y estado local     |
| Adjuntos    | CRT_Adjuntos    | Folio, nombre, URL original, ruta local, SHA-256 y URL Drive       |
| Consultas   | CRT_Consultas   | Periodos, reportados, visibles, histórico, páginas y validación |
| Ejecuciones | CRT_Ejecuciones | Inicio, fin, nuevos, errores y diagnóstico                        |

La UI, el Excel, la carpeta de documentos y Sheets comparten el folio. El ID
técnico por contenido también se conserva para idempotencia. El programa
regenera el libro: escribir anotaciones humanas en otro archivo o una pestaña
Google fuera de CRT_*. Los textos mayores al límite de Excel se recortan con una
marca; SQLite conserva el texto íntegro. Los folios se exportan como **texto**,
para que `001` no se convierta en `1`.

En ausencia de un ID nativo de comentario verificado, una edición de contenido
se conserva como otro registro/versión y recibe otro folio. Duplicados idénticos
se distinguen por ocurrencia. Un retirado se conserva con `presente=0`; el
histórico no se suma al conteo de visibles. Un fallo conserva la última captura
válida y marca `REVISAR`. Siempre consultar la fecha de revisión.

## Comandos

```bash
python -m crt_comments run --only 24,25,26
python -m crt_comments run
python -m crt_comments export
python -m crt_comments retry-downloads
python -m crt_comments bundle
python -m crt_comments sync
python -m crt_comments ui --port 8765
```

- `run` descubre nuevas consultas y revisa conocidas. Por defecto solo opera local.
- `export`, `retry-downloads` y `bundle` no escriben en Google.
- `sync` activa explícitamente la escritura en Sheets para esa operación.
- Para publicar automáticamente en cada corrida: `CRT_SYNC_SHEETS=true`.
- Para revisar también IDs históricos menores a 24: `CRT_START_ID=1`.
- `-v` antes del subcomando habilita detalle adicional.
- `daemon` es alternativa al timer. No usar ambos para programar la misma instalación.

Los logs usan hora de Ciudad de México y registran consulta, barrido, página,
folio nuevo, autor, fecha, archivo, bytes, SHA-256, ruta, Excel y resultado final.
Rotación: 5 archivos de respaldo de hasta 5 MB. No se escribe el cuerpo completo
del comentario en los logs. Ejemplo de estructura, no de una descarga real:

```text
CORRIDA 12 | INICIO | datos=... | descarga_local=True | sheets=False | drive=False
PAGINA | consulta=24 | página=2/4 | comentarios=10
COMENTARIO | folio=001 | consulta=24 | nuevo | autor=... | fecha=... | adjuntos=1
ADJUNTO | consulta=24 folio=001 | guardado=... | bytes=... | sha256=...
EXCEL | guardado=... | comentarios=33 | adjuntos=... | bytes=...
CORRIDA | FIN | consultas=... nuevos=... errores=... advertencias=... duración=...
```

## Google: configurar para después

Documento preconfigurado:
https://docs.google.com/spreadsheets/d/115cwia3lnqSTNIy8ndqlp7woN1tWnepjpMIZTxgn-28/edit

Habilitar Sheets API y Drive API en Google Cloud. Para Sheets en servidor, una
cuenta de servicio funciona si su `client_email` tiene permiso de editor sobre
el documento. Instalar su JSON en una ruta privada y configurar:

```bash
export CRT_GOOGLE_CREDENTIALS=/ruta/privada/google.json
python -m crt_comments sync
```

Sheets recibe rangos deterministas de celdas del XLSX, con `RAW`, conservando los
folios y tratando posibles fórmulas del portal como texto. No usa `append_row`.
Reintentar no duplica filas. Conserva otras pestañas. La migración acepta solo
los encabezados antiguos conocidos de la versión 2; un encabezado desconocido
frena la escritura para proteger contenido ajeno.

Una sincronización entre pestañas no es atómica. Si se interrumpe, reintentar
`sync`. SQLite y el Excel local sobreviven al error. La última fecha de
sincronización exitosa queda registrada en la UI. Para automatizar, activar
`CRT_SYNC_SHEETS=true` antes de arrancar la UI/timer.

### Drive opcional

Los adjuntos se guardan **solo localmente por defecto**. Activar Drive requiere
`CRT_SYNC_DRIVE=true` y revisar el ID de carpeta copiado del test:
`CRT_DRIVE_FOLDER_ID=11zSH8drBUGh6yjwpmPjBayrKm7doNwcT`.
Una cuenta de servicio debe usar una unidad compartida con acceso adecuado;
para Mi unidad, usar OAuth del usuario que tiene almacenamiento.

```bash
python scripts/authorize_google.py --client /ruta/client_secret.json --output /ruta/privada/google.json
```

Ejecutar ese paso una vez en una estación con navegador y transferir el JSON por
un canal seguro. El timer no abre pantallas de login. El token puede revocarse o
vencer; una app OAuth en Testing puede requerir nueva autorización. El scope
Drive completo permite acceder a la carpeta preexistente del ejemplo. El programa
no modifica permisos ni hace públicos los documentos. No carga archivos pickle.
Drive utiliza un ID de archivo persistido antes de la subida para reintentar un
timeout sin crear otro archivo. Después de subir, regenera el Excel con las URLs
Drive y publica ese archivo en Sheets. La copia local se mantiene.

## Red Hat con Podman y systemd

Requiere RHEL 8/9 x86_64, Podman y salida HTTPS al portal, PyPI, registro Microsoft
y Google cuando se active. La imagen y el paquete Playwright tienen la misma
versión. El navegador corre sin root y con sandbox. Las políticas del servidor
pueden requerir un perfil de namespaces/seccomp aprobado por su administrador.
No se ha desplegado esta versión en tu servidor.

```bash
sudo bash deploy/install_rhel.sh
sudo systemctl start crt-comments.service
sudo journalctl -u crt-comments.service -n 100 --no-pager
sudo systemctl enable --now crt-comments-ui.service
```

Acceso al panel desde tu equipo:

```bash
ssh -L 8765:127.0.0.1:8765 usuario@servidor
```

Abrir http://127.0.0.1:8765. Los servicios comparten volúmenes con etiquetas
SELinux `:z` y el mismo bloqueo de SQLite. Para Google, instalar el JSON con
permisos restringidos y editar `/etc/crt-comments/crt.env`, luego reiniciar la UI.
La instalación conserva una configuración existente y no habilita el timer
antes de la prueba inicial.

```bash
sudo install -o crtbot -g crtbot -m 0600 /ruta/privada/google.json /etc/crt-comments/secrets/google.json
sudoedit /etc/crt-comments/crt.env
sudo systemctl restart crt-comments-ui.service
sudo systemctl enable --now crt-comments.timer
sudo systemctl list-timers crt-comments.timer
```

Horario: **00:00, 08:00 y 16:00 America/Mexico_City**, sin fecha final.
`Persistent=true` recupera una ejecución omitida por apagado. Tiempo máximo del
servicio de captura: seis horas. Códigos: 0 completo, 2 parcial/error, 3 ocupado;
2 y 3 se reportan como fallos al monitoreo.

Datos: `/var/lib/crt-comments/data`; logs: `/var/log/crt-comments`; secretos y
configuración: `/etc/crt-comments`. Actualizar la imagen no debe borrar estos
volúmenes. Dos servidores con copias independientes de SQLite no deben escribir
en las mismas pestañas. El bloqueo cubre una instalación con volumen compartido.

## Desarrollo, límites y aceptación

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```

La suite cubre folios, migración, descarga, Excel, nube simulada, ZIP e interfaz
por HTTP, incluyendo un proceso real de exportación lanzado desde el panel. El
E2E usa un portal local con JavaScript y se omite si Chromium no arranca. Ver
`VALIDACION.md`; en CI/servidor debe pasar también esa prueba.

Módulos: `discovery` catálogo, `parsers` HTML, `scraper` paginación, `storage`
transacciones y folios, `attachments` descarga, `reporting` esquema, `excel`
XLSX, `google_sync` nube, `bundle` ZIP, `ui` HTTP, `runner` coordinación y
`__main__` CLI. Todos los campos de `Config` admiten variables `CRT_<NOMBRE>`.

No puede capturar comentarios publicados y retirados entre corridas, contenido
privado ni IDs ocultos más allá de la ventana de respaldo. CAPTCHA, rediseño,
límites de Sheets/Excel o errores de permisos requieren atención. Un CAPTCHA
se reporta y detiene el acceso al portal; no se elude. El scraping real completo
no pudo verificarse en este entorno. Se conserva el registro local ante errores
y se reintenta sin perder el histórico. Para crecimiento prolongado, archivar
por año o migrar el almacenamiento antes de alcanzar límites de filas/celdas.

Respaldar SQLite con su API `backup` o el paquete ZIP, y mantener adjuntos y
configuración. No copiar solo el `.sqlite3` mientras WAL esté activo. Monitorear
fallos, espacio libre y ausencia de ejecuciones exitosas durante más de 16 horas.

Documentación oficial consultada:

- https://playwright.dev/python/docs/docker
- https://playwright.dev/python/docs/browsers
- https://developers.google.com/workspace/sheets/api/guides/values
- https://developers.google.com/workspace/drive/api/guides/about-shareddrives
- https://developers.google.com/identity/protocols/oauth2
