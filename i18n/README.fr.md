[en English](../README.md) · [ru Русский](README.ru.md) · [es Español](README.es.md) · [pl Polski](README.pl.md) · [uk Українська](README.uk.md) · [de Deutsch](README.de.md) · [fr Français](README.fr.md) · [zh-CN 简体中文](README.zh-CN.md) · [ar العربية](README.ar.md) · [he עברית](README.he.md)

# paranoid-podman

`paranoid-podman` est un wrapper expérimental au niveau utilisateur pour Podman
rootless et `podman-compose`. Il s'adresse aux développeurs qui exécutent parfois
des commandes de conteneur depuis des projets auxquels ils ne font pas entièrement
confiance.

Le projet ajoute des garde-fous simples et directs contre les erreurs courantes
d'évasion de conteneur et d'accès à l'hôte, tout en préservant les workflows de
développement local habituels. Ce n'est ni un antivirus, ni un produit de sécurité
d'entreprise, ni une sandbox complète.

> [!WARNING]
> Il s'agit d'une défense en profondeur, pas d'une protection absolue. Elle peut
> ne pas arrêter les exploits zero-day, les techniques d'attaque inconnues, les
> vulnérabilités du noyau ou du runtime, ni un processus qui possède déjà un accès
> au compte utilisateur de l'hôte.

## Sommaire

