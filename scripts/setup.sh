#!/usr/bin/env bash
# setup.sh — one-time preparation: check prerequisites, fetch the Open5GS/UERANSIM packaging at a
# pinned commit, and build the two base images if you do not have them yet.
source "$(dirname "$0")/lib.sh"
UPSTREAM_URL=https://github.com/herlesupreeth/docker_open5gs.git
UPSTREAM_COMMIT=1f3bcf73f926b5b7526ef3c41cd2b839c2b17ac3

say "Checking prerequisites"
command -v docker >/dev/null || die "docker is not installed (see README §3)"
docker info >/dev/null 2>&1 || die "cannot talk to Docker. Is the daemon running and are you in the 'docker' group? (sudo usermod -aG docker \$USER, then log out/in)"
docker compose version >/dev/null 2>&1 || die "docker compose v2 plugin missing"
ok "docker $(docker version -f '{{.Server.Version}}'), $(docker compose version --short)"
ok "CPU architecture: $(uname -m)   (images are built natively, so x86-64 and ARM64 both work)"
mem_gb=$(awk '/MemTotal/{printf "%d", $2/1048576}' /proc/meminfo); [ "$mem_gb" -ge 6 ] && ok "RAM: ${mem_gb} GB" || warn "only ${mem_gb} GB RAM — 8 GB+ recommended"

[ -f .env ] || { cp .env.example .env; ok "created .env from .env.example (put your Gemini key there)"; }
chmod 600 .env

say "Fetching Open5GS docker packaging @ ${UPSTREAM_COMMIT:0:7}"
if [ ! -d third_party/docker_open5gs/.git ]; then
  mkdir -p third_party
  git clone -q "$UPSTREAM_URL" third_party/docker_open5gs
fi
git -C third_party/docker_open5gs fetch -q origin 2>/dev/null || true
git -C third_party/docker_open5gs checkout -q "$UPSTREAM_COMMIT"
ok "third_party/docker_open5gs at $(git -C third_party/docker_open5gs rev-parse --short HEAD)"

for spec in "docker_open5gs:base" "docker_ueransim:ueransim"; do
  img=${spec%%:*}; dir=${spec##*:}
  if docker image inspect "$img:latest" >/dev/null 2>&1; then
    ok "image $img already present — skipping build"
  else
    say "Building $img from source (first time only; Open5GS takes 15–40 min, UERANSIM ~5 min)"
    docker build -t "$img" "third_party/docker_open5gs/$dir"
    ok "built $img"
  fi
done
mkdir -p runtime/log
say "Setup finished. Next:  scripts/ibg.sh up"
