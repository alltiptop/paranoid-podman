# Code map

Application code lives under `src/paranoid_podman`. Source commands in `bin/`
and `tools/lifecycle.py` load this checkout's `src`; installed commands use the
private wheel runtime and lazy `__main__.py` dispatcher.

| Directory | Purpose |
| --- | --- |
| `src/` | Packaged application code |
| `bin/` | Source checkout launchers, used by development and tests |
| `dist/` | Generated wheel and checksum; excluded from version control |
| `scripts/` | Build and validation commands |
| `tests/` | Domain tests, shared fake providers, and artifact checks |

The installer generates commands in the selected `--bindir`. Those commands
run the private wheel environment and do not depend on the checkout's `bin/`.

| Package | Owners |
| --- | --- |
| `common` | Environment filtering, host paths, provenance, build inputs, Dockerfile checks, executable identity, and typed rejection guidance |
| `podman` | `arguments` and `commands` parse command forms; `runtime`, `build`, `mounts`, and `validation` enforce policy; `execution` owns provider calls and process replacement |
| `compose` | `arguments` and `options` parse inputs; `source` and `dotenv` preflight files; `policy`, `resources`, `mounts`, and `build` validate models; `snapshot` prepares private files; `mount_locations` retains source lines; `review` confirms external binds; `provider` executes |
| `devpod` | `context` protects provider options; `keys`, `agent`, and `locks` isolate identities; `ssh_config` edits managed blocks; `modes` selects access; `session` protects SSH before opening an IDE |
| `management` | `cli` routes workspace audit/setup and `build_context` inspection/protection through focused DevPod and common APIs |
| `lifecycle` | `providers` discovers external tools; `metadata` validates ownership; `launchers` renders Bash; `releases` verifies and activates releases; `operations` keeps install/update/remove transactions and rollback visible; `ssh_restore` restores managed proxies |
| `lifecycle` delivery | `bootstrap` prepares source installs in temporary tool environments; `artifacts` verifies wheels, RECORD, and runtime locks; `runtime` invokes the external installer and checks isolation; `wheel_release` prepares releases; `integrity` inventories files, modes, and symlinks |

Common code has no domain or process dependencies. Podman and Compose policy
produce checked data without importing execution. Management depends on DevPod;
DevPod does not depend on management. Lifecycle accesses only the DevPod SSH
configuration and error APIs. `tests/test_boundaries.py` enforces domain edges,
process owners, absence of import cycles, and exclusion of development tooling.

Orchestration preserves the order of observable effects: Podman checks start
provenance per target, exec provenance after argument validation, and copy
provenance before host-file checks. Compose preflights sources, renders, validates,
writes mode-0600 snapshots, reviews external binds, then executes while snapshots remain alive.
DevPod keeps setup messages on stderr and protects SSH before the IDE phase.
Lifecycle verifies owned files before changes and rolls back failed activation.

Policy failures carry an explicit `ViolationCategory`; CLI diagnostics use it
without parsing the message. Denial remains 125. Provider stderr is classified
and redacted at the external boundary. Shared environment filters retain separate
guard and lifecycle profiles. Provenance and Git protection retain import-time
settings; provider environments are copied at each call.

Tests mirror the domain packages. Shared temporary fixtures and fake providers
live in `tests/support`; wheel and real-provider gates remain explicit. Build,
installer, and validation environments stay outside the managed runtime.
