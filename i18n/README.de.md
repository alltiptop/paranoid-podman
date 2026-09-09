[en English](../README.md) · [ru Русский](README.ru.md) · [es Español](README.es.md) · [pl Polski](README.pl.md) · [uk Українська](README.uk.md) · [de Deutsch](README.de.md) · [fr Français](README.fr.md) · [zh-CN 简体中文](README.zh-CN.md) · [ar العربية](README.ar.md) · [he עברית](README.he.md)

# paranoid-podman

`paranoid-podman` ist ein experimenteller Wrapper auf Benutzerebene für
Rootless Podman und `podman-compose`. Er richtet sich an Entwickler, die
Containerbefehle gelegentlich aus Projekten ausführen, denen sie nicht
vollständig vertrauen.

Das Projekt ergänzt einfache, direkte Schutzmaßnahmen gegen typische
Container-Ausbruchs- und Hostzugriffsfehler, ohne normale lokale
Entwicklungsabläufe zu verhindern. Es ist kein Virenschutz, kein
Enterprise-Sicherheitsprodukt und keine vollständige Sandbox.

> [!WARNING]
> Dies ist mehrschichtige Absicherung, kein absoluter Schutz. Zero-Day-Exploits,
> unbekannte Angriffstechniken, Schwachstellen im Kernel oder in der Runtime
> sowie Prozesse, die bereits Zugriff auf das Host-Benutzerkonto haben, werden
> möglicherweise nicht aufgehalten.

## Inhalt

