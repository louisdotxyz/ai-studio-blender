# SPDX-License-Identifier: GPL-3.0-or-later
"""Translations.

The add-on is written in English. Blender shows the German strings below when
its interface language is German (Preferences › Interface › Translation).
Labels, operator names, properties and tooltips are translated by Blender
automatically; strings built at runtime go through ``tr()`` / ``tip()``.

To add a language, add a dict like ``DE`` and register it under its locale.
"""

import bpy

DE = {
    # -- input bar, chat ----------------------------------------------------
    "How can I help?": "Wie kann ich helfen?",
    "Claude sees your scene and viewport and can control Blender directly. Attach images and files with the "
    "upload button or drop them onto this sidebar.":
        "Claude sieht deine Szene und den Viewport und kann Blender direkt steuern. Bilder und Dateien mit dem "
        "Upload-Knopf anhängen oder auf diese Leiste ziehen.",
    "Reply…": "Antworten …",
    "Your own answer…": "Eigene Antwort …",
    "Add a message while Claude works…": "Zwischenfrage …",
    "Message": "Nachricht",
    "Message to Claude – Enter sends, clicking elsewhere keeps the text":
        "Nachricht an Claude – Enter sendet, Klick daneben behält den Text",
    "Send": "Senden",
    "Send the message, also while Claude is working": "Nachricht senden – auch während Claude arbeitet",
    "Write a message first": "Schreib zuerst eine Nachricht",
    "Stop": "Stoppen",
    "Stop Claude": "Claude anhalten",
    "Stopped": "Gestoppt",
    "your message is next": "deine Nachricht kommt als Nächstes dran",
    "Newest": "Neueste",
    "Jump to Newest Messages": "Zu den neuesten Nachrichten",
    "Scroll Chat": "Chat scrollen",
    "(undone)": "(rückgängig gemacht)",
    "Copy": "Kopieren",
    "Copied": "Kopiert",
    "Copy the answer to the clipboard": "Antwort in die Zwischenablage kopieren",
    "Edit": "Bearbeiten",
    "Load this message into the input field to edit and resend it":
        "Diese Nachricht zum Bearbeiten ins Eingabefeld laden und neu senden",
    "Prompt Confirmed": "Eingabe bestätigt",
    "Selection: {}": "Auswahl: {}",
    "Send Selection": "Auswahl mitsenden",
    "Send details of the selected objects (click to turn on/off)":
        "Details der ausgewählten Objekte mitsenden (klicken zum Ein-/Ausschalten)",
    "Skill: {}": "Skill: {}",
    "Auto": "Auto",

    # -- chats, history, memory ---------------------------------------------
    "Chats": "Chats",
    "New Chat": "Neuer Chat",
    "Start a new chat (the current one stays in the history)":
        "Neuen Chat beginnen (der aktuelle bleibt im Verlauf)",
    "Open Chat": "Chat öffnen",
    "Open this chat and continue it": "Diesen Chat öffnen und weiterführen",
    "Delete Chat": "Chat löschen",
    "Delete This Chat": "Diesen Chat löschen",
    "Chat Name": "Chat-Name",
    "Name of this chat – click to rename it": "Name dieses Chats – klicken zum Umbenennen",
    "Delete the current chat from the history": "Den aktuellen Chat aus dem Verlauf löschen",
    "No chats yet": "Noch keine Chats",
    "Project Memory…": "Projekt-Gedächtnis …",
    "Global Memory…": "Allgemeines Gedächtnis …",
    "Open": "Öffnen",
    "Open in the file browser or default editor": "Im Finder bzw. Standard-Editor öffnen",
    "Open the skills folder: edit skills as Markdown files or add new ones":
        "Skills-Ordner öffnen: Skills als Markdown-Dateien bearbeiten oder neue anlegen",
    "Open this project's memory (Markdown next to the .blend file)":
        "Gedächtnis dieses Projekts öffnen (Markdown neben der .blend-Datei)",
    "Open the memory for all projects (Markdown)": "Gedächtnis für alle Projekte öffnen (Markdown)",
    "Save the .blend file first": "Speichere die .blend-Datei zuerst",
    "Remembered: {}": "Gemerkt: {}",
    "Remembered something": "Etwas gemerkt",

    # -- attachments, skills, render ----------------------------------------
    "Upload": "Hochladen",
    "Attach images, PDFs, text or 3D files to the message (opens ~/Downloads)":
        "Bilder, PDFs, Text- oder 3D-Dateien an die Nachricht anhängen (öffnet ~/Downloads)",
    "Files or Images…": "Dateien oder Bilder …",
    "Paste from Clipboard": "Aus Zwischenablage einfügen",
    "Attach an image or a copied file from the clipboard":
        "Bild oder kopierte Datei aus der Zwischenablage anhängen",
    "Attach to Claude": "An Claude anhängen",
    "Remove": "Entfernen",
    "Remove the attachment": "Anhang entfernen",
    "Not a file: {}": "Keine Datei: {}",
    "Could not read the image": "Bild konnte nicht gelesen werden",
    "There is no image or file in the clipboard": "In der Zwischenablage ist kein Bild und keine Datei",
    "No image in the clipboard (requires wl-paste or xclip)":
        "Kein Bild in der Zwischenablage (benötigt wl-paste oder xclip)",
    "Could not read attachment '{}': {}": "Anhang '{}' konnte nicht gelesen werden: {}",
    "Skills": "Skills",
    "Use Skill": "Skill verwenden",
    "Check or uncheck this skill (checked skills apply to all your messages)":
        "Skill an- oder abhaken (angehakte Skills gelten für alle deine Nachrichten)",
    "New Skill": "Neuer Skill",
    "Create a new skill: reusable instructions you can check for your messages":
        "Neuen Skill anlegen: wiederverwendbare Anweisungen, die du für deine Nachrichten anhaken kannst",
    "Short Description": "Kurzbeschreibung",
    "Instructions": "Anweisungen",
    "What the model should do when this skill is checked": "Was das Modell tun soll, wenn der Skill angehakt ist",
    "Edit Longer Text in the Editor": "Längeren Text im Editor bearbeiten",
    "Give the skill a name": "Gib dem Skill einen Namen",
    "Show the skills: check the ones that should apply to your messages":
        "Skills zeigen: hake an, welche für deine Nachrichten gelten sollen",
    "Remove skill": "Skill entfernen",
    "Edit Skills…": "Skills bearbeiten …",
    "The scene has no camera": "Die Szene hat keine Kamera",
    "No render result": "Kein Render-Ergebnis",
    "No 3D viewport found": "Kein 3D-Viewport gefunden",

    # -- questions, code approval, undo -------------------------------------
    "Claude asks": "Claude fragt",
    "Answer": "Antworten",
    "Send this answer to Claude": "Diese Antwort an Claude schicken",
    "or type your own answer below": "oder unten eigene Antwort tippen",
    "(no answer)": "(keine Antwort)",
    "Claude wants to run code": "Claude möchte Code ausführen",
    "(no description)": "(ohne Beschreibung)",
    "… {} more lines": "… {} weitere Zeilen",
    "Run": "Ausführen",
    "Reject": "Ablehnen",
    "Rejected: {}": "Abgelehnt: {}",
    "Code": "Code",
    "Run Code": "Code ausführen",
    "Run the code (undo with Ctrl+Z) or reject it": "Code ausführen (rückgängig mit Ctrl+Z) oder ablehnen",
    "Show Full Code": "Ganzen Code zeigen",
    "Open the code in the Text Editor (text 'Claude_Code.py')":
        "Code im Text-Editor öffnen (Text 'Claude_Code.py')",
    "Code error:": "Code-Fehler:",
    "Ran code": "Code ausgeführt",
    "Undo": "Rückgängig",
    "Undo these changes by Claude (and everything Claude changed after them)":
        "Diese Änderungen von Claude rückgängig machen (und alles, was Claude danach geändert hat)",
    "Undone: {}": "Rückgängig gemacht: {}",
    "Claude's changes": "Claude-Änderungen",
    "Undo only works in Object Mode.": "Rückgängig geht nur im Object Mode.",
    "Undo is not possible right now.": "Rückgängig ist gerade nicht möglich.",
    "You changed something yourself afterwards. Undo your own changes first with Ctrl+Z, or use "
    "Edit › Undo History.":
        "Danach hast du selbst etwas geändert. Nimm deine Änderungen zuerst mit Ctrl+Z zurück oder "
        "nutze Bearbeiten › Rückgängig-Verlauf.",

    # -- actions shown in the chat ------------------------------------------
    "Looked at a render preview": "Render-Vorschau angeschaut",
    "Looked at the scene": "Szene angeschaut",
    "Looked at the viewport": "Viewport angeschaut",
    "Looked at '{}'": "'{}' angeschaut",

    # -- errors and status --------------------------------------------------
    "Claude CLI not found. Install Claude Code and log in once by running 'claude' in a terminal, or set "
    "the path in the add-on preferences.":
        "Claude CLI nicht gefunden. Installiere Claude Code und melde dich einmal im Terminal mit 'claude' "
        "an, oder setze den Pfad in den Add-on-Einstellungen.",
    "Claude CLI exited with code {}.": "Claude CLI beendet mit Code {}.",
    "Tip: open a terminal and run 'claude' once to log in.":
        "Tipp: Öffne das Terminal und führe einmal 'claude' aus, um dich anzumelden.",
    "Tip: update Claude Code with 'claude update' in a terminal.":
        "Tipp: Aktualisiere Claude Code im Terminal mit 'claude update'.",
    "Could not connect to Blender (MCP status: {}).": "Verbindung zu Blender fehlgeschlagen (MCP-Status: {}).",
    "Tool error: {}": "Tool-Fehler: {}",
    "Unknown error": "Unbekannter Fehler",
    "Sending failed:": "Senden fehlgeschlagen:",
    "Could not describe the scene: {}": "Szeneninfo konnte nicht erstellt werden: {}",
    "Viewport screenshot not possible: {}": "Viewport-Screenshot nicht möglich: {}",
    "Online access is disabled, but Claude Code needs the internet. Enable it in Preferences › System › "
    "Network › Allow Online Access.":
        "Online-Zugriff ist ausgeschaltet, Claude Code braucht aber das Internet. Schalte ihn unter "
        "Einstellungen › System › Netzwerk › Online-Zugriff erlauben ein.",
    "Online access is disabled: Claude Code needs the internet.":
        "Online-Zugriff ist ausgeschaltet: Claude Code braucht das Internet.",
    "Enable it in Preferences › System › Network › Allow Online Access.":
        "Einschalten unter Einstellungen › System › Netzwerk › Online-Zugriff erlauben.",

    # -- usage --------------------------------------------------------------
    "Usage": "Usage",
    "Session": "Sitzung",
    "Week": "Woche",
    "{} left": "{} übrig",
    "{}: {}% left": "{}: {} % übrig",
    ", resets {}": ", Reset {}",
    "Limit reached": "Limit erreicht",
    " – available again {}": " – wieder ab {}",
    "Usage appears after the first answer": "Usage erscheint nach der ersten Antwort",
    "Updated with every answer from Claude": "Wird mit jeder Antwort von Claude aktualisiert",
    "Mon": "Mo", "Tue": "Di", "Wed": "Mi", "Thu": "Do", "Fri": "Fr", "Sat": "Sa", "Sun": "So",

    # -- local models (LM Studio) ------------------------------------------
    "Cloud / Local": "Cloud / Lokal",
    "Local": "Lokal",
    "Claude models in the cloud (your Claude subscription)": "Claude-Modelle in der Cloud (dein Claude-Abo)",
    "Local models from LM Studio (~/.lmstudio/models), run on this computer":
        "Lokale Modelle aus LM Studio (~/.lmstudio/models), laufen auf diesem Computer",
    "Local Model": "Lokales Modell",
    "LM Studio model key, e.g. qwen/qwen3.8-27b": "LM-Studio-Modellname, z.B. qwen/qwen3.8-27b",
    "Local Context Length": "Kontextlänge (lokal)",
    "Context length for loading a local model. 32k fits a chat with a few screenshots and keeps memory use "
    "and prompt processing low; older parts are summarized automatically":
        "Kontextlänge beim Laden eines lokalen Modells. 32k reicht für einen Chat mit einigen Screenshots und "
        "hält Speicher und Prompt-Verarbeitung klein; ältere Teile werden automatisch zusammengefasst",
    "Choose Model": "Modell wählen",
    "Use Local Model": "Lokales Modell verwenden",
    "Use this LM Studio model (loaded automatically when you send)":
        "Dieses LM-Studio-Modell verwenden (wird beim Senden automatisch geladen)",
    "Refresh Model List": "Modellliste aktualisieren",
    "Read the list of models from LM Studio again": "Modellliste neu aus LM Studio lesen",
    "Open Models Folder": "Modellordner öffnen",
    "Models Folder…": "Modellordner …",
    "Open the LM Studio models folder (~/.lmstudio/models)": "LM-Studio-Modellordner öffnen (~/.lmstudio/models)",
    "No LM Studio models found": "Keine LM-Studio-Modelle gefunden",
    "No LM Studio models found (~/.lmstudio/models)": "Keine LM-Studio-Modelle gefunden (~/.lmstudio/models)",
    "Choose a local model first (LM Studio models in ~/.lmstudio/models).":
        "Wähle zuerst ein lokales Modell (LM-Studio-Modelle in ~/.lmstudio/models).",
    "Local via LM Studio – no usage limit": "Lokal über LM Studio – kein Usage-Limit",
    "Local model": "Lokales Modell",
    "{} sees your scene and viewport and can control Blender directly. Attach images and files with the "
    "upload button or drop them onto this sidebar.":
        "{} sieht deine Szene und den Viewport und kann Blender direkt steuern. Bilder und Dateien mit dem "
        "Upload-Knopf anhängen oder auf diese Leiste ziehen.",
    "{} asks": "{} fragt",
    "{} wants to run code": "{} möchte Code ausführen",
    "Add a message while it works…": "Zwischenfrage …",

    # -- activity -----------------------------------------------------------
    "loading the model in LM Studio…": "lädt das Modell in LM Studio …",
    "summarizing the chat…": "fasst den Chat zusammen …",
    "thinking… {} tokens": "denkt nach … {} Tokens",
    "writing… {} tokens": "schreibt … {} Tokens",
    "writing code… {} tokens": "schreibt Code … {} Tokens",
    "preparing {}…": "bereitet {} vor …",
    "waiting for you": "wartet auf dich",
    "reading the prompt…": "liest den Prompt …",
    "thinking…": "denkt nach …",
    "running: {}": "führt aus: {}",

    # -- context, summarizing -----------------------------------------------
    "Summarize Chat": "Chat zusammenfassen",
    "Summarize older parts of this chat so it uses less context (faster, avoids context errors)":
        "Ältere Teile dieses Chats zusammenfassen, damit er weniger Kontext braucht (schneller, keine "
        "Kontext-Fehler)",
    "Chat summarized ({}k → {}k tokens), older details are condensed.":
        "Chat zusammengefasst ({}k → {}k Tokens), ältere Details sind verdichtet.",
    "Summarizing the chat failed: {}": "Zusammenfassen fehlgeschlagen: {}",
    "The chat uses {}% of the context – summarizing it…": "Der Chat nutzt {} % des Kontexts – fasse ihn zusammen …",
    "Context {}%": "Kontext {} %",
    "(auto {}%)": "(auto {} %)",
    "Auto Summarize Long Chats": "Lange Chats automatisch zusammenfassen",
    "When a chat fills the model's context, summarize older parts automatically (keeps answers fast and "
    "avoids context errors)":
        "Wenn ein Chat den Kontext des Modells füllt, ältere Teile automatisch zusammenfassen (hält Antworten "
        "schnell und vermeidet Kontext-Fehler)",
    "Summarize At": "Zusammenfassen ab",
    "Context usage at which the chat is summarized": "Kontext-Auslastung, ab der der Chat zusammengefasst wird",
    "reset {}": "Reset {}",

    # -- local model status, effort -----------------------------------------
    "No model chosen": "Kein Modell gewählt",
    "{}: loading… {}s": "{}: lädt … {}s",
    "LM Studio server is off – starts with the first message": "LM-Studio-Server aus – startet mit der ersten Nachricht",
    "Loaded · {}k": "Geladen · {}k",
    "Not loaded": "Nicht geladen",
    "Load Model": "Modell laden",
    "Load the chosen model in LM Studio now, so the first answer comes faster":
        "Gewähltes Modell jetzt in LM Studio laden, damit die erste Antwort schneller kommt",
    "Unload Model": "Modell entladen",
    "Unload the model from LM Studio to free its memory (loaded again with the next message)":
        "Modell aus LM Studio entladen und Speicher freigeben (wird mit der nächsten Nachricht neu geladen)",
    "Local Effort": "Effort (lokal)",
    "Fast": "Schnell",
    "Think": "Denken",
    "Think More": "Mehr denken",
    "No thinking: answers right away (recommended for local models)":
        "Ohne Nachdenken: antwortet sofort (empfohlen für lokale Modelle)",
    "The model reasons before answering: better for tricky tasks, but often 1-2 minutes slower":
        "Das Modell denkt vor der Antwort nach: besser bei kniffligen Aufgaben, aber oft 1–2 Minuten langsamer",
    "Like Think, asks for more effort (LM Studio hardly makes a difference)":
        "Wie Denken, fordert mehr Aufwand an (LM Studio macht kaum einen Unterschied)",

    # -- lessons ------------------------------------------------------------
    "Learn From Errors": "Aus Fehlern lernen",
    "After fixing an error, the model saves the problem and solution (one page per lesson, separate for each "
    "model) and gets its own lessons in future chats":
        "Nach dem Beheben eines Fehlers speichert das Modell Problem und Lösung (eine Seite pro Lektion, "
        "getrennt pro Modell) und bekommt seine Lektionen in künftigen Chats mit",
    "Lessons Folder": "Ordner für Lektionen",
    "Leave empty: Obsidian vault 'CLAUDE' › 30 Private/blender-assistant (or the add-on folder if there "
    "is no such vault)":
        "Leer lassen: Obsidian-Vault 'CLAUDE' › 30 Private/blender-assistant (oder der Add-on-Ordner, "
        "falls es den Vault nicht gibt)",
    "Lessons…": "Lektionen …",
    "Open the lessons the models learned from their errors (Obsidian project folder)":
        "Lektionen öffnen, die die Modelle aus ihren Fehlern gelernt haben (Obsidian-Projektordner)",
    "Learned: {}": "Gelernt: {}",
    "Saved a lesson": "Lektion gespeichert",
    "{} lessons": "{} Lektionen",

    # -- renders, statistics, versions, crash --------------------------------
    "Animation rendered: {}": "Animation gerendert: {}",
    "File not found": "Datei nicht gefunden",
    "Open the file in the default app": "Datei in der Standard-App öffnen",
    "session +{:.1f}%": "Sitzung +{:.1f} %",
    "Answer Statistics": "Antwort-Statistik",
    "Show duration, tokens and usage share under every answer":
        "Dauer, Tokens und Usage-Anteil unter jeder Antwort zeigen",
    "code: {}": "Code: {}",
    "render preview": "Render-Vorschau",
    "Code from the answer (not run by the model)": "Code aus der Antwort (vom Modell nicht ausgeführt)",
    "The model wrote code instead of running it – run it with the card below.":
        "Das Modell hat Code geschrieben statt ihn auszuführen – mit der Karte unten ausführen.",
    "Versions": "Versionen",
    "Go to Version": "Zu Version",
    "Move the scene back or forward to this version (the model's steps are undone or redone)":
        "Szene zu dieser Version zurück oder vor bewegen (Schritte des Modells werden rückgängig gemacht oder "
        "wiederholt)",
    "0 · before the first step": "0 · vor dem ersten Schritt",
    "start": "Start",
    "Wait until the model is done.": "Warte, bis das Modell fertig ist.",
    "Nothing to redo: later versions were replaced by newer changes.":
        "Nichts zu wiederholen: Spätere Versionen wurden durch neuere Änderungen ersetzt.",
    "Blender did not quit cleanly last time (crash?)": "Blender wurde letztes Mal nicht sauber beendet (Absturz?)",
    "Last step: {}": "Letzter Schritt: {}",
    "Backup: {}": "Sicherung: {}",
    "Open Last Backup": "Letzte Sicherung öffnen",
    "Open the last auto save copy from before the crash (the current file is replaced in Blender)":
        "Letzte Sicherungskopie von vor dem Absturz öffnen (die aktuelle Datei wird in Blender ersetzt)",
    "No backup found": "Keine Sicherung gefunden",
    "Dismiss": "Ausblenden",

    # -- answer summary, versions, settings ----------------------------------
    "{} written": "{} geschrieben",
    "context {}": "Kontext {}",
    "Change": "Änderung",
    "Material": "Material",
    "Camera": "Kamera",
    "Light": "Licht",
    "World": "Welt",
    "Render": "Render",
    "Looked": "Angeschaut",
    "Objects": "Objekte",
    "{} steps": "{} Schritte",
    "undone": "rückgängig",
    "Steps": "Schritte",
    "Show or hide the single steps of this answer": "Einzelne Schritte dieser Antwort zeigen oder verbergen",
    "Scroll To": "Springen",
    "Jump to this part of the chat": "Zu dieser Stelle im Chat springen",
    "Start": "Start",
    "Chat Settings": "Chat-Einstellungen",
    "Sent With Every Message": "Mit jeder Nachricht senden",
    "Chat": "Chat",
    "Auto Summarize": "Auto-Zusammenfassen",
    "Icon": "Symbol",

    "Save as Main File": "Als Hauptdatei speichern",
    "Make this the main file?": "Als Hauptdatei verwenden?",
    "Saves as {}, the previous version becomes a backup": "Speichert als {}, die bisherige Version wird zur Sicherung",
    "Save the open file as the main file <name>_now.blend. The file that was there becomes a backup in autosave, "
    "nothing is deleted":
        "Die offene Datei als Hauptdatei <name>_now.blend speichern. Die bisherige Datei wird zur Sicherung in "
        "autosave, nichts wird gelöscht",
    "Make Project Folder": "Projektordner anlegen",
    "Make project folder {}/": "Projektordner {}/ anlegen",
    "Make a project folder?": "Projektordner anlegen?",
    "Saves the file as {}/{}_now.blend; backups and memory move along":
        "Speichert die Datei als {}/{}_now.blend; Sicherungen und Memory ziehen mit",
    "Give this file its own folder <name>/ with autosave, assets and archiv and save it there as "
    "<name>_now.blend. Its backups and project memory move along, nothing is deleted":
        "Gibt der Datei einen eigenen Ordner <name>/ mit autosave, assets und archiv und speichert sie dort als "
        "<name>_now.blend. Sicherungen und Projekt-Memory ziehen mit, nichts wird gelöscht",
    "You are working in a backup copy": "Du arbeitest in einer Sicherungskopie",
    "Save as {}": "Speichern als {}",
    "Crash Details": "Absturz-Details",
    "Save failed": "Speichern fehlgeschlagen",
    "Context of this chat: {}% used": "Kontext dieses Chats: {} % belegt",
    "Limit": "Limit",
    "Last backup {} ({}): {}": "Letzte Sicherung {} ({}): {}",
    "Auto save": "Sicherung",
    "rendering a preview in the background…": "rendert eine Vorschau im Hintergrund…",
    "No camera named {}": "Keine Kamera mit dem Namen {}",
    "on": "an",
    "off": "aus",
    "Open the crash trace, the newest freeze report and the render queue logs "
    "(Claude can read them too: just say that Blender froze)":
        "Absturz-Protokoll, neuesten Freeze-Bericht und Render-Logs öffnen "
        "(Claude kann sie auch selbst lesen: sag einfach, dass Blender eingefroren ist)",

    # -- auto save ----------------------------------------------------------
    "Auto Save": "Automatisch sichern",
    "Save regularly and right before risky moments (model code, renders, low memory)":
        "Regelmässig sichern und direkt vor riskanten Momenten (Code des Modells, Renders, wenig Speicher)",
    "Auto Save Mode": "Art der Sicherung",
    "Backup Copies": "Sicherungskopien",
    "Copies next to the file in <name>_autosave/ – your file itself is not touched":
        "Kopien neben der Datei in <name>_autosave/ – deine Datei selbst bleibt unberührt",
    "Save the File": "Datei speichern",
    "Saves the .blend itself (the previous version stays as .blend1)":
        "Speichert die .blend selbst (die vorherige Version bleibt als .blend1)",
    "Every": "Alle",
    "Minutes between regular saves (only when something changed)":
        "Minuten zwischen den regelmässigen Sicherungen (nur wenn sich etwas geändert hat)",
    "Keep Copies": "Kopien behalten",
    "Number of backup copies kept per file (the oldest are deleted)":
        "Anzahl Sicherungskopien pro Datei (die ältesten werden gelöscht)",
    "Before Code and Renders": "Vor Code und Renders",
    "Also save right before the model runs code and before renders (at most every 2 minutes) – the moments in "
    "which Blender most often crashes":
        "Auch direkt sichern, bevor das Modell Code ausführt und vor Renders (höchstens alle 2 Minuten) – "
        "die Momente, in denen Blender am häufigsten abstürzt",
    "When Memory Runs Low": "Wenn der Speicher knapp wird",
    "Save immediately when macOS reports memory pressure (often shortly before a crash)":
        "Sofort sichern, wenn macOS Speicherdruck meldet (oft kurz vor einem Absturz)",
    "Auto save failed: {}": "Automatisch sichern fehlgeschlagen: {}",
    "Saved {} · {}": "Gesichert {} · {}",
    "regular": "regelmässig",
    "before code": "vor Code",
    "before render": "vor Render",
    "memory low": "Speicher knapp",
    "manual": "von Hand",
    "Auto save every {} min": "Automatisch sichern alle {} min",
    "Save Backup Now": "Jetzt sichern",
    "Auto save: click to save a backup right now": "Automatisch sichern: klicken, um sofort zu sichern",
    "Saved: {}": "Gesichert: {}",
    "Not saved (a render is running)": "Nicht gesichert (ein Render läuft)",
    "Open Backups": "Sicherungen öffnen",
    "Open the folder with the auto save copies (File › Open to restore one)":
        "Ordner mit den Sicherungskopien öffnen (Datei › Öffnen, um eine wiederherzustellen)",
    "LM Studio: loading model… {}s": "LM Studio: lade Modell … {}s",
    "LM Studio: {}": "LM Studio: {}",
    "Local · ready · {}k context": "Lokal · bereit · {}k Kontext",
    "Local (LM Studio)": "Lokal (LM Studio)",
    "Screenshot With Every Message (Local)": "Screenshot bei jeder Nachricht (lokal)",
    "Attach a viewport screenshot to every message in local mode. Off: the model takes a screenshot itself "
    "when it needs one (faster)":
        "Im Lokal-Modus jeder Nachricht einen Viewport-Screenshot anhängen. Aus: Das Modell macht selbst "
        "einen Screenshot, wenn es einen braucht (schneller)",

    # -- preferences --------------------------------------------------------
    "Model": "Modell",
    "Effort": "Effort",
    "Latest Sonnet model – fast and strong (default)": "Neuestes Sonnet-Modell – schnell und stark (Standard)",
    "Latest Opus model – the most capable": "Neuestes Opus-Modell – am stärksten",
    "Latest Fable model": "Neuestes Fable-Modell",
    "Latest Haiku model – the fastest": "Neuestes Haiku-Modell – am schnellsten",
    "Low": "Niedrig",
    "Medium": "Mittel",
    "High": "Hoch",
    "Extra High": "Sehr hoch",
    "Max": "Max",
    "Quick answers, little thinking": "Schnelle Antworten, wenig Nachdenken",
    "Balanced (default for Sonnet)": "Ausgewogen (Standard für Sonnet)",
    "More thorough (default for Opus)": "Gründlicher (Standard für Opus)",
    "Even more thorough": "Noch gründlicher",
    "Maximum thinking – slower, uses more of your usage":
        "Maximales Nachdenken – langsamer, verbraucht mehr Usage",
    "Claude CLI Path": "Pfad zur Claude CLI",
    "Leave empty to search automatically (~/.local/bin, ~/.claude/local, Homebrew, npm, nvm)":
        "Leer lassen = automatisch suchen (~/.local/bin, ~/.claude/local, Homebrew, npm, nvm)",
    "Found: {}": "Gefunden: {}",
    "Claude CLI not found – see the README": "Claude CLI nicht gefunden – siehe README",
    "Run Code Without Asking": "Code ohne Nachfrage ausführen",
    "Auto mode: Claude runs code directly without asking every time (undo with Ctrl+Z)":
        "Auto-Modus: Claude führt Code direkt aus, ohne jedes Mal zu fragen (rückgängig mit Ctrl+Z)",
    "Send Scene Info": "Szeneninfo mitsenden",
    "Attach a description of the scene to every message":
        "Jeder Nachricht automatisch eine Beschreibung der Szene anhängen",
    "Send Viewport Screenshot": "Viewport-Screenshot mitsenden",
    "Attach a screenshot of the 3D viewport to every message":
        "Jeder Nachricht automatisch einen Screenshot des 3D-Viewports anhängen",
    "Send details of the selected objects, so 'this' is clear":
        "Details der ausgewählten Objekte mitsenden, damit 'das hier' klar ist",
    "Max Image Size": "Max. Bildgrösse",
    "Longest side of screenshots and attached images in pixels":
        "Längste Seite von Screenshots und angehängten Bildern in Pixeln",
    "Shown Messages": "Angezeigte Nachrichten",
    "How many of the latest messages are shown in the chat":
        "Wie viele der letzten Nachrichten im Chat angezeigt werden",
    "Chat Height": "Chat-Höhe",
    "Height of the chat area as a percentage of the Blender window height":
        "Höhe des Chat-Fensters in Prozent der Blender-Fensterhöhe",
    "Custom Instructions": "Eigene Anweisungen",
    "Additional instructions Claude receives with every message":
        "Zusätzliche Anweisungen, die Claude bei jeder Nachricht bekommt",
    "Extra CLI Arguments": "Zusätzliche CLI-Argumente",
    "Appended to the claude command (advanced)": "Werden an den claude-Aufruf angehängt (für Fortgeschrittene)",
}

# Blender looks up operator names in the "Operator" context and everything else
# in the default context "*": register every string for both.
_CONTEXTS = ("*", "Operator")


def _build():
    de = {}
    for en, translated in DE.items():
        for ctx in _CONTEXTS:
            de[(ctx, en)] = translated
    return {"de_DE": de}


def tr(msgid):
    """Translate a UI string built at runtime (labels, messages)."""
    return bpy.app.translations.pgettext_iface(msgid)


def tip(msgid):
    """Translate a tooltip built at runtime."""
    return bpy.app.translations.pgettext_tip(msgid)


def register():
    bpy.app.translations.register(__package__, _build())


def unregister():
    bpy.app.translations.unregister(__package__)
