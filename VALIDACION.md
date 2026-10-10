# Validación de versión 3.0

Pruebas realizadas en esta entrega; no equivalen a aceptación en producción.

| Verificación | Resultado |
|---|---|
| Suite Python | 35 aprobadas; 1 E2E omitida |
| Folios | Permanentes, sin renumerar, sin saltos por corridas idénticas, ceros conservados en XLSX y publicación |
| Migración | Base original sin columna folio; reapertura sin reasignación |
| Catálogo | Caso del checkbox oculto corregido y probado con un doble |
| Paginación | Secuencia, totales, barridos discrepantes y contenido parcial |
| Cola local | Firma PDF, origen, tamaño y checksum; fallos mantienen Excel con pendientes |
| Libro local | Cinco hojas, filtros, hipervínculos y lectura posterior |
| Google simulado | RAW, rangos deterministas y lectura del XLSX existente |
| ZIP | Documentos, enlaces relativos, SQLite consistente y manifiesto |
| UI por HTTP | Lectura, búsqueda, Excel descargable, CSRF, Host y rechazo de rutas externas |
| UI con proceso real | Botón de exportación lanza CLI, crea XLSX y registra logs |
| JavaScript / shell | Sintaxis verificada |
| Excel inicial | Creado con Artifact Tool, inspeccionado y revisado visualmente en las cinco hojas |
| Apariencia de la UI | HTML/CSS implementados; sin revisión visual en navegador en este entorno |
| Chromium E2E | No arrancó en este entorno, por eso se omite explícitamente |
| Google real | Credenciales no usadas; pendiente validación en la instalación del usuario |
| Red Hat / Podman | Despliegue preparado; no ejecutado en el servidor del usuario |

El log adjunto aporta evidencia de 33/33 comentarios y 4/4 páginas en la
consulta 24 en la instalación del usuario, pero no entrega el contenido ni los
adjuntos. El Excel inicial está vacío. No se afirma una descarga real completa
realizada desde este entorno: el portal presentó un CAPTCHA al abrir el detalle.

## Aceptación recomendada en el servidor

1. Respaldar y conservar `data/`, instalar la versión 3 y regenerar el libro.
2. Abrir la UI y comprobar folios y registros existentes de la consulta 24.
3. Ejecutar una captura de 24,25,26 y comparar el conteo con el portal.
4. Abrir dos PDFs descargados y comparar autor, URL, firma y checksum.
5. Repetir: mismo contenido produce cero nuevos y conserva folios.
6. Crear y extraer el paquete ZIP; comprobar sus enlaces locales.
7. Configurar Google y sincronizar: folios de texto, cinco pestañas y sin duplicados.
8. Comprobar que el E2E pase sin omisiones y revisar visualmente la UI.
9. Habilitar timer y monitorear logs, pendientes, espacio y última ejecución.
