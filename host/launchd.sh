#!/bin/bash
# Manages the deskdeck service as a launchd LaunchAgent: starts at login and restarts
# 10 s after it crashes or exits.
#
#   ./launchd.sh install     writes the plist and starts the service (reloads it if present)
#   ./launchd.sh uninstall   stops the service and removes the plist
#   ./launchd.sh restart     restarts the service (after code changes)
#   ./launchd.sh status      launchd status
#   ./launchd.sh logs        follows the log
set -euo pipefail

LABEL=local.deskdeck.host
HOST_DIR="$(cd "$(dirname "$0")" && pwd -P)"  # real path even when called through a symlink (see docs/troubleshooting.md)
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/deskdeck.log"
DOMAIN="gui/$(id -u)"
# launchd's PATH is very short: add media-control (Homebrew) and system tools
AGENT_PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

write_plist() {
    mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$HOST_DIR/.venv/bin/deskdeck</string>
    </array>
    <key>WorkingDirectory</key><string>$HOST_DIR</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>$AGENT_PATH</string>
        <key>PYTHONUNBUFFERED</key><string>1</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>10</integer>
    <key>ProcessType</key><string>Interactive</string>
    <key>StandardOutPath</key><string>$LOG</string>
    <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
    plutil -lint "$PLIST" >/dev/null
}

case "${1:-}" in
install)
    (cd "$HOST_DIR" && uv sync --quiet --no-dev)  # the service needs no test tools
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    write_plist
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "installed: $PLIST"
    echo "log: $LOG"
    ;;
uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "removed"
    ;;
restart)
    launchctl kickstart -k "$DOMAIN/$LABEL"
    ;;
status)
    launchctl print "$DOMAIN/$LABEL" | grep -E "state =|pid =|last exit|runs ="
    ;;
logs)
    tail -n 30 -f "$LOG"
    ;;
*)
    sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
