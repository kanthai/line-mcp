#!/usr/bin/env bash
# 01 — inside the LXC (root): Docker CE, adb, sqlite3, python venv, the `line` service user.
# Debian 12 (bookworm) assumed — that is what CT103 runs.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root inside the LXC" >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive

echo "== base packages"
apt-get update -q
apt-get install -y -q ca-certificates curl gnupg git sqlite3 python3 python3-venv python3-pip adb acl jq

echo "== Docker CE (official repo)"
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io
fi
systemctl enable --now docker
docker info >/dev/null || { echo "docker not working — is the LXC nesting=1?" >&2; exit 1; }

echo "== line user (uid 1000, docker group)"
if ! id line >/dev/null 2>&1; then
  useradd -m -u 1000 -s /bin/bash line 2>/dev/null || useradd -m -s /bin/bash line
fi
usermod -aG docker line
install -d -o line -g line -m 755 /home/line/.config/line-mcp /home/line/Downloads/line-media
install -d -m 755 /etc/line-mcp

echo "== sudoers: line may restart its own services"
cat > /etc/sudoers.d/line-services <<'SUDO'
line ALL=(root) NOPASSWD: /bin/systemctl restart line-mcp.service
SUDO
chmod 440 /etc/sudoers.d/line-services
visudo -cf /etc/sudoers.d/line-services >/dev/null

echo "done. Next: setup/02-redroid.sh"
