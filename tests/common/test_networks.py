"""The loopback exception accepts ports, not arbitrary pasta arguments."""

import unittest

from paranoid_podman.common.networks import loopback_tcp_port


class LoopbackNetworkTests(unittest.TestCase):
    def test_accepts_the_whole_port_range_without_project_specific_exceptions(self):
        for port in range(1, 65536):
            self.assertEqual(loopback_tcp_port(f"pasta:-T,{port}"), port)

    def test_rejects_broader_forwarding_and_argument_injection(self):
        for value in (
            None,
            3000,
            True,
            [],
            {},
            "host",
            "bridge",
            "pasta",
            "slirp4netns",
            "pasta:-T,0",
            "pasta:-T,65536",
            "pasta:-T,03000",
            "pasta:-T,+3000",
            "pasta:-T,٣٠٠٠",
            "pasta:-T,3000\n",
            "pasta:-T,127.0.0.1/3000",
            "pasta:-T,all",
            "pasta:-T,auto",
            "pasta:-T,3000-3005",
            "pasta:-T,3000:4000",
            "pasta:-T,3000,4000",
            "pasta:-T,0.0.0.0/3000",
            "pasta:-T,::1/3000",
            "pasta:-T,127.0.0.2/3000",
            "pasta:-U,127.0.0.1/3000",
            "pasta:-t,127.0.0.1/3000",
            "pasta:-T,3000,-T,127.0.0.1/4000",
            "pasta:-T,3000,--map-gw",
            "pasta:--map-host-loopback,169.254.1.2",
            "pasta:-T,3000,--netns,/proc/1/ns/net",
        ):
            with self.subTest(value=value):
                self.assertIsNone(loopback_tcp_port(value))
