---
title: ANIM
description: Product films: sections, camera moves, the cut
icon: RENDER_ANIMATION
studio_tab: ANIM
chat: yes
---
Product films with AI Studio's tools. Answer briefly.
Films live in Film mode (the Still | Film switch on top: scene.studio.mode = 'ANIM'); finished films,
also LQ ones, are watched in the Render tab (each with an MP4 per section and for the whole film).
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
  the other (frames 0 = to the end). The steps show in the Animation tab.
- api.key_pose(): the viewport's view as a key at this frame (with the lens).
- Render: ⌘E, Film LQ/HQ or a section's LQ/HQ; films run in their own process strictly one after the
  other (the queue waits). Check with a viewport preview (studio.anim_preview) before High Quality.
  api.render_status() says what renders (part, frame, seconds left) and the finished videos' paths;
  studio_render_queue 'wait' answers only when the films are done, with their paths. Never start own
  background renders to check a film.
- Cut: api.cut_build({"01 Intro": ("CROSS", 12, 0), "02 Hero": ("BLACK", 8, 24)}) makes the scene "Schnitt"
  in the Video Sequencer (transition into the next section, frames, hold frames + PNG still). Our strips
  are replaced, the user's own stay; re-rendered sections update by themselves. Music + BPM:
  scene.studio.cut_music / cut_bpm, then studio.cut(action='SNAP') puts sections on whole beats (ask first,
  it moves keys). api.cut_export(edl=False) writes the MP4 in the background.
