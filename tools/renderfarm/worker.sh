#!/bin/bash
# Ordner-Renderfarm - Worker fuer Mac (Apple Silicon)
# Rendert Jobs aus <Renderfarm>/queue. Mehrere Rechner teilen sich die Frames automatisch.
# Wird vom Server gestartet, Updates wirken also sofort auf allen Rechnern.
# 0.5.0: Uebersicht der Jobs, Frames und Macs gleich nach dem Start und beim Warten; jedes fertige
#        Frame mit Zeit und Stand des Jobs.
# 0.6.0: alle Macs auf einmal abmelden, wenn du es sagst (AI Studio: "Log Out All Macs", oder Terminal:
#        bash "Renderfarm Start (Mac).command" --alle-abmelden); control.json "abmelden_nach" (Minuten ohne
#        Job, Standard 0 = nie). Nach dem naechsten Anmelden startet der Autostart die Renderfarm wieder.

ROOT="${RF_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"   # vom Start gesetzt (lokale Kopie)
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
control() {   # liest _worker/control.json: farm an/aus, ende standby/abmelden/herunterfahren, abmelden_nach (min)
  python3 -c "import json,sys;c=json.load(open(sys.argv[1]));print(c.get('farm','an'),c.get('ende','standby'),int(c.get('seit',0)),int(c.get('abmelden_nach',0)))" "$ROOT/_worker/control.json" 2>/dev/null || echo "an standby 0 0"
}
LAST_WORK=$(date +%s)
frames_done() { find "$OUT/$1" -type f -size +0 2>/dev/null | wc -l | tr -d ' '; }
bar() {       # [########............] fuer done/total
  local n=0 i s=""; [ "$2" -gt 0 ] && n=$((20 * $1 / $2)); [ $n -gt 20 ] && n=20
  for ((i = 0; i < 20; i++)); do [ $i -lt $n ] && s="$s#" || s="$s."; done; printf '[%s]' "$s"
}
banner() {
  echo "BLENDER RENDER FARM  |  WhatsApp +41 78 720 14 11"
  echo "--------------------------------------------------------------"
}
board() {     # die Uebersicht: offene Jobs mit Frames, die Macs mit ihrem Zustand
  clear
  banner
  echo "v$VERSION | $NAME | Blender $BV"
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
  read -r _ _ _ IDLE_MIN < <(control)
  if [ "${IDLE_MIN:-0}" -gt 0 ]; then
    echo "Ohne Jobs meldet sich dieser Mac in $(( IDLE_MIN - ($(date +%s) - LAST_WORK) / 60 )) min ab (Autostart danach)."
  fi
  echo "-----------------------------------------"
}

# Blender finden: /Applications/Blender.app, sonst neuestes Blender*.app
BLENDER=""
for app in "/Applications/Blender.app" $(ls -d /Applications/Blender*.app "$HOME"/Applications/Blender*.app 2>/dev/null | sort -V -r); do
  [ -x "$app/Contents/MacOS/Blender" ] && { BLENDER="$app/Contents/MacOS/Blender"; break; }
done
clear
banner
echo "Worker $NAME | v$VERSION"
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
  read -r FARM ENDE SEIT IDLE_MIN < <(control)
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
    if [ -n "$RF_ROOT" ] && [ "$ROOT/_worker/worker.sh" -nt "${BASH_SOURCE[0]}" ]; then
      echo "$(date +%H:%M) neue Worker-Version auf dem Server -> startet neu"; exit 3   # der Start holt sie
    fi
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
    LAST_WORK=$(date +%s)
    # die .blend einmal auf diesen Mac kopieren: jedes Paket laedt dann von der eigenen Platte statt
    # 0.5 GB ueber den Server (bei 12 Macs war das der Engpass)
    CACHE="$HOME/.renderfarm/cache"; mkdir -p "$CACHE"
    LB="$CACHE/$JOB.blend"
    if [ ! -f "$LB" ] || [ "$(stat -f %z "$LB" 2>/dev/null)" != "$(stat -f %z "$B" 2>/dev/null)" ]; then
      find "$CACHE" -name "*.blend*" -mmin +720 -delete 2>/dev/null      # alte Kopien (12 h) weg
      echo "$(date +%H:%M:%S) kopiert die Datei auf diesen Mac ..."
      cp -f "$B" "$LB.part" 2>/dev/null && mv -f "$LB.part" "$LB" || LB="$B"
    fi
    ARGS=(-b "$LB" --factory-startup); [ "$SCENE" != "-" ] && ARGS+=(-S "$SCENE")
    # in Paketen zu je CHUNK Frames rendern: so reagiert der Worker schnell auf "Farm aus"
    CHUNK=20; STOPPED=0
    for ((CS=START; CS<=END; CS+=CHUNK)); do
      CE=$((CS + CHUNK - 1)); [ $CE -gt $END ] && CE=$END
      FREE=0; for ((f=CS; f<=CE; f++)); do ls "$OUT/$JOB/${JOB}_$(printf %04d $f)".* >/dev/null 2>&1 || { FREE=1; break; }; done
      [ $FREE = 1 ] || continue
      read -r FARM _ _ _ < <(control); [ "$FARM" = "aus" ] && { STOPPED=1; break; }
      echo "$(date +%H:%M:%S) Frames $CS-$CE ..."
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
    LAST_WORK=$(date +%s)
    [ $STOPPED = 1 ] && { echo "$(date +%H:%M) Farm aus - Job $JOB pausiert"; continue; }
    DONE=$(frames_done "$JOB")
    if [ "$DONE" -ge "$TOTAL" ]; then
      date > "$Q/$JOB.done"; echo "$(date +%H:%M) FERTIG: $JOB ($DONE/$TOTAL Frames)"
    fi
  done
  status wartet
  if [ "${IDLE_MIN:-0}" -gt 0 ] && [ $(( $(date +%s) - LAST_WORK )) -ge $(( IDLE_MIN * 60 )) ]; then
    status abgemeldet; echo "$(date +%H:%M) $IDLE_MIN min ohne Job -> melde ab"; sleep 5
    osascript -e 'tell application "loginwindow" to «event aevtrlgo»'; exit 0
  fi
  board
  echo "$(date +%H:%M) wartet auf Jobs ... (Beenden: Ctrl+C)"
  sleep 20
done
