"""External bind warnings retain source marks and require deliberate terminal input."""

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from paranoid_podman.compose.cli import run_project_command
from paranoid_podman.compose.errors import PolicyViolation
from paranoid_podman.compose.models import Invocation
from paranoid_podman.compose.mount_locations import (
    bind_locations,
    source_mount_locations,
)
from paranoid_podman.compose.review import confirm_external_binds


class MountLocationTests(unittest.TestCase):
    def test_aliases_and_interpolated_short_and_long_sources_keep_original_lines(self):
        source = """x-volume: &volume
  type: bind
  source: ${SHARED_DIR:-../shared}
  target: /long
x-service: &service
  volumes:
    - ${SHARED_DIR:-${FALLBACK:-../shared}}:/short:ro
    - *volume
services:
  app:
    <<: *service
"""
        locations = source_mount_locations(yaml.compose(source), Path("compose.yaml"))[
            "app"
        ]
        self.assertEqual(
            [(item.line, item.target) for item in locations],
            [(7, "/short"), (3, "/long")],
        )

    def test_override_locations_and_interpolated_targets(self):
        source = """services:
  app:
    volumes:
      - ../shared:/data
      - ${WHOLE_MOUNT}
      - type: bind
        source: ../other
        target: ${TARGET:-/other}
"""
        locations = source_mount_locations(yaml.compose(source), Path("compose.yaml"))[
            "app"
        ]
        self.assertEqual(
            [item.line for item in bind_locations(locations, "/other")], [5, 7]
        )
        override = "services:\n  app:\n    volumes: ['../new:/data']\n"
        locations.extend(
            source_mount_locations(yaml.compose(override), Path("override.yaml"))["app"]
        )
        matched = bind_locations(locations, "/data")
        self.assertEqual(
            [(item.file.name, item.line) for item in matched], [("override.yaml", 3)]
        )

    def test_escaped_dollar_is_literal(self):
        source = "services:\n  app:\n    volumes: ['../shared:/$$DATA']\n"
        locations = source_mount_locations(yaml.compose(source), Path("compose.yaml"))[
            "app"
        ]
        self.assertEqual(len(bind_locations(locations, "/$DATA")), 1)
        self.assertEqual(bind_locations(locations, "/other"), [])


class ExternalBindTerminalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / "project"
        self.project.mkdir()
        self.source_file = self.project / "compose.yaml"
        self.source_file.write_text(
            "services:\n  app:\n    image: example.invalid/image\n    volumes: ['../shared:/shared']\n",
            encoding="utf-8",
        )
        self.invocation = Invocation(
            "up", [], [self.source_file], None, [], self.project, "example", []
        )
        self.origins = source_mount_locations(
            yaml.compose(self.source_file.read_text()), self.source_file
        )
        self.model = {
            "services": {
                "app": {
                    "volumes": [
                        {
                            "type": "bind",
                            "source": str(self.project.parent / "shared"),
                            "target": "/shared",
                        }
                    ]
                }
            }
        }

    def test_orange_heading_respects_terminal_no_color_and_dumb_term(self):
        for tty, environment, colored in (
            (True, {"TERM": "xterm-256color"}, True),
            (False, {}, False),
            (True, {"NO_COLOR": ""}, False),
            (True, {"TERM": "dumb"}, False),
        ):
            with self.subTest(tty=tty, environment=environment):
                output = io.StringIO()
                output.isatty = lambda tty=tty: tty
                with (
                    patch("sys.stderr", output),
                    patch("sys.stdin.isatty", return_value=False),
                    patch.dict(os.environ, environment, clear=True),
                    self.assertRaises(PolicyViolation),
                ):
                    confirm_external_binds(self.invocation, self.model, self.origins)
                self.assertEqual("\033[1;38;5;208m" in output.getvalue(), colored)
                self.assertIn("[warning]", output.getvalue())

    def test_noninteractive_input_is_never_read_even_if_it_contains_y(self):
        input_stream = io.StringIO("y\ncontainer input\n")
        with (
            patch("sys.stdin", input_stream),
            patch("sys.stderr", io.StringIO()),
            self.assertRaises(PolicyViolation),
        ):
            confirm_external_binds(self.invocation, self.model, self.origins)
        self.assertEqual(input_stream.tell(), 0)

    def test_eof_errors_and_interrupt_never_approve(self):
        for reads in ([b""], [b"y", b""], OSError(), KeyboardInterrupt()):
            with self.subTest(reads=reads):
                with (
                    patch("sys.stdin.isatty", return_value=True),
                    patch("os.read", side_effect=reads),
                    patch("sys.stderr", io.StringIO()),
                    self.assertRaises((PolicyViolation, KeyboardInterrupt)),
                ):
                    confirm_external_binds(self.invocation, self.model, self.origins)

    def test_approval_reads_only_its_own_line(self):
        data = iter([b"y", b"\n", b"c"])
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("os.read", side_effect=lambda *_: next(data)),
            patch("sys.stderr", io.StringIO()),
        ):
            confirm_external_binds(self.invocation, self.model, self.origins)
        self.assertEqual(next(data), b"c")

    def test_paths_cannot_inject_terminal_control_sequences(self):
        self.model["services"]["app"]["volumes"][0]["source"] += "\n\033[2J\u202e"
        output = io.StringIO()
        with (
            patch("sys.stdin.isatty", return_value=False),
            patch("sys.stderr", output),
            self.assertRaises(PolicyViolation),
        ):
            confirm_external_binds(self.invocation, self.model, self.origins)
        self.assertNotIn("\033", output.getvalue())
        self.assertNotIn("\u202e", output.getvalue())
        self.assertIn("\\u001b[2J\\u202e", output.getvalue())

    def test_writable_child_is_not_hidden_by_a_read_only_parent_warning(self):
        volumes = self.model["services"]["app"]["volumes"]
        volumes[0]["read_only"] = True
        volumes.append(
            {
                "type": "bind",
                "source": str(self.project.parent / "shared" / "data"),
                "target": "/shared/data",
                "read_only": False,
            }
        )
        source = "services:\n  app:\n    volumes: ['../shared:/shared:ro', '../shared/data:/shared/data:rw']\n"
        origins = source_mount_locations(yaml.compose(source), self.source_file)
        output = io.StringIO()
        with (
            patch("sys.stdin.isatty", return_value=False),
            patch("sys.stderr", output),
            self.assertRaises(PolicyViolation),
        ):
            confirm_external_binds(self.invocation, self.model, origins)
        self.assertEqual(output.getvalue().count("Host path:"), 2)
        self.assertIn("read-only", output.getvalue())
        self.assertIn("read-write", output.getvalue())

    def test_edits_during_confirmation_do_not_change_the_executed_snapshot(self):
        (self.project.parent / "shared").mkdir()
        rendered = self.source_file.read_text()
        answers = iter([b"y", b"\n"])

        def answer(*_):
            self.source_file.write_text(
                "services:\n  app:\n    privileged: true\n", encoding="utf-8"
            )
            return next(answers)

        def execute(_invocation, snapshot):
            service = yaml.safe_load(snapshot.read_text())["services"]["app"]
            self.assertNotIn("privileged", service)
            self.assertEqual(
                service["volumes"][0]["source"], str(self.project.parent / "shared")
            )
            return 23

        with (
            patch("paranoid_podman.compose.cli.render_config", return_value=rendered),
            patch(
                "paranoid_podman.compose.cli.require_current_policy_project_containers"
            ),
            patch("paranoid_podman.compose.cli.require_safe_named_networks"),
            patch("paranoid_podman.compose.cli.run_snapshot", side_effect=execute),
            patch("sys.stdin.isatty", return_value=True),
            patch("os.read", side_effect=answer),
            patch("sys.stderr", io.StringIO()),
        ):
            self.assertEqual(run_project_command(self.invocation), 23)
