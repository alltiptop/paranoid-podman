"""Architecture rules enforced without importing or running application code."""

import ast
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src/paranoid_podman"
DOMAINS = {"common", "podman", "compose", "devpod", "management", "lifecycle"}
ALLOWED_DOMAINS = {
    "common": {"common"},
    "podman": {"common", "podman"},
    "compose": {"common", "compose"},
    "devpod": {"common", "devpod"},
    "management": {"common", "devpod", "management"},
    "lifecycle": {"common", "devpod", "lifecycle"},
}
PROCESS_MODULES = {
    "podman.execution",
    "compose.provider",
    "devpod.agent",
    "devpod.cli",
    "devpod.keys",
    "devpod.process",
    "devpod.provider",
    "devpod.session",
    "lifecycle.devpod",
    "lifecycle.providers",
    "lifecycle.runtime",
    "lifecycle.bootstrap",
}
TOOLING = {
    "build",
    "setuptools",
    "wheel",
    "pip",
    "pytest",
    "mypy",
    "ruff",
    "bandit",
    "coverage",
    "pylint",
    "flake8",
    "pylsp",
    "scripts",
    "tests",
}


def imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise AssertionError(
                    "use absolute imports so dependency owners are explicit"
                )
            yield node.module or ""
            yield from (f"{node.module}.{item.name}" for item in node.names)


class DependencyBoundaryTests(unittest.TestCase):
    def test_runtime_dependencies_and_process_owners(self):
        for path in PACKAGE.rglob("*.py"):
            module = ".".join(path.relative_to(PACKAGE).with_suffix("").parts)
            domain = module.split(".")[0]
            tree = ast.parse(path.read_text())
            with self.subTest(module=module):
                for target in imports(tree):
                    self.assertNotIn(target.split(".")[0], TOOLING)
                    if target == "subprocess" or target.startswith("subprocess."):
                        self.assertIn(module, PROCESS_MODULES)
                    if not target.startswith("paranoid_podman."):
                        continue
                    dependency = target.removeprefix("paranoid_podman.")
                    other = dependency.split(".")[0]
                    if domain in DOMAINS and other in DOMAINS:
                        self.assertIn(other, ALLOWED_DOMAINS[domain])
                    if domain == "lifecycle" and other == "devpod":
                        self.assertTrue(
                            dependency.startswith(
                                ("devpod.errors.", "devpod.ssh_config.")
                            )
                            or dependency in {"devpod.errors", "devpod.ssh_config"}
                        )
                for node in ast.walk(tree):
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "os"
                        and (
                            node.func.attr.startswith(("exec", "spawn"))
                            or node.func.attr in {"system", "popen", "fork"}
                        )
                    ):
                        self.assertIn(module, PROCESS_MODULES)

    def test_application_import_graph_is_acyclic(self):
        sources = {
            "paranoid_podman."
            + ".".join(path.relative_to(PACKAGE).with_suffix("").parts): ast.parse(
                path.read_text()
            )
            for path in PACKAGE.rglob("*.py")
        }
        graph = {
            name: set(imports(tree)) & sources.keys() for name, tree in sources.items()
        }
        visited = set()

        def visit(name, stack):
            self.assertNotIn(name, stack, " -> ".join((*stack, name)))
            if name in visited:
                return
            for dependency in graph[name]:
                visit(dependency, (*stack, name))
            visited.add(name)

        for name in graph:
            visit(name, ())
