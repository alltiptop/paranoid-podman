[en English](../README.md) · [ru Русский](README.ru.md) · [es Español](README.es.md) · [pl Polski](README.pl.md) · [uk Українська](README.uk.md) · [de Deutsch](README.de.md) · [fr Français](README.fr.md) · [zh-CN 简体中文](README.zh-CN.md) · [ar العربية](README.ar.md) · [he עברית](README.he.md)

# paranoid-podman

`paranoid-podman` to eksperymentalna nakładka użytkownika na rootless Podman i
`podman-compose`. Jest przeznaczona dla programistów, którzy czasami uruchamiają
polecenia kontenerów z projektów, którym nie ufają w pełni.

Projekt dodaje proste, bezpośrednie zabezpieczenia przed typowymi próbami wyjścia
kontenera poza oczekiwane granice i błędami dostępu do hosta, zachowując zwykły
lokalny sposób pracy. Nie jest to antywirus, korporacyjny produkt
bezpieczeństwa ani kompletna piaskownica.

> [!WARNING]
> Jest to ochrona warstwowa, a nie absolutna. Może nie zatrzymać exploitów zero-day,
> nieznanych technik ataku, luk w jądrze lub runtime ani procesu, który już ma
> dostęp do konta użytkownika na hoście.

## Spis treści

