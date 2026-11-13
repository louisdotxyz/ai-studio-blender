# ID Toolkit für Claude

CAD-Teile mit Claude konstruieren, nach Blender schicken und Produktbilder rendern.

## Installieren
1. Claude-Desktop-App (Mac), Plugins: den Marketplace `louisdotxyz/ai-studio-blender` (GitHub) hinzufügen und
   **ID Toolkit** installieren. So kommen Updates automatisch. Alternativ die Datei `id-toolkit.plugin` öffnen.
2. In einem neuen Chat schreiben: **«ID Toolkit einrichten»**.

Das Setup erledigt den Rest selbst und fragt nur, wenn etwas fehlt:
Blender 5 (falls nicht installiert), das Blender-Add-on (mit automatischen Updates) und die Verbindung zur
Claude-App. Am Ende einmal die Claude-App mit **Cmd+Q** beenden und neu öffnen.

## Was drin ist
| Skill | Wofür |
|---|---|
| **setup** | Einrichtung in einem Schritt, prüft auch, ob alles läuft |
| **cad** | Teile parametrisch bauen und prüfen: Onshape (im Browser der Claude-App, nichts zu installieren), Rhino oder Fusion, sonst Code-CAD (STEP) |
| **cad-to-blender** | CAD-Versionen direkt ins offene Blender, Materialien, Prüfbilder, Änderungsaufträge zurück an den CAD-Chat |
| **blender** | Im offenen Blender arbeiten und rendern: Kamera, Licht, Materialien, Vorschau → finales Bild |

## Voraussetzungen
- Mac mit der Claude-Desktop-App
- Für Onshape: ein (kostenloser) Onshape-Account
- Rhino oder Fusion nur, wenn du sie schon nutzt und ihr MCP verbunden ist

Inoffiziell, nicht verbunden mit Anthropic, Blender Foundation oder Onshape. Lizenz GPL-3.0-or-later.

**Claude Code:** `/plugin install id-toolkit --marketplace louisdotxyz/ai-studio-blender`
