#!/bin/bash
# BLENDER RENDER FARM starten (Mac Studio): Doppelklick, oder im Terminal: bash + diese Datei hineinziehen.
# Geht von überall (Schreibtisch, Stick, Server). Ist der Server nicht verbunden, verbindet sie ihn
# (ZHdK-Login, wenn macOS fragt) und wartet, bis er da ist. Der eigentliche Start liegt auf dem Server in
# _worker/start_mac.sh und läuft als lokale Kopie.
SUB="BDE_VID/BA/02_Studierende/Louis Widmer/Flamenco"
URL="smb://fileredu.ad.zhdk.ch/DDE"
[ -s "$HOME/.renderfarm_server" ] && URL="$(cat "$HOME/.renderfarm_server")"
DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
start() {
  for f in "$DIR/_worker/start_mac.sh" /Volumes/DDE*/"$SUB"/_worker/start_mac.sh; do
    [ -f "$f" ] && { mkdir -p "$HOME/.renderfarm"; cp -f "$f" "$HOME/.renderfarm/start.sh"
                     exec bash "$HOME/.renderfarm/start.sh" "$@"; }
  done
}
start "$@"
echo "Server nicht verbunden – verbinde $URL ..."
echo "(Fragt macOS nach dem Login: ZHdK-Benutzername und Passwort, 'Im Schlüsselbund sichern' anhaken.)"
osascript -e "mount volume \"$URL\"" >/dev/null 2>&1 || open "$URL"
for i in $(seq 1 90); do start "$@"; sleep 2; done
until false; do
  echo "Server noch nicht da (WLAN/VPN?) – neuer Versuch in 30 s ... (Ctrl+C: beenden)"
  osascript -e "mount volume \"$URL\"" >/dev/null 2>&1
  for i in $(seq 1 15); do start "$@"; sleep 2; done
done