- [O projekcie](#about)
- [Co chroni](#what-it-protects)
- [Instalacja](#install)
- [Użycie](#usage)
- [DevPod](#devpod)
- [Zgodność](#compatibility)
- [Ograniczenia](#limitations)
- [Rozwój](#development)
- [Bezpieczeństwo](#security)
- [Licencja](#license)

<a id="about"></a>

## O projekcie

Przepływ polecenia wygląda następująco:

```text
polecenie podman / compose -> parser -> polityka -> zweryfikowany prawdziwy provider
```

Bezpieczne argumenty są zachowywane. Niebezpieczny dostęp do hosta jest odrzucany,
dodawane są wybrane bezpieczne wartości domyślne, a chronione pliki projektu są
montowane tylko do odczytu w nowych kontenerach.

Projekt jest celowo prosty:

- blokuje najbardziej bezpośrednie drogi do uprawnień i dostępu do hosta;
- zachowuje normalną pracę, jeśli pozostaje ona w granicach projektu;
- preferuje jawne, zrozumiałe ograniczenia montowania zamiast skanerów treści;
- odmawia działania, gdy forma polecenia istotna dla bezpieczeństwa jest nieznana; oraz
- uczciwie dokumentuje pozostałe granice ochrony.

<a id="what-it-protects"></a>

## Co chroni

| Dane wejściowe lub zachowanie | Działanie |
| --- | --- |
| Tryb privileged, namespace hosta lub dołączony, dodane capabilities, urządzenia, dowolne security options | Odrzuć |
| Sockety silnika Podman/Docker i wrażliwe ścieżki runtime hosta | Odrzuć |
| Root systemu plików, cały katalog domowy, szerokie drzewa systemowe lub bind szerszy niż projekt | Odrzuć |
| Jawnie wskazane pojedyncze zwykłe pliki i katalogi | Zezwól i kanonikalizuj |
| Opcje bind zmieniające właściciela lub etykiety hosta, np. `U`, `idmap` lub niebezpieczny relabeling | Odrzuć |
| Publikowane porty | Zachowaj zwykłe mapowania TCP/UDP, także na wszystkich interfejsach i adresach sieci lokalnej |
| Odziedziczone credentials, endpointy pulpitu/sesji i zmienne routingu providera | Usuń lub odrzuć |
| Istniejąca chroniona konfiguracja projektu pod zapisywalnym bindem | Dodaj submounty tylko do odczytu |
| `start`, `exec` lub aktywny lifecycle Compose na starym albo obcym kontenerze | Odrzuć do ponownego utworzenia przez bieżącą instalację i politykę |
| Konfiguracja Compose | Rozwiąż, zweryfikuj, przepisz i wykonaj prywatny sprawdzony snapshot |
| Jawnie zapisane sekrety w Compose lub przypisaniach Dockerfile | Odrzuć bez ujawniania wartości |
| Credentials w root build context nieobjęte plikiem ignore | Odrzuć przed uruchomieniem buildera |

Bezpośrednie polecenia `run` i `create` otrzymują również zgodne wartości domyślne,
takie jak `no-new-privileges`, limit PID, wyłączone automatyczne przekazywanie proxy,
nietrwała polityka restartu i brak niejawnego pobierania obrazu. Nieznane opcje
runtime nie są przekazywane dalej.

### Chroniona konfiguracja projektu

Wykryte ścieżki o następujących nazwach są tylko do odczytu w nowych kontenerach:

- `.devcontainer`, `.devcontainer.json`, `devcontainer.json`;
- `.dockerignore`, `.containerignore`;
- aktywne pliki dotenv i popularne pliki konfiguracji credentials; oraz
- `.git`, `.gitmodules`, `.git-credentials`.

Brakujące ścieżki nie są tworzone. Zawartość devcontainera nie jest skanowana ani
przepisywana: ochrona jest regułą montowania systemu plików. Dockerfile otrzymuje
jedynie małą kontrolę literal secrets opisaną niżej.

Pliki Dockerfile, Containerfile i Compose poza `.devcontainer` można edytować
wewnątrz kontenera. Cała zawartość `.devcontainer` pozostaje tylko do odczytu.
Odtwórz istniejące kontenery, aby zastosować nowe uprawnienia montowania.

Nieczytelne katalogi należące do innego UID, na przykład dane bazy rootless,
są montowane bez przeglądania zawartości i zmiany uprawnień. Pliki wewnątrz
nie są objęte automatyczną ochroną przed zapisem.

Domyślnie `.git` jest tylko do odczytu. Dla jednego wywołania można pozostawić go
zapisywalnym bez osłabiania innych chronionych ścieżek:

```bash
PODMAN_GUARD_PROTECT_GIT=0 podman compose up
```

### Weryfikacja Compose

Adapter Compose:

1. wstępnie sprawdza pliki źródłowe i renderuje konfigurację po podstawieniu wartości;
2. sprawdza dostęp do hosta, namespaces, mounty, zasoby i obsługiwane pola;
3. zapisuje prywatny snapshot o trybie `0600` z zakończoną interpolacją;
4. uruchamia wyłącznie ten sprawdzony snapshot.

Montowania wewnątrz projektu pozostają ciche. Przed `up` lub `run` dostęp do plików
lub katalogów hosta poza projektem wymaga potwierdzenia: pomarańczowe `[warning]`
pokazuje pełną ścieżkę, usługę, tryb dostępu i plik Compose z numerem wiersza. Wpisz
dokładnie `y` i Enter dla tego polecenia. Enter, inna odpowiedź lub EOF anuluje; bez
wejścia terminala operacja jest blokowana bez odczytu stdin. Wybór nie jest zapisywany.
Podgląd, usuwanie i polecenia dla istniejących kontenerów nie wymagają potwierdzenia.
Nie można w ten sposób zezwolić na zabronione ścieżki ani gniazda.

`PODMAN_GUARD_DEBUG=1` pokazuje podsumowanie bez poufnych wartości. Terminal i
automatyzacja podlegają tym samym regułom. Odmowy wyróżnia nagłówek `BLOCKED`,
`UNSUPPORTED` lub `ERROR`, z powodem i nazwą usługi, jeśli jest znana.

Diagnostyka Compose nie drukuje rozwiązanych wartości środowiska ani surowych
błędów providera. Kontrola literal secrets jest celowo mała i oparta na nazwach;
nie jest ogólnym skanerem sekretów.

Nowe kontenery direct i Compose otrzymują dwie zarezerwowane etykiety provenance:
generację polityki i losowy identyfikator lokalnej instalacji. Operacje, które mogą
uruchomić, wznowić lub wykonać kod, wymagają zgodności obu etykiet istniejącego
kontenera. Dzięki temu obraz nie przejdzie kontroli przez samo zadeklarowanie
publicznej etykiety polityki. Inspekcja tylko do odczytu i cleanup pozostają
dostępne, aby bezpiecznie diagnozować i usuwać stare kontenery.

Dokładne interfejsy opisują
[polityka bezpośrednia Podman](../docs/direct-policy.md),
[polityka Compose](../docs/compose-policy.md) i
[model zagrożeń](../docs/threat-model.md).

<a id="install"></a>

## Instalacja

Potrzebne są Linux z rootless Podman 6.1.x, `podman-compose` 1.6.x, Python 3.10
lub nowszy z obsługą środowisk wirtualnych oraz Bash. Dla DevPod potrzebne są też
DevPod i narzędzia klienckie OpenSSH. Przetestowano DevPod 0.6.15;
inne wersje są akceptowane. W repozytorium, jako zwykły użytkownik:

```bash
./install.sh install
```

Bez argumentów `./install.sh` pokazuje stan i menu: `install` dla nowej
instalacji lub `update` / `uninstall` dla istniejącej. Enter kończy działanie bez
zmian. Bez interaktywnego terminala wyświetlane są tylko stan i dostępne działania;
w skryptach podawaj polecenie jawnie.

`./install.sh --help` opisuje opcje według poleceń. Opcje dostawców i pakietów
wheel dotyczą `install` / `update`; `--without-devpod` i `--backup-existing`
tylko `install`, a `--devpod-ssh-config` tylko `uninstall`. Zwykła instalacja
nie wymaga dodatkowych opcji.

Instalator znajduje Podman, Compose i dostępny DevPod, pobiera narzędzia Python
oraz zależności, buduje aplikację i instaluje ją w prywatnym środowisku. Nie trzeba
samodzielnie przygotowywać pakietów, sum kontrolnych ani środowisk Python.
Istniejące polecenia są archiwizowane po potwierdzeniu i przywracane przy usuwaniu.

Podgląd bez pobierania i zmiany plików:

```bash
./install.sh install --dry-run
```

### Położenie poleceń

Domyślny katalog poleceń to `${XDG_BIN_HOME:-$HOME/.local/bin}`.
Umieść go na początku `PATH`, aby używać chronionych poleceń:

```bash
export PATH="${XDG_BIN_HOME:-$HOME/.local/bin}:$PATH"
command -v podman docker compose-guard
```

Jeśli katalog nie jest jeszcze pierwszy, dodaj wiersz `export` do konfiguracji
powłoki. Aplikacja znajduje się w `${XDG_DATA_HOME:-$HOME/.local/share}/paranoid-podman`.

### Aktualizacja i usuwanie

Uruchamiaj z repozytorium zawierającego wybraną wersję aplikacji:

```bash
./install.sh update
./install.sh status
```

Usunięcie instalacji i przywrócenie zapisanych poleceń:

```bash
./install.sh uninstall
```

Uruchom `./uninstall.sh`, aby sprawdzić stan i zobaczyć menu `uninstall` /
`Exit`. Enter kończy działanie bez zmian; bez interaktywnego terminala skrypt
tylko wyświetla informacje. `./uninstall.sh --help` opisuje opcje usuwania, a
`./uninstall.sh --dry-run` pokazuje planowane działania po wyborze w menu.

`--dry-run` działa też przy aktualizacji i usuwaniu. Zmienione pliki instalacji są zgłaszane zamiast usuwane. Aby dodać
DevPod do instalacji bez niego, usuń aplikację i zainstaluj ponownie po udostępnieniu
DevPod. Chronione kontenery trzeba wtedy utworzyć ponownie.

### Ustawienia opcjonalne

`--without-devpod` pozostawia tylko ochronę Podman i Compose. `--devpod PATH`
wskazuje program poza `PATH`; `--podman` i `--compose-provider` zastępują automatyczne
wyszukiwanie. `--backup-existing` pozwala na kopie zapasowe bez interaktywnego
potwierdzenia. Dla własnych katalogów używaj spójnych `--bindir` i `--libdir`.

Konfiguracje SSH w domyślnym katalogu DevPod są przywracane automatycznie.
Dla kontekstów w innym `DEVPOD_HOME` podaj jawnie każdą niestandardową ścieżkę:

```bash
./install.sh uninstall --devpod-ssh-config /absolute/path/to/ssh-config
```

[Zaawansowana instrukcja instalacji offline](../docs/wheel-packaging.md) opisuje
przygotowane pakiety. Zwykła instalacja nie wymaga tych opcji.

<a id="usage"></a>

## Użycie

Uruchamiaj polecenia z istniejącego katalogu projektu. Zastąp
`localhost/my-dev-image:latest` obrazem dostępnym lokalnie: bezpośredni start
używa `--pull=never`. Compose wymaga pliku konfiguracji i usługi o nazwie `app`.

```bash
podman run --rm -v .:/workspace localhost/my-dev-image:latest
podman compose up
podman compose ps
podman compose exec app sh
podman compose run --rm --build app
podman compose down
```

Jednorazowe zadania Compose obsługują profile, `--no-deps` oraz opcje środowiska,
użytkownika, katalogu roboczego i portów. `run --build` zatrzymuje się po błędzie
budowania. Dostępne są `up --force-recreate` i jawny reset `down --volumes
--remove-orphans`; ten drugi usuwa wolumeny projektu.

Dla zgodnych workflow instalowane są aliasy `docker`, `podman-compose` i
`docker-compose`. Polecenia spoza sprawdzonego interfejsu są odrzucane; do
administracji hostem świadomie wywołuj prawdziwy plik binarny Podman.

Odrzucenia polityki bezpośredniego Podman i Compose kończą się kodem 125 i pokazują
powód oraz linię `next step:` bez tajnych wartości.
Błędy kontekstu build zgłaszają
od razu wszystkie wykryte ścieżki. Wyklucz aktywne pliki dotenv i typowe ścieżki
poświadczeń z kopiowanych danych wejściowych przez `.containerignore` lub
`.dockerignore`. `.git` jest dozwolony w kontekście build i pozostaje dostępny
w runtime workspace, gdzie guard domyślnie chroni go osobnym mountem read-only.

Bezpośrednie chronione run używają `--pull=never`, więc obraz musi już istnieć.
DevPod ma wąsko sprawdzone formy lifecycle pull/build obrazu.

Sprawdź i popraw wykluczenia kontekstu budowania:

```bash
paranoid-podman build-context audit .
paranoid-podman build-context protect .
```

`protect` pozwala wybrać ścieżki; `--all` stosuje wszystkie wykryte wykluczenia bez
pytania. Zmienia tylko standardowy plik ignore i nie omija polityki.

<a id="devpod"></a>

## DevPod

Nowe kontenery DevPod otrzymują nazwę workspace jako hostname. Jawny hostname pozostaje
bez zmian; skrócone lub znormalizowane nazwy otrzymują krótki hash. Istniejące kontenery
wymagają ponownego utworzenia.

Nakładka obsługuje sprawdzone formy poleceń sterowników Docker i Compose
w DevPod 0.6.15, w tym:

- wykrywanie, inspect, start, stop, logs, exec i usuwanie kontenera;
- inspect, pull, build, tag i push obrazu;
- prywatny przepływ kopiowania konfiguracji `/etc/passwd` i `/etc/group`;
- wyszukiwanie projektu Compose, nazwy projektu, projektowy `.env` i generowane
  pliki override; oraz
- operacje Compose build, up, stop i down.

DevPod nie omija wspólnej polityki tworzenia. Workspace żądający trybu privileged,
namespace hosta, socketów silnika, niebezpiecznych capabilities lub szerokiego
mountu hosta nadal jest odrzucany.

Submounty tylko do odczytu i etykiety provenance są stosowane przy tworzeniu
kontenera. Po instalacji mechanizmu utwórz ponownie starszy workspace DevPod przed
startem lub wejściem przez wrapper. Dotyczy to również świeżej reinstalacji, która
otrzymuje nowy identyfikator. Polecenia cleanup pozostają dostępne. Aby `.git` był
zapisywalny, uruchom DevPod z `PODMAN_GUARD_PROTECT_GIT=0`.

### Izolacja poświadczeń SSH

DevPod wybiera IDE według ustawień domyślnych, konfiguracji środowiska pracy lub
parametru `--ide`. Nakładka nie wymaga konkretnego edytora. Otwieranie i ponowne
łączenie w trybie IDE-only sprawdzono ręcznie w Codium; inne IDE i pełny przebieg
sterownika Compose nadal wymagają testów. W połączeniu Codium z Open Remote - SSH 0.1.2
zaobserwowano problem z czasem życia gniazda agenta projektu:
[DEV-001](../KNOWN_ISSUES.md#dev-001-vscode-loses-the-project-ssh-agent-socket).
Nowe połączenia OpenSSH i `devpod ssh` sprawdzono z jednym kluczem projektu.

Menu interaktywne: **1** utwórz klucz projektu, **2** IDE bez poświadczeń hosta,
**3** wybierz istniejący klucz projektu, **4** udostępnij pełnego agenta hosta na
jeden raz po dokładnym potwierdzeniu `y`, **5** anuluj. Enter wybiera opcję 1;
dla IDE-only wybierz jawnie **2**.

Tryb projektu uruchamia dedykowany `ssh-agent` z dokładnie jedną zweryfikowaną
tożsamością. Klucz prywatny nie jest kopiowany ani montowany w kontenerze, lecz
kod workspace może prosić agenta o podpis. Ogranicz klucz publiczny do jednego
repozytorium w GitHub, GitLab lub Gitea.

Tryby chronione wyłączają automatyczne przekazywanie poświadczeń Git i registry,
GPG-agent oraz klucza podpisu SSH w wybranym kontekście DevPod. Samodzielny
`devpod build` bez skonfigurowanego trybu nie wyświetla pytania i nie przekazuje
poświadczeń hosta do workspace.

Aby DevPod nie otworzył IDE przed zabezpieczeniem bloku SSH, wrapper najpierw
tworzy workspace z `--open-ide=false`, ogranicza blok, a potem otwiera IDE bez
ponownego tworzenia kontenera i bez kolejnego zapisu konfiguracji SSH.

Istniejący klucz prywatny musi być przeznaczony tylko dla projektu i znajdować
się poza samym katalogiem `~/.ssh`; podkatalog jest dozwolony.

```bash
paranoid-podman devpod audit WORKSPACE
paranoid-podman devpod configure WORKSPACE
paranoid-podman devpod key show WORKSPACE
paranoid-podman devpod key stop WORKSPACE
```

Stan jest wyprowadzany z bloku SSH DevPod i kluczy w
`~/.ssh/paranoid-podman/`; osobny plik polityki nie powstaje. Szczegóły:
[izolacja poświadczeń DevPod](../docs/devpod-credentials.md) (po angielsku).

Zastąp `WORKSPACE` istniejącym identyfikatorem środowiska pracy. W razie potrzeby
podaj pasujące `--context`, `--devpod-home` i `--ssh-config`. Dla nowego projektu
lokalnego podaj `devpod up` istniejący katalog; nieistniejąca ścieżka może zostać
uznana za URL repozytorium. Wybierz IDE-only podczas pierwszego interaktywnego `up`:
`configure` nie zapisze wyboru, dopóki DevPod nie utworzy bloku SSH. Zobacz
[przykład lokalny](../docs/devpod-credentials.md#open-a-local-workspace).

Tryby chronione wyłączają także automatyczne wyszukiwanie kluczy prywatnych.
Ustawienia kontekstu dotyczą wszystkich jego środowisk pracy.

<a id="compatibility"></a>

## Zgodność

| Komponent | Zgodność |
| --- | --- |
| System operacyjny | Linux |
| Python | 3.10 i nowszy |
| Podman | Rootless 6.1.x |
| Provider Compose | `podman-compose` 1.6.x przez `podman compose` |
| DevPod | Przetestowano 0.6.15; inne wersje są akceptowane; sprawdzone polecenia Docker/Compose i ograniczenia IDE powyżej |
| Docker Compose v2 | Nieobsługiwany |

Installer odrzuca niesprawdzoną serię major/minor Podman lub Compose. Wrappery
zależą od zachowania CLI providera, dlatego każda nowa seria wymaga przeglądu i
testów zgodności.

Zgodność innych wersji DevPod nie została jeszcze zweryfikowana. Kontrole dostępu
do danych uwierzytelniających i dozwolonych poleceń pozostają aktywne.

<a id="limitations"></a>

## Ograniczenia

- Sprawdzanie ignore wymaga całkowitego wykluczenia wrażliwych ścieżek w katalogu głównym.
  Wyjątki dla plików potomnych lub niezweryfikowane wzorce mogą wymagać końcowej
  jawnej reguły wykluczającej. Dodatkowe konteksty obrazów wymagają jawnego prefiksu
  transportu. Zobacz [ograniczenia budowania](../docs/direct-policy.md#build-boundary).
- Agent może podpisywać dane kluczem projektu bez ujawniania klucza prywatnego.
  Serwer Git musi ograniczać zakres dostępu do repozytorium.
- Wrapper w `PATH` nie jest sandboxem. Proces hosta działający jako użytkownik
  może wywołać prawdziwy Podman lub bezpośrednio uzyskać dostęp do tych samych plików.
- Wrapper nie chroni przed lukami w jądrze, Podman, OCI runtime, obrazie, parserze
  ani przed zero-day.
- Zwykła sieć kontenera nie jest sandboxem ruchu wychodzącego.
- Build może wykonywać dowolne instrukcje obrazu i czytać każdą ścieżkę context,
  której nie wyklucza `.containerignore` lub `.dockerignore`. Guard sprawdza tylko
  popularne ścieżki credentials w root i oczywiste przypisania literalne.
- Wcześniej utworzone lub już działające kontenery nie otrzymują nowych zabezpieczeń
  mount. Chroniony lifecycle odrzuca kontenery bez bieżącej provenance polityki i
  instalacji.
- Etykiety provenance są lokalnymi znacznikami zgodności, nie podpisami
  kryptograficznymi. Proces już działający jako użytkownik hosta może je odczytać,
  skopiować lub ominąć. Bezpośrednie uruchomienie ze źródeł bez jawnego installation
  ID używa deterministycznej wartości developerskiej; użyj installera dla
  provenance konkretnej instalacji.
- Pliki mogą zmienić się między walidacją a uruchomieniem providera.
- Obsługiwany interfejs jest celowo mniejszy niż pełne CLI Podman i Compose.

Gdy potrzebna jest silniejsza izolacja, użyj jednorazowej VM lub oddzielnego konta
o niskich uprawnieniach.

<a id="development"></a>

## Rozwój

Szybkie kontrole lokalne:

```bash
scripts/test.sh
scripts/check.sh syntax
```

Testy lokalne używają fałszywych providerów i tymczasowych katalogów home/config; odziedziczone zgody na integracje są wyłączone. `scripts/check.sh` domyślnie wybiera `local` z Ruff, mypy, Bandit, ShellCheck, `zizmor` offline i skanowaniem źródeł przez Gitleaks z ukrytymi sekretami. `scripts/audit.sh dependencies` jawnie uruchamia audyt sieciowy; `scripts/check.sh all` dodaje go bez uruchamiania rzeczywistych integracji.

`scripts/format.sh` stosuje formatowanie. Przygotuj narzędzia osobno zgodnie z [CONTRIBUTING.md](../CONTRIBUTING.md). CI rozdziela testy, analizę statyczną, sekrety i audyt zależności.

Implementacja znajduje się w `src/paranoid_podman`. Zobacz [mapę kodu](../docs/architecture.md) i [instrukcję budowania i instalacji wheel offline](../docs/wheel-packaging.md).

| Katalog | Przeznaczenie |
| --- | --- |
| `src/paranoid_podman/` | Kod aplikacji |
| `bin/` | Małe programy uruchamiające źródła i testy |
| `dist/` | Wygenerowany wheel i suma kontrolna |

Instalator osobno tworzy polecenia w `--bindir`; korzystają one z prywatnego
środowiska Python. `bin/` pozostaje częścią źródeł.

`scripts/test.sh compose-provider` wybiera kontrolę z rzeczywistym providerem Compose. Może ona odpytać Podman, ale nie uruchamia kontenerów; używaj jednorazowego środowiska ze sprawdzonymi wersjami.
Testy obejmują też codzienne scenariusze z prawdziwym Compose i atrapą silnika
zapisującą wywołania; dodano je do CI.

Opcjonalny zestaw testów z rzeczywistymi kontenerami wymaga istniejącego lokalnego obrazu z `sh` i `sleep`.
Nigdy nie pobiera obrazu i używa wyłącznie unikalnie nazwanych kontenerów
jednorazowych:

```bash
PARANOID_PODMAN_TEST_IMAGE=docker.io/library/alpine:latest \
  scripts/test.sh rootless
```

Uruchamiaj go tylko ze sprawdzonymi wersjami Podman i Compose na jednorazowym
koncie rootless bez wartościowych kontenerów i credentials.

Test agenta SSH tworzy tymczasowy klucz i gniazdo, nie odczytując zwykłych kluczy SSH:

```bash
scripts/test.sh ssh-agent
```

Pozostałe prace opisuje [TODO.md](../TODO.md), a zasady współtworzenia
[CONTRIBUTING.md](../CONTRIBUTING.md).

<a id="security"></a>

## Bezpieczeństwo

Podejrzane luki zgłaszaj zgodnie z [SECURITY.md](../SECURITY.md). Nie publikuj
aktywnych credentials, prywatnych danych projektu ani szczegółów exploita w
publicznym issue.

<a id="license"></a>

## Licencja

[MIT](../LICENSE)
