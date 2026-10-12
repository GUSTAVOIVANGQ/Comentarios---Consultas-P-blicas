# Ejecución permanente en Red Hat

El programa necesita un servidor o VM RHEL 8/9 x86_64 con disco persistente y
salida HTTPS. El panel web permite operarlo; systemd ejecuta la captura sin
depender de un navegador abierto ni de mantener una sesión SSH conectada.
Un alojamiento de páginas estáticas no ejecuta este proceso Python/Chromium.

## 1. Actualizar sin perder los 33 comentarios

Detener las operaciones y hacer un respaldo antes de cambiar la imagen.
Conservar `data/` completo y las credenciales. No sustituir la base por la
plantilla Excel. Si ya está instalado en el servidor:

```bash
sudo systemctl stop crt-comments.timer crt-comments-ui.service
sudo systemctl stop crt-comments.service
```

Extraer el ZIP y entrar en `crt-comments`. En una instalación nueva, trasladar
la carpeta de datos anterior al volumen `/var/lib/crt-comments/data` antes de
capturar. La base y los adjuntos deben pertenecer a `crtbot:crtbot`. Si se
copian desde Windows, las rutas absolutas antiguas pueden ser inválidas: el
comando `retry-downloads` reconstruye las copias locales en el servidor.

## 2. Instalar

```bash
sudo bash deploy/install_rhel.sh
```

Esto instala Podman, construye `localhost/crt-comments:3.1` e instala los tres
servicios. Conserva el archivo de configuración existente; por eso una
configuración antigua con `CRT_SYNC_DRIVE=false` seguirá desactivando Drive
hasta editarla. No borra datos ni habilita el timer automáticamente.

## 3. Autorizar Google

Habilitar Drive API y Sheets API en el proyecto Google Cloud.

Para una carpeta de **Mi unidad**, generar el JSON OAuth con la cuenta que tiene
acceso y almacenamiento. Ejecutar en una estación con navegador:

```bash
python scripts/authorize_google.py --client /ruta/client_secret.json --output /ruta/privada/google.json
```

Para una **unidad compartida de Workspace**, se puede usar un JSON de cuenta de
servicio que tenga permiso de escritura. Una carpeta compartida de Mi unidad
no se convierte por ello en una unidad compartida. Las cuentas de servicio no
tienen cuota propia para crear archivos en Mi unidad.

Instalar el JSON en el servidor y editar las variables:

```bash
sudo install -o crtbot -g crtbot -m 0600 /ruta/privada/google.json /etc/crt-comments/secrets/google.json
sudoedit /etc/crt-comments/crt.env
```

Valores para actualizar adjuntos y Sheets en cada corrida:

```ini
CRT_START_ID=24
CRT_DOWNLOAD_ATTACHMENTS=true
CRT_SYNC_DRIVE=true
CRT_SYNC_SHEETS=true
CRT_GOOGLE_CREDENTIALS=/secrets/google.json
CRT_DRIVE_FOLDER_ID=11zSH8drBUGh6yjwpmPjBayrKm7doNwcT
CRT_SPREADSHEET_ID=115cwia3lnqSTNIy8ndqlp7woN1tWnepjpMIZTxgn-28
```

Verificar que el ID de carpeta sea el destino deseado. La cuenta usada debe
poder agregar archivos allí y editar el documento Sheets. El programa no
cambia permisos ni hace públicos los documentos.

Para operación prolongada con OAuth externo, no dejar la app en **Testing**:
con los permisos de Drive/Sheets sus refresh tokens expiran en siete días.
Configurar el estado de publicación y, cuando corresponda, la verificación
de Google o el uso interno en Workspace. Incluso en producción, una
revocación o un cambio administrativo puede requerir autorizar nuevamente.

## 4. Ejecutar y comprobar

```bash
sudo bash deploy/operate_rhel.sh export
sudo bash deploy/operate_rhel.sh run --only 24,25,26
sudo bash deploy/operate_rhel.sh sync-drive
sudo bash deploy/operate_rhel.sh sync
```

La consulta 25 debe validarse con el total de comentarios que tenga al momento
de la captura, aunque no exista paginador. Comprobar en Excel los totales y las
URLs Drive. No basta con que existan archivos en local. Drive registra cada
éxito/fallo en logs y conserva el ID remoto antes de subir para evitar
duplicados al reintentar.

Para el histórico completo del rango **1..100**, sin detenerse ante IDs vacíos:

```bash
sudo bash deploy/operate_rhel.sh scan-all --end-id 100
```

Sin `--end-id`, usa el mayor ID conocido o descubierto + `CRT_MAX_ID_SPAN`
(500 por defecto). El log declara el límite real. Un espacio de IDs sin límite
no se puede terminar de recorrer: ampliar ese límite cuando sea necesario.
Las consultas cerradas también se revisan. Si aparece un CAPTCHA o falla la
estructura, se señala el barrido incompleto y se conservan los datos previos.

## 5. Activar el horario sin fecha final

```bash
sudo systemctl enable --now crt-comments-ui.service
sudo systemctl start crt-comments.service
sudo journalctl -u crt-comments.service -n 100 --no-pager
sudo systemctl enable --now crt-comments.timer
sudo systemctl list-timers --all crt-comments.timer
```

El timer ejecuta a las **00:00, 08:00 y 16:00 de Ciudad de México**. Al estar
habilitado, vuelve a funcionar tras reiniciar el servidor. `Persistent=true`
ejecuta una corrida de recuperación si venció un horario mientras estaba
apagado; no reproduce por separado todas las corridas perdidas.

El servidor debe seguir encendido y tener red y espacio disponible. La
captura tiene un límite de seis horas por ejecución. Las ejecuciones fallidas
no deshabilitan los siguientes horarios. SQLite y el bloqueo compartido
impiden dos capturas simultáneas dentro de esta instalación. No programar
además `daemon`, cron o una tarea ChatGPT para la misma base.

## 6. Abrir el panel

Desde tu computadora:

```bash
ssh -L 8765:127.0.0.1:8765 usuario@servidor
```

Abrir `http://127.0.0.1:8765`. Hay acciones para captura normal, barrido
histórico, subida Drive, sincronización Sheets, Excel y paquete portable.
El panel muestra si Drive está activado y la fecha de la última subida sin
fallos. Reiniciar la UI después de editar `crt.env`.

## 7. Revisar y mantener

```bash
sudo systemctl status crt-comments.timer
sudo systemctl status crt-comments.service
sudo journalctl -u crt-comments.service -n 150 --no-pager
sudo tail -n 150 /var/log/crt-comments/crt_comments.log
sudo bash deploy/operate_rhel.sh bundle
```

Datos: `/var/lib/crt-comments/data`. Logs: `/var/log/crt-comments`.
El paquete incluye un respaldo consistente de SQLite y adjuntos; copiarlo a
otro disco. Vigilar errores, adjuntos pendientes, cuota Google, espacio libre
y ausencia de corridas exitosas durante más de 16 horas. Los logs rotan para
limitar su tamaño. Las credenciales se respaldan por separado con acceso
restringido. Para actualizar, repetir instalación conservando estos volúmenes.

No se ha accedido ni desplegado en tu servidor desde esta entrega.

Referencias oficiales:
- https://developers.google.com/workspace/drive/api/guides/about-shareddrives
- https://developers.google.com/identity/protocols/oauth2#expiration
- https://github.com/systemd/systemd/blob/main/man/systemd.timer.xml
- https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/8/html/building_running_and_managing_containers/assembly_porting-containers-to-systemd-using-podman_building-running-and-managing-containers
