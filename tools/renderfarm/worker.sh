#!/bin/bash
# Ordner-Renderfarm - Worker fuer Mac (Apple Silicon)
# Rendert Jobs aus <Renderfarm>/queue. Mehrere Rechner teilen sich die Frames automatisch.
# Wird vom Server gestartet, Updates wirken also sofort auf allen Rechnern.
# 0.5.0: Uebersicht der Jobs, Frames und Macs gleich nach dem Start und beim Warten; jedes fertige
#        Frame mit Zeit und Stand des Jobs.
# 0.6.0: alle Macs auf einmal abmelden, wenn du es sagst (AI Studio: "Log Out All Macs", oder Terminal:
#        bash "Renderfarm Start (Mac).command" --alle-abmelden); control.json "abmelden_nach" (Minuten ohne
#        Job, Standard 0 = nie). Nach dem naechsten Anmelden startet der Autostart die Renderfarm wieder.
# 0.8.0: Logbuch pro Mac (~/.renderfarm/worker.log, Kopie auf dem Server in _worker/logs/<Mac>.log):
#        Start, Jobs, Pakete mit Zeit, Server weg/zurueck, Ende mit Grund. Beim Start steht drin, wie die
#        letzte Sitzung endete (sauber, Abmelden, Neustart, Ausschalten, Schlaf) und welche Regeln der Mac
#        hat (automatisch abmelden nach, geplantes Ausschalten): so sieht AI Studio, warum Macs wegfallen.
#        Low Quality vor High Quality: nach jedem Paket schaut der Mac, ob ein Job weiter vorne wartet
#        (Namen 1_… = LQ vor 2_… = HQ) und wechselt dorthin; Pakete dauern hoechstens ~10 min.
# 0.9.0: Haengende Frames: ein leerer Platzhalter eines Macs, der weggefallen ist (aelter als 30 min),
#        wird wieder frei (vorher wartete die ganze Farm darauf, eine Stunde 0 Frames). Freie Macs nehmen
#        den naechsten Job mit freien Frames statt zu warten. Reihenfolge: Rang (<job>.rang: als naechstes /
#        als letztes), LQ vor HQ, dann der Job mit den wenigsten offenen Frames zuerst (control.json
#        "sortierung": "kurz", sonst "reihe" = Sendereihenfolge). Frames, die ein neuerer Job uebernommen hat
#        (<job>.skip oder "skip" in der .json), rendert kein Mac mehr. Beim Warten steht der Grund im Status.

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
LDIR="$HOME/.renderfarm"; LLOG="$LDIR/worker.log"; SLOG="$ROOT/_worker/logs/$NAME.log"
mkdir -p "$LDIR"
log() {       # eine Zeile ins Logbuch (lokal immer; der Server bekommt es mit sync_log)
  echo "$(date '+%Y-%m-%d %H:%M:%S') | $*" >> "$LLOG"
}
sync_log() {  # das lokale Logbuch auf den Server (haengt der Server, wartet es hoechstens 10 s)
  if [ -f "$LLOG" ] && [ "$(wc -c < "$LLOG")" -gt 2000000 ]; then
    tail -n 5000 "$LLOG" > "$LLOG.tmp" && mv -f "$LLOG.tmp" "$LLOG"
  fi
  t 10 mkdir -p "$ROOT/_worker/logs" 2>/dev/null && t 10 cp -f "$LLOG" "$SLOG" 2>/dev/null
}
why_ended() { # wie die letzte Sitzung endete: aus dem Logbuch und dem System
  local last since
  last=$(tail -1 "$LLOG" 2>/dev/null)
  case "$last" in
    *"| ENDE"*|"") ;;
    *) since=$(echo "$last" | cut -c1-19)
       log "VORHER kein sauberes Ende, letztes Lebenszeichen $since" ;;
  esac
  log "SYSTEM letzte Anmeldungen: $(last -5 "$USER" 2>/dev/null | grep -v '^$' | grep -v wtmp | awk '{$1=$1};1' | tr '\n' ';' | cut -c1-400)"
  log "SYSTEM Neustarts: $(last reboot 2>/dev/null | head -3 | awk '{$1=$1};1' | tr '\n' ';')  Ausschalten: $(last shutdown 2>/dev/null | head -3 | awk '{$1=$1};1' | tr '\n' ';')"
  log "SYSTEM Schlaf/Wach: $(pmset -g log 2>/dev/null | grep -E ' (Sleep|Wake|DarkWake|Shutdown) ' | tail -4 | cut -c1-90 | tr '\n' ';')"
  log "REGELN automatisch abmelden nach: $(defaults read /Library/Preferences/.GlobalPreferences com.apple.autologout.AutoLogOutDelay 2>/dev/null || echo aus) s | geplant: $(pmset -g sched 2>/dev/null | tr -s ' ' | tr '\n' ';' | cut -c1-200) | Schlaf: $(pmset -g 2>/dev/null | grep -E '^ *(sleep|displaysleep) ' | tr -s ' ' | tr '\n' ';')"
}
remount() {   # Server-Verbindung pruefen und bei Bedarf neu verbinden (Login aus dem Schluesselbund)
  t 10 ls "$Q" >/dev/null 2>&1 && return 0
  echo "$(date +%H:%M) Server weg -> verbinde neu ..."
  [ -d "$VOL" ] && diskutil unmount force "$VOL" >/dev/null 2>&1
  [ -s "$SRVF" ] && osascript -e "mount volume \"$(cat "$SRVF")\"" >/dev/null 2>&1
  t 10 ls "$Q" >/dev/null 2>&1
}
control() {   # liest _worker/control.json: farm an/aus, ende standby/abmelden/herunterfahren, abmelden_nach (min), sortierung
  python3 -c "import json,sys;c=json.load(open(sys.argv[1]));print(c.get('farm','an'),c.get('ende','standby'),int(c.get('seit',0)),int(c.get('abmelden_nach',0)),c.get('sortierung','kurz'))" "$ROOT/_worker/control.json" 2>/dev/null || echo "an standby 0 0 kurz"
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
  read -r _ _ _ IDLE_MIN _ < <(control)
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

HELP="$(mktemp -t renderfarm-help).py"   # welcher Job, welche Frames (ein Python-Aufruf statt vieler ls)
cat > "$HELP" <<'PYEOF'
import json, os, re, sys, time
STALE = 1800          # ein leerer Platzhalter, so alt: sein Mac ist weggefallen, der Frame ist wieder frei


def frames_of(spec):
    out = set()
    for part in spec:
        if isinstance(part, int):
            out.add(part)
            continue
        a, _, b = str(part).partition("-")
        try:
            out.update(range(int(a), int(b or a) + 1))
        except ValueError:
            pass
    return out


def load(q, job):
    with open(os.path.join(q, job + ".json")) as f:
        c = json.load(f)
    skip = frames_of(c.get("skip", []))
    try:
        with open(os.path.join(q, job + ".skip")) as f:
            skip |= frames_of(f.read().split())
    except OSError:
        pass
    return c, int(c.get("start", 0)), int(c.get("end", 0)), skip


def taken(q, out, job, start, end, skip):
    """Frames, die vergeben sind (Bild da, frischer Platzhalter, uebernommen); alte Platzhalter gehen."""
    now = time.time()
    try:
        since = os.path.getmtime(os.path.join(q, job + ".json"))
    except OSError:
        since = now
    have = {f for f in skip if start <= f <= end}
    freed = []
    try:
        entries = list(os.scandir(os.path.join(out, job)))
    except OSError:
        entries = []
    pre = job + "_"
    for e in entries:
        if not e.name.startswith(pre) or e.name.endswith(".part"):
            continue
        m = re.match(r"(\d+)\.", e.name[len(pre):])
        if not m:
            continue
        frame = int(m.group(1))
        try:
            st = e.stat()
        except OSError:
            continue
        if st.st_size == 0 and frame not in skip and st.st_mtime > since + 60 and now - st.st_mtime > STALE:
            try:
                os.remove(e.path)
                freed.append(frame)
                continue
            except OSError:
                pass
        have.add(frame)
    return have, freed


def order(q, out, mode):
    rows, full = [], 0
    for name in sorted(os.listdir(q)):
        if not name.endswith(".json"):
            continue
        job = name[:-5]
        if not os.path.exists(os.path.join(q, job + ".blend")) or os.path.exists(os.path.join(q, job + ".done")):
            continue
        try:
            _c, s, e, skip = load(q, job)
        except (OSError, ValueError):
            continue
        have, freed = taken(q, out, job, s, e, skip)
        for f in freed:
            print("# FREI %s %d" % (job, f))
        left = (e - s + 1) - len(have)
        if left <= 0:
            full += 1
            continue
        m = re.match(r"^(\d)_", job)
        prio = int(m.group(1)) if m else 2
        try:
            with open(os.path.join(q, job + ".rang")) as f:
                rank = int(f.read().split()[0])
        except (OSError, ValueError, IndexError):
            rank = 50
        stamp = re.search(r"(\d{8}-\d{6})", job)
        key = (rank, prio, left if mode == "kurz" else 0, stamp.group(1) if stamp else job)
        rows.append((key, job, left, rank, prio))
    rows.sort()
    for _key, job, left, rank, prio in rows:
        print(job, left, rank, prio)
    if not rows:
        print("# WARTET", full)


def chunk(q, out, job, n):
    _c, s, e, skip = load(q, job)
    have, _freed = taken(q, out, job, s, e, skip)
    free = [f for f in range(s, e + 1) if f not in have]
    if free:
        a = b = free[0]
        while b + 1 <= e and b + 1 not in have and b - a + 1 < n:
            b += 1
        print(a, b)


def info(q, job):
    c, s, e, skip = load(q, job)
    print(c.get("scene", "-") or "-", s, e, c.get("blender", "-") or "-", (e - s + 1) - len({f for f in skip if s <= f <= e}))


try:
    cmd = sys.argv[1]
    if cmd == "order":
        order(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "chunk":
        chunk(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]))
    elif cmd == "info":
        info(sys.argv[2], sys.argv[3])
