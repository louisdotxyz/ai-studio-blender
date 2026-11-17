#!/bin/bash
# RENDERFARM START (Mac Studio) – Doppelklick. Fenster offen lassen, Beenden: Ctrl+C.
# Startet den Worker vom Server und zeigt gleich die Übersicht der Jobs und Frames.
# Geht auch, wenn der Server als /Volumes/DDE-1 (-2 …) verbunden ist, und startet den Worker neu,
# wenn er abstürzt oder der Server kurz weg war.
# Lässt sich der Doppelklick nicht öffnen (macOS-Sperre): Rechtsklick > Öffnen > Öffnen (einmal),
# oder im Terminal:  bash "<diese Datei ins Terminal ziehen>"
printf '\033]0;Renderfarm\007'
SUB="BDE_VID/BA/02_Studierende/Louis Widmer/Flamenco"
DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
find_farm() {
  [ -f "$DIR/_worker/mac_worker.sh" ] && { echo "$DIR"; return; }
  for v in /Volumes/DDE*; do [ -f "$v/$SUB/_worker/mac_worker.sh" ] && { echo "$v/$SUB"; return; }; done
}
FARM="$(find_farm)"
if [ -z "$FARM" ]; then
  echo "Server nicht verbunden – verbinde (ZHdK-Login, wenn macOS fragt) ..."
  open "smb://fileredu.ad.zhdk.ch/DDE"
  for i in $(seq 1 90); do FARM="$(find_farm)"; [ -n "$FARM" ] && break; sleep 2; done
fi
[ -n "$FARM" ] || { echo "Server nicht gefunden. VPN/WLAN prüfen und nochmals starten."; read -n1 -p "Taste ..."; exit 1; }
xattr -dr com.apple.quarantine "$FARM/_worker" "$0" 2>/dev/null   # die macOS-Sperre einmal weg
cd "$HOME" || exit 1
while true; do
  bash "$FARM/_worker/mac_worker.sh"; rc=$?
  [ $rc -eq 0 ] && break
  echo "Worker beendet (Code $rc) – Neustart in 15 s ... (Ctrl+C: beenden)"; sleep 15
  [ -f "$FARM/_worker/mac_worker.sh" ] || FARM="$(find_farm)"
  while [ -z "$FARM" ]; do sleep 15; FARM="$(find_farm)"; done
done
