#!/usr/bin/env bash
# Ejecutar como root, desde cualquier directorio. RHEL 8/9 x86_64 con Podman.
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo 'Ejecutar con sudo.' >&2; exit 1; fi
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
dnf install -y podman tzdata
id crtbot >/dev/null 2>&1 || useradd --system --no-create-home --shell /sbin/nologin crtbot
CRT_UID="$(id -u crtbot)"
CRT_GID="$(id -g crtbot)"
install -d -m 0750 /etc/crt-comments /etc/crt-comments/secrets
install -d -m 0750 -o "$CRT_UID" -g "$CRT_GID" /var/lib/crt-comments/data /var/log/crt-comments
chown "$CRT_UID:$CRT_GID" /etc/crt-comments/secrets
if [[ ! -f /etc/crt-comments/crt.env ]]; then
  install -m 0600 "$PROJECT_ROOT/deploy/crt.env.example" /etc/crt-comments/crt.env
fi
podman build --build-arg APP_UID="$CRT_UID" --build-arg APP_GID="$CRT_GID" \
  -t localhost/crt-comments:3.0 -f "$PROJECT_ROOT/deploy/Containerfile" "$PROJECT_ROOT"
install -m 0644 "$PROJECT_ROOT/deploy/crt-comments.service" "$PROJECT_ROOT/deploy/crt-comments.timer" "$PROJECT_ROOT/deploy/crt-comments-ui.service" /etc/systemd/system/
systemctl daemon-reload
echo 'Instalado. Primera corrida: systemctl start crt-comments.service'
echo 'Revisar: journalctl -u crt-comments.service -n 100 --no-pager'
echo 'Activar después de validar: systemctl enable --now crt-comments.timer'
