#!/bin/sh
set -eu

VERSION="${BUYWELL_EDGE_VERSION:-latest}"
INSTALL_ROOT="${BUYWELL_EDGE_INSTALL_ROOT:-/opt/buywell-edge}"
STATE_ROOT="${BUYWELL_EDGE_STATE_ROOT:-/var/lib/buywell-edge}"
REPOSITORY="${BUYWELL_EDGE_REPOSITORY:-moreveal/buywell-edge}"
PAIR_CODE="${1:-}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this installer as root (sudo)." >&2
  exit 1
fi

libc_version="$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{ print $2 }' || true)"
if [ -z "$libc_version" ]; then
  echo "Buywell Edge requires a glibc-based Linux distribution (glibc 2.28 or newer)." >&2
  exit 1
fi
if ! awk -v current="$libc_version" 'BEGIN {
  split(current, value, ".");
  exit !((value[1] > 2) || (value[1] == 2 && value[2] >= 28));
}'; then
  echo "Buywell Edge requires glibc 2.28 or newer; this system has glibc $libc_version." >&2
  exit 1
fi

architecture="$(uname -m)"
case "$architecture" in
  x86_64) asset_arch="x86_64" ;;
  aarch64|arm64) asset_arch="aarch64" ;;
  *) echo "Unsupported architecture: $architecture" >&2; exit 1 ;;
esac

mkdir -p "$INSTALL_ROOT/releases" "$STATE_ROOT"
release_url="https://github.com/$REPOSITORY/releases/$VERSION/download/buywell-edge-linux-$asset_arch.tar.gz"
checksum_url="$release_url.sha256"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT
curl --fail --location --silent --show-error "$release_url" -o "$temporary/edge.tar.gz"
curl --fail --location --silent --show-error "$checksum_url" -o "$temporary/edge.tar.gz.sha256"
expected_checksum="$(awk 'NR == 1 { print $1 }' "$temporary/edge.tar.gz.sha256")"
actual_checksum="$(sha256sum "$temporary/edge.tar.gz" | awk '{ print $1 }')"
if [ -z "$expected_checksum" ] || [ "$actual_checksum" != "$expected_checksum" ]; then
  echo "Buywell Edge archive checksum verification failed." >&2
  exit 1
fi
release_name="$(sha256sum "$temporary/edge.tar.gz" | cut -c1-16)"
if [ ! -d "$INSTALL_ROOT/releases/$release_name" ]; then
  mkdir "$INSTALL_ROOT/releases/$release_name"
  tar -xzf "$temporary/edge.tar.gz" -C "$INSTALL_ROOT/releases/$release_name"
fi
ln -sfn "$INSTALL_ROOT/releases/$release_name" "$INSTALL_ROOT/current"
ln -sfn "$INSTALL_ROOT/current/bin/buywell-edge" /usr/local/bin/buywell-edge

id buywell-edge >/dev/null 2>&1 || useradd --system --home "$STATE_ROOT" --shell /usr/sbin/nologin buywell-edge
chown -R buywell-edge:buywell-edge "$STATE_ROOT"
install -m 0644 "$INSTALL_ROOT/current/share/buywell-edge.service" /etc/systemd/system/buywell-edge.service
systemctl daemon-reload
systemctl enable buywell-edge
if systemctl is-active --quiet buywell-edge; then
  systemctl restart --no-block buywell-edge
else
  systemctl start --no-block buywell-edge
fi

if [ -n "$PAIR_CODE" ]; then
  sudo -u buywell-edge "$INSTALL_ROOT/current/bin/buywell-edge" connect "$PAIR_CODE"
fi
echo "Buywell Edge is installed. Run: buywell-edge status"
