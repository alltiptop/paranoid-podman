"""File-validation ordering and errors shared by all build interfaces."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paranoid_podman.common.build_inputs import (
    inspect_build_context,
    validate_build_context,
    validate_build_inputs,
)
from paranoid_podman.common.dockerfile import lint_dockerfile
from paranoid_podman.common.errors import BuildInputViolation, ViolationCategory


class BuildInputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.context = Path(temporary.name)
        self.dockerfile = self.context / "Dockerfile"

    def test_context_rejection_precedes_dockerfile_rejection(self):
        (self.context / ".env").touch()
        self.dockerfile.write_text("ENV API_KEY=synthetic-marker\n", encoding="utf-8")
        with self.assertRaisesRegex(
            BuildInputViolation, "sensitive build-context"
        ) as caught:
            validate_build_inputs(self.context, self.dockerfile)
        self.assertIs(caught.exception.category, ViolationCategory.BUILD_CONTEXT)
        (self.context / ".containerignore").write_text(".env\n", encoding="utf-8")
        with self.assertRaises(BuildInputViolation) as caught:
            validate_build_inputs(self.context, self.dockerfile)
        self.assertIs(caught.exception.category, ViolationCategory.SECRET)
        self.assertEqual(
            str(caught.exception),
            "blocked literal secret assigned to API_KEY in Dockerfile line 1",
        )

    def test_containerignore_precedence_and_ordered_negation(self):
        for name in (".env", ".env.local", ".env.example"):
            (self.context / name).touch()
        (self.context / ".dockerignore").write_text(".env*\n", encoding="utf-8")
        containerignore = self.context / ".containerignore"
        containerignore.write_text(".env*\n!.env.local\n", encoding="utf-8")
        self.assertEqual(
            inspect_build_context(self.context), (containerignore, [".env.local"])
        )
        containerignore.write_text(".env*\n!.env.local\n.env.local\n", encoding="utf-8")
        self.assertEqual(inspect_build_context(self.context), (containerignore, []))

    def test_control_file_encoding_size_and_nul_errors(self):
        for data, category in (
            (b"\xff", "non-UTF-8"),
            (b"\x00", "containing a NUL byte"),
            (b"#" * (1024 * 1024 + 1), "larger than 1 MiB"),
        ):
            for kind, path, validate in (
                (
                    "Dockerfile",
                    self.dockerfile,
                    lambda: lint_dockerfile(self.dockerfile),
                ),
                (
                    "build ignore file",
                    self.context / ".containerignore",
                    lambda: inspect_build_context(self.context),
                ),
            ):
                with self.subTest(kind=kind, category=category):
                    path.write_bytes(data)
                    with self.assertRaises(BuildInputViolation) as caught:
                        validate()
                    expected = (
                        f"blocked {category} {kind}"
                        if category == "non-UTF-8"
                        else f"blocked {kind} {category}"
                    )
                    self.assertEqual(str(caught.exception), expected)

    def test_sensitive_subtree_requires_a_final_complete_exclusion(self):
        (self.context / ".ssh").mkdir()
        ignore = self.context / ".containerignore"
        cases = (
            (".ssh\n", True),
            ("*\n!.ssh/project-key\n", False),
            (".ssh\n!.ssh/nested/project-key\n", False),
            (".ssh\n!.s?h/project-key\n", False),
            (".ssh\n!**/project-key\n", False),
            (".ssh\n!src/**\n", True),
            (".ssh\n!.ssh/project-key\n.ssh/project-key\n", False),
            (".ssh\n!.ssh/project-key\n.ssh\n", True),
            ("src/.ssh\n", False),
            ("**/.ssh\n", True),
            (".ssh/**\n", False),
            (".s[s-z]h\n", True),
            (".s[^a-r]h\n", True),
            (".s[!s]h\n", False),
            (".ssh\n!src/(file|.ssh)\n", False),
            (".ssh\n!src/(file|.ssh)\n.ssh\n", True),
            ("/.ssh/\n", True),
            (" .ssh \n", True),
            (" / .ssh / \n", False),
            ("dir/../.ssh\n", True),
            (".ssh\ndir/../!.ssh\n", False),
            (".ssh\n!.ssh/../src\n", True),
            ("# comment\v.ssh\n", False),
            (".ssh\u2028unrelated\n", False),
            (".ssh\n!\n", False),
        )
        for patterns, excluded in cases:
            with self.subTest(patterns=patterns):
                ignore.write_text(patterns, encoding="utf-8")
                self.assertEqual(
                    inspect_build_context(self.context),
                    (ignore, [] if excluded else [".ssh"]),
                )

    def test_dockerfile_specific_ignore_rules_take_precedence(self):
        (self.context / ".ssh").mkdir()
        (self.context / ".containerignore").write_text(".ssh\n", encoding="utf-8")
        self.dockerfile.write_text("FROM scratch\n", encoding="utf-8")
        specific = self.context / "Dockerfile.containerignore"
        specific.write_text("*\n!.ssh/project-key\n", encoding="utf-8")
        with self.assertRaises(BuildInputViolation):
            validate_build_inputs(self.context, self.dockerfile)
        docker_ignore = self.context / "Dockerfile.dockerignore"
        docker_ignore.write_text(".ssh\n", encoding="utf-8")
        validate_build_inputs(self.context, self.dockerfile)
        docker_ignore.unlink()
        docker_ignore.symlink_to(self.context / "missing-rules")
        with self.assertRaisesRegex(BuildInputViolation, "invalid build ignore file"):
            validate_build_inputs(self.context, self.dockerfile)

    def test_exception_walk_cannot_reopen_newline_named_sensitive_files(self):
        directory = self.context / ".ssh"
        directory.mkdir()
        (directory / "key\nsynthetic-marker").touch()
        ignore = self.context / ".containerignore"
        ignore.write_text("*\n!.ssh/key\n.ssh\n", encoding="utf-8")
        with self.assertRaises(BuildInputViolation) as caught:
            validate_build_context(self.context)
        self.assertNotIn("synthetic-marker", str(caught.exception))
        self.assertIs(caught.exception.category, ViolationCategory.BUILD_CONTEXT)
        ignore.write_text(".ssh\n", encoding="utf-8")
        validate_build_context(self.context)

    def test_sensitive_tree_name_inspection_is_bounded_and_does_not_follow_links(self):
        directory = self.context / ".ssh"
        directory.mkdir()
        (directory / "key").touch()
        (directory / "outside").symlink_to(self.context, target_is_directory=True)
        (self.context / ".containerignore").write_text(
            "*\n!.ssh/key\n.ssh\n", encoding="utf-8"
        )
        validate_build_context(self.context)
        with patch("paranoid_podman.common.build_inputs.MAX_IGNORED_TREE_ENTRIES", 1):
            with self.assertRaisesRegex(
                BuildInputViolation, "cannot be safely ignored"
            ):
                validate_build_context(self.context)

    def test_build_ignore_symlinks_and_directories_are_rejected(self):
        target = self.context / "rules"
        target.write_text("*\n", encoding="utf-8")
        ignore = self.context / ".containerignore"
        ignore.symlink_to(target)
        with self.assertRaisesRegex(BuildInputViolation, "invalid build ignore file"):
            inspect_build_context(self.context)
        ignore.unlink()
        ignore.mkdir()
        with self.assertRaisesRegex(BuildInputViolation, "invalid build ignore file"):
            inspect_build_context(self.context)

    def test_sensitive_path_diagnostics_hide_control_characters(self):
        (self.context / ".env.\nsynthetic-marker").touch()
        with self.assertRaises(BuildInputViolation) as caught:
            validate_build_context(self.context)
        self.assertEqual(
            str(caught.exception),
            "blocked sensitive build-context path not excluded by "
            ".containerignore or .dockerignore: <sensitive-path>",
        )

    def test_dockerfile_continuations_preserve_instruction_line_numbers(self):
        self.dockerfile.write_text(
            "# escape=`\nFROM scratch\nENV APP_MODE=dev `\n    API_KEY=synthetic-marker\n",
            encoding="utf-8",
        )
        with self.assertRaises(BuildInputViolation) as caught:
            lint_dockerfile(self.dockerfile)
        self.assertEqual(
            str(caught.exception),
            "blocked literal secret assigned to API_KEY in Dockerfile line 3",
        )

    def test_indirection_does_not_allow_literal_defaults(self):
        for value in ("$VALUE", "${VALUE}", "${VALUE?required}", "${VALUE:?required}"):
            with self.subTest(value=value):
                self.dockerfile.write_text(f"ARG API_KEY={value}\n", encoding="utf-8")
                lint_dockerfile(self.dockerfile)
        self.dockerfile.write_text(
            "ARG API_KEY=${VALUE:-synthetic-marker}\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(BuildInputViolation, "literal secret assigned"):
            lint_dockerfile(self.dockerfile)

    def test_cors_dockerfile_settings_do_not_disable_credential_checks(self):
        self.dockerfile.write_text(
            "FROM scratch\nARG CORS_CREDENTIALS=false\nENV CORS_ALLOW_CREDENTIALS=true\n",
            encoding="utf-8",
        )
        lint_dockerfile(self.dockerfile)
        self.dockerfile.write_text("ENV APP_CREDENTIALS=false\n", encoding="utf-8")
        with self.assertRaisesRegex(BuildInputViolation, "literal secret assigned"):
            lint_dockerfile(self.dockerfile)
