#!/bin/bash
# RENDERFARM START (Mac Studio), liegt in _worker/ und läuft als lokale Kopie (~/.renderfarm/start.sh), so
# bleibt die Datei auf dem Server nie offen und lässt sich jederzeit ersetzen. Fenster offen lassen.
# Gestartet von "Renderfarm Start (Mac).terminal" (Doppelklick) oder "Renderfarm Start (Mac).command".
# Macht alles selbst:
#   - findet den Server (auch als /Volumes/DDE-1 …) und verbindet ihn, wenn er fehlt (Login aus dem
#     Schlüsselbund; beim ersten Mal fragt macOS)
#   - richtet den Autostart ein: nach jedem Anmelden öffnet sich dieses Fenster von selbst
#   - hält den Mac wach, solange es läuft, und startet den Worker nach Absturz oder Server-Ausfall neu
#   - meldet alle Macs auf einmal ab, wenn du es sagst: AI Studio "Log Out All Macs" oder
#     bash "<diese Datei>" --alle-abmelden   (wieder rendern: --farm-an; beim nächsten Job geht es von selbst)
#   - nur ein Renderfarm-Fenster pro Mac: ein neues Fenster ersetzt das alte ohne Rückfrage
# Autostart wieder entfernen:  bash "<diese Datei>" --entfernen
printf '\033]0;Blender Render Farm\007'
# schwarzes Fenster, weisse Schrift
printf '\033]11;#000000\007\033]10;#e6e6e6\007'
osascript -e 'tell application "Terminal" to tell selected tab of front window
  set background color to {0, 0, 0}
  set normal text color to {60000, 60000, 60000}
end tell' >/dev/null 2>&1 &
SUB="BDE_VID/BA/02_Studierende/Louis Widmer/Flamenco"
SERVER="smb://fileredu.ad.zhdk.ch/DDE"
HELP="$HOME/.renderfarm"
AGENT="$HOME/Library/LaunchAgents/ch.zhdk.renderfarm.worker.plist"

if [ "$1" = "--entfernen" ]; then
  launchctl bootout "gui/$(id -u)" "$AGENT" 2>/dev/null; rm -f "$AGENT"; rm -rf "$HELP"
  echo "Autostart entfernt."; exit 0
fi

control() {   # _worker/control.json schreiben: alle Macs hören darauf
  python3 - "$FARM/_worker/control.json" "$1" "$2" <<'PY'
import json, sys, time, socket
json.dump({"farm": sys.argv[2], "ende": sys.argv[3], "seit": int(time.time()), "von": socket.gethostname()},
          open(sys.argv[1], "w"))
PY
}
find_farm() {
  for v in /Volumes/DDE*; do [ -f "$v/$SUB/_worker/worker.sh" ] && { echo "$v/$SUB"; return; }; done
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
if [ "$1" = "--alle-abmelden" ]; then
  control aus abmelden && echo "Alle Render-Macs melden sich ab (nach ihrem laufenden Paket von 10 Frames)."; exit 0
fi
if [ "$1" = "--farm-an" ]; then
  control an standby && echo "Renderfarm an."; exit 0
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
  echo "Autostart eingerichtet: nach dem Anmelden startet die Renderfarm von selbst."
fi

# nur ein Renderfarm-Fenster: ältere Start-Schleifen und Worker auf diesem Mac gehen, dieses übernimmt
for pid in $(pgrep -f "Renderfarm Start|_worker/(worker_mac|mac_worker|worker|start_mac)\.sh|\.renderfarm/(start|worker)\.sh"); do
  [ "$pid" = "$$" ] || [ "$pid" = "$PPID" ] || kill "$pid" 2>/dev/null
done
sleep 1

# der Hinweis gross in einem eigenen Fenster (TextEdit), damit er nicht im Terminal wegrutscht
cp -f "$FARM/_worker/Hinweis.rtf" "$HELP/Blender Render Farm.rtf" 2>/dev/null && \
  open -a TextEdit "$HELP/Blender Render Farm.rtf" 2>/dev/null
caffeinate -ims -w $$ &              # wach bleiben, solange dieses Fenster laeuft (auch am Strom ohne Bildschirm)
cd "$HOME" || exit 1
while true; do
  cp -f "$FARM/_worker/worker.sh" "$HELP/worker.sh" 2>/dev/null     # jedes Mal die neueste Fassung vom Server
  RF_ROOT="$FARM" bash "$HELP/worker.sh"; rc=$?
  [ $rc -eq 0 ] && break
  echo "Worker beendet (Code $rc) – Neustart in 15 s ... (Ctrl+C: beenden)"; sleep 15
  [ -f "$FARM/_worker/worker.sh" ] || FARM="$(find_farm)"
  while [ -z "$FARM" ]; do connect || sleep 60; done
done
