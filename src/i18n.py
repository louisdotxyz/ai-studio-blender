# SPDX-License-Identifier: GPL-3.0-or-later
"""Translations.

The add-on is written in English and shows English by default. With Preferences › General ›
German Interface on, Blender shows the German strings below when its interface language is
German (Preferences › Interface › Translation).
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
    "You are logged out of Claude. Log in with the button below (it opens the browser), then send your message again.":
        "Du bist bei Claude abgemeldet. Melde dich mit dem Knopf unten an (öffnet den Browser) und sende "
        "die Nachricht dann noch einmal.",
    "Logged out of Claude": "Bei Claude abgemeldet",
    "Removed {} helper objects (temp)": "{} Hilfsobjekte entfernt (temp)",
    'Setup': 'Einrichtung',
    'German Interface': 'Deutsche Oberfläche',
    "Show the add-on in German when Blender's interface is German (off: always English)": 'Das Add-on auf Deutsch zeigen, wenn Blenders Oberfläche Deutsch ist (aus: immer Englisch)',
    'Updates': 'Updates',
    'Automatic': 'Automatisch',
    'Ask': 'Fragen',
    'New versions of AI Studio (when it was installed from its install page, not from a zip)': 'Neue Versionen von AI Studio (wenn es über seine Installationsseite installiert wurde, nicht aus einem Zip)',
    'Install new versions on their own when Blender is idle; the chat says so': 'Neue Versionen von selbst installieren, wenn Blender gerade nichts tut; der Chat sagt es',
    'Show new versions in the chat with an Install button': 'Neue Versionen im Chat mit einem Installieren-Knopf zeigen',
    "Don't look for new versions": 'Nicht nach neuen Versionen suchen',
    'AI Studio {} is available': 'AI Studio {} ist verfügbar',
    'Base materials ready': 'Grundmaterialien bereit',
    'General': 'Allgemein',
    'Learning': 'Lernen',
    'Advanced': 'Erweitert',
    'Sees the Viewport': 'Sieht den Viewport',
    'Claude App and Claude Code': 'Claude-App und Claude Code',
    'Backups and Project Folders': 'Backups und Projektordner',
    'Render Folders': 'Render-Ordner',
    'Collections and Shortcuts': 'Collections und Tastenkürzel',
    'Live Connection Port': 'Port der Live-Verbindung',
    'Local Models (LM Studio, ~/.lmstudio/models)': 'Lokale Modelle (LM Studio, ~/.lmstudio/models)',
    'What most people change: Claude, model, updates, language': 'Was die meisten ändern: Claude, Modell, Updates, Sprache',
    'Last step, in the Claude app:': 'Letzter Schritt, in der Claude-App:',
    'Set the permission switch under the message box to Auto: the Blender tools then run without asking each time (or Customize › Connectors › ai-studio › Tool permissions › Always allow).': 'Den Berechtigungs-Schalter unter dem Eingabefeld auf Auto stellen: dann laufen die Blender-Werkzeuge ohne Rückfrage (oder Customize › Connectors › ai-studio › Tool permissions › Always allow).',
    'Base materials missing': 'Grundmaterialien fehlen',
    'Run directly: Claude changes the scene without asking each time (Undo works)': 'Direkt: Claude ändert die Szene ohne jedes Mal zu fragen (Rückgängig geht)',
    'AI Studio was updated to {}.': 'AI Studio wurde auf {} aktualisiert.',
    'Install Update': 'Update installieren',
    'Install the new version of AI Studio now (Blender loads it again right away)': 'Die neue Version von AI Studio jetzt installieren (Blender lädt sie gleich neu)',
    'Wait until the answer or the render is done': 'Warte, bis die Antwort oder der Render fertig ist',
    'Renders': 'Renders',
    'Next to the file': 'Neben der Datei',
    'One folder': 'Ein Ordner',
    'Backups': 'Backups',
    'Every 5 min': 'Alle 5 Min.',
    'Off': 'Aus',
    'New files': 'Neue Dateien',
    'Own folder': 'Eigener Ordner',
    'Just the file': 'Nur die Datei',
    "Claude's code": 'Claudes Code',
    'Ask first': 'Zuerst fragen',
    'Run directly': 'Direkt ausführen',
    'How you would like it (all of it can be changed later in the preferences)': 'Wie du es gern hättest (alles lässt sich später in den Einstellungen ändern)',
    'Setup Questions Answered': 'Einrichtungsfragen beantwortet',
    'The questions of the setup card were answered once': 'Die Fragen der Einrichtungskarte wurden einmal beantwortet',
    'The chat worked once (the setup card\'s steps are done)': 'Der Chat hat einmal funktioniert (die Schritte der Einrichtung sind erledigt)',
    'Set Up Everything': 'Alles einrichten',
    'Does every open step of the setup: allows online access, installs Claude Code, opens the login, creates the base materials and connects Claude Code and the Claude app to this Blender':
        'Erledigt alle offenen Schritte der Einrichtung: erlaubt den Online-Zugriff, installiert Claude Code, öffnet '
        'die Anmeldung, legt die Grundmaterialien an und verbindet Claude Code und die Claude-App mit diesem Blender',
    'Cancel Setup': 'Einrichtung abbrechen',
    'Stop the setup (a download that was started is thrown away)': 'Einrichtung anhalten (ein begonnener Download wird verworfen)',
    'Installs Claude Code with its official installer (claude.ai/install.sh) in the background, then opens the login':
        'Installiert Claude Code mit dem offiziellen Installer (claude.ai/install.sh) im Hintergrund und öffnet danach die Anmeldung',
    'Setting up Claude Code…': 'Claude Code wird eingerichtet …',
    'Downloading Claude Code: {} of {} MB': 'Claude Code wird geladen: {} von {} MB',
    'Downloading Claude Code…': 'Claude Code wird geladen …',
    'Log in in the browser (the terminal window closes by itself)': 'Im Browser anmelden (das Terminalfenster schliesst sich selbst)',
    'Creating the base materials…': 'Grundmaterialien werden angelegt …',
    'Connecting the Claude apps…': 'Claude-Apps werden verbunden …',
    'Setting up…': 'Wird eingerichtet …',
    'Claude Code could not be installed:': 'Claude Code konnte nicht installiert werden:',
    'Claude Code is installed.': 'Claude Code ist installiert.',
    'The login was not finished: log in with the button in the card': 'Die Anmeldung wurde nicht abgeschlossen: melde dich mit dem Knopf in der Karte an',
    'The Claude app gets the Blender tools after it was quit and opened again once.': 'Die Claude-App bekommt die Blender-Werkzeuge, nachdem sie einmal beendet und neu geöffnet wurde.',
    'Could not connect the Claude apps: {}': 'Claude-Apps konnten nicht verbunden werden: {}',
    'Setup complete. Ask Claude something about your scene.': 'Einrichtung fertig. Frag Claude etwas zu deiner Szene.',
    'Claude Code installed': 'Claude Code installiert',
    'Claude Code missing': 'Claude Code fehlt',
    'Install': 'Installieren',
    'Logged in': 'Angemeldet',
    'Not logged in': 'Nicht angemeldet',
    'Checking the login…': 'Anmeldung wird geprüft …',
    'Base materials: {}': 'Grundmaterialien: {}',
    'No base materials yet': 'Noch keine Grundmaterialien',
    'Create': 'Anlegen',
    'Renders: {}': 'Renders: {}',
    'Live Connection for Cowork / Claude Code': 'Live Connection für Cowork / Claude Code',
    'Done': 'Fertig',
    'Claude Code is too old for this add-on': 'Claude Code ist zu alt für dieses Add-on',
    'Update': 'Aktualisieren',
    'LM Studio does not answer': 'LM Studio antwortet nicht',
    'Start LM Studio': 'LM Studio starten',
    'Cowork waits until this answer is done': 'Cowork wartet, bis diese Antwort fertig ist',
    'Cowork wants to run code': 'Cowork möchte Code ausführen',
    'Cowork connected · last request {} s ago': 'Cowork verbunden · letzte Anfrage vor {} s',
    'Copied: paste it into the other Claude session': 'Kopiert: in die andere Claude-Session einfügen',
    'Could not create the base materials: {}': 'Grundmaterialien konnten nicht angelegt werden: {}',
    'Try': 'Ausprobieren',
    'Good to Know': 'Gut zu wissen',
    'Show Setup': 'Einrichtung zeigen',
    'Help': 'Hilfe',
    'Sort the scene into collections and give the parts clear names': 'Sortiere die Szene in Collections und gib den Teilen klare Namen',
    'Make a soft studio light from the front left and render a preview': 'Mach ein weiches Studiolicht von vorne links und rendere eine Vorschau',
    "Give the housing the base material 'Aluminium anodized' and show me three colors": "Gib dem Gehäuse das Grundmaterial 'Alu eloxiert' und zeig mir drei Farben",
    'Import the newest GLB from Downloads and take the materials over from the old version': 'Importiere das neueste GLB aus Downloads und übernimm die Materialien der alten Version',
    '⌘R: render High Quality, ⇧⌘R: Low Quality, ⇧⌘E: view the render': '⌘R: HQ rendern, ⇧⌘R: Vorschau, ⇧⌘E: Render ansehen',
    'Material library: click switches all objects with the current material': 'Material-Library: Klick tauscht alle Objekte mit dem aktuellen Material',
    'Shift-click: only the selection · Ctrl-click: select its objects': 'Shift-Klick: nur die Auswahl · Strg-Klick: seine Objekte auswählen',
    'Right-click a material: favorite, rename, delete': 'Rechtsklick auf ein Material: Favorit, umbenennen, löschen',
    'Replaced objects go into the archive collection (Archive), you delete them': 'Ersetzte Objekte kommen in die Archiv-Collection (Archive), löschen tust du sie',
    "Claude's helper objects go into 'temp' and are removed after the answer": "Claudes Hilfsobjekte kommen in 'temp' und werden nach der Antwort entfernt",
    'Port': 'Port',
    'Fixed port for the Live Connection (0 = a free one each start). Takes effect when Blender starts': 'Fester Port für die Live Connection (0 = bei jedem Start ein freier). Gilt ab dem nächsten Blender-Start',
    'Copy instructions for another Claude session (Cowork, Claude Code): how to reach this Blender over the Live Connection. Paste them into that session': 'Anleitung für eine andere Claude-Session (Cowork, Claude Code) kopieren: wie sie dieses Blender über die Live Connection erreicht. In diese Session einfügen',
    "Opens a terminal with 'claude update'": "Öffnet ein Terminal mit 'claude update'",
    "Start LM Studio's server ('lms server start'), then send the message again": "Startet den Server von LM Studio ('lms server start'), danach die Nachricht nochmals senden",
    'Hide the setup card (the ? at the top shows it again)': 'Einrichtungskarte ausblenden (das ? oben zeigt sie wieder)',
    'Put this example into the message field': 'Dieses Beispiel ins Eingabefeld übernehmen',
    "Log in": "Anmelden",
    "Could not open the login: {}": "Anmeldung konnte nicht geöffnet werden: {}",
    "Log in to Claude again: opens a terminal with 'claude auth login', which opens the browser":
        "Neu bei Claude anmelden: öffnet ein Terminal mit 'claude auth login', das den Browser öffnet",
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
    "Live Connection (External Claude Sessions)": "Live Connection (externe Claude-Sessions)",
    "Let other Claude sessions on this computer (Cowork, blender-mcp) work in the open Blender. "
    "Port and token are in ~/.config/ai-studio/live.json. 'Run Code Without Asking' applies to them too":
        "Andere Claude-Sessions auf diesem Computer (Cowork, blender-mcp) können im offenen Blender arbeiten. "
        "Port und Token stehen in ~/.config/ai-studio/live.json. 'Code ohne Nachfrage ausführen' gilt auch für sie",
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
    # -- online access, depth of field, samples (0.24) --------------------
    'Allow Online Access': 'Online-Zugriff erlauben',
    'Turns on Preferences › System › Network › Allow Online Access. Claude Code needs the internet; a new Blender starts with it off': 'Schaltet Einstellungen › System › Netzwerk › Online-Zugriff erlauben ein. Claude Code braucht das Internet; ein neues Blender startet mit ausgeschaltetem Zugriff',
    'Blender was started with --offline-mode: start it without it': 'Blender wurde mit --offline-mode gestartet: ohne diese Option starten',
    'Online access is on': 'Online-Zugriff ist an',
    'Online access on': 'Online-Zugriff an',
    'Online access off': 'Online-Zugriff aus',
    'Allow': 'Erlauben',
    'Offline: Blender was started with --offline-mode': 'Offline: Blender wurde mit --offline-mode gestartet',
    'Blender was started offline (--offline-mode), but Claude Code needs the internet. Start Blender without it, or use a local model.': 'Blender wurde offline gestartet (--offline-mode), Claude Code braucht aber das Internet. Blender ohne diese Option starten oder ein lokales Modell nehmen.',
    "Online access is off in Blender, but Claude Code needs the internet. Click 'Allow' in the setup card below, then send the message again.": 'Der Online-Zugriff ist in Blender aus, Claude Code braucht aber das Internet. Unten in der Einrichtung auf «Erlauben» klicken und die Nachricht nochmals senden.',
    'Logged in to Claude.': 'Bei Claude angemeldet.',
    'Depth of Field': 'Tiefenschärfe',
    'Aperture': 'Blende',
    'Distance': 'Distanz',
    'Focus: Distance': 'Fokus: Distanz',
    'Shown in Material Preview and Rendered': 'Sichtbar in Material-Vorschau und Gerendert',
    'Shown in the camera view (Numpad 0)': 'Sichtbar in der Kameraansicht (Numpad 0)',
    'Noise Threshold': 'Rauschschwelle',
    'High: Samples': 'High: Samples',
    "Cycles samples for High Quality in new files and in files that still have Blender's 4096 (set once per scene; with Denoise and Noise Threshold 512 is enough for product shots)": 'Cycles-Samples für High Quality in neuen Dateien und in Dateien, die noch Blenders 4096 haben (einmal pro Szene; mit Entrauschen und Rauschschwelle reichen 512 für Produktbilder)',
    # -- 1.0: new id, render folders, connecting Claude apps --------------
    'next to the .blend file': 'neben der .blend-Datei',
    'Next to the .blend file': 'Neben der .blend-Datei',
    'Connect': 'Verbinden',
    'Claude Code connected': 'Claude Code verbunden',
    'Claude Code: not connected': 'Claude Code: nicht verbunden',
    'Claude app (Cowork) connected': 'Claude-App (Cowork) verbunden',
    'Claude app (Cowork): not connected': 'Claude-App (Cowork): nicht verbunden',
    'Connected: quit and reopen the Claude app once': 'Verbunden: die Claude-App einmal beenden und neu öffnen',
    'Connected: new Claude Code sessions have the ai-studio tools': 'Verbunden: neue Claude-Code-Sessions haben die ai-studio-Tools',
    "Give this Claude app the 'ai-studio' tools (an MCP server of this add-on), so its chats can see and change the open Blender over the Live Connection": 'Gibt dieser Claude-App die «ai-studio»-Tools (ein MCP-Server dieses Add-ons), damit ihre Chats das offene Blender über die Live Connection sehen und ändern können',
    "AI Studio has a new name inside Blender (AI_Studio). Settings and chats were taken over and the old version 'claude_assistant' is off: remove it in Preferences › Get Extensions.": 'AI Studio hat in Blender einen neuen Namen (AI_Studio). Einstellungen und Chats wurden übernommen und die alte Version «claude_assistant» ist aus: in Einstellungen › Get Extensions entfernen.',
    'Where the renders go: a folder per file (<name> + suffix) with subfolders for Low, High, Animation and one archive folder for sorting by hand. Empty: right next to the .blend file (unsaved files: Documents/AI Studio)': 'Wohin die Renders gehen: ein Ordner pro Datei (<Name> + Endung) mit Unterordnern für Low, High, Animation und einem Archiv zum Sortieren von Hand. Leer: direkt neben der .blend-Datei (ungespeicherte Dateien: Dokumente/AI Studio)',
    "HDRI files (.hdr, .exr) in this folder and its subfolders appear in the Light tab (empty: an 'HDRI' folder in the add-on's data folder)": 'HDRI-Dateien (.hdr, .exr) in diesem Ordner und seinen Unterordnern erscheinen im Licht-Tab (leer: ein Ordner «HDRI» im Datenordner des Add-ons)',
    # -- 1.1: default model ----------------------------------------------
    'Default Model': 'Standardmodell',
    'Default Effort': 'Standard-Aufwand',
    'New Chats Start With': 'Neue Chats starten mit',
    'The model every new chat starts with (also after starting Blender)': 'Das Modell, mit dem jeder neue Chat startet (auch nach dem Start von Blender)',
    "The effort every new chat starts with (set to the model's usual one when the model changes)": 'Der Aufwand, mit dem jeder neue Chat startet (wechselt beim Modellwechsel auf den üblichen Wert des Modells)',
    'The local model chosen in the chat (LM Studio)': 'Das im Chat gewählte lokale Modell (LM Studio)',
    # -- 1.7: language, preferences in new order -----------------------------
    'Language': 'Sprache',
    'The add-on in English': 'Das Add-on auf Englisch',
    "Language of the add-on (Blender's own menus follow Blender's language)": 'Sprache des Add-ons (Blenders eigene Menüs folgen Blenders Sprache)',
    'Blender in German Too': 'Blender auch auf Deutsch',
    "Switch Blender's own menus and tooltips to German as well (names of new objects stay English). Back: Preferences › Interface › Translation": 'Stellt auch Blenders eigene Menüs und Tooltips auf Deutsch (Namen neuer Objekte bleiben englisch). Zurück: Einstellungen › Oberfläche › Übersetzung',
    'Live Connection': 'Live Connection',
    'Live Connection: the Claude app and Claude Code work in this Blender': 'Live Connection: Claude-App und Claude Code arbeiten in diesem Blender',
    'After connecting: quit the Claude app once (Cmd+Q) and open it again': 'Nach dem Verbinden: die Claude-App einmal beenden (Cmd+Q) und neu öffnen',
    "Let the Claude app and Claude Code on this computer work in the open Blender and take CAD jobs. Port and token are in ~/.config/ai-studio/live.json. 'Run Code Without Asking' applies to them too": 'Claude-App und Claude Code auf diesem Computer arbeiten im offenen Blender und nehmen CAD-Aufträge an. Port und Token stehen in ~/.config/ai-studio/live.json. «Code ohne Nachfrage ausführen» gilt auch für sie',
    'Language, Claude, the connection to the Claude app, updates': 'Sprache, Claude, Verbindung zur Claude-App, Updates',
    'Auto save, backups and project folders': 'Automatisch speichern, Backups und Projektordner',
    'Rarely needed: Claude CLI, what is sent, local models, lessons, render folder names, collections, shortcuts': 'Selten gebraucht: Claude CLI, was mitgeschickt wird, lokale Modelle, Lektionen, Render-Ordnernamen, Collections, Tastenkürzel',
    'Folder Names': 'Ordnernamen',
    'Port': 'Port',
    'CAD job ready': 'CAD-Auftrag bereit',
    'Name the chat (e.g. CAD 5) or choose New Chat': 'Den Chat nennen (z.B. CAD 5) oder «Neuer Chat» wählen',
    'To Chat': 'An Chat',
    'New Chat': 'Neuer Chat',
    'Chat': 'Chat',
    "The Claude app gets Blender's tools now; reopen Claude once": 'Die Claude-App bekommt jetzt Blenders Werkzeuge; Claude einmal neu öffnen',
    # -- 1.8: Camera tab, CAD in the chat, Up / Down ---------------------------
    'Up / Down': 'Hoch / Runter',
    'Camera and the point it looks at move up or down in the picture together (also in a straight top view; 0 = centered)': 'Kamera und Blickpunkt wandern zusammen im Bild nach oben oder unten (auch in der Draufsicht; 0 = mittig)',
    'CAD Comments and Jobs': 'CAD-Kommentare und -Aufträge',
    'Show the CAD comments and jobs for the CAD side': 'Die CAD-Kommentare und -Aufträge für die CAD-Seite zeigen',
    # -- 1.9: simpler CAD connection --------------------------------------------
    'CAD Jobs': 'CAD-Aufträge',
    'Send changes from Blender to a Claude chat that works on the CAD model (Send CAD Job). Off: the CAD box only imports versions and notes comments':
        'Änderungen aus Blender an einen Claude-Chat schicken, der am CAD-Modell arbeitet (CAD-Auftrag senden). Aus: Die CAD-Box importiert nur Versionen und notiert Kommentare',
    'New Chat': 'Neuer Chat',
    'Start a new chat in the Claude app with the job': 'Einen neuen Chat in der Claude-App mit dem Auftrag starten',
    'Other Chat…': 'Anderer Chat …',
    'Type the name of the chat': 'Den Namen des Chats eintippen',
    'Chat': 'Chat',
    'Which Claude chat does it': 'Welcher Claude-Chat es macht',
    'Name': 'Name',
    "Name of the chat in the Claude app, e.g. 'CAD 5' (remembered for this CAD export)":
        "Name des Chats in der Claude-App, z. B. «CAD 5» (wird für diesen CAD-Export gemerkt)",
    'not seen yet': 'noch nicht gemeldet',
    'active just now': 'gerade aktiv',
    'active {} min ago': 'aktiv vor {} min',
    'active {} h ago': 'aktiv vor {} h',
    'active {} days ago': 'aktiv vor {} Tagen',
    'new chat': 'neuer Chat',
    'Claude app not found': 'Claude-App nicht gefunden',
    'Claude app not connected to Blender': 'Claude-App nicht mit Blender verbunden',
    'Connect': 'Verbinden',
    "Give the chats of the Claude app Blender's tools (blender_jobs), so they can take CAD jobs. Quit and reopen Claude once afterwards":
        'Den Chats der Claude-App die Blender-Tools geben (blender_jobs), damit sie CAD-Aufträge holen können. Danach Claude einmal beenden und neu öffnen',
    'Connected: quit Claude (Cmd+Q) and open it again': 'Verbunden: Claude beenden (Cmd+Q) und wieder öffnen',
    'CAD job for {}': 'CAD-Auftrag für {}',
    'Write «CAD job» in that chat: it takes it from Blender': 'Schreib «CAD-Auftrag» in diesen Chat: Er holt ihn aus Blender',
    'Ask the CAD side for a change: a new chat in the Claude app opens with the job, or a chat that knows the model gets it (a notification; it takes the job from Blender)':
        'Die CAD-Seite um eine Änderung bitten: Ein neuer Chat in der Claude-App öffnet sich mit dem Auftrag, oder ein Chat, der das Modell kennt, bekommt ihn (Mitteilung; er holt den Auftrag aus Blender)',
    'CAD jobs are switched off (link button of the CAD box)': 'CAD-Aufträge sind ausgeschaltet (Verbindungs-Knopf der CAD-Box)',
    'Note a change for the CAD side in CAD-Feedback.md next to the export (the CAD chat reads it)':
        'Eine Änderung für die CAD-Seite in CAD-Feedback.md neben dem Export notieren (der CAD-Chat liest sie)',
    'Tick this point off': 'Diesen Punkt abhaken',
    # -- 2.0: the CAD link is a skill ------------------------------------------
    'CAD job taken by {}: {}': 'CAD-Auftrag übernommen von {}: {}',
    'a chat': 'einem Chat',
    'CAD job done{}: {}': 'CAD-Auftrag erledigt{}: {}',
    # -- 2.1 -------------------------------------------------------------------
    'Upload': 'Hochladen',
    'Claude or Local Model': 'Claude oder lokales Modell',
    # -- 2.2: other add-ons --------------------------------------------------
    'Other Add-ons': 'Andere Add-ons',
    'Covered: ': 'Abgedeckt: ',
    'Atmosphere': 'Atmosphäre',
    'Sun Height': 'Sonnenhöhe',
    'Sun Direction': 'Sonnenrichtung',
    'Sun Strength': 'Sonnenstärke',
    'Haze': 'Dunst',
    'Clouds': 'Wolken',
    'Place Light': 'Licht platzieren',
    'Turn Off Add-on': 'Add-on ausschalten',
    'Turn Off': 'Ausschalten',
    'Sky, sun, clouds, stars and fog (World)': 'Himmel, Sonne, Wolken, Sterne und Nebel (Welt)',
    'Place a light by dragging on the product; gobos, IES, the scene as a 360° HDRI':
        'Licht durch Ziehen auf dem Produkt platzieren; Gobos, IES, Szene als 360°-HDRI',
    'Color grading (its own sidebar tab "Render Raw")': 'Farbkorrektur (eigener Tab «Render Raw»)',
    'Physical camera: exposure, white balance': 'Physikalische Kamera: Belichtung, Weissabgleich',
    'Light presets and the light list in this tab': 'Licht-Presets und Lichterliste in diesem Tab',
    'Light presets (key, fill, rim) in this tab': 'Licht-Presets (Key, Fill, Rim) in diesem Tab',
    'The three-point presets in this tab': 'Die Drei-Punkt-Presets in diesem Tab',
    'Place this light by dragging on the product (Light Wrangler). Esc or right-click ends it':
        'Licht durch Ziehen auf dem Produkt platzieren (Light Wrangler). Esc oder Rechtsklick beendet',
    'Physical sky with sun, haze and clouds on the World (Physical Starlight & Atmosphere). Click again to take it off':
        'Physikalischer Himmel mit Sonne, Dunst und Wolken als Welt (Physical Starlight & Atmosphere). Nochmals klicken entfernt ihn',
    'Turn this add-on off: the Light tab does the same. It stays installed; turn it on again in Edit › Preferences › Add-ons':
        'Dieses Add-on ausschalten: der Licht-Tab kann dasselbe. Es bleibt installiert; einschalten unter Bearbeiten › Einstellungen › Add-ons',
    # -- 2.3: material library ------------------------------------------------
    'Favorites': 'Favoriten',
    'Recent': 'Zuletzt benutzt',
    'Metal': 'Metall',
    'Plastic': 'Kunststoff',
    'Glass': 'Glas',
    'Rubber': 'Gummi',
    'Wood': 'Holz',
    'Fabric': 'Stoff',
    'Other': 'Andere',
    'Project': 'Projekt',
    'Library': 'Bibliothek',
    'Tiles': 'Kacheln',
    'List': 'Liste',
    'Save to Library': 'In Bibliothek speichern',
    'No materials yet': 'Noch keine Materialien',
    'Library View': 'Bibliotheksansicht',
    'The materials of this file': 'Die Materialien dieser Datei',
    'Your materials for every project (the base material library)': 'Deine Materialien für alle Projekte (die Basis-Materialbibliothek)',
    'Previews in a grid': 'Vorschauen im Raster',
    'One material per row, more at a glance': 'Ein Material pro Zeile, mehr auf einen Blick',
    'Save the material into your library, so every project can use it (replaces one of the same name)': 'Das Material in deine Bibliothek speichern, damit jedes Projekt es nutzen kann (ersetzt eines mit gleichem Namen)',
    'Save to your library?': 'In deine Bibliothek speichern?',
    # -- 2.3: Environment tab -------------------------------------------------
    'Environment': 'Umgebung',
    'Scene': 'Szene',
    'Choose a scene': 'Szene wählen',
    'Save Scene': 'Szene speichern',
    'Scene Presets': 'Szenen-Presets',
    'Light preset': 'Licht-Preset',
    'Solo': 'Solo',
    'Only the Product': 'Nur das Produkt',
    'Flag / Gobo': 'Abschatter / Gobo',
    'Flag': 'Abschatter',
    'Blinds': 'Jalousie',
    'Window': 'Fenster',
    'Leaves': 'Blätter',
    'Remove Flag': 'Abschatter entfernen',
    'Sun from HDRI': 'Sonne aus HDRI',
    'Haze Color': 'Dunstfarbe',
    'Background': 'Hintergrund',
    'Floor': 'Boden',
    'Cove': 'Hohlkehle',
    'None': 'Keiner',
    'Floor Color': 'Bodenfarbe',
    'Curve': 'Rundung',
    'Shadow Catcher': 'Shadow Catcher',
    'The World still lights the scene': 'Die Welt beleuchtet die Szene weiterhin',
    'Best with a transparent or colored background': 'Am besten mit transparentem oder farbigem Hintergrund',
    'Scene presets, lights, World, background and floor': 'Szenen-Presets, Lichter, Welt, Hintergrund und Boden',
    'Apply this scene: lights, World, background, floor, haze and look together': 'Diese Szene anwenden: Lichter, Welt, Hintergrund, Boden, Dunst und Look zusammen',
    'Save the whole environment as a scene preset (every blend file): lights, World, background, floor, haze and look': 'Die ganze Umgebung als Szenen-Preset speichern (für jede Datei): Lichter, Welt, Hintergrund, Boden, Dunst und Look',
    'Render only this light; click again to bring the others back as they were': 'Nur dieses Licht rendern; nochmals klicken stellt die anderen wieder her',
    'This light lights only the product, not the floor, room or background (light linking, Cycles). Click again to light everything': 'Dieses Licht beleuchtet nur das Produkt, nicht Boden, Raum oder Hintergrund (Light Linking, Cycles). Nochmals klicken beleuchtet alles',
    'A card in front of the active light: cuts (flag) or shapes (gobo) its light': 'Eine Karte vor dem aktiven Licht: schneidet (Abschatter) oder formt (Gobo) sein Licht',
    'A sun light from the brightest spot of the HDRI (direction and color): sharp shadows that match the World': 'Ein Sonnenlicht aus der hellsten Stelle des HDRI (Richtung und Farbe): harte Schatten passend zur Welt',
    'A black card that cuts part of the light': 'Eine schwarze Karte, die einen Teil des Lichts abschneidet',
    'Stripes of light and shadow': 'Streifen aus Licht und Schatten',
    "A window frame's shadow": 'Der Schatten eines Fensterrahmens',
    'Dappled light through leaves': 'Licht durch Blätter',
    'No floor of our own': 'Kein eigener Boden',
    'A floor under the product': 'Ein Boden unter dem Produkt',
    'A floor that curves up into a back wall (infinity cove), opposite the camera': 'Ein Boden, der in eine Rückwand übergeht (Hohlkehle), gegenüber der Kamera',
    '0 = mirror-like reflections, 1 = matte': '0 = spiegelnd, 1 = matt',
    "Width of the floor as a multiple of the product's size": 'Breite des Bodens als Vielfaches der Produktgrösse',
    'How round the cove curves up into the wall': 'Wie rund die Hohlkehle in die Wand übergeht',
    'The floor itself is invisible, only shadows and reflections on it stay (with a transparent or colored background)': 'Der Boden selbst ist unsichtbar, nur Schatten und Spiegelungen bleiben (mit transparentem oder farbigem Hintergrund)',
    'Haze in the air: makes light beams visible (0 = off). Slower renders': 'Dunst in der Luft: macht Lichtstrahlen sichtbar (0 = aus). Langsamere Renderings',
    # -- 2.3: shots, render check, presets, simple view ---------------------------
    'Render queue, shots and the results': 'Render-Warteschlange, Shots und Resultate',
    'Simple View': 'Einfache Ansicht',
    'Show only the main settings in the Studio tabs (the rest stays as it is)': 'Nur die wichtigsten Einstellungen in den Studio-Tabs zeigen (der Rest bleibt wie er ist)',
    'All Presets': 'Alle Presets',
    'Export': 'Exportieren',
    'Import': 'Importieren',
    'Export all your presets (scenes, lights, materials) into one file, or import such a file': 'Alle deine Presets (Szenen, Lichter, Materialien) in eine Datei exportieren oder eine solche Datei importieren',
    'Shots': 'Shots',
    'Add Shot': 'Shot hinzufügen',
    'Remove Shot': 'Shot entfernen',
    'Render Shots': 'Shots rendern',
    'Stop Shots': 'Shots stoppen',
    'Camera + scene + variant, rendered together': 'Kamera + Szene + Variante, zusammen gerendert',
    'A new shot from the active camera and the current scene preset': 'Ein neuer Shot aus der aktiven Kamera und dem aktuellen Szenen-Preset',
    'Render every shot one after another: its scene preset is applied to the scene, then its camera with its variant is rendered': 'Jeden Shot nacheinander rendern: sein Szenen-Preset wird angewendet, dann wird seine Kamera mit ihrer Variante gerendert',
    'No further shots (the one rendering now finishes)': 'Keine weiteren Shots (der laufende wird fertig)',
    'Pick the scene preset of this shot (empty: the scene as it is)': 'Szenen-Preset dieses Shots wählen (leer: die Szene wie sie ist)',
    'As it is now': 'Wie jetzt',
    'Variant': 'Variante',
    'Before rendering': 'Vor dem Rendern',
    'No camera to render': 'Keine Kamera zum Rendern',
    'No light: no lamp, and the World is off': 'Kein Licht: keine Lampe und die Welt ist aus',
    'Very large image (over 60 megapixels)': 'Sehr grosses Bild (über 60 Megapixel)',
    'Contact Sheet': 'Kontaktbogen',
    'Compare': 'Vergleichen',
    'The last renders on one sheet (PNG next to them), opened right away': 'Die letzten Renderings auf einem Bogen (PNG daneben), gleich geöffnet',
    'The last two renders side by side (in Preview)': 'Die letzten zwei Renderings nebeneinander (in Vorschau)',
    'Render at least two images first': 'Zuerst mindestens zwei Bilder rendern',
    # -- 2.4: simpler tabs -----------------------------------------------------
    'Lights': 'Lichter',
    'Preset': 'Preset',
    'Pick a preset or add a light': 'Preset wählen oder Licht hinzufügen',
    'Backdrop': 'Hintergrund & Boden',
    'Scene preset': 'Szenen-Preset',
    'Lights aim at': 'Lichter zielen auf',
    '3D Cursor': '3D-Cursor',
    'Save Light Preset': 'Licht-Preset speichern',
    'Own Copy for This Object': 'Eigene Kopie für dieses Objekt',
    'New Variant': 'Neue Variante',
    'Unlink from the Others': 'Von den anderen lösen',
    'Pick Color from Object': 'Farbe von Objekt übernehmen',
    'Shader Editor': 'Shader-Editor',
    'Select an object or click a material': 'Objekt wählen oder Material anklicken',
    'Open or close this section': 'Diesen Bereich öffnen oder schliessen',
    'Roughness': 'Rauheit',
    # -- 2.5: click placing, studio World, sky -----------------------------------
    'Image': 'Bild',
    'Studio': 'Studio',
    'Sky': 'Himmel',
    'An HDRI image lights the scene': 'Ein HDRI-Bild beleuchtet die Szene',
    'A studio environment made of lights you place (like an HDRI light editor)': 'Eine Studio-Umgebung aus Lichtern, die du platzierst (wie ein HDRI-Lichteditor)',
    'A sky by time of day: day, golden hour, night': 'Ein Himmel nach Tageszeit: Tag, goldene Stunde, Nacht',
    'Ambient': 'Grundlicht',
    'Ambient Color': 'Grundlichtfarbe',
    'Brightness of the studio around the lights': 'Helligkeit des Studios um die Lichter',
    'Time': 'Zeit',
    "Time of day (hours): the sun's height and direction follow": 'Tageszeit (Stunden): Sonnenhöhe und -richtung folgen',
    'North': 'Norden',
    'Turns the sky: where north is': 'Dreht den Himmel: wo Norden ist',
    'Left / Right': 'Links / Rechts',
    'Brightness': 'Helligkeit',
    'Softness': 'Weichheit',
    'Shape': 'Form',
    'Rectangle': 'Rechteck',
    'Round': 'Rund',
    '0 = hard edge, 1 = fades out from the middle': '0 = harte Kante, 1 = läuft von der Mitte aus',
    'Around the product: 0° = from the camera, negative = left': 'Um das Produkt: 0° = von der Kamera, negativ = links',
    '0° = at the horizon, 90° = straight from above': '0° = am Horizont, 90° = direkt von oben',
    'Place by Click': 'Per Klick platzieren',
    'Reflection': 'Reflexion',
    'Rim': 'Kante',
    "The light's reflection appears where you click": 'Die Spiegelung des Lichts erscheint dort, wo du klickst',
    'The light shines straight onto the spot you click': 'Das Licht scheint direkt auf die angeklickte Stelle',
    'The light comes from behind the spot you click (edge light)': 'Das Licht kommt von hinter der angeklickten Stelle (Kantenlicht)',
    'Add Light': 'Licht hinzufügen',
    'Remove Light': 'Licht entfernen',
    'A new light in the studio World': 'Ein neues Licht in der Studio-Welt',
    'Light Settings': 'Licht-Einstellungen',
    'Show the settings of this light (click again to close)': 'Einstellungen dieses Lichts zeigen (nochmals klicken schliesst)',
    'Time of Day': 'Tageszeit',
    'Sunrise': 'Sonnenaufgang',
    'Morning': 'Morgen',
    'Noon': 'Mittag',
    'Golden': 'Golden',
    'Dusk': 'Dämmerung',
    'Night': 'Nacht',
    'Stars': 'Sterne',
    'Clouds and stars: with Physical Starlight & Atmosphere': 'Wolken und Sterne: mit Physical Starlight & Atmosphere',
    'Use it in the 3D viewport': 'Im 3D-Viewport verwenden',
    'At most 16 lights': 'Höchstens 16 Lichter',
    'Kelvin': 'Kelvin',
    # -- 2.6: one light list, light map, placing on the product, looks, moving gobos
    'Place Light on the Product': 'Licht am Produkt platzieren',
    'Click or drag on the product to place the open light. Ctrl + click: a new light. While dragging: wheel = distance, Ctrl + wheel = size, Shift + wheel = brightness': 'Auf das Produkt klicken oder ziehen, um das offene Licht zu platzieren. Ctrl + Klick: neues Licht. Beim Ziehen: Rad = Abstand, Ctrl + Rad = Grösse, Shift + Rad = Helligkeit',
    'As Chosen': 'Wie gewählt',
    'The way picked in the Environment tab': 'Die im Umgebungs-Tab gewählte Art',
    'Shade': 'Schatten',
    'Shadow': 'Schattenwurf',
    'The spot you click falls into shadow: the light goes to the other side': 'Die angeklickte Stelle fällt in den Schatten: das Licht geht auf die andere Seite',
    "The product's shadow falls onto the spot you click (click the floor)": 'Der Schatten des Produkts fällt auf die angeklickte Stelle (auf den Boden klicken)',
    'Change the Open Light': 'Offenes Licht ändern',
    'Size, brightness or distance of the open light, one step': 'Grösse, Helligkeit oder Abstand des offenen Lichts, ein Schritt',
    'Click or drag on the product: the open light of the Environment tab goes there (reflection, light, rim, shade or shadow).\nCtrl + click: a new light. Ctrl / Shift / Alt + wheel: size, brightness, distance': 'Auf das Produkt klicken oder ziehen: das offene Licht des Umgebungs-Tabs geht dorthin (Reflexion, Licht, Kante, Schatten oder Schattenwurf).\nCtrl + Klick: neues Licht. Ctrl / Shift / Alt + Rad: Grösse, Helligkeit, Abstand',
    'Open a light in the Environment tab': 'Im Umgebungs-Tab ein Licht öffnen',
    'Place on the Product': 'Am Produkt platzieren',
    'How a click on the product places the open light': 'Wie ein Klick auf das Produkt das offene Licht platziert',
    'Click on the product': 'Klick auf das Produkt',
    'Its lights are in the list above': 'Seine Lichter stehen in der Liste oben',
    'Place': 'Platzieren',
    'Open Light': 'Licht öffnen',
    'Show the settings of this light (click again to close). While it is open, a click on the product places it': 'Einstellungen dieses Lichts zeigen (nochmals klicken schliesst). Solange es offen ist, platziert ein Klick auf das Produkt das Licht',
    'New Light': 'Neues Licht',
    'Add a light': 'Ein Licht hinzufügen',
    'Area Light': 'Flächenlicht',
    'Spot Light': 'Spot',
    'Sun Light': 'Sonne',
    'Point Light': 'Punktlicht',
    'Light in the Studio World': 'Licht in der Studio-Welt',
    'A soft light with a size, front left, aimed at the product': 'Ein weiches Licht mit Grösse, vorne links, aufs Produkt gerichtet',
    'A cone of light, for accents and hard shadows': 'Ein Lichtkegel, für Akzente und harte Schatten',
    'Parallel light with sharp shadows': 'Paralleles Licht mit scharfen Schatten',
    'A bulb that shines to all sides': 'Eine Birne, die nach allen Seiten strahlt',
    'A softbox painted into the environment: it lights and shows in reflections, but has no place in the room. Switches the World to Studio': 'Eine Softbox, in die Umgebung gemalt: sie beleuchtet und ist in Spiegelungen sichtbar, hat aber keinen Ort im Raum. Schaltet die Welt auf Studio',
    'Delete Light': 'Licht löschen',
    'Delete this light': 'Dieses Licht löschen',
    'A copy of this light; the copy is opened': 'Eine Kopie dieses Lichts; die Kopie wird geöffnet',
    'Mirror': 'Spiegeln',
    'Mirrored Copy': 'Gespiegelte Kopie',
    'Mirror All Lights': 'Alle Lichter spiegeln',
    'Move this light to the other side of the product (left and right as the camera sees it)': 'Dieses Licht auf die andere Seite des Produkts setzen (links und rechts aus Sicht der Kamera)',
    'Flip the whole lighting: every light goes to the other side of the product': 'Das ganze Licht umdrehen: jedes Licht geht auf die andere Seite des Produkts',
    'Leave this light and put a copy on the other side of the product': 'Dieses Licht lassen und eine Kopie auf die andere Seite des Produkts setzen',
    'More': 'Mehr',
    'Color Temperature': 'Farbtemperatur',
    'Warm (2700 K) to cool (8000 K) light': 'Warmes (2700 K) bis kühles (8000 K) Licht',
    'How big the light is (width and height together)': 'Wie gross das Licht ist (Breite und Höhe zusammen)',
    'How big the light is: bigger gives softer shadows and wider reflections': 'Wie gross das Licht ist: grösser gibt weichere Schatten und breitere Spiegelungen',
    '0 = the light shines straight ahead with a hard edge, 1 = it spreads to all sides': '0 = das Licht strahlt gerade nach vorn mit harter Kante, 1 = es streut nach allen Seiten',
    'At most %d lights in the studio World': 'Höchstens %d Lichter in der Studio-Welt',
    'Looks': 'Looks',
    'New Look': 'Neuer Look',
    'Keep the lighting as it is and go on in a copy: lights, World, background, floor and haze. Switch between the looks with their buttons to compare': 'Das Licht so behalten und in einer Kopie weiterarbeiten: Lichter, Welt, Hintergrund, Boden und Dunst. Mit den Knöpfen zwischen den Looks wechseln und vergleichen',
    'Compare Looks (A / B)': 'Looks vergleichen (A / B)',
    'Look': 'Look',
    'Switch to this look (the open look keeps what you changed)': 'Zu diesem Look wechseln (der offene Look behält deine Änderungen)',
    'Remove Look': 'Look entfernen',
    'Remove the open look and go back to the one next to it': 'Den offenen Look entfernen und zum Look daneben zurückgehen',
    'The lighting so far is look A. You are now in look %s: change it and compare with the buttons': 'Das bisherige Licht ist Look A. Du bist jetzt in Look %s: ändere ihn und vergleiche mit den Knöpfen',
    'Light Map': 'Lichtkarte',
    "Show the light map at the bottom of the 3D viewport: every light as a point to drag. The middle is the camera's side, the edges are behind the product": 'Die Lichtkarte unten im 3D-Viewport zeigen: jedes Licht als Punkt zum Ziehen. Die Mitte ist die Kameraseite, die Ränder liegen hinter dem Produkt',
    'Show the light map in the 3D viewport while the Environment tab is open': 'Die Lichtkarte im 3D-Viewport zeigen, solange der Umgebungs-Tab offen ist',
    'Map Size': 'Kartengrösse',
    'Size of the light map in the 3D viewport': 'Grösse der Lichtkarte im 3D-Viewport',
    'Front': 'Vorne',
    'Back': 'Hinten',
    'Left': 'Links',
    'Right': 'Rechts',
    'Drag: move   ·   Ctrl + drag: size   ·   Shift + drag: brightness   ·   Ctrl + click: new light': 'Ziehen: bewegen   ·   Ctrl + Ziehen: Grösse   ·   Shift + Ziehen: Helligkeit   ·   Ctrl + Klick: neues Licht',
    'Switch gizmos on (viewport header) to drag the lights': 'Gizmos einschalten (Viewport-Kopfzeile), um die Lichter zu ziehen',
    'Tree': 'Baum',
    'Water': 'Wasser',
    'Speed': 'Tempo',
    'Light through leaves that move in the wind': 'Licht durch Blätter, die sich im Wind bewegen',
    'Leaves and branches of a tree, moving in the wind': 'Blätter und Äste eines Baums, im Wind bewegt',
    'Moving ripples of light, like light reflected off water': 'Bewegte Lichtwellen, wie von Wasser reflektiertes Licht',
    'Soft shadows that drift slowly': 'Weiche Schatten, die langsam ziehen',
    'A card in front of the open light: cuts (flag) or shapes (gobo) its light. Leaves, tree, water and clouds move when the animation plays; a small light gives sharper shadows': 'Eine Karte vor dem offenen Licht: schneidet (Abschatter) oder formt (Gobo) sein Licht. Blätter, Baum, Wasser und Wolken bewegen sich, wenn die Animation läuft; ein kleines Licht gibt schärfere Schatten',
    'A lighting mood of this file': 'Eine Lichtstimmung dieser Datei',
    'Around': 'Rundum',
    # -- 2.7: material structures, wear, library filter
    'New Structure': 'Neue Struktur',
    'A new procedural material to go on from, for the selected objects': 'Ein neues prozedurales Material als Ausgangspunkt, für die gewählten Objekte',
    'Structures (procedural)': 'Strukturen (prozedural)',
    'Simple': 'Einfach',
    'Worn Metal': 'Metall gebraucht',
    'Layered Earth': 'Stampflehm',
    'Speckled': 'Gesprenkelt',
    'Resin': 'Harz',
    'Hammered': 'Gehämmert',
    'Dark Anodized': 'Dunkel eloxiert',
    'Tumbled or used metal: scratches in all directions, uneven sheen, patina': 'Getrommeltes oder gebrauchtes Metall: Kratzer in alle Richtungen, ungleichmässiger Glanz, Patina',
    'Rammed earth, cast stone: layers along Z, grain and pores': 'Stampflehm, Gussstein: Schichten entlang Z, Körnung und Poren',
    'Recycled plastic, powder coat: flecks of two colors and a fine grain': 'Recycling-Kunststoff, Pulverlack: Sprenkel in zwei Farben und feines Korn',
    'Amber, acetate, tinted glass: clear, the color deepens with the thickness, soft bands (Cycles)': 'Bernstein, Acetat, getöntes Glas: klar, die Farbe wird mit der Dicke tiefer, weiche Bänder (Cycles)',
    'Polished metal with hammered dents': 'Poliertes Metall mit gehämmerten Dellen',
    'Dark metal with fine long scratches and wipe marks': 'Dunkles Metall mit feinen langen Kratzern und Wischspuren',
    'Wear': 'Gebrauchsspuren',
    'Scratches, fingerprints and dust over this material, each with its own amount (procedural; what the material had stays)': 'Kratzer, Fingerabdrücke und Staub über diesem Material, jedes mit eigener Stärke (prozedural; was das Material hatte, bleibt)',
    'Take the wear layer off this material': 'Die Gebrauchsspuren von diesem Material nehmen',
    'Remove Wear': 'Gebrauchsspuren entfernen',
    'Add Wear (Scratches, Prints, Dust)': 'Gebrauchsspuren (Kratzer, Abdrücke, Staub)',
    'Surface Depth': 'Oberflächentiefe',
    'Wear Depth': 'Tiefe der Spuren',
    'Scratches': 'Kratzer',
    'Fingerprints': 'Fingerabdrücke',
    'Dust': 'Staub',
    'Dust Color': 'Staubfarbe',
    'Patina': 'Patina',
    'Color A': 'Farbe A',
    'Color B': 'Farbe B',
    'Layer Height': 'Schichthöhe',
    'Grain': 'Körnung',
    'Fleck Color A': 'Sprenkelfarbe A',
    'Fleck Color B': 'Sprenkelfarbe B',
    'Fleck Size': 'Sprenkelgrösse',
    'Dent Size': 'Dellengrösse',
    'Smudges': 'Wischspuren',
    'Band Color': 'Bandfarbe',
    'Bands': 'Bänder',
    'Band Size': 'Bandbreite',
    "Every material: this file's and the library's": 'Alle Materialien: die dieser Datei und die der Bibliothek',
    'Your favorites (the heart on a tile)': 'Deine Favoriten (das Herz auf einer Kachel)',
    'All': 'Alle',
}

# Blender looks up operator names in the "Operator" context and everything else
# in the default context "*": register every string for both.
_CONTEXTS = ("*", "Operator", "AI_Studio")   # AI_Studio: see preferences.I18N_CONTEXT


def _build():
    de = {}
    for en, translated in DE.items():
        for ctx in _CONTEXTS:
            de[(ctx, en)] = translated
    return {"de_DE": de}


_active = [False]   # the German strings are registered (Preferences › General › German Interface)


def tr(msgid):
    """Translate a UI string built at runtime (labels, messages). English unless the German
    interface is on (Blender's own catalog would otherwise translate common words)."""
    if not _active[0]:
        return msgid
    # Our own table first: German also when Blender itself is English (Blender only translates
    # registered strings when its interface language is German).
    return DE.get(msgid) or bpy.app.translations.pgettext_iface(msgid)


def tip(msgid):
    """Translate a tooltip built at runtime."""
    if not _active[0]:
        return msgid
    return DE.get(msgid) or bpy.app.translations.pgettext_tip(msgid)


def apply(german):
    """The add-on in English (default) or, with German on, in German when Blender is."""
    if german and not _active[0]:
        bpy.app.translations.register(__package__, _build())
        _active[0] = True
    elif not german and _active[0]:
        bpy.app.translations.unregister(__package__)
        _active[0] = False


def register():
    pass   # see apply(): called once the preferences are there


def unregister():
    apply(False)
