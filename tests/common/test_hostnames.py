import unittest

from paranoid_podman.common.hostnames import workspace_hostname


class WorkspaceHostnameTests(unittest.TestCase):
    def test_workspace_name_is_preserved_and_normalization_is_bounded(self):
        for name in ("example-workspace", "a", "a" * 63):
            self.assertEqual(workspace_hostname(name), name)
        for name in ("Example_Workspace", "a" * 128, "工作区", "../app\n--privileged"):
            hostname = workspace_hostname(name)
            self.assertLessEqual(len(hostname), 63)
            self.assertRegex(hostname, r"^[a-z0-9][a-z0-9-]*-[0-9a-f]{8}$")
            self.assertEqual(hostname, workspace_hostname(name))
        self.assertNotEqual(workspace_hostname("my_app"), workspace_hostname("my.app"))
        for value in (None, "", 10, "a" * 4097):
            self.assertIsNone(workspace_hostname(value))
