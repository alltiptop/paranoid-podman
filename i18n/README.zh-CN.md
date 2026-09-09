[en English](../README.md) · [ru Русский](README.ru.md) · [es Español](README.es.md) · [pl Polski](README.pl.md) · [uk Українська](README.uk.md) · [de Deutsch](README.de.md) · [fr Français](README.fr.md) · [zh-CN 简体中文](README.zh-CN.md) · [ar العربية](README.ar.md) · [he עברית](README.he.md)

# paranoid-podman

`paranoid-podman` 是一个面向普通用户的实验性 rootless Podman 和
`podman-compose` 包装器。它适用于有时需要从并不完全可信的项目中运行容器命令的开发者。

本项目通过简单、直接的防护规则，降低常见容器越界和主机访问错误的风险，同时尽量保留日常本地开发流程。它不是杀毒软件、企业安全产品，也不是完整的沙箱。

> [!WARNING]
> 这是纵深防御，而不是绝对保护。它可能无法阻止零日漏洞、未知攻击技术、内核或运行时漏洞，也无法约束已经获得主机用户账户权限的进程。

## 目录

- [项目简介](#about)
- [保护范围](#what-it-protects)
- [安装](#install)
- [使用](#usage)
- [DevPod](#devpod)
- [兼容性](#compatibility)
- [限制](#limitations)
- [开发](#development)
- [安全](#security)
- [许可证](#license)

<a id="about"></a>

## 项目简介

命令处理流程如下：

```text
podman / compose 命令 -> 解析器 -> 策略 -> 已验证的真实 provider
```

安全参数会被保留。危险的主机访问会被拒绝，系统会加入选定的安全默认值，并在新容器内将受保护的项目文件挂载为只读。

设计原则刻意保持直接：

- 阻止最直接的提权和主机访问路径；
- 只要开发活动保持在项目边界内，就维持正常的开发体验；
- 优先采用明确、易理解的挂载限制，而不是内容扫描器；
- 对未知且与安全相关的命令形式采取失败关闭策略；以及
- 如实记录剩余的安全边界。

<a id="what-it-protects"></a>

## 保护范围

| 输入或行为 | 处理方式 |
| --- | --- |
| 特权模式、host 或 joined namespace、增加 capability、设备、任意 security option | 拒绝 |
| Podman/Docker 引擎 socket 和主机敏感 runtime 路径 | 拒绝 |
| 文件系统根目录、完整用户 home、宽泛的系统目录树，或范围大于当前项目的 bind | 拒绝 |
| 明确指定的单个普通文件和目录 | 允许并规范化路径 |
| 会修改主机所有权或标签的 bind 选项，例如 `U`、`idmap` 或不安全的 relabeling | 拒绝 |
| 发布端口 | 保留常规 TCP/UDP 映射，包括绑定所有接口及局域网地址 |
| 环境中的凭据、桌面/会话 endpoint 和 provider routing 变量 | 删除或拒绝 |
| 位于可写 bind 下的现有受保护项目配置 | 增加只读子挂载 |
| 对旧容器或外部容器执行 `start`、`exec` 或主动 Compose lifecycle | 拒绝，直到由当前安装和策略重新创建 |
| Compose 配置 | 解析、验证、重写并执行私有的已审核 snapshot |
| Compose 或 Dockerfile 赋值中的明显字面量 secret | 拒绝且隐藏值 |
| build context 根目录中未被 ignore 文件排除的凭据 | 在 builder 运行前拒绝 |

直接 `run` 和 `create` 还会获得兼容的安全默认值，例如
`no-new-privileges`、PID 上限、禁用自动 proxy 转发、非持久化 restart policy，
以及禁止隐式拉取镜像。未知 runtime 选项不会被透传。

### 受保护的项目配置

新容器内，扫描发现的以下名称对应的路径会被设为只读：

- `.devcontainer`, `.devcontainer.json`、`devcontainer.json`；
- `.dockerignore`、`.containerignore`；
- 实际使用的 dotenv 文件和常见凭据配置文件；以及
- `.git`、`.gitmodules`、`.git-credentials`。

不存在的路径不会被创建。devcontainer 内容不会被扫描或重写：该保护只是文件系统挂载规则。Dockerfile 只会接受下文所述的小范围字面量 secret 检查。

容器内可以编辑 `.devcontainer` 之外的 Dockerfile、Containerfile 和 Compose 文件。
`.devcontainer` 内的所有内容保持只读。现有容器需要重新创建才能应用新的挂载权限。

属于其他 UID 且不可读取的目录（如 rootless 数据库的数据目录）会直接挂载，不遍历内容或修改权限。这些目录内的文件不受自动只读保护。

`.git` 默认只读。可以仅为一次调用将它保留为可写，而不削弱其他受保护路径：

```bash
PODMAN_GUARD_PROTECT_GIT=0 podman compose up
```

### Compose 审核

Compose 适配器会：

1. 预检源文件并渲染已解析配置；
2. 验证主机访问、namespace、mount、资源和受支持字段；
3. 写入权限为 `0600`、已完成变量插值的私有 snapshot;
4. 只运行该已审核 snapshot。

项目内的挂载保持安静。`up` 或 `run` 访问项目外的主机文件或目录前，橙色 `[warning]`
会显示完整路径、服务、访问模式及原始 Compose 文件和行号。必须输入准确的 `y` 并回车，
才能允许本次命令的这些挂载。直接回车、其他回答或 EOF 会取消；没有终端输入时会阻止
操作，且不会读取 stdin。选择不会保存。查看、清理和操作现有容器的命令不会要求确认。
此确认不能放行原本禁止的路径或套接字。

`PODMAN_GUARD_DEBUG=1` 可显示隐藏敏感值的摘要。终端与自动化使用相同策略。拒绝操作时会突出显示
`BLOCKED`、`UNSUPPORTED` 或 `ERROR`，并说明原因及已知的相关服务。

Compose 诊断不会输出已解析的环境变量值或 provider 原始错误。字面量 secret 检查刻意保持小范围并依赖变量名称；它不是通用 secret 扫描器。

新建的直接容器和 Compose 容器会获得两个保留的 provenance label：策略代次，以及本地安装生成的随机标识。任何能够启动、恢复或执行代码的操作，在使用现有容器前都要求两个 label 同时匹配。因此，镜像无法仅通过预先声明公开的策略 label 来通过检查。旧容器仍可进行只读检查和 cleanup，以便安全诊断和删除。

精确接口请参阅
[直接 Podman 策略](../docs/direct-policy.md)、
[Compose 策略](../docs/compose-policy.md)和
[威胁模型](../docs/threat-model.md)。

<a id="install"></a>

## 安装

需要 Linux、rootless Podman 6.1.x、`podman-compose` 1.6.x、支持虚拟环境的
Python 3.10 或更新版本以及 Bash。如需 DevPod，还需要 DevPod 和 OpenSSH
客户端工具。已测试 DevPod 0.6.15；其他版本也可使用。在仓库目录中，以普通用户运行：

```bash
./install.sh install
```

也可以直接运行不带参数的 `./install.sh`：它会显示状态和菜单，未安装时提供
`install`，已安装时提供 `update` / `uninstall`。按 Enter 可退出而不做更改。
没有交互式终端时，只显示状态和可用操作；脚本中应明确指定操作。

`./install.sh --help` 按操作说明各个选项。提供程序和 wheel 选项适用于
`install` / `update`；`--without-devpod` 和 `--backup-existing` 仅适用于
`install`，`--devpod-ssh-config` 仅适用于 `uninstall`。普通安装无需额外选项。

安装程序会自动查找 Podman、Compose 和已安装的 DevPod，下载 Python 工具及依赖，
构建应用并安装到独立环境。无需手动准备软件包、校验和或 Python 环境。
已有的命令文件会在确认后备份，并在卸载时恢复。

预览操作，不下载或修改文件：

```bash
./install.sh install --dry-run
```

### 命令位置

默认命令目录是 `${XDG_BIN_HOME:-$HOME/.local/bin}`。
将其放在 `PATH` 最前面，以使用受保护的命令：

```bash
export PATH="${XDG_BIN_HOME:-$HOME/.local/bin}:$PATH"
command -v podman docker compose-guard
```

如果该目录还不在最前面，请将 `export` 行加入 shell 配置。
应用位于 `${XDG_DATA_HOME:-$HOME/.local/share}/paranoid-podman`。

### 更新或卸载

从包含所需版本的仓库目录运行：

```bash
./install.sh update
./install.sh status
```

卸载并恢复备份的命令：

```bash
./install.sh uninstall
```

运行 `./uninstall.sh` 可检查状态并显示 `uninstall` / `Exit` 菜单。
按 Enter 可退出而不做更改；没有交互式终端时只显示信息。
`./uninstall.sh --help` 说明卸载选项；`./uninstall.sh --dry-run`
可在菜单中选择卸载后预览操作。

更新和卸载也支持 `--dry-run`。
安装文件如被修改，会报告问题而不会直接删除。若要为原本未包含 DevPod 的安装添加
DevPod，请在 DevPod 可用后卸载并重新安装应用；之后需要重新创建受保护的容器。

### 可选设置

`--without-devpod` 仅安装 Podman 和 Compose 防护。`--devpod PATH` 指定不在
`PATH` 中的可执行文件；`--podman` 和 `--compose-provider` 覆盖自动查找结果。
`--backup-existing` 允许备份而不进行交互确认。使用自定义目录时，保持 `--bindir`
和 `--libdir` 一致。

默认 DevPod 主目录中的 SSH 配置会自动恢复。对于其他 `DEVPOD_HOME` 中的 context，
请指定每个自定义 SSH 配置路径：

```bash
./install.sh uninstall --devpod-ssh-config /absolute/path/to/ssh-config
```

[高级离线安装指南](../docs/wheel-packaging.md)介绍了预先准备软件包的方式。
正常安装不需要这些选项。

<a id="usage"></a>

## 使用

请从实际存在的项目目录运行命令。将 `localhost/my-dev-image:latest` 替换为
本地已有的镜像：直接运行使用 `--pull=never`。Compose 命令需要配置文件和名为
`app` 的服务。

```bash
podman run --rm -v .:/workspace localhost/my-dev-image:latest
podman compose up
podman compose ps
podman compose exec app sh
podman compose run --rm --build app
podman compose down
```

Compose 一次性任务支持 profile、`--no-deps` 以及环境、用户、工作目录和端口选项。`run --build` 在构建失败时停止。支持 `up
--force-recreate` 和显式重置 `down --volumes --remove-orphans`；后者会删除项目卷。

为了兼容相关工作流，还会安装 `docker`、`podman-compose` 和
`docker-compose` 别名。已审核接口之外的命令会被拒绝；需要管理主机时，请有意地直接调用真实 Podman 二进制文件。

直接 Podman 和 Compose 的策略拒绝以状态码 125 退出，并显示原因及不含秘密值的
`next step:` 提示。
受保护的直接 run 使用 `--pull=never`，因此镜像必须已经存在。DevPod 的镜像 pull/build lifecycle 仅开放经过窄范围审核的形式。

检查并修正构建上下文的排除规则：

```bash
paranoid-podman build-context audit .
paranoid-podman build-context protect .
```

`protect` 提供路径多选；`--all` 无需询问即可应用所有检测到的排除项。
它只修改标准 ignore 文件，不会绕过策略。

<a id="devpod"></a>

## DevPod

新建的 DevPod 容器使用 workspace 名称作为 hostname。明确指定的 hostname
会保留；名称需要缩短或规范化时才添加短哈希。现有容器需重新创建才能应用此更改。

包装器处理经过审查的 DevPod 0.6.15 Docker 和 Compose 驱动命令形式，包括：

- 容器发现、inspect、start、stop、logs、exec 和删除；
- 镜像 inspect、pull、build、tag 和 push；
- 用于配置 `/etc/passwd` 和 `/etc/group` 的私有复制流程；
- Compose 项目查询、项目名称、项目 `.env` 和生成的 override 文件；以及
- Compose build、up、stop 和 down 操作。

DevPod 不会绕过通用创建策略。请求 privileged mode、host namespace、引擎 socket、危险 capability 或宽泛 host mount 的 workspace 仍会被拒绝。

只读子挂载和 provenance label 在容器创建时应用。安装此机制后，旧的 DevPod workspace 必须重新创建，才能通过 wrapper 启动或进入。完整重装后也一样，因为重装会获得新的安装标识。Cleanup 命令仍然可用。若要让 `.git` 保持可写，请使用 `PODMAN_GUARD_PROTECT_GIT=0` 启动 DevPod。

### SSH 凭据隔离

IDE 仍由 DevPod 根据默认设置、工作区配置或 `--ide` 选项选择，包装器不要求使用
某个编辑器。IDE-only 模式的首次打开和重新连接已在 Codium 中手动验证；其他 IDE
组合和完整的 Compose 驱动工作区流程仍需验收。Codium 与 Open Remote - SSH 0.1.2
组合中发现了项目代理套接字生命周期问题：
[DEV-001](../KNOWN_ISSUES.md#dev-001-vscode-loses-the-project-ssh-agent-socket)。
新的 OpenSSH 和 `devpod ssh` 会话已验证可使用一个项目密钥。

交互菜单：**1** 创建项目密钥，**2** 保留 IDE 访问但不暴露主机凭据，**3** 选择已有
项目密钥，**4** 准确输入 `y` 后仅本次开放整个主机代理，**5** 取消。
直接按 Enter 会选择 1；要使用 IDE-only，请明确选择 **2**。

项目模式会启动专用 `ssh-agent`，其中恰好只有一个经过验证的身份。私钥
不会被复制或挂载到容器中，但 workspace 内的代码仍可请求 agent 使用该
密钥签名。请在 GitHub、GitLab 或 Gitea 中将公钥限制到单个仓库。

受保护模式会在所选 DevPod context 中禁用 Git 和 registry 凭据、GPG-agent
以及 SSH 签名密钥的自动注入。没有已配置模式的独立 `devpod build` 不会
要求交互选择，也不会把主机凭据转发到 workspace。

为防止 DevPod 在 SSH 配置块受到保护前打开 IDE，包装器先以
`--open-ide=false` 创建 workspace，限制该配置块，然后在不重建容器、
不再次写入 SSH 配置的情况下打开 IDE。

选择的现有私钥必须专用于该项目，且不能直接放在 `~/.ssh` 下；可以放在其子目录中。

```bash
paranoid-podman devpod audit WORKSPACE
paranoid-podman devpod configure WORKSPACE
paranoid-podman devpod key show WORKSPACE
paranoid-podman devpod key stop WORKSPACE
```

状态从 DevPod SSH 配置块和 `~/.ssh/paranoid-podman/` 中保存的密钥推断，
不会创建额外的策略配置文件。详情见
[DevPod 凭据隔离](../docs/devpod-credentials.md)（英文）。

将 `WORKSPACE` 替换为已有工作区 ID。需要时指定一致的 `--context`、
`--devpod-home` 和 `--ssh-config`。新建本地项目时，传给 `devpod up` 的目录必须存在；
否则可能被解释为仓库 URL。在首次交互式 `up` 中选择 IDE-only：DevPod 创建 SSH
配置块之前，`configure` 无法保存该选择。参阅
[本地工作区示例](../docs/devpod-credentials.md#open-a-local-workspace)。

受保护模式也会禁用自动搜索私钥；上下文设置会影响该上下文中的所有工作区。

<a id="compatibility"></a>

## 兼容性

| 组件 | 兼容性 |
| --- | --- |
| 操作系统 | Linux |
| Python | 3.10 及以上 |
| Podman | Rootless 6.1.x |
| Compose provider | 通过 `podman compose` 使用 `podman-compose` 1.6.x |
| DevPod | 已测试 0.6.15；接受其他版本；经过审核的 Docker/Compose 命令和 IDE 限制见上文 |
| Docker Compose v2 | 不支持 |

安装程序会拒绝未经审核的 Podman 或 Compose major/minor 系列。Wrapper 依赖 provider 的 CLI 行为，因此每个新系列都需要重新审核和进行兼容性测试。

其他 DevPod 版本的兼容性尚未验证。凭据访问和允许命令的检查仍然有效。

<a id="limitations"></a>

## 限制

- ignore 检查要求完整排除根目录中的敏感路径。重新包含子级文件的例外规则或未经审核的模式
  可能需要在最后添加明确的排除规则。附加镜像上下文必须使用明确的传输前缀。参阅
  [构建限制](../docs/direct-policy.md#build-boundary)。
- 代理即使不泄露私钥，也能使用项目身份签名。仓库访问范围必须由 Git 托管服务限制。
- `PATH` 中的 wrapper 不是沙箱。以您的用户身份运行的主机进程可以调用真实 Podman，或直接访问相同文件。
- Wrapper 无法防御内核、Podman、OCI runtime、镜像、parser 或零日漏洞。
- 普通容器网络不是出站流量沙箱。
- Build 可以执行任意镜像指令，并读取未被 `.containerignore` 或 `.dockerignore` 排除的任何 context 路径。Guard 只检查根目录中的常见凭据路径和明显的字面量赋值。
- 以前创建或已在运行的容器不会获得新的 mount 保护。主动 guarded lifecycle 命令会拒绝缺少当前策略和安装 provenance 的容器。
- Provenance label 是本地兼容性标记，不是加密签名。已经以主机用户身份运行的进程可以读取、复制或绕过它们。未显式提供安装 ID 而直接从源码树运行 guard 时，会使用确定性的开发 fallback；要获得每次安装独立的 provenance，请使用安装程序。
- 文件可能在验证与 provider 执行之间发生变化。
- 支持的接口有意小于完整的 Podman 和 Compose CLI。

需要更强隔离时，请使用一次性 VM 或单独的低权限账户。

<a id="development"></a>

## 开发

快速本地检查：

```bash
scripts/test.sh
scripts/check.sh syntax
```

本地测试使用 fake provider 和临时 home/config 目录，并禁用继承的集成测试开关。`scripts/check.sh` 默认使用 `local` 模式，包含 Ruff, mypy、Bandit、ShellCheck、离线 `zizmor` 和隐藏密钥值的 Gitleaks 源码扫描。`scripts/audit.sh dependencies` 显式运行联网审计；`scripts/check.sh all` 会添加该审计，但仍不运行真实集成测试。

`scripts/format.sh` 应用格式化。请按照 [CONTRIBUTING.md](../CONTRIBUTING.md) 单独准备工具。CI 将测试、静态检查、密钥扫描和依赖审计分开运行。

实现代码位于 `src/paranoid_podman`。请参阅[代码结构](../docs/architecture.md)和 [wheel 构建及离线安装说明](../docs/wheel-packaging.md)。

| 目录 | 用途 |
| --- | --- |
| `src/paranoid_podman/` | 应用代码 |
| `bin/` | 从源码运行和测试使用的小型启动器 |
| `dist/` | 生成的 wheel 和校验和 |

安装器会在 `--bindir` 中另行生成命令，通过私有 Python 环境运行应用。
`bin/` 仍是源码树的一部分。

`scripts/test.sh compose-provider` 显式选择真实 Compose provider 的重新解析检查。它可能查询 Podman，但不会启动容器；请在使用已审核版本的一次性环境中运行。
这些测试还使用真实 Compose 和记录调用的模拟引擎检查日常流程，并已加入 CI。

可选的 runtime suite 需要一个已经存在并包含 `sh` 与 `sleep` 的本地镜像。它绝不会拉取镜像，只使用名称唯一的一次性容器：

```bash
PARANOID_PODMAN_TEST_IMAGE=docker.io/library/alpine:latest \
  scripts/test.sh rootless
```

请只在一次性的 rootless 账户中，使用已审核的 Podman 和 Compose 版本运行它，并确保该账户没有重要容器或凭据。

SSH 代理测试会创建临时密钥和套接字，不会读取日常使用的 SSH 密钥：

```bash
scripts/test.sh ssh-agent
```

剩余工作见 [TODO.md](../TODO.md)，贡献指南见
[CONTRIBUTING.md](../CONTRIBUTING.md)。

<a id="security"></a>

## 安全

请按照 [SECURITY.md](../SECURITY.md) 中的流程报告疑似漏洞。不要在公开 issue 中发布有效凭据、私有项目数据或 exploit 细节。

<a id="license"></a>

## 许可证

[MIT](../LICENSE)
