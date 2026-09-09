"""Provider call ordering and redaction across the Podman execution boundary."""

import os
import subprocess
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from paranoid_podman.common.provenance import expected_provenance_output
from paranoid_podman.podman import execution
from paranoid_podman.podman.errors import PolicyViolation


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.environment = {"APP_MODE": "test", "TOKEN": "synthetic-value"}
        stack.enter_context(patch.dict(os.environ, self.environment, clear=True))
        self.query = stack.enter_context(
            patch.object(
                execution.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    [], 0, expected_provenance_output() + "\n", ""
                ),
            )
        )
        self.execute = stack.enter_context(
            patch.object(execution.os, "execve", side_effect=SystemExit(0))
        )

    def test_start_checks_each_target_before_validating_the_next(self):
        with self.assertRaisesRegex(PolicyViolation, "invalid container name"):
            execution.execute_start(["start", "reviewed", "--invalid"], 0)
        self.query.assert_called_once()
        self.assertEqual(self.query.call_args.args[0][-1], "reviewed")
        self.execute.assert_not_called()

    def test_start_queries_all_targets_before_final_execution(self):
        order = []
        query_result = self.query.return_value

        def query(arguments, **_kwargs):
            order.append(arguments[-1])
            return query_result

        def execute(*_args):
            order.append("execute")
            raise SystemExit(0)

        self.query.side_effect = query
        self.execute.side_effect = execute
        arguments = ["container", "start", "first", "second"]
        with self.assertRaises(SystemExit):
            execution.execute_start(arguments, 1)
        self.assertEqual(order, ["first", "second", "execute"])
        self.execute.assert_called_once_with(
            execution.REAL_PODMAN,
            [execution.REAL_PODMAN, *arguments],
            {"APP_MODE": "test"},
        )

    def test_exec_denies_invalid_options_before_querying_provenance(self):
        with self.assertRaisesRegex(PolicyViolation, "implicit host environment"):
            execution.execute_exec(["exec", "--env", "APP_MODE", "box", "env"], 0)
        self.query.assert_not_called()
        self.execute.assert_not_called()

    def test_exec_preserves_container_command_tokens_and_query_environment(self):
        arguments = ["container", "exec", "-it", "--", "box", "tool", "--privileged"]
        with self.assertRaises(SystemExit):
            execution.execute_exec(arguments, 1)
        self.query.assert_called_once()
        self.assertEqual(self.query.call_args.args[0][-1], "box")
        self.assertEqual(self.query.call_args.kwargs["env"], {"APP_MODE": "test"})
        self.execute.assert_called_once_with(
            execution.REAL_PODMAN,
            [execution.REAL_PODMAN, *arguments],
            {"APP_MODE": "test"},
        )

    def test_copy_rejects_container_path_before_provenance(self):
        with self.assertRaisesRegex(PolicyViolation, "outside DevPod account setup"):
            execution.execute_copy(["cp", "box:/unreviewed", "relative-file"])
        self.query.assert_not_called()
        self.execute.assert_not_called()

    def test_copy_checks_provenance_before_host_file_metadata(self):
        for returncode, reason in (
            (0, "invalid DevPod temporary file"),
            (1, "could not verify existing container provenance"),
        ):
            with self.subTest(returncode=returncode):
                self.query.reset_mock()
                self.query.return_value.returncode = returncode
                with self.assertRaisesRegex(PolicyViolation, reason):
                    execution.execute_copy(["cp", "box:/etc/passwd", "relative-file"])
                self.query.assert_called_once()
                self.execute.assert_not_called()

    def test_provenance_transport_errors_are_redacted_and_stop_execution(self):
        for error in (
            OSError("synthetic-private-marker"),
            subprocess.TimeoutExpired(["synthetic-private-marker"], 30),
        ):
            with self.subTest(error=type(error).__name__):
                self.query.side_effect = error
                with self.assertRaises(PolicyViolation) as caught:
                    execution.execute_start(["start", "box"], 0)
                self.assertEqual(
                    str(caught.exception),
                    "failed to verify existing container provenance",
                )
                self.execute.assert_not_called()

    def test_compose_delegation_restores_only_its_installation_identity(self):
        command = ["isolated-python", "-I", "-B", "-m", "guard", "compose-guard"]
        with (
            patch.object(execution, "command_arguments", return_value=command),
            self.assertRaises(SystemExit),
        ):
            execution.execute_compose(["--file", "compose.yaml", "ps"])
        self.execute.assert_called_once_with(
            command[0],
            [*command, "--file", "compose.yaml", "ps"],
            {
                "APP_MODE": "test",
                "PODMAN_GUARD_INSTALLATION_ID": execution.GUARD_INSTALLATION_ID,
            },
        )
        self.query.assert_not_called()
