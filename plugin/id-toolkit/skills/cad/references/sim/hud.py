"""HUD im Wärmebild-Look über die gerenderten Frames legen und als 9:16-MP4 kodieren.

python hud.py <reel.json>
reel.json zusätzlich: "title": "SUB", "subtitle": "18\" TAPPED HORN", "mode_labels": {"<idx>": "Seitenwand"},
                      "tech": "...", "video": ".../SUB_modal.mp4", "tone": true
"""
import sys, os, json, subprocess, glob
import numpy as np
from PIL import Image, ImageDraw, ImageFont

cfg = json.load(open(sys.argv[1]))
src = cfg["out"]
dst = src.rstrip("/") + "_hud"
os.makedirs(dst, exist_ok=True)
meta = json.load(open(os.path.join(src, "frames.json")))
fps = meta["fps"]
W, H = cfg.get("res", [1080, 1920])

def font(size, bold=False):
    for p in cfg.get("fonts", []) + ["/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/SFNSMono.ttf"]:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size, index=1 if (bold and p.endswith(".ttc")) else 0)
            except Exception:
                continue
    return ImageFont.load_default()

F_S, F_M, F_L, F_XL = font(30), font(40, True), font(52, True), font(170, True)
PAL = [(0.00, (9, 3, 23)), (0.18, (51, 5, 107)), (0.38, (158, 15, 128)), (0.55, (230, 46, 51)),
       (0.72, (255, 122, 10)), (0.88, (255, 209, 51)), (1.00, (255, 255, 242))]
def pal(x):
    for (p0, c0), (p1, c1) in zip(PAL, PAL[1:]):
        if x <= p1:
            f = (x - p0) / (p1 - p0)
            return tuple(int(c0[i] + (c1[i] - c0[i]) * f) for i in range(3))
    return PAL[-1][1]
bar_h, bar_w = 760, 26
bar = Image.new("RGB", (bar_w, bar_h))
bd = ImageDraw.Draw(bar)
for y in range(bar_h):
    bd.line([(0, y), (bar_w, y)], fill=pal(1 - y / (bar_h - 1)))

WHITE, GREY = (245, 240, 235), (150, 140, 150)
M = 64
labels = cfg.get("mode_labels", {})
nseg = len(cfg.get("modes", []))
for fi in meta["frames"]:
    im = Image.open(os.path.join(src, f"f_{fi['frame']:05d}.png")).convert("RGB")
    d = ImageDraw.Draw(im)
    # header
    d.text((M, M), cfg.get("brand", ""), font=F_S, fill=GREY)
    d.text((M, M + 44), cfg.get("title", ""), font=F_L, fill=WHITE)
    d.text((M, M + 108), cfg.get("subtitle", ""), font=F_S, fill=GREY)
    d.text((W - M, M), f"MODE {fi['mode_no']:02d}/{nseg:02d}", font=F_S, fill=GREY, anchor="ra")
    d.text((W - M, M + 44), "MODALANALYSE", font=F_S, fill=GREY, anchor="ra")
    # colour bar (right)
    bx, by = W - M - bar_w, 330
    im.paste(bar, (bx, by))
    d.text((bx - 14, by), "MAX", font=F_S, fill=WHITE, anchor="ra")
    d.text((bx - 14, by + bar_h), "0", font=F_S, fill=WHITE, anchor="rd")
    for k in range(1, 4):
        y = by + bar_h * k / 4
        d.line([(bx - 8, y), (bx, y)], fill=GREY, width=2)
    d.text((bx + bar_w, by + bar_h + 16), "rel. Auslenkung", font=F_S, fill=GREY, anchor="ra")
    # frequency (fades with segment)
    a = fi["env"]
    col = tuple(int(c * a) for c in WHITE)
    y0 = H - 430
    d.text((M, y0), f"{fi['f_hz']:.0f}", font=F_XL, fill=col)
    wnum = d.textlength(f"{fi['f_hz']:.0f}", font=F_XL)
    d.text((M + wnum + 18, y0 + 112), "Hz", font=F_L, fill=col)
    lab = labels.get(str(fi["mode_idx"]), "")
    if lab:
        d.text((M, y0 + 200), lab, font=F_M, fill=tuple(int(c * a) for c in (255, 190, 90)))
    # footer
    d.line([(M, H - 120), (W - M, H - 120)], fill=(60, 50, 60), width=2)
    d.text((M, H - 96), cfg.get("tech", ""), font=F_S, fill=GREY)
    im.save(os.path.join(dst, f"h_{fi['frame']:05d}.png"))

video = cfg["video"]
cmd = ["ffmpeg", "-y", "-framerate", str(fps), "-i", os.path.join(dst, "h_%05d.png")]
if cfg.get("tone"):
    # each segment plays its real natural frequency (quiet sine, fades)
    segs = []
    spm = cfg.get("sec_per_mode", 3.0)
    for j in cfg["modes"]:
        f = meta["freqs"][j]
        segs.append(f"sine=f={f:.2f}:d={spm}:sample_rate=48000,volume=0.25,afade=t=in:d=0.4,afade=t=out:st={spm-0.4}:d=0.4")
    graph = ";".join(f"{s}[a{i}]" for i, s in enumerate(segs)) + ";" + "".join(f"[a{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=0:a=1[aout]"
    cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-filter_complex", graph, "-map", "0:v", "-map", "[aout]", "-c:a", "aac", "-b:a", "160k"]
cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-shortest", video]
r = subprocess.run(cmd, capture_output=True, text=True)
print(r.stderr[-1500:])
print("VIDEO", video, os.path.exists(video))
