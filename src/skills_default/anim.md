---
title: ANIM
description: Product films: sections, camera moves, the Video Editor
icon: RENDER_ANIMATION
chat: yes
---
Product films with AI Studio's tools. Answer briefly. On by itself when the scene has sections or a film
renders (or with its button in the skill row).
Sections = timeline markers "NN Name" (01 Hero, 02 Explosion). A section runs from its marker to the frame
before the next one. Its camera is marker.camera (own camera P_<name>_01 on a pivot, Pass Index = number).
- api.section_add("Hero", frame=None, from_view=False): a new section from this frame with its own camera,
  starting where the active camera is (or the viewport).
- api.section_frames("01 Intro", start=None, length=None, move_keys=True): move / lengthen; everything after
  moves along with its keys. Never move markers by hand when keys exist.
- api.motion_preset(preset, start, end, amount, reverse, replace=True): one move on the camera's pivot.
  TURNTABLE degrees (360) · SWING ± degrees, there and back · PUSH_IN / PULL_OUT % of the distance (60 / 160)
  · CRANE degrees up (reverse: down) · FOCUS start % (needs drop_focus=True if the camera focuses on an
  object: ask first) · FADE_IN / FADE_OUT frames, from black (reverse: white) via exposure · HOLD.
  Default frames = the current section. Keys already there: replace=True removes them in the frames, False
  keeps them; say which you did.
- api.motion_chain("02 Hero", [("PULL_OUT", 24, 140), ("HOLD", 12), ("TURNTABLE", 0, 90)]): steps one after
  the other (frames 0 = to the end). Motion is in the Camera tab.
- api.key_pose(): the viewport's view as a key at this frame (with the lens).
- Render: api.section_render(section=None, quality=None, frames=None) (⌘E, or Render in the Video Editor's
  Studio tab): each section renders its frames in its own process into animation/Frames/<section>/,
  strictly one after the other. quality None = by itself: Low Quality first, High Quality once nothing
  changed; sections already in High Quality with nothing changed are skipped. frames=(a, b) renders only
  those frames again (the others stay). No video per section. Each section renders from its marker's
  camera; check a section's camera (marker.camera) and its framing at a frame before rendering it.
  api.render_status() says what renders (frame, seconds left); studio_render_queue 'wait' answers only
  when the films are done. Never start own background renders to check a film.
- The Video Editor of the same scene shows every section as an image strip (they come in and fill in by
  themselves; the scene renders 3D, render.use_sequencer stays off). api.cut_build({"01 Intro": ("CROSS",
  12, 0), "02 Hero": ("BLACK", 8, 24)}) sets the transition into the next section, its frames and hold
  frames, and builds the strips again (ours are replaced, the user's own stay). Music + BPM:
  scene.studio.cut_music / cut_bpm, studio.cut(action='SNAP') puts sections on whole beats (ask first, it
  moves keys). api.cut_export(edl=False) renders all strips into one MP4 at the end (animation/Export).
- Explosion view (Camera tab): api.explode_group("Lampenkopf", [parts]) gives a handle "Griff | Lampenkopf";
  the parts follow it only while scene["Explosion"] > 0 (0 = exactly as in CAD). Place groups by moving /
  turning their handles with the explosion open, never the parts themselves. api.explode_arrange(names,
  flat, upright, in_row) turns groups flat to the camera and puts them in a row; api.explode_key_section("02
  Explosion") keys it open in that section and closed after it; api.realign_hooks() after new origins.
