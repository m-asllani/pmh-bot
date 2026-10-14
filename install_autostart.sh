#!/bin/bash
# Starts the bot automatically at login and keeps it running (macOS LaunchAgent).
# Undo with:  ./install_autostart.sh --remove
set -e
cd "$(dirname "$0")"
DIR="$(pwd)"
LABEL="com.pmhbot.agent"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "$1" == "--remove" ]; then
  rm -f "$PLIST"
  echo "Autostart removed."
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" logs
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DIR/.venv/bin/python</string><string>-m</string><string>pmh_bot</string><string>run</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/logs/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/launchd.err.log</string>
</dict>
</plist>
EOF
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Bot is running in the background and will start at every login."
echo "Logs: $DIR/logs/pmh_bot.log"
