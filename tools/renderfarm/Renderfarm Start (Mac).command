#!/bin/bash
# RENDERFARM START (Mac Studio). Fenster offen lassen, Beenden: Ctrl+C.
# Doppelklick auf "Renderfarm Start (Mac).terminal" (oder: bash + diese Datei ins Terminal ziehen).
# Macht alles selbst:
#   - findet den Server (auch als /Volumes/DDE-1 …) und verbindet ihn, wenn er fehlt (Login aus dem
#     Schlüsselbund; beim ersten Mal fragt macOS)
#   - richtet den Autostart ein: nach jedem Anmelden öffnet sich dieses Fenster von selbst
#   - hält den Mac wach, solange es läuft, und startet den Worker nach Absturz oder Server-Ausfall neu
#   - meldet den Mac ab, wenn 60 min kein Job kam (_worker/control.json "abmelden_nach", 0 = nie)
#   - startet nie einen zweiten Worker auf demselben Mac
# Autostart wieder entfernen:  bash "<diese Datei>" --entfernen
printf '\033]0;Renderfarm\007'
SUB="BDE_VID/BA/02_Studierende/Louis Widmer/Flamenco"
SERVER="smb://fileredu.ad.zhdk.ch/DDE"
HELP="$HOME/.renderfarm"
AGENT="$HOME/Library/LaunchAgents/ch.zhdk.renderfarm.worker.plist"
DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"

if [ "$1" = "--entfernen" ]; then
  launchctl bootout "gui/$(id -u)" "$AGENT" 2>/dev/null; rm -f "$AGENT"; rm -rf "$HELP"
  echo "Autostart entfernt."; exit 0
fi

find_farm() {
  [ -f "$DIR/_worker/mac_worker.sh" ] && { echo "$DIR"; return; }
  for v in /Volumes/DDE*; do [ -f "$v/$SUB/_worker/mac_worker.sh" ] && { echo "$v/$SUB"; return; }; done
}
connect() {   # den Server verbinden (gespeicherte Adresse mit Benutzername: Passwort aus dem Schlüsselbund)
  local url="$SERVER"; [ -s "$HOME/.renderfarm_server" ] && url="$(cat "$HOME/.renderfarm_server")"
  echo "Server nicht verbunden – verbinde $url ..."
  osascript -e "mount volume \"$url\"" >/dev/null 2>&1 || open "$url"
  for i in $(seq 1 60); do FARM="$(find_farm)"; [ -n "$FARM" ] && return 0; sleep 2; done
  return 1
}

FARM="$(find_farm)"
if [ -z "$FARM" ]; then
  until connect; do echo "Server nicht erreichbar (WLAN/VPN?) – neuer Versuch in 60 s ... (Ctrl+C: beenden)"; sleep 60; done
fi
xattr -dr com.apple.quarantine "$FARM/_worker" "$FARM/Renderfarm Start (Mac).command" \
  "$FARM/Renderfarm Start (Mac).terminal" 2>/dev/null

# Autostart: nach dem Anmelden öffnet sich das Renderfarm-Fenster von selbst (Terminal-Fenster mit Übersicht)
mkdir -p "$HELP" "$(dirname "$AGENT")"
cp -f "$FARM/Renderfarm Start (Mac).terminal" "$HELP/Renderfarm.terminal" 2>/dev/null
if [ -f "$HELP/Renderfarm.terminal" ] && ! grep -q "Renderfarm.terminal" "$AGENT" 2>/dev/null; then
  launchctl bootout "gui/$(id -u)" "$AGENT" 2>/dev/null        # ein älterer Autostart (vom Stick) wird ersetzt
  cat > "$AGENT" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ch.zhdk.renderfarm.worker</string>
  <key>ProgramArguments</key><array><string>/usr/bin/open</string><string>-a</string><string>Terminal</string>
    <string>$HELP/Renderfarm.terminal</string></array>
  <key>RunAtLoad</key><true/>
</dict></plist>
PLIST
  launchctl bootstrap "gui/$(id -u)" "$AGENT" 2>/dev/null &
  echo "Autostart eingerichtet: nach dem Anmelden startet die Renderfarm von selbst."
fi

# nie zwei Worker auf einem Mac
OTHER=$(pgrep -f "_worker/mac_worker.sh" | grep -v "^$$\$" | head -1)
if [ -n "$OTHER" ]; then
  echo "Auf diesem Mac läuft schon ein Worker (PID $OTHER)."
  read -t 15 -n1 -p "Den alten beenden und hier neu starten? [j/N] " a; echo
  [ "$a" = "j" ] || [ "$a" = "J" ] || exit 0
  pkill -f "_worker/mac_worker.sh"; sleep 2
fi

caffeinate -ims -w $$ &              # wach bleiben, solange dieses Fenster laeuft (auch am Strom ohne Bildschirm)
cd "$HOME" || exit 1
while true; do
  bash "$FARM/_worker/mac_worker.sh"; rc=$?
  [ $rc -eq 0 ] && break
  echo "Worker beendet (Code $rc) – Neustart in 15 s ... (Ctrl+C: beenden)"; sleep 15
  [ -f "$FARM/_worker/mac_worker.sh" ] || FARM="$(find_farm)"
  while [ -z "$FARM" ]; do connect || sleep 60; done
done