- [Über das Projekt](#about)
- [Was geschützt wird](#what-it-protects)
- [Installation](#install)
- [Verwendung](#usage)
- [DevPod](#devpod)
- [Kompatibilität](#compatibility)
- [Einschränkungen](#limitations)
- [Entwicklung](#development)
- [Sicherheit](#security)
- [Lizenz](#license)

<a id="about"></a>

## Über das Projekt

Der Befehlsfluss lautet:

```text
podman-/compose-Befehl -> Parser -> Richtlinie -> verifizierter realer Provider
```

Sichere Argumente bleiben erhalten. Gefährlicher Hostzugriff wird abgelehnt,
ausgewählte Härtungswerte werden ergänzt und geschützte Projektdateien werden in
neuen Containern schreibgeschützt eingehängt.

Das Design ist bewusst geradlinig:

- die direktesten Wege zu Privilegien und Hostzugriff blockieren;
- normale Entwicklung innerhalb der Projektgrenzen ermöglichen;
- explizite, verständliche Mount-Beschränkungen gegenüber Inhaltsscannern bevorzugen;
- bei unbekannten sicherheitsrelevanten Befehlsformen geschlossen ablehnen; und
- die verbleibenden Grenzen ehrlich dokumentieren.

<a id="what-it-protects"></a>

## Was geschützt wird

| Eingabe oder Verhalten | Aktion |
| --- | --- |
| Privilegierter Modus, Host- oder verbundene Namespaces, zusätzliche Capabilities, Geräte, beliebige Sicherheitsoptionen | Ablehnen |
| Podman-/Docker-Engine-Sockets und sensible Runtime-Pfade des Hosts | Ablehnen |
| Dateisystemwurzel, vollständiges Benutzer-Home, breite Systembäume oder ein Bind-Mount oberhalb des Projekts | Ablehnen |
| Explizit angegebene einzelne reguläre Dateien und Verzeichnisse | Erlauben und kanonisieren |
| Bind-Optionen, die Besitz oder Labels des Hosts ändern, etwa `U`, `idmap` oder unsicheres Relabeling | Ablehnen |
| Veröffentlichte Ports | Normale TCP/UDP-Zuordnungen beibehalten, auch für alle Schnittstellen und LAN-Adressen |
| Umgebungs-Credentials, Desktop-/Sitzungsendpunkte und Provider-Routing-Variablen | Entfernen oder ablehnen |
| Vorhandene geschützte Projektkonfiguration unter beschreibbaren Bind-Mounts | Schreibgeschützte Submounts ergänzen |
| `start`, `exec` oder aktiver Compose-Lifecycle auf alten oder fremden Containern | Bis zur Neuerstellung durch aktuelle Installation und Richtlinie ablehnen |
| Compose-Konfiguration | Auflösen, validieren, umschreiben und privaten geprüften Snapshot ausführen |
| Offensichtliche literale Secrets in Compose oder Dockerfile-Zuweisungen | Mit verborgenen Werten ablehnen |
| Credentials im Build-Context-Wurzelverzeichnis ohne passende Ignore-Regel | Vor dem Builder ablehnen |

Direkte `run`- und `create`-Befehle erhalten außerdem kompatible Vorgaben wie
`no-new-privileges`, ein PID-Limit, deaktivierte automatische Proxy-Weitergabe,
eine nicht persistente Restart-Richtlinie und kein implizites Image-Pull.
Unbekannte Runtime-Optionen werden nicht weitergereicht.

### Geschützte Projektkonfiguration

Gefundene Pfade mit folgenden Namen sind in neu erstellten Containern nur
lesbar:

- `.devcontainer`, `.devcontainer.json`, `devcontainer.json`;
- `.dockerignore`, `.containerignore`;
- aktive dotenv-Dateien und übliche Credential-Konfigurationsdateien; sowie
- `.git`, `.gitmodules`, `.git-credentials`.

Fehlende Pfade werden nicht angelegt. Devcontainer-Inhalte werden weder gescannt
noch umgeschrieben: Der Schutz ist eine Dateisystem-Mount-Regel. Dockerfiles
erhalten nur die unten beschriebene kleine Prüfung auf literale Secrets.

Dockerfile-, Containerfile- und Compose-Dateien außerhalb von `.devcontainer`
bleiben im Container bearbeitbar. Der gesamte Inhalt von `.devcontainer` bleibt
schreibgeschützt. Bestehende Container müssen für die neuen Mount-Rechte neu erstellt werden.

Unlesbare Verzeichnisse einer anderen UID, etwa Daten einer Rootless-Datenbank,
werden ohne Inhaltsprüfung oder Änderung der Berechtigungen eingebunden.
Dateien darin fallen nicht unter den automatischen Schreibschutz.

`.git` ist standardmäßig schreibgeschützt. Für einen einzelnen Aufruf kann es
beschreibbar bleiben, ohne andere geschützte Pfade zu schwächen:

```bash
PODMAN_GUARD_PROTECT_GIT=0 podman compose up
```

### Compose-Prüfung

Der Compose-Adapter:

1. prüft die Quelldateien vorab und rendert die aufgelöste Konfiguration;
2. validiert Hostzugriff, Namespaces, Mounts, Ressourcen und unterstützte Felder;
3. schreibt einen privaten Snapshot im Modus `0600` mit fertiger Interpolation;
4. führt ausschließlich diesen geprüften Snapshot aus.

Projektinterne Mounts bleiben still. Vor `up` oder `run` verlangt der Zugriff auf
Hostdateien oder Verzeichnisse außerhalb des Projekts eine Bestätigung: Ein oranges
`[warning]` zeigt den vollständigen Pfad, Dienst, Zugriffsmodus und die Compose-Datei
mit Zeilennummer. Genau `y` und Enter erlaubt die Mounts für diesen Aufruf. Enter,
eine andere Antwort oder EOF bricht ab; ohne Terminaleingabe wird der Vorgang ohne
Lesen von stdin blockiert. Die Auswahl wird nicht gespeichert. Inspektion, Entfernen
und Befehle für bestehende Container fragen nicht nach. Verbotene Pfade und Sockets
lassen sich damit nicht freigeben.

`PODMAN_GUARD_DEBUG=1` zeigt eine Übersicht ohne vertrauliche Werte.
Terminal und Automatisierung unterliegen denselben Regeln. Fehler erscheinen mit
`BLOCKED`, `UNSUPPORTED` oder `ERROR`, einer Begründung und gegebenenfalls dem
betroffenen Dienst.

Compose-Diagnosen geben weder aufgelöste Umgebungswerte noch rohe Providerfehler
aus. Die Prüfung literaler Secrets ist bewusst klein und namensbasiert; sie ist
kein allgemeiner Secret-Scanner.

Neue direkte und Compose-Container erhalten zwei reservierte Provenance-Labels:
die Richtliniengeneration und eine zufällige Kennung der lokalen Installation.
Aktionen, die Code starten, fortsetzen oder ausführen können, verlangen vor der
Nutzung eines bestehenden Containers die Übereinstimmung beider Labels. Ein
Image kann die Prüfung daher nicht allein durch das öffentliche Policy-Label
bestehen. Schreibgeschützte Inspektion und Cleanup bleiben verfügbar, damit alte
Container sicher untersucht und entfernt werden können.

Die genauen Schnittstellen beschreiben die
[direkte Podman-Richtlinie](../docs/direct-policy.md), die
[Compose-Richtlinie](../docs/compose-policy.md) und das
[Bedrohungsmodell](../docs/threat-model.md).

<a id="install"></a>

## Installation

Erforderlich sind Linux mit rootless Podman 6.1.x, `podman-compose` 1.6.x,
Python ab 3.10 mit Unterstützung für virtuelle Umgebungen und Bash. Für DevPod
benötigen Sie zusätzlich DevPod und die OpenSSH-Clientprogramme.
DevPod 0.6.15 wurde getestet; andere Versionen werden akzeptiert.
Führen Sie im Repository als normaler Benutzer aus:

```bash
./install.sh install
```

Ohne Argumente zeigt `./install.sh` den Status und ein Menü: `install` für
eine neue Installation, sonst `update` / `uninstall`. Enter beendet das Programm
ohne Änderungen. Ohne interaktives Terminal werden nur Status und verfügbare
Aktionen angezeigt; geben Sie in Skripten die Aktion ausdrücklich an.

`./install.sh --help` beschreibt die Optionen nach Aktion. Anbieter- und
Wheel-Optionen gelten für `install` / `update`; `--without-devpod` und
`--backup-existing` nur für `install`, `--devpod-ssh-config` nur für
`uninstall`. Für eine normale Installation sind keine Zusatzoptionen nötig.

Das Installationsprogramm findet Podman, Compose und gegebenenfalls DevPod,
lädt Python-Werkzeuge und Abhängigkeiten herunter, baut die Anwendung und
installiert sie in einer privaten Umgebung. Pakete, Prüfsummen und Python-Umgebungen
müssen Sie nicht selbst vorbereiten. Vorhandene Befehle werden nach Bestätigung
gesichert und bei der Deinstallation wiederhergestellt.

Vorschau ohne Downloads oder Dateiänderungen:

```bash
./install.sh install --dry-run
```

### Befehlsverzeichnis

Das Standardverzeichnis für Befehle ist `${XDG_BIN_HOME:-$HOME/.local/bin}`.
Setzen Sie es an den Anfang von `PATH`, damit die geschützten Befehle verwendet werden:

```bash
export PATH="${XDG_BIN_HOME:-$HOME/.local/bin}:$PATH"
command -v podman docker compose-guard
```

Falls es noch nicht an erster Stelle steht, ergänzen Sie die `export`-Zeile in
Ihrer Shell-Konfiguration. Die Anwendung liegt unter
`${XDG_DATA_HOME:-$HOME/.local/share}/paranoid-podman`.

### Aktualisieren oder deinstallieren

Führen Sie dies im Repository mit der gewünschten Anwendungsversion aus:

```bash
./install.sh update
./install.sh status
```

Deinstallieren und gesicherte Befehle wiederherstellen:

```bash
./install.sh uninstall
```

Mit `./uninstall.sh` erhalten Sie eine Statusprüfung und ein Menü mit
`uninstall` / `Exit`. Enter beendet es ohne Änderungen; ohne interaktives Terminal
werden nur Informationen angezeigt. `./uninstall.sh --help` erklärt die Optionen
zur Entfernung; `./uninstall.sh --dry-run` zeigt nach der Menüauswahl eine Vorschau.

`--dry-run` funktioniert auch beim Aktualisieren und Deinstallieren. Geänderte Installationsdateien werden gemeldet und
nicht gelöscht. Um DevPod nachträglich hinzuzufügen, deinstallieren Sie die Anwendung
und installieren sie erneut, sobald DevPod verfügbar ist. Geschützte Container
müssen danach neu erstellt werden.

### Optionale Einstellungen

`--without-devpod` installiert nur die Schutzfunktionen für Podman und Compose.
`--devpod PATH` wählt eine ausführbare Datei außerhalb von `PATH`; `--podman` und
`--compose-provider` ersetzen die automatische Suche. `--backup-existing` erlaubt
Sicherungen ohne interaktive Bestätigung. Verwenden Sie bei eigenen Verzeichnissen
übereinstimmende Werte für `--bindir` und `--libdir`.

SSH-Konfigurationen im Standardverzeichnis von DevPod werden automatisch
wiederhergestellt. Bei Kontexten in einem anderen `DEVPOD_HOME` geben Sie jeden
eigenen SSH-Konfigurationspfad an:

```bash
./install.sh uninstall --devpod-ssh-config /absolute/path/to/ssh-config
```

Die [erweiterte Anleitung zur Offline-Installation](../docs/wheel-packaging.md)
beschreibt vorbereitete Pakete. Die normale Installation benötigt diese Optionen nicht.

<a id="usage"></a>

## Verwendung

Führen Sie die Befehle in einem vorhandenen Projektverzeichnis aus. Ersetzen Sie
`localhost/my-dev-image:latest` durch ein bereits lokal vorhandenes Image:
direkte Starts verwenden `--pull=never`. Compose benötigt eine Konfigurationsdatei
und einen Dienst namens `app`.

```bash
podman run --rm -v .:/workspace localhost/my-dev-image:latest
podman compose up
podman compose ps
podman compose exec app sh
podman compose run --rm --build app
podman compose down
```

Einmalige Compose-Aufgaben unterstützen Profile, `--no-deps` sowie Umgebungs-,
Benutzer-, Arbeitsverzeichnis- und Portoptionen. `run --build` stoppt bei einem
Buildfehler. `up --force-recreate` und der ausdrückliche Reset `down --volumes
--remove-orphans` werden unterstützt; Letzterer löscht Projektvolumes.

Für kompatible Abläufe werden die Aliase `docker`, `podman-compose` und
`docker-compose` installiert. Befehle außerhalb der geprüften Schnittstelle
werden abgelehnt; verwenden Sie für Hostadministration bewusst das reale
Podman-Binary.

Richtlinienablehnungen für direktes Podman und Compose enden mit Code 125 und
zeigen den Grund sowie eine Zeile `next step:` ohne geheime Werte.
Build-Kontextfehler nennen alle
gefundenen Pfade auf einmal. Schließen Sie aktive dotenv-Dateien und übliche
Zugangsdatenpfade über `.containerignore` oder `.dockerignore` aus den kopierten
Build-Eingaben aus. `.git` ist im Build-Kontext erlaubt und bleibt im
Runtime-Workspace verfügbar, wo der Guard es standardmäßig mit einem separaten
read-only-Mount schützt.

Direkte geschützte Runs verwenden `--pull=never`; das Image muss daher bereits
vorhanden sein. DevPod besitzt eng geprüfte Image-Pull-/Build-Lifecycle-Formen.

Build-Kontext-Ausschlüsse prüfen und korrigieren:

```bash
paranoid-podman build-context audit .
paranoid-podman build-context protect .
```

`protect` bietet eine Pfadauswahl; `--all` übernimmt alle erkannten Ausschlüsse ohne
Rückfrage. Nur die Standard-Ignore-Datei wird geändert; die Richtlinie bleibt wirksam.

<a id="devpod"></a>

## DevPod

Neue DevPod-Container erhalten den Workspace-Namen als Hostnamen. Ein expliziter
Hostname bleibt erhalten; gekürzte oder normalisierte Namen erhalten einen kurzen Hash.
Bestehende Container müssen dafür neu erstellt werden.

Der Wrapper verarbeitet die geprüften Befehlsformen der Docker- und
Compose-Treiber von DevPod 0.6.15, darunter:

- Containererkennung, inspect, start, stop, logs, exec und Entfernung;
- Image inspect, pull, build, tag und push;
- der private Kopierablauf zur Einrichtung von `/etc/passwd` und `/etc/group`;
- Compose-Projektsuche, Projektnamen, Projekt-`.env` und generierte
  Override-Dateien; sowie
- Compose build, up, stop und down.

DevPod umgeht die gemeinsame Erstellungsrichtlinie nicht. Ein Workspace, der
privilegierten Modus, Host-Namespaces, Engine-Sockets, gefährliche Capabilities
oder einen breiten Host-Mount verlangt, wird weiterhin abgelehnt.

Schreibgeschützte Submounts und Provenance-Labels werden beim Erstellen eines
Containers gesetzt. Nach Installation dieses Mechanismus muss ein älterer
DevPod-Workspace vor Start oder Eintritt über den Wrapper neu erstellt werden.
Dasselbe gilt nach einer vollständigen Neuinstallation, da sie eine neue Kennung
erhält. Cleanup-Befehle bleiben verfügbar. Um `.git` beschreibbar zu halten,
starten Sie DevPod mit `PODMAN_GUARD_PROTECT_GIT=0`.

### Isolation von SSH-Zugangsdaten

DevPod wählt die IDE über seine Vorgaben, die Workspace-Einstellungen oder `--ide`.
Der Wrapper setzt keinen bestimmten Editor voraus. Öffnen und Wiederverbinden im
IDE-only-Modus wurden manuell mit Codium geprüft; andere IDEs und der vollständige
Compose-Treiberablauf benötigen weitere Tests. Mit Codium und Open Remote - SSH 0.1.2
wurde ein Problem mit der Lebensdauer des Projekt-Agent-Sockets beobachtet:
[DEV-001](../KNOWN_ISSUES.md#dev-001-vscode-loses-the-project-ssh-agent-socket).
Frische OpenSSH- und `devpod ssh`-Verbindungen wurden mit einem Projektschlüssel geprüft.

Interaktives Menü: **1** neuen Projektschlüssel erstellen, **2** IDE ohne Host-Zugangsdaten,
**3** vorhandenen Projektschlüssel wählen, **4** vollständigen Host-Agenten einmalig
mit exaktem `y` bestätigen, **5** abbrechen. Enter wählt Option 1; wählen Sie für
IDE-only ausdrücklich **2**.

Der Projektmodus startet einen eigenen `ssh-agent` mit genau einer geprüften
Identität. Der private Schlüssel wird weder kopiert noch in den Container
gemountet; Workspace-Code kann den Agenten jedoch zum Signieren auffordern.
Beschränken Sie den öffentlichen Schlüssel bei GitHub, GitLab oder Gitea auf ein
einziges Repository.

Geschützte Modi deaktivieren die automatische Weitergabe von Git- und
Registry-Zugangsdaten, GPG-Agent und SSH-Signaturschlüssel im gewählten
DevPod-Kontext. Ein eigenständiges `devpod build` ohne konfigurierten Modus fragt
nicht nach und leitet keine Host-Zugangsdaten in den Workspace weiter.

Damit DevPod die IDE nicht vor dem Schutz des SSH-Blocks öffnet, erstellt der
Wrapper den Workspace zuerst mit `--open-ide=false`, beschränkt den Block und
öffnet die IDE danach ohne erneute Container-Erstellung oder SSH-Konfigänderung.

Ein vorhandener privater Schlüssel muss ausschließlich für das Projekt bestimmt
sein und darf nicht direkt in `~/.ssh` liegen; ein Unterverzeichnis ist erlaubt.

```bash
paranoid-podman devpod audit WORKSPACE
paranoid-podman devpod configure WORKSPACE
paranoid-podman devpod key show WORKSPACE
paranoid-podman devpod key stop WORKSPACE
```

Der Zustand wird aus dem DevPod-SSH-Block und den Schlüsseln unter
`~/.ssh/paranoid-podman/` abgeleitet; eine separate Richtliniendatei wird nicht
erstellt. Details: [DevPod-Zugangsdatenisolation](../docs/devpod-credentials.md)
(Englisch).

Ersetzen Sie `WORKSPACE` durch eine vorhandene Workspace-ID. Verwenden Sie bei Bedarf
passende Werte für `--context`, `--devpod-home` und `--ssh-config`. Ein neuer lokaler
Projektpfad für `devpod up` muss existieren; andernfalls kann er als Repository-URL
interpretiert werden. Wählen Sie IDE-only beim ersten interaktiven `up`:
`configure` kann die Wahl erst nach Erstellung des DevPod-SSH-Blocks speichern.
Siehe [lokales Beispiel](../docs/devpod-credentials.md#open-a-local-workspace).

Geschützte Modi schalten auch die automatische Suche nach privaten Schlüsseln ab.
Die Kontexteinstellungen gelten für alle Workspaces dieses Kontexts.

<a id="compatibility"></a>

## Kompatibilität

| Komponente | Kompatibilität |
| --- | --- |
| Betriebssystem | Linux |
| Python | 3.10 und neuer |
| Podman | Rootless 6.1.x |
| Compose-Provider | `podman-compose` 1.6.x über `podman compose` |
| DevPod | Mit 0.6.15 getestet; andere Versionen werden akzeptiert; geprüfte Docker-/Compose-Befehle und IDE-Grenzen siehe oben |
| Docker Compose v2 | Nicht unterstützt |

Der Installer lehnt nicht geprüfte Podman- oder Compose-Major/Minor-Serien ab.
Wrapper hängen vom CLI-Verhalten des Providers ab; jede neue Serie erfordert
daher Prüfung und Kompatibilitätstests.

Die Kompatibilität anderer DevPod-Versionen wurde noch nicht geprüft. Die Prüfungen
für den Zugriff auf Zugangsdaten und zulässige Befehle bleiben aktiv.

<a id="limitations"></a>

## Einschränkungen

- Die Ignore-Prüfung verlangt den vollständigen Ausschluss sensibler Pfade im Wurzelverzeichnis.
  Ausnahmen für untergeordnete Dateien oder ungeprüfte Muster können einen letzten
  ausdrücklichen Ausschluss erfordern. Zusätzliche Image-Kontexte benötigen ein
  ausdrückliches Transportpräfix. Siehe [Build-Grenzen](../docs/direct-policy.md#build-boundary).
- Ein Agent kann mit dem Projektschlüssel signieren, ohne ihn offenzulegen. Der
  Git-Server muss den Repository-Zugriff begrenzen.
- Ein Wrapper in `PATH` ist keine Sandbox. Ein Hostprozess unter Ihrem Benutzer
  kann das reale Podman aufrufen oder direkt auf dieselben Dateien zugreifen.
- Der Wrapper schützt nicht vor Schwachstellen in Kernel, Podman, OCI-Runtime,
  Image oder Parser und nicht vor Zero-Day-Lücken.
- Normale Containernetzwerke sind keine Sandbox für ausgehenden Verkehr.
- Builds können beliebige Image-Anweisungen ausführen und jeden Context-Pfad
  lesen, der nicht durch `.containerignore` oder `.dockerignore` ausgeschlossen
  ist. Der Guard prüft nur übliche Credentials im Wurzelverzeichnis und
  offensichtliche literale Zuweisungen.
- Früher erstellte oder bereits laufende Container erhalten keine neuen
  Mount-Schutzregeln. Aktive geschützte Lifecycle-Befehle lehnen Container ohne
  aktuelle Policy- und Installations-Provenance ab.
- Provenance-Labels sind lokale Kompatibilitätsmarker, keine kryptografischen
  Signaturen. Ein bereits als Hostbenutzer laufender Prozess kann sie lesen,
  kopieren oder umgehen. Direkte Ausführung aus dem Source Tree ohne explizite
  Installations-ID verwendet einen deterministischen Entwicklungs-Fallback;
  nutzen Sie den Installer für installationsspezifische Provenance.
- Dateien können sich zwischen Validierung und Provider-Ausführung ändern.
- Die unterstützte Schnittstelle ist absichtlich kleiner als die vollständigen
  Podman- und Compose-CLIs.

Verwenden Sie eine kurzlebige VM oder ein separates Konto mit geringen Rechten,
wenn stärkere Isolation erforderlich ist.

<a id="development"></a>

## Entwicklung

Schnelle lokale Prüfungen:

```bash
scripts/test.sh
scripts/check.sh syntax
```

Lokale Tests verwenden Fake-Provider und temporäre Home-/Konfigurationsverzeichnisse; geerbte Integrationsfreigaben sind deaktiviert. `scripts/check.sh` verwendet standardmäßig `local` mit Ruff, mypy, Bandit, ShellCheck, offline ausgeführtem `zizmor` und einem Gitleaks-Quellscan mit geschwärzten Geheimnissen. `scripts/audit.sh dependencies` startet ausdrücklich die Netzwerkprüfung; `scripts/check.sh all` ergänzt diese, führt aber keine realen Integrationen aus.

`scripts/format.sh` wendet Formatierung an. Werkzeuge separat gemäß [CONTRIBUTING.md](../CONTRIBUTING.md) vorbereiten. CI trennt Tests, statische Prüfungen, Geheimnisprüfung und Abhängigkeitsprüfung.

Die Implementierung liegt unter `src/paranoid_podman`. Siehe [Codeübersicht](../docs/architecture.md) und [Anleitung zum Wheel-Bau und zur Offline-Installation](../docs/wheel-packaging.md).

| Verzeichnis | Zweck |
| --- | --- |
| `src/paranoid_podman/` | Anwendungscode |
| `bin/` | Kleine Launcher für Quellcode-Ausführung und Tests |
| `dist/` | Generiertes Wheel und Prüfsumme |

Der Installer erzeugt die Befehle in `--bindir` separat; sie nutzen die private
Python-Umgebung. `bin/` bleibt Bestandteil des Quellbaums.

`scripts/test.sh compose-provider` wählt ausdrücklich die Prüfung mit dem realen Compose-Provider. Sie kann Podman abfragen, startet aber keine Container; eine temporäre Umgebung mit den geprüften Versionen verwenden.
Diese Tests prüfen außerdem alltägliche Abläufe mit echtem Compose und einem
aufzeichnenden Ersatz für die Engine; sie laufen auch in CI.

Die optionale Runtime-Suite benötigt ein vorhandenes lokales Image mit `sh` und
`sleep`. Sie lädt niemals ein Image herunter und verwendet nur eindeutig benannte
kurzlebige Container:

```bash
PARANOID_PODMAN_TEST_IMAGE=docker.io/library/alpine:latest \
  scripts/test.sh rootless
```

Führen Sie sie nur mit den geprüften Podman- und Compose-Versionen in einem
kurzlebigen Rootless-Konto ohne wertvolle Container oder Credentials aus.

Der SSH-Agent-Test erzeugt einen temporären Schlüssel und Socket und liest keine
normalen SSH-Schlüssel:

```bash
scripts/test.sh ssh-agent
```

Verbleibende Arbeiten stehen in [TODO.md](../TODO.md), Hinweise für Beiträge in
[CONTRIBUTING.md](../CONTRIBUTING.md).

<a id="security"></a>

## Sicherheit

Melden Sie vermutete Schwachstellen nach dem Verfahren in
[SECURITY.md](../SECURITY.md). Veröffentlichen Sie keine aktiven Credentials,
privaten Projektdaten oder Exploitdetails in einem öffentlichen Issue.

<a id="license"></a>

## Lizenz

[MIT](../LICENSE)
