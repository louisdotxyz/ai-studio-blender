#!/bin/bash
# Ordner-Renderfarm - Worker fuer Mac (Apple Silicon)
# Rendert Jobs aus <Renderfarm>/queue. Mehrere Rechner teilen sich die Frames automatisch.
# Wird vom Server gestartet, Updates wirken also sofort auf allen Rechnern.
# 0.5.0: Uebersicht der Jobs, Frames und Macs gleich nach dem Start und beim Warten; jedes fertige
#        Frame mit Zeit und Stand des Jobs.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION=$(cat "$ROOT/_worker/VERSION" 2>/dev/null || echo "?")
NAME=$(scutil --get ComputerName 2>/dev/null || hostname)
Q="$ROOT/queue"; OUT="$ROOT/renders"; ST="$ROOT/_worker/status"
mkdir -p "$Q" "$OUT" "$ST"
STARTED=$(date +%s)
VOL="/Volumes/$(echo "$ROOT" | cut -d/ -f3)"
SRVF="$HOME/.renderfarm_server"
SMB=$(mount | grep " on $VOL (" | head -1 | sed -E 's#^//([^@]*@)?([^ ]+) on .*#smb://\1\2#')
[ -n "$SMB" ] && echo "$SMB" > "$SRVF"
t() { perl -e 'alarm shift; exec @ARGV' "$@"; }
remount() {   # Server-Verbindung pruefen und bei Bedarf neu verbinden (Login aus dem Schluesselbund)
  t 10 ls "$Q" >/dev/null 2>&1 && return 0
  echo "$(date +%H:%M) Server weg -> verbinde neu ..."
  [ -d "$VOL" ] && diskutil unmount force "$VOL" >/dev/null 2>&1
  [ -s "$SRVF" ] && osascript -e "mount volume \"$(cat "$SRVF")\"" >/dev/null 2>&1
  t 10 ls "$Q" >/dev/null 2>&1
}
control() {   # liest _worker/control.json: farm an/aus, ende standby/abmelden/herunterfahren
  python3 -c "import json,sys;c=json.load(open(sys.argv[1]));print(c.get('farm','an'),c.get('ende','standby'),int(c.get('seit',0)))" "$ROOT/_worker/control.json" 2>/dev/null || echo "an standby 0"
}
frames_done() { find "$OUT/$1" -type f -size +0 2>/dev/null | wc -l | tr -d ' '; }
bar() {       # [########............] fuer done/total
  local n=0 i s=""; [ "$2" -gt 0 ] && n=$((20 * $1 / $2)); [ $n -gt 20 ] && n=20
  for ((i = 0; i < 20; i++)); do [ $i -lt $n ] && s="$s#" || s="$s."; done; printf '[%s]' "$s"
}
board() {     # die Uebersicht: offene Jobs mit Frames, die Macs mit ihrem Zustand
  clear
  echo "=== Renderfarm v$VERSION | $NAME | Blender $BV ==="
  echo "Server: $ROOT"
  echo
  echo "JOBS"
  local any=0 J JOB S E TOTAL DONE
  for J in "$Q"/*.json; do
    [ -e "$J" ] || continue
    JOB=$(basename "$J" .json)
    [ -f "$Q/$JOB.done" ] || [ ! -f "$Q/$JOB.blend" ] && continue
    read -r S E < <(python3 -c "import json,sys;c=json.load(open(sys.argv[1]));print(c.get('start',0),c.get('end',0))" "$J" 2>/dev/null)
    TOTAL=$(( ${E:-0} - ${S:-0} + 1 )); DONE=$(frames_done "$JOB"); any=1
    local short="$JOB"; [ ${#JOB} -gt 34 ] && short="..${JOB:${#JOB}-32}"
    printf "  %-34s %s %4d/%-4d\n" "$short" "$(bar "$DONE" "$TOTAL")" "$DONE" "$TOTAL"
  done
  [ $any = 0 ] && echo "  keine offenen Jobs"
  echo
  echo "MACS"
  local f now age line
  now=$(date +%s)
  for f in "$ST"/*-render.txt; do
    [ -e "$f" ] || continue
    age=$(( now - $(stat -f %m "$f" 2>/dev/null || echo 0) ))
    [ $age -gt 3600 ] && continue          # seit ueber einer Stunde still: nicht mehr da
    line=$(head -1 "$f" | cut -d'|' -f1-2 | tr -s ' ')
    echo "  $line"
  done
  echo "-----------------------------------------"
}

# Blender finden: /Applications/Blender.app, sonst neuestes Blender*.app
BLENDER=""
for app in "/Applications/Blender.app" $(ls -d /Applications/Blender*.app "$HOME"/Applications/Blender*.app 2>/dev/null | sort -V -r); do
  [ -x "$app/Contents/MacOS/Blender" ] && { BLENDER="$app/Contents/MacOS/Blender"; break; }
done
clear
echo "=== Ordner-Renderfarm v$VERSION | Worker $NAME ==="
echo "Ordner:  $ROOT"
[ -n "$BLENDER" ] || { echo "FEHLER: Kein Blender in /Applications gefunden."; read -n1 -p "Taste..."; exit 1; }
BV=$("$BLENDER" --version 2>/dev/null | head -1 | awk '{print $2}')
echo "Blender: $BV"

PY="$(mktemp -t renderfarm).py"
cat > "$PY" <<'PYEOF'
import bpy, os, json
job = os.environ["RF_JOB"]; out = os.environ["RF_OUT"]
cfg = json.load(open(os.environ["RF_JSON"]))
sc = bpy.data.scenes.get(cfg.get("scene", "")) or bpy.context.scene
r = sc.render
r.use_overwrite = False
r.use_placeholder = True
r.filepath = os.path.join(out, job, job + "_####")
r.image_settings.file_format = cfg.get("format", "PNG")
try:
    p = bpy.context.preferences.addons["cycles"].preferences
    p.compute_device_type = "METAL"; p.get_devices()
    for d in p.devices: d.use = True
    if r.engine == "CYCLES": sc.cycles.device = "GPU"
except Exception as e:
    print("RF: GPU-Setup:", e)
print("RF: Szene", sc.name, "|", r.engine)
PYEOF

status() { echo "$NAME | $1 | $(date '+%Y-%m-%d %H:%M') | Blender $BV | v$VERSION" > "$ST/$NAME-render.txt" 2>/dev/null; }
trap 'status beendet; echo; echo "Beendet."; exit 0' INT TERM
caffeinate -i -w $$ &

status wartet
remount && board
echo "Wartet auf Jobs ... (Beenden: Ctrl+C)"
while true; do
  remount || { echo "$(date +%H:%M) Server nicht erreichbar, warte ..."; sleep 60; continue; }
  read -r FARM ENDE SEIT < <(control)
  if [ "$FARM" = "aus" ]; then
    if [ "$SEIT" -ge "$STARTED" ] && [ "$ENDE" = "abmelden" ]; then
      status abgemeldet; echo "$(date +%H:%M) Farm aus -> melde ab"; sleep 5
      osascript -e 'tell application "loginwindow" to «event aevtrlgo»'; exit 0
    elif [ "$SEIT" -ge "$STARTED" ] && [ "$ENDE" = "herunterfahren" ]; then
      status heruntergefahren; echo "$(date +%H:%M) Farm aus -> fahre herunter"; sleep 5
      osascript -e 'tell application "System Events" to shut down'; exit 0
    fi
    status "pausiert (Farm aus)"; sleep 60; continue
  fi
  for J in "$Q"/*.json; do
    [ -e "$J" ] || continue
    JOB=$(basename "$J" .json); B="$Q/$JOB.blend"
    [ -f "$B" ] && [ ! -f "$Q/$JOB.done" ] || continue
    read -r SCENE START END JBV < <(python3 -c "import json,sys;c=json.load(open(sys.argv[1]));print(c.get('scene','-') or '-',c.get('start',0),c.get('end',0),c.get('blender','-'))" "$J" 2>/dev/null)
    TOTAL=$((END - START + 1))
    if [ "$JBV" != "-" ] && [ "${JBV%.*}" != "${BV%.*}" ]; then
      echo "$(date +%H:%M) WARNUNG: $JOB wurde mit Blender $JBV gesendet, hier laeuft $BV."
    fi
    board
    echo "$(date +%H:%M) Job $JOB (Szene $SCENE, Frames $START-$END)"
    status "rendert $JOB"
    ARGS=(-b "$B" --factory-startup); [ "$SCENE" != "-" ] && ARGS+=(-S "$SCENE")
    # in Paketen zu je CHUNK Frames rendern: so reagiert der Worker schnell auf "Farm aus"
    CHUNK=10; STOPPED=0
    for ((CS=START; CS<=END; CS+=CHUNK)); do
      CE=$((CS + CHUNK - 1)); [ $CE -gt $END ] && CE=$END
      FREE=0; for ((f=CS; f<=CE; f++)); do ls "$OUT/$JOB/${JOB}_$(printf %04d $f)".* >/dev/null 2>&1 || { FREE=1; break; }; done
      [ $FREE = 1 ] || continue
      read -r FARM _ _ < <(control); [ "$FARM" = "aus" ] && { STOPPED=1; break; }
      echo "$(date +%H:%M:%S) laedt die Datei fuer Frames $CS-$CE ..."
      T0=$(date +%s)
      RF_JOB="$JOB" RF_OUT="$OUT" RF_JSON="$J" "$BLENDER" "${ARGS[@]}" --python "$PY" -s $CS -e $CE -a 2>&1 \
        | while IFS= read -r line; do
            case "$line" in
              *Saved:*)
                FR=${line##*_}; FR=${FR%%.*}; NOW=$(date +%s)
                echo "$(date +%H:%M:%S) Frame $((10#$FR)) fertig ($((NOW - T0)) s) | Job $(frames_done "$JOB")/$TOTAL"
                T0=$NOW ;;
              *RF:*|*rror*) echo "$line" ;;
            esac
          done
    done
    [ $STOPPED = 1 ] && { echo "$(date +%H:%M) Farm aus - Job $JOB pausiert"; continue; }
    DONE=$(frames_done "$JOB")
    if [ "$DONE" -ge "$TOTAL" ]; then
      date > "$Q/$JOB.done"; echo "$(date +%H:%M) FERTIG: $JOB ($DONE/$TOTAL Frames)"
    fi
  done
  status wartet
  board
  echo "$(date +%H:%M) wartet auf Jobs ... (Beenden: Ctrl+C)"
  sleep 20
done
