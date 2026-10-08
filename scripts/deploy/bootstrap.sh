#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 VPS for CS2 Coach. Run as root; safe to
# re-run. Contains no secrets.
#
#   bash bootstrap.sh [REPO_URL]
#
# Env overrides: REPO_URL (default https://github.com/luc1ferxx/CS2.git),
# APP_USER (cs2coach), APP_DIR (/opt/cs2coach), BRANCH (main),
# SSH_PORT (also allowed through ufw when sshd listens on a non-default port).
#
# Installs: security updates + unattended-upgrades, Docker Engine + compose
# plugin (Docker's apt repo), a 4 GB swapfile when no swap exists, ufw
# (OpenSSH, 80/tcp, 443/tcp, 443/udp), rclone. Creates APP_USER in the docker
# group and clones or fast-forwards the repo into APP_DIR.
set -Eeuo pipefail

REPO_URL="${1:-${REPO_URL:-https://github.com/luc1ferxx/CS2.git}}"
APP_USER="${APP_USER:-cs2coach}"
APP_DIR="${APP_DIR:-/opt/cs2coach}"
BRANCH="${BRANCH:-main}"
BACKUP_DIR="/var/backups/cs2coach"
SWAPFILE="/swapfile"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
trap 'printf "bootstrap FAILED at line %s\n" "$LINENO" >&2' ERR

[[ "$(id -u)" -eq 0 ]] || die "run as root (sudo bash $0)"
# shellcheck source=/dev/null
. /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "24.04" ]]; then
  printf 'WARNING: tested on Ubuntu 24.04; this is %s %s\n' "${ID:-?}" "${VERSION_ID:-?}"
fi
export DEBIAN_FRONTEND=noninteractive
APT_OPTS=(-y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold)

log "Updating packages"
apt-get update
apt-get "${APT_OPTS[@]}" upgrade
apt-get "${APT_OPTS[@]}" install ca-certificates curl git gnupg ufw unattended-upgrades rclone openssl

log "Enabling unattended security upgrades"
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
systemctl enable --now unattended-upgrades

log "Installing Docker Engine and the compose plugin"
install -m 0755 -d /etc/apt/keyrings
if [[ ! -s /etc/apt/keyrings/docker.asc ]]; then
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
fi
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" \
  >/etc/apt/sources.list.d/docker.list
apt-get update
apt-get "${APT_OPTS[@]}" install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker compose version

log "Swap"
if [[ -n "$(swapon --show --noheadings)" ]]; then
  echo "swap already active"
else
  if [[ ! -f "$SWAPFILE" ]]; then
    fallocate -l 4G "$SWAPFILE" || dd if=/dev/zero of="$SWAPFILE" bs=1M count=4096
    chmod 600 "$SWAPFILE"
    mkswap "$SWAPFILE"
  fi
  swapon "$SWAPFILE"
  grep -qE "^${SWAPFILE}[[:space:]]" /etc/fstab || echo "$SWAPFILE none swap sw 0 0" >>/etc/fstab
fi
echo 'vm.swappiness=10' >/etc/sysctl.d/99-cs2coach-swap.conf
sysctl -q --system

log "Firewall (ufw)"
# Docker publishes container ports around ufw, which is why only Caddy
# publishes ports (80/443) in docker-compose.prod.yml.
ufw allow OpenSSH
if [[ -n "${SSH_PORT:-}" ]]; then
  ufw allow "${SSH_PORT}/tcp"
fi
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable
ufw status verbose

log "App user $APP_USER"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$APP_USER"
fi
usermod -aG docker "$APP_USER"
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
# Let the operator ssh in as the app user with root's keys (first run only).
if [[ -s /root/.ssh/authorized_keys && ! -e "$APP_HOME/.ssh/authorized_keys" ]]; then
  install -d -m 0700 -o "$APP_USER" -g "$APP_USER" "$APP_HOME/.ssh"
  install -m 0600 -o "$APP_USER" -g "$APP_USER" /root/.ssh/authorized_keys "$APP_HOME/.ssh/authorized_keys"
fi
install -d -m 0700 -o "$APP_USER" -g "$APP_USER" "$BACKUP_DIR"

log "Repository $REPO_URL -> $APP_DIR"
as_app() { runuser -u "$APP_USER" -- "$@"; }
if [[ -d "$APP_DIR/.git" ]]; then
  if as_app git -C "$APP_DIR" symbolic-ref -q HEAD >/dev/null; then
    as_app git -C "$APP_DIR" pull --ff-only
  else
    echo "$APP_DIR is on a detached HEAD (a rollback?); leaving it as is"
  fi
else
  install -d -m 0755 -o "$APP_USER" -g "$APP_USER" "$APP_DIR"
  as_app git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR" \
    || die "git clone failed. $APP_DIR must be empty; for a private repo add a read-only deploy key for $APP_USER and pass the SSH URL (git@github.com:<owner>/<repo>.git)"
fi

cat <<EOF

Bootstrap complete. Next steps (docs/vps_deploy_v1.md):

  1. Switch to the app user:      su - $APP_USER    (or: ssh $APP_USER@<vps>)
  2. Create the env file:         cd $APP_DIR
                                  cp deploy/env.production.example deploy/.env.production
                                  chmod 600 deploy/.env.production
                                  nano deploy/.env.production   # replace coach.example.com and every CHANGE_ME
  3. Point the domain's DNS A/AAAA record at this VPS (Cloudflare: DNS only, grey cloud).
  4. Deploy:                      bash scripts/deploy/deploy.sh
  5. Enable nightly backups and the 5-minute health watch (as root):
       cp $APP_DIR/scripts/deploy/cs2coach-backup.service $APP_DIR/scripts/deploy/cs2coach-backup.timer /etc/systemd/system/
       cp $APP_DIR/scripts/deploy/cs2coach-watch.service $APP_DIR/scripts/deploy/cs2coach-watch.timer /etc/systemd/system/
       systemctl daemon-reload && systemctl enable --now cs2coach-backup.timer cs2coach-watch.timer
     Set ALERT_WEBHOOK_URL (and the ping URLs) in deploy/.env.production first, then
     test the channel as $APP_USER: bash scripts/deploy/watch.sh --test-alert
EOF
