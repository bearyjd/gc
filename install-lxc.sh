#!/usr/bin/env bash
# install-lxc.sh — Install gc CLI on OpenClaw LXC
#
#   scp install-lxc.sh root@<LXC_IP>:/tmp/
#   ssh root@<LXC_IP> bash /tmp/install-lxc.sh
#
#   ssh root@<LXC_IP> 'bash -s' < install-lxc.sh

set -euo pipefail

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# ---------------------------------------------------------------
# 1. Install gc CLI from GitHub
# ---------------------------------------------------------------
if command -v gc &>/dev/null; then
    log "gc CLI already installed: $(which gc)"
    log "Upgrading..."
    pip install --upgrade git+https://github.com/bearyjd/gc --break-system-packages -q
else
    log "Installing gc CLI..."
    pip install git+https://github.com/bearyjd/gc --break-system-packages -q
fi

# ---------------------------------------------------------------
# 2. Clone repo for cron script (pip doesn't include it)
# ---------------------------------------------------------------
if [[ -d /opt/gc/.git ]]; then
    log "Repo already at /opt/gc — pulling latest"
    git -C /opt/gc pull --ff-only -q
else
    log "Cloning repo to /opt/gc..."
    git clone -q https://github.com/bearyjd/gc.git /opt/gc
fi
chmod +x /opt/gc/cron/gc-cron.sh

# ---------------------------------------------------------------
# 3. Configure credentials (skip if already exists)
# ---------------------------------------------------------------
mkdir -p ~/.gc && chmod 700 ~/.gc

if [[ -f ~/.gc/.env ]]; then
    log ".env already exists — not overwriting"
else
    log "No .env found. Create it manually:"
    log "  cat > ~/.gc/.env << 'EOF'"
    log '  GC_TOKEN="your-gc-token-here"'
    log "  EOF"
    log "  chmod 600 ~/.gc/.env"
fi

if [[ -f ~/.gc/teams.json ]]; then
    log "teams.json already exists — not overwriting"
else
    log "No teams.json found. Run 'gc teams --json > ~/.gc/teams.json' after setting token."
fi

# ---------------------------------------------------------------
# 4. Smoke test
# ---------------------------------------------------------------
log "Running smoke test: gc teams --json..."
if [[ -f ~/.gc/.env ]] && gc teams --json > /dev/null 2>&1; then
    log "Smoke test PASSED"
else
    log "WARN: Smoke test skipped or failed"
    log "  If .env is missing, create it first (see above)"
fi

log "Testing cron script..."
if /opt/gc/cron/gc-cron.sh 2>/dev/null; then
    log "Cron script PASSED — files in /tmp/gc/:"
    ls -la /tmp/gc/ 2>/dev/null || true
else
    log "WARN: Cron script had errors — check token and teams.json"
fi

# ---------------------------------------------------------------
# Done
# ---------------------------------------------------------------
log ""
log "Installation complete!"
log ""
log "  gc CLI:       $(which gc)"
log "  Cron script:  /opt/gc/cron/gc-cron.sh"
log "  Config:       ~/.gc/.env (token) + ~/.gc/teams.json (team IDs)"
log "  JSON output:  /tmp/gc/"
log ""
log "Next steps:"
log "  1. Create ~/.gc/.env with GC_TOKEN"
log "  2. Run: gc teams --json > ~/.gc/teams.json"
log "  3. Add cron: 0 6 * * * /opt/gc/cron/gc-cron.sh 2>/tmp/gc-cron.log"