except Exception as ex:                       # der Server haengt oder eine Datei ist halb: naechstes Mal
    print("# FEHLER", ex)
PYEOF

status() { echo "$NAME | $1 | $(date '+%Y-%m-%d %H:%M') | Blender $BV | v$VERSION" > "$ST/$NAME-render.txt" 2>/dev/null; }
ended() {     # Ende mit Grund ins Logbuch (INT: Ctrl+C, TERM: Abmelden/Ausschalten/kill, HUP: Fenster zu)
  log "ENDE $1"; status beendet; sync_log; echo; echo "Beendet."; exit 0
}
trap 'ended "Signal INT (Ctrl+C)"' INT
trap 'ended "Signal TERM (Abmelden, Ausschalten oder beendet)"' TERM
trap 'ended "Signal HUP (Terminal-Fenster zu oder Abmelden)"' HUP
caffeinate -i -w $$ &

why_ended
log "START v$VERSION | Blender $BV | angemeldet: $(stat -f %Su /dev/console 2>/dev/null) | Strom: $(pmset -g batt 2>/dev/null | grep -o "'.*'" | head -1)"
status wartet
remount && board
sync_log
BEAT=$(date +%s)
echo "Wartet auf Jobs ... (Beenden: Ctrl+C)"
while true; do
  if ! remount; then
    [ "${SERVER_GONE:-0}" = 0 ] && log "SERVER weg"; SERVER_GONE=1
    echo "$(date +%H:%M) Server nicht erreichbar, warte ..."; sleep 60; continue
  fi
  [ "${SERVER_GONE:-0}" = 1 ] && { log "SERVER wieder da"; SERVER_GONE=0; }
  if [ $(( $(date +%s) - BEAT )) -ge 300 ]; then BEAT=$(date +%s); log "LEBT"; sync_log; fi
  read -r FARM ENDE SEIT IDLE_MIN SORT < <(control)
  if [ "$FARM" = "aus" ]; then
    if [ "$SEIT" -ge "$STARTED" ] && [ "$ENDE" = "abmelden" ]; then
      status abgemeldet; log "ENDE abgemeldet auf Befehl (Farm aus, abmelden)"; sync_log
      echo "$(date +%H:%M) Farm aus -> melde ab"; sleep 5
      osascript -e 'tell application "loginwindow" to «event aevtrlgo»'; exit 0
    elif [ "$SEIT" -ge "$STARTED" ] && [ "$ENDE" = "herunterfahren" ]; then
      status heruntergefahren; log "ENDE ausgeschaltet auf Befehl (Farm aus, herunterfahren)"; sync_log
      echo "$(date +%H:%M) Farm aus -> fahre herunter"; sleep 5
      osascript -e 'tell application "System Events" to shut down'; exit 0
    fi
    [ "${PAUSED:-0}" = 0 ] && log "PAUSE Farm aus"; PAUSED=1
    status "pausiert (Farm aus)"; sleep 60; continue
  fi
  [ "${PAUSED:-0}" = 1 ] && { log "PAUSE vorbei (Farm an)"; PAUSED=0; }
  if [ -n "$RF_ROOT" ] && [ "$ROOT/_worker/worker.sh" -nt "${BASH_SOURCE[0]}" ]; then
    log "ENDE Update: neue Worker-Version auf dem Server"; sync_log
    echo "$(date +%H:%M) neue Worker-Version auf dem Server -> startet neu"; exit 3   # der Start holt sie
  fi
  # der Job, der jetzt dran ist: Rang, LQ vor HQ, wenige offene Frames zuerst; nur Jobs mit freien Frames
  WHY="keine offenen Jobs"; JOB=""
  while read -r A B C D; do
    case "$A" in
      "#") case "$B" in
             FREI) log "FREI $C Frame $D (Platzhalter eines weggefallenen Macs, wieder frei)" ;;
             WARTET) [ "${C:-0}" -gt 0 ] && WHY="alle Frames vergeben ($C Jobs laufen auf anderen Macs)" ;;
             FEHLER) WHY="Server antwortet nicht richtig" ;;
           esac ;;
      *) [ -n "$JOB" ] && continue
         [ "$A" = "$BAD_JOB" ] && [ "$(date +%s)" -lt "${BAD_UNTIL:-0}" ] && { WHY="$A schlug 3x fehl, Pause"; continue; }
         JOB="$A" ;;
    esac
  done < <(t 60 python3 "$HELP" order "$Q" "$OUT" "$SORT" 2>/dev/null)
  if [ -n "$JOB" ]; then
    J="$Q/$JOB.json"; B="$Q/$JOB.blend"
    read -r SCENE START END JBV TOTAL < <(python3 "$HELP" info "$Q" "$JOB" 2>/dev/null)
    [ -n "$END" ] || { sleep 5; continue; }
    if [ "$JBV" != "-" ] && [ "${JBV%.*}" != "${BV%.*}" ]; then
      echo "$(date +%H:%M) WARNUNG: $JOB wurde mit Blender $JBV gesendet, hier laeuft $BV."
    fi
    board
    echo "$(date +%H:%M) Job $JOB (Szene $SCENE, Frames $START-$END)"
    log "JOB $JOB Frames $START-$END"
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
    # in Paketen rendern (hoechstens ~10 min): so reagiert der Mac schnell auf "Farm aus" und LQ-Jobs
    [ "$JOB" = "${LAST_JOB:-}" ] || CHUNK=20; LAST_JOB="$JOB"; STOPPED=0; FAILS=0
    PLOG="$LDIR/paket.log"
    while true; do
      read -r CS CE < <(t 60 python3 "$HELP" chunk "$Q" "$OUT" "$JOB" "$CHUNK" 2>/dev/null)
      [ -n "$CE" ] && [ "$CS" != "#" ] || break         # nichts mehr frei: fertig oder die anderen Macs haben den Rest
      read -r FARM _ _ _ _ < <(control); [ "$FARM" = "aus" ] && { STOPPED=1; break; }
      echo "$(date +%H:%M:%S) Frames $CS-$CE ..."
      T0=$(date +%s); PK0=$T0
      RF_JOB="$JOB" RF_OUT="$OUT" RF_JSON="$J" "$BLENDER" "${ARGS[@]}" --python "$PY" -s $CS -e $CE -a 2>&1 \
        | tee "$PLOG" | while IFS= read -r line; do
            case "$line" in
              *Saved:*)
                FR=${line##*_}; FR=${FR%%.*}; NOW=$(date +%s)
                echo "$(date +%H:%M:%S) Frame $((10#$FR)) fertig ($((NOW - T0)) s) | Job $(frames_done "$JOB")/$TOTAL"
                T0=$NOW ;;
              *RF:*|*rror*) echo "$line" ;;
            esac
          done
      RC=${PIPESTATUS[0]}
      SECS=$(( $(date +%s) - PK0 )); NEW=$(grep -c "Saved:" "$PLOG" 2>/dev/null); NEW=${NEW:-0}
      log "PAKET $JOB $CS-$CE | $NEW neue Frames | $SECS s | Blender-Code $RC"
      if [ $(( $(date +%s) - BEAT )) -ge 300 ]; then BEAT=$(date +%s); sync_log; fi
      if [ "$NEW" -gt 0 ]; then
        FAILS=0; SPF=$(( SECS / NEW )); [ $SPF -lt 1 ] && SPF=1; CHUNK=$(( 600 / SPF ))
        [ $CHUNK -lt 1 ] && CHUNK=1; [ $CHUNK -gt 20 ] && CHUNK=20
      elif [ "$RC" != 0 ]; then
        FAILS=$((FAILS + 1))
        if [ $FAILS -ge 3 ]; then             # Blender bricht bei diesem Job immer ab: 30 min die anderen
          log "FEHLER $JOB: 3 Pakete ohne Frame (Blender-Code $RC), 30 min Pause fuer diesen Job"
          BAD_JOB="$JOB"; BAD_UNTIL=$(( $(date +%s) + 1800 )); break
        fi
      fi
      # ein Job, der jetzt vorgeht (Rang, LQ, deutlich weniger offene Frames): dorthin wechseln
      read -r TOP TLEFT TRANK TPRIO < <(t 60 python3 "$HELP" order "$Q" "$OUT" "$SORT" 2>/dev/null | grep -v '^#' | head -1)
      if [ -n "$TOP" ] && [ "$TOP" != "$JOB" ]; then
        read -r MLEFT MRANK MPRIO < <(t 60 python3 "$HELP" order "$Q" "$OUT" "$SORT" 2>/dev/null | awk -v j="$JOB" '$1==j{print $2,$3,$4}')
        if [ -z "$MLEFT" ] || [ "$TRANK" -lt "${MRANK:-50}" ] || [ "$TPRIO" -lt "${MPRIO:-2}" ] \
           || { [ "$SORT" = "kurz" ] && [ "$TRANK" -eq "${MRANK:-50}" ] && [ "$TPRIO" -eq "${MPRIO:-2}" ] \
                && [ $(( TLEFT + CHUNK )) -lt "${MLEFT:-0}" ]; }; then
          log "WECHSEL zu $TOP (geht vor $JOB)"; break
        fi
      fi
    done
    LAST_WORK=$(date +%s)
    [ $STOPPED = 1 ] && { log "PAUSE Job $JOB (Farm aus)"; echo "$(date +%H:%M) Farm aus - Job $JOB pausiert"; continue; }
    DONE=$(frames_done "$JOB")
    if [ "$DONE" -ge "$TOTAL" ] && [ ! -f "$Q/$JOB.done" ]; then
      date > "$Q/$JOB.done"; echo "$(date +%H:%M) FERTIG: $JOB ($DONE/$TOTAL Frames)"
      log "FERTIG $JOB ($DONE/$TOTAL)"
    fi
    continue
  fi
  [ "$WHY" != "${LAST_WHY:-}" ] && { log "WARTET $WHY"; LAST_WHY="$WHY"; }
  status "wartet ($WHY)"
  if [ "${IDLE_MIN:-0}" -gt 0 ] && [ $(( $(date +%s) - LAST_WORK )) -ge $(( IDLE_MIN * 60 )) ]; then
    status abgemeldet; log "ENDE abgemeldet nach $IDLE_MIN min ohne Job"; sync_log
    echo "$(date +%H:%M) $IDLE_MIN min ohne Job -> melde ab"; sleep 5
    osascript -e 'tell application "loginwindow" to «event aevtrlgo»'; exit 0
  fi
  board
  echo "$(date +%H:%M) wartet auf Jobs ... (Beenden: Ctrl+C)"
  sleep 20
done
