#!/usr/bin/env bash
# Usa la misma imagen, configuración, volúmenes y bloqueo que el timer.
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo 'Ejecutar con sudo.' >&2; exit 1; fi
case "${1:-}" in
  run|scan-all|sync-drive|sync|export|retry-downloads|bundle) ;;
  *) echo 'Uso: sudo bash deploy/operate_rhel.sh {run|scan-all|sync-drive|sync|export|retry-downloads|bundle} [opciones]' >&2; exit 1 ;;
esac
exec /usr/bin/podman run --rm --name crt-comments-manual --init --shm-size=512m \
  --env-file=/etc/crt-comments/crt.env \
  -v /var/lib/crt-comments/data:/data:z \
  -v /var/log/crt-comments:/logs:z \
  -v /etc/crt-comments/secrets:/secrets:z \
  localhost/crt-comments:3.1 "$@"
