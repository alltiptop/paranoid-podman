[en English](../README.md) · [ru Русский](README.ru.md) · [es Español](README.es.md) · [pl Polski](README.pl.md) · [uk Українська](README.uk.md) · [de Deutsch](README.de.md) · [fr Français](README.fr.md) · [zh-CN 简体中文](README.zh-CN.md) · [ar العربية](README.ar.md) · [he עברית](README.he.md)

# paranoid-podman

`paranoid-podman` es un envoltorio experimental de nivel de usuario para Podman
sin root y `podman-compose`. Está dirigido a desarrolladores que a veces ejecutan
comandos de contenedores desde proyectos en los que no confían por completo.

El proyecto añade barreras sencillas y directas contra errores habituales de
escape del contenedor y acceso al host, sin impedir los flujos normales de
desarrollo local. No es un antivirus, un producto de seguridad empresarial ni
un sandbox completo.

> [!WARNING]
> Es una defensa en profundidad, no una protección absoluta. Puede no detener
> exploits de día cero, técnicas de ataque desconocidas, vulnerabilidades del
> kernel o del runtime, ni un proceso que ya tenga acceso a la cuenta de usuario
> del host.

## Contenido

- [Acerca del proyecto](#about)
- [Qué protege](#what-it-protects)
- [Instalación](#install)
- [Uso](#usage)
- [DevPod](#devpod)
- [Compatibilidad](#compatibility)
- [Limitaciones](#limitations)
- [Desarrollo](#development)
- [Seguridad](#security)
- [Licencia](#license)

<a id="about"></a>

## Acerca del proyecto

El flujo de un comando es:

```text
comando podman / compose -> analizador -> política -> proveedor real verificado
```

Los argumentos seguros se conservan. El acceso peligroso al host se rechaza,
se añaden valores de endurecimiento predeterminados y los archivos protegidos
del proyecto se montan como solo lectura dentro de los contenedores nuevos.

El diseño es deliberadamente directo:

- bloquear las vías más directas de privilegios y acceso al host;
- mantener el desarrollo normal dentro de los límites del proyecto;
- preferir restricciones de montaje explícitas y comprensibles a escáneres de contenido;
- cerrar con error cuando se desconoce una forma de comando relevante para la seguridad; y
- documentar con honestidad los límites restantes.

<a id="what-it-protects"></a>

## Qué protege

| Entrada o comportamiento | Acción |
| --- | --- |
| Modo privilegiado, namespaces del host o unidos, capabilities añadidas, dispositivos u opciones de seguridad arbitrarias | Rechazar |
| Sockets del motor Podman/Docker y rutas sensibles del runtime del host | Rechazar |
| Raíz del sistema de archivos, home completo, árboles amplios del sistema o un bind más amplio que el proyecto | Rechazar |
| Archivos y directorios regulares individuales indicados explícitamente | Permitir y canonicalizar |
| Opciones bind que cambian propietario o etiquetas del host, como `U`, `idmap` o relabeling inseguro | Rechazar |
| Puertos publicados | Conservar los mapeos TCP/UDP habituales, incluidos todas las interfaces y las direcciones de la red local |
| Credenciales ambientales, endpoints de escritorio/sesión y variables de routing del proveedor | Eliminar o rechazar |
| Configuración protegida existente bajo binds escribibles | Añadir submontajes de solo lectura |
| `start`, `exec` o lifecycle Compose activo sobre un contenedor antiguo o ajeno | Rechazar hasta recrearlo con la instalación y política actuales |
| Configuración Compose | Resolver, validar, reescribir y ejecutar un snapshot privado revisado |
| Secretos literales evidentes en Compose o asignaciones Dockerfile | Rechazar ocultando los valores |
| Credenciales en la raíz del build context no cubiertas por un archivo ignore | Rechazar antes del builder |

Los comandos directos `run` y `create` también reciben valores compatibles como
`no-new-privileges`, un límite de PID, forwarding automático de proxy desactivado,
una política de reinicio no persistente y ningún pull implícito. Las opciones de
runtime desconocidas no se reenvían.

### Configuración protegida del proyecto

Las rutas detectadas con estos nombres son de solo lectura dentro de los
contenedores nuevos:

- `.devcontainer`, `.devcontainer.json`, `devcontainer.json`;
- `.dockerignore`, `.containerignore`;
- archivos dotenv activos y archivos comunes de configuración de credenciales; y
- `.git`, `.gitmodules`, `.git-credentials`.

Las rutas ausentes no se crean. El contenido de devcontainer no se analiza ni
reescribe: esta protección es una regla de montaje del sistema de archivos. Los
Dockerfiles solo reciben la pequeña comprobación de secretos literales descrita
más adelante.

Los archivos Dockerfile, Containerfile y Compose fuera de `.devcontainer` se pueden
editar dentro del contenedor. Todo el contenido de `.devcontainer` sigue siendo de
solo lectura. Recree los contenedores existentes para aplicar los nuevos permisos.

Los directorios ilegibles de otro UID, como los datos de una base de datos
rootless, se montan sin recorrer su contenido ni cambiar sus permisos.
Sus archivos no reciben la protección automática contra escritura.

`.git` es de solo lectura por defecto. Puede dejarse escribible para una sola
invocación sin debilitar las demás rutas protegidas:

```bash
PODMAN_GUARD_PROTECT_GIT=0 podman compose up
```

### Revisión de Compose

El adaptador Compose:

1. comprueba los archivos fuente y resuelve la configuración;
2. valida acceso al host, namespaces, montajes, recursos y campos admitidos;
3. escribe un snapshot privado con modo `0600` y la interpolación ya resuelta;
4. ejecuta únicamente ese snapshot revisado.

Los montajes dentro del proyecto son silenciosos. Antes de `up` o `run`, el acceso
a archivos o directorios del host fuera del proyecto requiere confirmación: un
`[warning]` naranja muestra la ruta completa, el servicio, el modo de acceso y el
archivo Compose con número de línea. Escriba exactamente `y` y Enter para ese comando.
Enter, otra respuesta o EOF cancela; sin entrada de terminal se bloquea sin leer
stdin. La elección no se guarda. La inspección, eliminación y los comandos para
contenedores existentes no piden confirmación. Esto no permite rutas ni sockets
prohibidos.

`PODMAN_GUARD_DEBUG=1` muestra un resumen sin valores sensibles.
Terminal y automatización reciben las mismas comprobaciones. Los rechazos destacan
`BLOCKED`, `UNSUPPORTED` o `ERROR`, con el motivo y el servicio afectado cuando se
conoce.

Los diagnósticos Compose no muestran valores de entorno resueltos ni errores
crudos del proveedor. La comprobación de secretos literales es deliberadamente
pequeña y basada en nombres; no es un escáner general de secretos.

Los contenedores directos y Compose nuevos reciben dos etiquetas de procedencia
reservadas: la generación de la política y un identificador aleatorio de la
instalación local. Las operaciones que pueden iniciar, reanudar o ejecutar código
exigen que ambas coincidan antes de usar un contenedor existente. Así, una imagen
no supera la comprobación declarando solo la etiqueta pública de política. La
inspección de solo lectura y la limpieza siguen disponibles para diagnosticar y
eliminar contenedores antiguos.

Las interfaces exactas están en
[Política directa de Podman](../docs/direct-policy.md),
[Política de Compose](../docs/compose-policy.md) y el
[modelo de amenazas](../docs/threat-model.md).

<a id="install"></a>

## Instalación

Necesita Linux con Podman rootless 6.1.x, `podman-compose` 1.6.x, Python 3.10 o
posterior con soporte de entornos virtuales y Bash. Para DevPod, necesita también
DevPod y las herramientas cliente de OpenSSH. Se ha probado DevPod 0.6.15;
se aceptan otras versiones. Desde el repositorio, como usuario normal:

```bash
./install.sh install
```

Sin argumentos, `./install.sh` muestra el estado y un menú: `install` para
una instalación nueva, o `update` / `uninstall` para una existente. Pulse Enter
para salir sin cambios. Sin terminal interactivo, solo muestra el estado y las
acciones disponibles; en los scripts, indique la acción explícitamente.

`./install.sh --help` describe las opciones por acción. Las opciones de
proveedores y wheels se aplican a `install` / `update`; `--without-devpod` y
`--backup-existing` solo a `install`, y `--devpod-ssh-config` solo a
`uninstall`. Una instalación normal no necesita opciones adicionales.

El instalador encuentra Podman, Compose y DevPod si está disponible, descarga
las herramientas Python y las dependencias, compila la aplicación y la instala en
un entorno privado. No necesita preparar paquetes, sumas de comprobación ni
entornos Python. Los comandos existentes se guardan tras su confirmación y se
restauran al desinstalar.

Vista previa sin descargas ni cambios en archivos:

```bash
./install.sh install --dry-run
```

### Ubicación de los comandos

El directorio de comandos predeterminado es `${XDG_BIN_HOME:-$HOME/.local/bin}`.
Colóquelo al principio de `PATH` para usar los comandos protegidos:

```bash
export PATH="${XDG_BIN_HOME:-$HOME/.local/bin}:$PATH"
command -v podman docker compose-guard
```

Si aún no está al principio, añada la línea `export` a la configuración de su
shell. La aplicación reside en `${XDG_DATA_HOME:-$HOME/.local/share}/paranoid-podman`.

### Actualizar o desinstalar

Ejecute desde el repositorio que contiene la versión deseada:

```bash
./install.sh update
./install.sh status
```

Desinstalar y restaurar los comandos guardados:

```bash
./install.sh uninstall
```

Ejecute `./uninstall.sh` para comprobar el estado y ver un menú `uninstall` /
`Exit`. Enter sale sin cambios; sin terminal interactivo, solo se muestra
información. `./uninstall.sh --help` explica las opciones de desinstalación;
`./uninstall.sh --dry-run` permite previsualizarla tras seleccionarla en el menú.

`--dry-run` también funciona al actualizar y desinstalar. Se informa de los archivos modificados en vez de borrarlos. Para
añadir DevPod a una instalación que no lo incluía, desinstale y vuelva a instalar
cuando DevPod esté disponible. Después deberá recrear los contenedores protegidos.

### Opciones adicionales

`--without-devpod` instala solo las protecciones de Podman y Compose. `--devpod PATH`
indica un ejecutable fuera de `PATH`; `--podman` y `--compose-provider` sustituyen
la detección automática. `--backup-existing` permite copias sin confirmación
interactiva. Use valores coherentes de `--bindir` y `--libdir` para rutas propias.

Las configuraciones SSH del directorio DevPod predeterminado se restauran
automáticamente. Para contextos en otro `DEVPOD_HOME`, indique cada ruta personalizada:

```bash
./install.sh uninstall --devpod-ssh-config /absolute/path/to/ssh-config
```

La [guía avanzada de instalación sin conexión](../docs/wheel-packaging.md) describe
los paquetes preparados. La instalación normal no requiere esas opciones.

<a id="usage"></a>

## Uso

Ejecute los comandos desde un directorio de proyecto existente. Sustituya
`localhost/my-dev-image:latest` por una imagen ya disponible localmente:
la ejecución directa usa `--pull=never`. Compose necesita su archivo de configuración
y un servicio llamado `app`.

```bash
podman run --rm -v .:/workspace localhost/my-dev-image:latest
podman compose up
podman compose ps
podman compose exec app sh
podman compose run --rm --build app
podman compose down
```

Las tareas puntuales de Compose admiten perfiles, `--no-deps` y opciones de entorno,
usuario, directorio y puertos. `run --build` se detiene si falla la compilación. Se
admiten `up --force-recreate` y el restablecimiento explícito `down --volumes
--remove-orphans`; este último elimina los volúmenes del proyecto.

Se instalan los alias `docker`, `podman-compose` y `docker-compose` para flujos
compatibles. Los comandos fuera de la interfaz revisada se rechazan; invoque
deliberadamente el Podman real para administrar el host.

Los rechazos de la política de Podman directo y Compose terminan con el código 125
y muestran el motivo y una línea `next step:` sin valores secretos.
Los errores del contexto de build
enumeran de una vez todas las rutas detectadas. Excluya los archivos dotenv
activos y las rutas de credenciales comunes de los inputs copiados mediante
`.containerignore` o `.dockerignore`. `.git` está permitido en el contexto de
build y sigue disponible en el workspace de runtime, donde el guard lo protege
con un mount read-only separado por defecto.

Los runs directos protegidos usan `--pull=never`, por lo que la imagen debe
existir previamente. DevPod dispone de formas lifecycle de pull/build de imagen
revisadas de manera limitada.

Para revisar y corregir las exclusiones del contexto de construcción:

```bash
paranoid-podman build-context audit .
paranoid-podman build-context protect .
```

`protect` permite seleccionar rutas; `--all` aplica todas las exclusiones detectadas
sin preguntar. Solo modifica el archivo ignore estándar y no evita la política.

<a id="devpod"></a>

## DevPod

Los nuevos contenedores DevPod usan el nombre del workspace como hostname. Se conserva
un hostname explícito; los nombres acortados o normalizados reciben un hash breve. Los
contenedores existentes deben recrearse para aplicar el cambio.

El envoltorio gestiona las formas de comandos revisadas de los controladores
Docker y Compose de DevPod 0.6.15, entre ellas:

- descubrimiento, inspect, start, stop, logs, exec y eliminación de contenedores;
- inspect, pull, build, tag y push de imágenes;
- el flujo privado de copia para preparar `/etc/passwd` y `/etc/group`;
- búsqueda del proyecto Compose, nombres de proyecto, `.env` del proyecto y
  archivos override generados; y
- operaciones Compose build, up, stop y down.

DevPod no evita la política común de creación. Se sigue rechazando un workspace
que solicite modo privilegiado, namespaces del host, sockets del motor,
capabilities peligrosas o un montaje amplio del host.

Los submontajes de solo lectura y las etiquetas de procedencia se aplican al
crear el contenedor. Tras instalar este mecanismo, recree un workspace DevPod
antiguo antes de iniciarlo o entrar en él mediante el wrapper. Lo mismo ocurre
tras una reinstalación completa, que recibe un identificador nuevo. Las órdenes
de limpieza siguen disponibles. Para que `.git` sea escribible, inicie DevPod
con `PODMAN_GUARD_PROTECT_GIT=0`.

### Aislamiento de credenciales SSH

DevPod elige el IDE mediante sus valores predeterminados, la configuración del
espacio de trabajo o `--ide`. El wrapper no exige un editor concreto. La apertura
y reconexión en modo IDE-only se comprobaron manualmente con Codium; otros IDE y
el flujo completo del driver Compose aún requieren pruebas.
Se observó un fallo de duración del socket del agente de proyecto con Codium y
Open Remote - SSH 0.1.2: [DEV-001](../KNOWN_ISSUES.md#dev-001-vscode-loses-the-project-ssh-agent-socket).
Las conexiones nuevas de OpenSSH y `devpod ssh` se verificaron con una clave de proyecto.

Menú interactivo: **1**, crear una clave de proyecto; **2**, IDE sin credenciales
del host; **3**, elegir una clave de proyecto existente; **4**, usar todo el agente
del host una vez, confirmando exactamente `y`; **5**, cancelar. Enter selecciona
la opción 1; elija **2** explícitamente para IDE-only.

El modo de proyecto inicia un `ssh-agent` dedicado con exactamente una
identidad verificada. La clave privada no se copia ni se monta en el contenedor,
pero el código del workspace puede pedir al agente que firme con ella. Limite la
clave pública a un solo repositorio en GitHub, GitLab o Gitea.

Los modos protegidos desactivan la inyección automática de credenciales Git y
de registry, el GPG-agent y la clave de firma SSH en el contexto DevPod elegido.
Un `devpod build` independiente sin modo configurado no solicita una elección ni
reenvía credenciales del host al workspace.

Para que DevPod no abra el IDE antes de proteger el bloque SSH, el wrapper crea
primero el workspace con `--open-ide=false`, restringe el bloque y después abre
el IDE sin recrear el contenedor ni volver a escribir la configuración SSH.

La clave privada existente debe estar dedicada al proyecto y no puede estar
directamente en `~/.ssh`; sí puede estar en un subdirectorio.

```bash
paranoid-podman devpod audit WORKSPACE
paranoid-podman devpod configure WORKSPACE
paranoid-podman devpod key show WORKSPACE
paranoid-podman devpod key stop WORKSPACE
```

El estado se deduce del bloque SSH de DevPod y de las claves guardadas en
`~/.ssh/paranoid-podman/`; no se crea otro archivo de política. Consulte
[aislamiento de credenciales de DevPod](../docs/devpod-credentials.md) (inglés).

Sustituya `WORKSPACE` por un ID existente. Use los valores correspondientes de
`--context`, `--devpod-home` y `--ssh-config` cuando sea necesario. Para un proyecto
local nuevo, pase a `devpod up` un directorio que exista; una ruta inexistente puede
interpretarse como URL de repositorio. Elija IDE-only durante el primer `up`
interactivo: `configure` no puede guardarlo antes de que DevPod cree el bloque SSH.
Consulte el [ejemplo local](../docs/devpod-credentials.md#open-a-local-workspace).

Los modos protegidos también desactivan el descubrimiento automático de claves
privadas. Los ajustes del contexto afectan a todos sus espacios de trabajo.

<a id="compatibility"></a>

## Compatibilidad

| Componente | Compatibilidad |
| --- | --- |
| Sistema operativo | Linux |
| Python | 3.10 y posterior |
| Podman | 6.1.x sin root |
| Proveedor Compose | `podman-compose` 1.6.x mediante `podman compose` |
| DevPod | Probado con 0.6.15; se aceptan otras versiones; comandos Docker/Compose revisados y límites de IDE indicados arriba |
| Docker Compose v2 | No admitido |

El instalador rechaza una serie major/minor de Podman o Compose no revisada. Los
wrappers dependen del comportamiento de la CLI del proveedor, por lo que cada
serie nueva requiere revisión y pruebas de compatibilidad.

La compatibilidad de otras versiones de DevPod aún no se ha verificado. Las
comprobaciones de acceso a credenciales y comandos permitidos siguen activas.

<a id="limitations"></a>

## Limitaciones

- La comprobación de ignore exige excluir por completo las rutas sensibles de la raíz.
  Las excepciones para descendientes o los patrones no revisados pueden requerir
  una exclusión explícita al final. Los contextos adicionales de imagen requieren
  un prefijo de transporte explícito. Véanse los [límites de construcción](../docs/direct-policy.md#build-boundary).
- Un agente puede firmar con la identidad del proyecto sin revelar la clave privada.
  El servidor Git debe imponer el alcance de acceso al repositorio.
- Un wrapper en `PATH` no es un sandbox. Un proceso del host ejecutado como su
  usuario puede invocar el Podman real o acceder directamente a los mismos archivos.
- El wrapper no puede proteger contra vulnerabilidades del kernel, Podman, OCI
  runtime, imágenes, parsers o día cero.
- La red normal del contenedor no es un sandbox de salida.
- Los builds pueden ejecutar instrucciones arbitrarias y leer cualquier ruta del
  context no excluida por `.containerignore` o `.dockerignore`. El guard solo
  comprueba credenciales comunes en la raíz y asignaciones literales evidentes.
- Los contenedores creados anteriormente o ya activos no reciben nuevas
  protecciones de montaje. El lifecycle protegido rechaza los que no tengan la
  procedencia actual de política e instalación.
- Las etiquetas de procedencia son marcadores locales de compatibilidad, no firmas
  criptográficas. Un proceso que ya se ejecute como el usuario del host puede
  leerlas, copiarlas o evitarlas. Ejecutar el guard directamente desde el árbol
  fuente sin un ID explícito usa un fallback de desarrollo determinista; use el
  instalador para una procedencia específica de la instalación.
- Los archivos pueden cambiar entre la validación y la ejecución del proveedor.
- La interfaz admitida es intencionadamente menor que las CLI completas de Podman
  y Compose.

Use una VM desechable o una cuenta separada de pocos privilegios cuando necesite
un aislamiento más fuerte.

<a id="development"></a>

## Desarrollo

Comprobaciones locales rápidas:

```bash
scripts/test.sh
scripts/check.sh syntax
```

Las pruebas locales usan proveedores falsos y directorios temporales de home/config; desactivan las opciones de integración heredadas. `scripts/check.sh` usa `local` por defecto e incluye Ruff, mypy, Bandit, ShellCheck, `zizmor` sin conexión y un análisis del código con Gitleaks y secretos ocultos. `scripts/audit.sh dependencies` ejecuta explícitamente la auditoría de red; `scripts/check.sh all` la añade sin ejecutar integraciones reales.

`scripts/format.sh` aplica el formato. Prepara las herramientas por separado según [CONTRIBUTING.md](../CONTRIBUTING.md). CI separa pruebas, análisis estático, secretos y auditoría de dependencias.

La implementación está en `src/paranoid_podman`. Consulta el [mapa del código](../docs/architecture.md) y las [instrucciones de compilación e instalación sin conexión del wheel](../docs/wheel-packaging.md).

| Directorio | Finalidad |
| --- | --- |
| `src/paranoid_podman/` | Código de la aplicación |
| `bin/` | Lanzadores pequeños para ejecutar las fuentes y las pruebas |
| `dist/` | Wheel y suma de comprobación generados |

El instalador crea por separado los comandos de `--bindir`, que usan el entorno
Python privado. `bin/` sigue formando parte de las fuentes.

`scripts/test.sh compose-provider` selecciona la comprobación con el proveedor Compose real. Puede consultar Podman, pero no inicia contenedores; usa un entorno desechable con las versiones revisadas.
Estas pruebas también cubren flujos cotidianos con Compose real y un motor simulado que
registra llamadas; se ejecutan en CI.

La suite runtime opt-in requiere una imagen local existente con `sh` y `sleep`.
Nunca descarga imágenes y usa únicamente contenedores desechables con nombres
únicos:

```bash
PARANOID_PODMAN_TEST_IMAGE=docker.io/library/alpine:latest \
  scripts/test.sh rootless
```

Ejecútela solo con las versiones revisadas de Podman y Compose en una cuenta
rootless desechable, sin contenedores ni credenciales valiosos.

La prueba del agente SSH crea una clave y un socket temporales; no lee las claves
SSH habituales:

```bash
scripts/test.sh ssh-agent
```

Consulte [TODO.md](../TODO.md) para el trabajo restante y
[CONTRIBUTING.md](../CONTRIBUTING.md) para colaborar.

<a id="security"></a>

## Seguridad

Informe posibles vulnerabilidades mediante el proceso de
[SECURITY.md](../SECURITY.md). No publique credenciales reales, datos privados
del proyecto ni detalles del exploit en un issue público.

<a id="license"></a>

## Licencia

[MIT](../LICENSE)