- [À propos](#about)
- [Ce qui est protégé](#what-it-protects)
- [Installation](#install)
- [Utilisation](#usage)
- [DevPod](#devpod)
- [Compatibilité](#compatibility)
- [Limites](#limitations)
- [Développement](#development)
- [Sécurité](#security)
- [Licence](#license)

<a id="about"></a>

## À propos

Le flux d'une commande est le suivant :

```text
commande podman / compose -> analyseur -> politique -> fournisseur réel vérifié
```

Les arguments sûrs sont conservés. Les accès dangereux à l'hôte sont refusés,
des valeurs de durcissement sont ajoutées et les fichiers de projet protégés sont
montés en lecture seule dans les nouveaux conteneurs.

La conception est volontairement directe :

- bloquer les voies les plus directes vers les privilèges et l'accès à l'hôte ;
- conserver un développement normal tant qu'il reste dans les limites du projet ;
- préférer des restrictions de montage explicites et compréhensibles aux scanners de contenu ;
- échouer de manière fermée lorsqu'une forme de commande sensible est inconnue ; et
- documenter honnêtement les limites restantes.

<a id="what-it-protects"></a>

## Ce qui est protégé

| Entrée ou comportement | Action |
| --- | --- |
| Mode privilégié, namespaces de l'hôte ou joints, capabilities ajoutées, périphériques, options de sécurité arbitraires | Refuser |
| Sockets du moteur Podman/Docker et chemins runtime sensibles de l'hôte | Refuser |
| Racine du système de fichiers, home utilisateur complet, larges arbres système ou bind plus large que le projet | Refuser |
| Fichiers et répertoires ordinaires individuels explicitement indiqués | Autoriser et canonicaliser |
| Options bind modifiant la propriété ou les labels de l'hôte, comme `U`, `idmap` ou un relabeling dangereux | Refuser |
| Ports publiés | Conserver les mappages TCP/UDP habituels, y compris sur toutes les interfaces et les adresses du réseau local |
| Credentials ambiants, endpoints de bureau/session et variables de routage du fournisseur | Supprimer ou refuser |
| Configuration de projet protégée existante sous un bind inscriptible | Ajouter des sous-montages en lecture seule |
| `start`, `exec` ou lifecycle Compose actif sur un conteneur ancien ou étranger | Refuser jusqu'à sa recréation par l'installation et la politique actuelles |
| Configuration Compose | Résoudre, valider, réécrire et exécuter un snapshot privé vérifié |
| Secrets littéraux évidents dans Compose ou des affectations Dockerfile | Refuser en masquant les valeurs |
| Credentials à la racine du build context non couverts par un fichier ignore | Refuser avant le builder |

Les commandes directes `run` et `create` reçoivent aussi des valeurs compatibles
par défaut : `no-new-privileges`, une limite de PID, la transmission automatique
du proxy désactivée, une politique de redémarrage non persistante et aucun pull
implicite. Les options runtime inconnues ne sont pas transmises.

### Configuration de projet protégée

Les chemins détectés portant les noms suivants sont en lecture seule dans les
nouveaux conteneurs :

- `.devcontainer`, `.devcontainer.json`, `devcontainer.json` ;
- `.dockerignore`, `.containerignore` ;
- fichiers dotenv actifs et fichiers courants de configuration de credentials ; et
- `.git`, `.gitmodules`, `.git-credentials`.

Les chemins absents ne sont pas créés. Le contenu devcontainer n'est ni analysé
ni réécrit : cette protection est une règle de montage du système de fichiers.
Les Dockerfiles ne reçoivent que la petite vérification des secrets littéraux
décrite plus bas.

Les fichiers Dockerfile, Containerfile et Compose hors de `.devcontainer` restent
modifiables dans le conteneur. Tout le contenu de `.devcontainer` reste en lecture
seule. Recréez les conteneurs existants pour appliquer les nouveaux droits de montage.

Les répertoires illisibles appartenant à un autre UID, comme les données d'une
base rootless, sont montés sans parcourir leur contenu ni modifier leurs droits.
Leurs fichiers ne bénéficient pas de la protection automatique contre l'écriture.

`.git` est en lecture seule par défaut. Il peut rester inscriptible pour un seul
appel sans affaiblir les autres chemins protégés :

```bash
PODMAN_GUARD_PROTECT_GIT=0 podman compose up
```

### Vérification de Compose

L'adaptateur Compose :

1. pré-vérifie les fichiers sources et produit la configuration résolue ;
2. valide l'accès à l'hôte, les namespaces, montages, ressources et champs pris en charge ;
3. écrit un snapshot privé en mode `0600`, avec interpolation déjà résolue ;
4. exécute uniquement ce snapshot vérifié.

Les montages internes au projet restent silencieux. Avant `up` ou `run`, l’accès
à un fichier ou répertoire de l’hôte hors du projet nécessite une confirmation :
un `[warning]` orange indique le chemin complet, le service, le mode d’accès et le
fichier Compose avec le numéro de ligne. Saisissez exactement `y` puis Entrée pour
cette commande. Entrée, une autre réponse ou EOF annule ; sans entrée de terminal,
l’opération est bloquée sans lire stdin. Le choix n’est pas enregistré. L’inspection,
la suppression et les commandes pour les conteneurs existants ne demandent pas de
confirmation. Les chemins et sockets interdits ne peuvent pas être autorisés ainsi.

`PODMAN_GUARD_DEBUG=1` affiche un résumé sans valeurs sensibles.
Le terminal et l’automatisation suivent les mêmes règles. Les refus affichent `BLOCKED`,
`UNSUPPORTED` ou `ERROR`, avec la raison et le service concerné lorsqu’il est connu.

Les diagnostics Compose n'affichent ni les valeurs d'environnement résolues ni
les erreurs brutes du fournisseur. La vérification des secrets littéraux est
volontairement petite et basée sur les noms ; ce n'est pas un scanner général.

Les nouveaux conteneurs directs et Compose reçoivent deux labels de provenance
réservés : la génération de politique et un identifiant aléatoire de l'installation
locale. Les opérations capables de démarrer, reprendre ou exécuter du code exigent
que les deux correspondent avant d'utiliser un conteneur existant. Une image ne
peut donc pas passer le contrôle en déclarant seulement le label public de
politique. L'inspection en lecture seule et le cleanup restent disponibles pour
diagnostiquer et supprimer les anciens conteneurs.

Les interfaces précises sont décrites dans la
[politique Podman directe](../docs/direct-policy.md), la
[politique Compose](../docs/compose-policy.md) et le
[modèle de menace](../docs/threat-model.md).

<a id="install"></a>

## Installation

Il faut Linux avec Podman rootless 6.1.x, `podman-compose` 1.6.x, Python 3.10 ou
ultérieur avec les environnements virtuels et Bash. Pour DevPod, il faut aussi
DevPod et les outils clients OpenSSH. DevPod 0.6.15 a été testé ; les autres
versions sont acceptées. Depuis le dépôt, avec votre compte habituel :

```bash
./install.sh install
```

Sans argument, `./install.sh` affiche l’état et un menu : `install` pour
une nouvelle installation, sinon `update` / `uninstall`. Appuyez sur Entrée
pour quitter sans modification. Sans terminal interactif, seuls l’état et les
actions disponibles sont affichés ; dans les scripts, précisez l’action.

`./install.sh --help` décrit les options par action. Les options de fournisseurs
et de wheels concernent `install` / `update` ; `--without-devpod` et
`--backup-existing` concernent uniquement `install`, et `--devpod-ssh-config`
uniquement `uninstall`. Une installation normale ne nécessite aucune option.

Le programme d’installation trouve Podman, Compose et DevPod s’il est présent,
télécharge les outils Python et les dépendances, construit l’application et
l’installe dans un environnement privé. Vous n’avez pas à préparer de paquets,
de sommes de contrôle ou d’environnements Python. Les commandes existantes sont
sauvegardées après confirmation et restaurées à la désinstallation.

Aperçu sans téléchargement ni modification de fichiers :

```bash
./install.sh install --dry-run
```

### Emplacement des commandes

Le répertoire des commandes par défaut est `${XDG_BIN_HOME:-$HOME/.local/bin}`.
Placez-le au début de `PATH` pour utiliser les commandes protégées :

```bash
export PATH="${XDG_BIN_HOME:-$HOME/.local/bin}:$PATH"
command -v podman docker compose-guard
```

S’il n’est pas déjà en premier, ajoutez la ligne `export` à la configuration de
votre shell. L’application réside dans
`${XDG_DATA_HOME:-$HOME/.local/share}/paranoid-podman`.

### Mise à jour ou désinstallation

Exécutez depuis le dépôt contenant la version souhaitée :

```bash
./install.sh update
./install.sh status
```

Désinstaller et restaurer les commandes sauvegardées :

```bash
./install.sh uninstall
```

Lancez `./uninstall.sh` pour vérifier l’état et afficher un menu `uninstall` /
`Exit`. Entrée quitte sans modification ; sans terminal interactif, seules les
informations sont affichées. `./uninstall.sh --help` décrit les options de
désinstallation ; `./uninstall.sh --dry-run` en affiche un aperçu après le choix dans le menu.

`--dry-run` fonctionne aussi pour la mise à jour et la désinstallation. Les fichiers modifiés sont signalés au lieu d’être
supprimés. Pour ajouter DevPod à une installation qui ne l’incluait pas,
désinstallez puis réinstallez une fois DevPod disponible. Les conteneurs protégés
devront alors être recréés.

### Réglages facultatifs

`--without-devpod` installe uniquement les protections Podman et Compose.
`--devpod PATH` choisit un exécutable hors de `PATH` ; `--podman` et
`--compose-provider` remplacent la détection automatique. `--backup-existing`
autorise les sauvegardes sans confirmation interactive. Utilisez des valeurs
cohérentes de `--bindir` et `--libdir` pour vos propres répertoires.

Les configurations SSH du répertoire DevPod par défaut sont restaurées
automatiquement. Pour les contextes d’un autre `DEVPOD_HOME`, indiquez chaque
chemin de configuration SSH personnalisé :

```bash
./install.sh uninstall --devpod-ssh-config /absolute/path/to/ssh-config
```

Le [guide avancé d’installation hors ligne](../docs/wheel-packaging.md) décrit
les paquets préparés. L’installation normale ne nécessite pas ces options.

<a id="usage"></a>

## Utilisation

Exécutez les commandes depuis un répertoire de projet existant. Remplacez
`localhost/my-dev-image:latest` par une image déjà disponible localement :
les lancements directs utilisent `--pull=never`. Compose nécessite un fichier de
configuration et un service nommé `app`.

```bash
podman run --rm -v .:/workspace localhost/my-dev-image:latest
podman compose up
podman compose ps
podman compose exec app sh
podman compose run --rm --build app
podman compose down
```

Les tâches ponctuelles Compose acceptent les profils, `--no-deps` et les options
d’environnement, d’utilisateur, de répertoire et de ports. `run --build` s’arrête si la
construction échoue. `up --force-recreate` et la remise à zéro explicite `down --volumes
--remove-orphans` sont acceptés ; cette dernière supprime les volumes du projet.

Les alias `docker`, `podman-compose` et `docker-compose` sont installés pour les
workflows compatibles. Les commandes hors de l'interface vérifiée sont refusées ;
utilisez délibérément le vrai binaire Podman pour administrer l'hôte.

Les refus de politique de Podman direct et de Compose se terminent par le code 125
et affichent la raison ainsi qu’une ligne `next step:` sans valeurs secrètes.
Les erreurs de contexte de build
énumèrent tous les chemins détectés en une fois. Excluez les fichiers dotenv
actifs et les chemins d'identifiants courants des entrées copiées via
`.containerignore` ou `.dockerignore`. `.git` est autorisé dans le contexte de
build et reste disponible dans le workspace runtime, où le guard le protège par
défaut avec un mount read-only distinct.

Les runs directs protégés utilisent `--pull=never`, l'image doit donc déjà
exister. DevPod dispose de formes lifecycle de pull/build d'image étroitement
vérifiées.

Pour vérifier et corriger les exclusions du contexte de construction :

```bash
paranoid-podman build-context audit .
paranoid-podman build-context protect .
```

`protect` propose une sélection de chemins ; `--all` applique toutes les exclusions
détectées sans question. Seul le fichier ignore standard est modifié, sans contourner
la politique.

<a id="devpod"></a>

## DevPod

Les nouveaux conteneurs DevPod utilisent le nom du workspace comme hostname. Un hostname
explicite est conservé ; les noms raccourcis ou normalisés reçoivent un court hash. Les
conteneurs existants doivent être recréés pour appliquer ce changement.

L'enveloppe gère les formes de commandes vérifiées des pilotes Docker et Compose
de DevPod 0.6.15, notamment :

- découverte, inspect, start, stop, logs, exec et suppression du conteneur ;
- inspect, pull, build, tag et push d'image ;
- flux privé de copie pour préparer `/etc/passwd` et `/etc/group` ;
- recherche de projet Compose, noms de projet, `.env` du projet et fichiers
  override générés ; et
- opérations Compose build, up, stop et down.

DevPod ne contourne pas la politique commune de création. Un workspace demandant
le mode privilégié, des namespaces hôte, des sockets moteur, des capabilities
dangereuses ou un large montage hôte est toujours refusé.

Les sous-montages en lecture seule et labels de provenance sont appliqués lors de
la création du conteneur. Après installation de ce mécanisme, recréez un ancien
workspace DevPod avant de le démarrer ou d'y entrer via le wrapper. Il en va de
même après une réinstallation complète, qui reçoit un nouvel identifiant. Les
commandes de cleanup restent disponibles. Pour garder `.git` inscriptible,
lancez DevPod avec `PODMAN_GUARD_PROTECT_GIT=0`.

### Isolation des identifiants SSH

DevPod choisit l’IDE selon ses valeurs par défaut, la configuration de l’espace de
travail ou `--ide`. Le wrapper n’impose aucun éditeur. L’ouverture et la reconnexion
en mode IDE-only ont été vérifiées manuellement avec Codium ; les autres IDE et
le parcours complet du pilote Compose restent à tester. Un problème de durée de vie
du socket de l’agent de projet a été observé avec Codium et Open Remote - SSH 0.1.2 :
[DEV-001](../KNOWN_ISSUES.md#dev-001-vscode-loses-the-project-ssh-agent-socket).
Les nouvelles connexions OpenSSH et `devpod ssh` ont été vérifiées avec une clé de projet.

Menu interactif : **1** créer une clé de projet, **2** IDE sans identifiants de l’hôte,
**3** choisir une clé de projet existante, **4** exposer tout l’agent de l’hôte une fois
avec confirmation exacte `y`, **5** annuler. Entrée choisit l’option 1 ; choisissez
explicitement **2** pour IDE-only.

Le mode projet démarre un `ssh-agent` dédié avec une seule identité vérifiée. La
clé privée n'est ni copiée ni montée dans le conteneur, mais le code du workspace
peut demander à l'agent de signer avec elle. Limitez la clé publique à un seul
dépôt dans GitHub, GitLab ou Gitea.

Les modes protégés désactivent l'injection automatique des identifiants Git et
registry, du GPG-agent et de la clé de signature SSH dans le contexte DevPod
sélectionné. Un `devpod build` autonome sans mode configuré ne demande aucun
choix et ne transmet pas les identifiants de l'hôte au workspace.

Pour éviter que DevPod ouvre l'IDE avant la protection du bloc SSH, le wrapper
crée d'abord le workspace avec `--open-ide=false`, restreint le bloc, puis ouvre
l'IDE sans recréer le conteneur ni réécrire la configuration SSH.

La clé privée existante doit être réservée au projet et ne doit pas se trouver
directement dans `~/.ssh` ; un sous-répertoire est autorisé.

```bash
paranoid-podman devpod audit WORKSPACE
paranoid-podman devpod configure WORKSPACE
paranoid-podman devpod key show WORKSPACE
paranoid-podman devpod key stop WORKSPACE
```

L'état est déduit du bloc SSH DevPod et des clés sous
`~/.ssh/paranoid-podman/` ; aucun fichier de politique séparé n'est créé. Voir
[l'isolation des identifiants DevPod](../docs/devpod-credentials.md) (anglais).

Remplacez `WORKSPACE` par un identifiant existant. Utilisez les valeurs correspondantes
de `--context`, `--devpod-home` et `--ssh-config` si nécessaire. Pour un projet local,
le répertoire passé à `devpod up` doit exister ; sinon, il peut être interprété comme
une URL de dépôt. Choisissez IDE-only lors du premier `up` interactif : `configure`
ne peut pas enregistrer ce choix avant la création du bloc SSH de DevPod.
Voir l’[exemple local](../docs/devpod-credentials.md#open-a-local-workspace).

Les modes protégés désactivent aussi la recherche automatique de clés privées.
Les paramètres du contexte affectent tous ses espaces de travail.

<a id="compatibility"></a>

## Compatibilité

| Composant | Compatibilité |
| --- | --- |
| Système d'exploitation | Linux |
| Python | 3.10 et plus récent |
| Podman | Rootless 6.1.x |
| Fournisseur Compose | `podman-compose` 1.6.x via `podman compose` |
| DevPod | Testé avec 0.6.15 ; autres versions acceptées ; commandes Docker/Compose vérifiées et limites IDE ci-dessus |
| Docker Compose v2 | Non pris en charge |

L'installateur refuse toute série major/minor Podman ou Compose non vérifiée.
Les wrappers dépendent du comportement CLI du fournisseur ; chaque nouvelle
série exige donc une revue et des tests de compatibilité.

La compatibilité des autres versions de DevPod n’a pas encore été vérifiée. Les
contrôles d’accès aux identifiants et des commandes autorisées restent actifs.

<a id="limitations"></a>

## Limites

- La vérification ignore exige l’exclusion complète des chemins sensibles à la racine.
  Les exceptions pour les descendants ou les motifs non vérifiés peuvent nécessiter
  une exclusion explicite finale. Les contextes d’image supplémentaires nécessitent
  un préfixe de transport explicite. Voir les [limites de construction](../docs/direct-policy.md#build-boundary).
- Un agent peut signer avec l’identité du projet sans révéler la clé privée.
  Le serveur Git doit limiter l’accès au dépôt.
- Un wrapper dans `PATH` n'est pas une sandbox. Un processus hôte exécuté comme
  votre utilisateur peut appeler le vrai Podman ou accéder directement aux mêmes fichiers.
- Le wrapper ne protège pas contre les vulnérabilités du noyau, de Podman, du
  runtime OCI, des images, des parsers ou les failles zero-day.
- Le réseau ordinaire du conteneur n'est pas une sandbox de trafic sortant.
- Les builds peuvent exécuter des instructions arbitraires et lire tout chemin du
  context non exclu par `.containerignore` ou `.dockerignore`. Le guard ne vérifie
  que les credentials courants à la racine et les affectations littérales évidentes.
- Les conteneurs créés auparavant ou déjà actifs ne reçoivent pas les nouvelles
  protections de montage. Le lifecycle protégé refuse les conteneurs sans la
  provenance actuelle de politique et d'installation.
- Les labels de provenance sont des marqueurs locaux de compatibilité, pas des
  signatures cryptographiques. Un processus déjà exécuté comme utilisateur hôte
  peut les lire, les copier ou les contourner. L'exécution directe depuis l'arbre
  source sans ID d'installation explicite utilise un fallback de développement
  déterministe ; utilisez l'installateur pour une provenance propre à l'installation.
- Les fichiers peuvent changer entre la validation et l'exécution du fournisseur.
- L'interface prise en charge est volontairement plus petite que les CLI Podman
  et Compose complètes.

Utilisez une VM jetable ou un compte séparé à faibles privilèges lorsqu'une
isolation plus forte est nécessaire.

<a id="development"></a>

## Développement

Vérifications locales rapides :

```bash
scripts/test.sh
scripts/check.sh syntax
```

Les tests locaux utilisent de faux fournisseurs et des répertoires home/config temporaires ; les activations d’intégration héritées sont désactivées. `scripts/check.sh` utilise `local` par défaut avec Ruff, mypy, Bandit, ShellCheck, `zizmor` hors ligne et une analyse Gitleaks des sources avec secrets masqués. `scripts/audit.sh dependencies` lance explicitement l’audit réseau ; `scripts/check.sh all` l’ajoute sans exécuter d’intégrations réelles.

`scripts/format.sh` applique le formatage. Préparez les outils séparément selon [CONTRIBUTING.md](../CONTRIBUTING.md). CI sépare tests, analyse statique, secrets et audit des dépendances.

L’implémentation se trouve dans `src/paranoid_podman`. Consultez le [plan du code](../docs/architecture.md) et les [instructions de construction et d’installation hors ligne du wheel](../docs/wheel-packaging.md).

| Répertoire | Rôle |
| --- | --- |
| `src/paranoid_podman/` | Code de l’application |
| `bin/` | Petits lanceurs pour les sources et les tests |
| `dist/` | Wheel et empreinte générés |

L’installateur crée séparément les commandes dans `--bindir`, qui utilisent
l’environnement Python privé. `bin/` reste dans l’arborescence des sources.

`scripts/test.sh compose-provider` sélectionne le contrôle avec le vrai fournisseur Compose. Il peut interroger Podman, mais ne démarre pas de conteneurs ; utilisez un environnement jetable avec les versions vérifiées.
Ces tests couvrent aussi les usages quotidiens avec le vrai Compose et un moteur simulé
qui enregistre les appels ; ils sont intégrés à la CI.

La suite runtime opt-in nécessite une image locale existante avec `sh` et
`sleep`. Elle ne télécharge jamais d'image et n'utilise que des conteneurs
jetables aux noms uniques :

```bash
PARANOID_PODMAN_TEST_IMAGE=docker.io/library/alpine:latest \
  scripts/test.sh rootless
```

Exécutez-la uniquement avec les versions Podman et Compose vérifiées, dans un
compte rootless jetable sans conteneurs ni credentials précieux.

Le test de l’agent SSH crée une clé et un socket temporaires sans lire les clés SSH
habituelles :

```bash
scripts/test.sh ssh-agent
```

Consultez [TODO.md](../TODO.md) pour le travail restant et
[CONTRIBUTING.md](../CONTRIBUTING.md) pour contribuer.

<a id="security"></a>

## Sécurité

Signalez les vulnérabilités présumées selon la procédure de
[SECURITY.md](../SECURITY.md). Ne publiez pas de credentials actifs, de données
privées du projet ni de détails d'exploit dans une issue publique.

<a id="license"></a>

## Licence

[MIT](../LICENSE)
