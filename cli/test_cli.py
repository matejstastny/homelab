"""the reconcile logic against a stand-in for adguard's api, and .env.

the real thing needs a live box and its web password, so the endpoints pi
actually uses are reimplemented here. what this pins down is the part worth
pinning down: that a plan only ever touches names inside the zone, that a
changed address becomes a delete plus an add rather than two live answers, and
that applying twice is a no-op.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import cli

from . import adguard, manifest


class FakeAdGuard(BaseHTTPRequestHandler):
    rewrites: list[dict[str, str]] = []
    upstream: list[str] = []
    bootstrap: list[str] = []
    calls: list[str] = []

    def log_message(self, *args):
        pass

    def _reply(self, body):
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        type(self).calls.append(f"GET {self.path}")
        if self.path == "/control/rewrite/list":
            return self._reply(type(self).rewrites)
        if self.path == "/control/dns_info":
            return self._reply({"upstream_dns": type(self).upstream})
        self.send_error(404)

    def do_POST(self):
        type(self).calls.append(f"POST {self.path}")
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        cls = type(self)

        if self.path == "/control/rewrite/add":
            cls.rewrites.append({"domain": body["domain"], "answer": body["answer"]})
            return self._reply({})
        if self.path == "/control/rewrite/delete":
            cls.rewrites = [
                r
                for r in cls.rewrites
                if not (r["domain"] == body["domain"] and r["answer"] == body["answer"])
            ]
            return self._reply({})
        if self.path == "/control/dns_config":
            cls.upstream = body["upstream_dns"]
            cls.bootstrap = body["bootstrap_dns"]
            return self._reply({})
        self.send_error(404)


class ReconcileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeAdGuard)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeAdGuard.rewrites = []
        FakeAdGuard.upstream = []
        FakeAdGuard.bootstrap = []
        FakeAdGuard.calls = []
        addresses = {"thebe": "100.64.0.10", "io": "100.64.0.11"}
        self.address_of = patch.object(adguard, "address_of", side_effect=addresses.__getitem__)
        self.address_of.start()
        self.addCleanup(self.address_of.stop)
        self.client = adguard.Client(host="127.0.0.1", user="elara", password="x", port=self.port)
        self.want = adguard.desired()

    def test_empty_instance_gets_every_name(self):
        plan = adguard.plan(self.client, "thebe")
        self.assertEqual(len(plan.add), len(self.want))
        self.assertEqual(plan.remove, [])
        self.assertTrue(plan.upstream_changes)

        adguard.apply(self.client, plan)
        live = {r["domain"]: r["answer"] for r in FakeAdGuard.rewrites}
        self.assertEqual(live, {d: a for d, (a, _) in self.want.items()})

    def test_apply_is_idempotent(self):
        adguard.apply(self.client, adguard.plan(self.client, "thebe"))
        again = adguard.plan(self.client, "thebe")
        self.assertTrue(again.empty, f"second pass still wanted {again.add}{again.remove}")
        self.assertEqual(len(again.keep), len(self.want))

    def test_foreign_names_are_left_alone(self):
        FakeAdGuard.rewrites = [{"domain": "nas.lan", "answer": "192.168.250.9"}]
        plan = adguard.plan(self.client, "leda")
        self.assertEqual(plan.foreign, 1)
        self.assertNotIn("nas.lan", [d for d, _ in plan.remove])

        adguard.apply(self.client, plan)
        self.assertIn({"domain": "nas.lan", "answer": "192.168.250.9"}, FakeAdGuard.rewrites)

    def test_changed_address_replaces_rather_than_duplicates(self):
        domain, (answer, _) = next(iter(sorted(self.want.items())))
        FakeAdGuard.rewrites = [{"domain": domain, "answer": "10.0.0.1"}]

        plan = adguard.plan(self.client, "leda")
        self.assertIn((domain, "10.0.0.1"), plan.remove)
        self.assertIn(domain, [d for d, _, _ in plan.add])

        adguard.apply(self.client, plan)
        answers = [r["answer"] for r in FakeAdGuard.rewrites if r["domain"] == domain]
        self.assertEqual(answers, [answer], "a name must have exactly one answer")

    def test_stale_zone_name_is_removed(self):
        FakeAdGuard.rewrites = [{"domain": "old.elara.boo", "answer": "100.1.2.3"}]
        plan = adguard.plan(self.client, "leda")
        self.assertIn(("old.elara.boo", "100.1.2.3"), plan.remove)

        adguard.apply(self.client, plan)
        self.assertNotIn("old.elara.boo", [r["domain"] for r in FakeAdGuard.rewrites])

    def test_upstream_is_per_host(self):
        resolvers = manifest.dns()["resolvers"]
        for host, upstream in resolvers.items():
            with self.subTest(host=host):
                FakeAdGuard.upstream = []
                adguard.apply(self.client, adguard.plan(self.client, host))
                self.assertEqual(FakeAdGuard.upstream, list(upstream))
                self.assertTrue(FakeAdGuard.bootstrap, "bootstrap must be set alongside")

    def test_no_resolver_points_at_a_loopback_upstream(self):
        """thebe once had unbound on 127.0.0.1:5335 and no longer does.

        a loopback upstream only resolves if something is listening on the box
        itself, which is exactly the thing a removed package stops being true
        of, quietly, at the next reboot.
        """
        for host, upstream in manifest.dns()["resolvers"].items():
            for entry in upstream:
                self.assertFalse(
                    entry.startswith(("127.", "[::1]", "::1")),
                    f"{host} resolves via {entry}, which nothing in hosts/ installs",
                )


class DotenvTest(unittest.TestCase):
    """the repo root's .env, which is where the adguard password lives"""

    def read(self, text: str) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".env").write_text(text)
            with patch.object(cli, "ROOT", Path(tmp)):
                cli.dotenv.cache_clear()
                self.addCleanup(cli.dotenv.cache_clear)
                return cli.dotenv()

    def test_reads_pairs_and_ignores_the_rest(self):
        got = self.read("# a comment\n\nADGUARD_PASSWORD = hunter2  \nQUOTED='x y'\njunk\n")
        self.assertEqual(got, {"ADGUARD_PASSWORD": "hunter2", "QUOTED": "x y"})

    def test_missing_file_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(cli, "ROOT", Path(tmp)):
                cli.dotenv.cache_clear()
                self.addCleanup(cli.dotenv.cache_clear)
                self.assertEqual(cli.dotenv(), {})

    def test_the_environment_wins(self):
        with patch.dict(os.environ, {"ADGUARD_PASSWORD": "from-the-shell"}):
            self.assertEqual(cli.env("ADGUARD_PASSWORD"), "from-the-shell")


class RetryTest(unittest.TestCase):
    """an adguard a sync just restarted needs a moment to accept connections"""

    def setUp(self):
        self.sleeps: list[float] = []
        patcher = patch("cli.adguard.time.sleep", self.sleeps.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = adguard.Client(host="127.0.0.1", user="elara", password="x", port=1)

    def test_a_refused_connection_is_retried_until_it_answers(self):
        refused = urllib.error.URLError(ConnectionRefusedError())
        attempts = [refused, refused, b'{"ok": true}']

        def urlopen(req, timeout):
            result = attempts.pop(0)
            if isinstance(result, Exception):
                raise result
            return BytesIO(result)

        with patch("cli.adguard.urllib.request.urlopen", urlopen):
            self.assertEqual(self.client._call("/status"), {"ok": True})
        self.assertEqual(self.sleeps, [1, 2], "should wait, not spin, between attempts")

    def test_still_refused_after_every_retry_is_a_fault(self):
        refused = urllib.error.URLError(ConnectionRefusedError())
        with patch("cli.adguard.urllib.request.urlopen", side_effect=refused):
            with self.assertRaises(adguard.APIError):
                self.client._call("/status")
        self.assertEqual(len(self.sleeps), len(adguard.RETRY_DELAYS), "one wait per retry")

    def test_a_bad_password_is_not_retried(self):
        unauthorized = urllib.error.HTTPError("url", 401, "unauthorized", {}, None)
        with patch("cli.adguard.urllib.request.urlopen", side_effect=unauthorized):
            with self.assertRaises(adguard.APIError):
                self.client._call("/status")
        self.assertEqual(self.sleeps, [], "a rejected login will not fix itself")

    def test_a_startup_404_is_retried_until_the_api_exists(self):
        """adguard listens on :3000 before it registers /control, so a box
        still working through a big query log 404s a good endpoint for a
        while. that is the failure that broke `pi sync` on thebe."""
        missing = urllib.error.HTTPError("url", 404, "Not Found", {}, None)
        attempts = [missing, missing, b'{"ok": true}']

        def urlopen(req, timeout):
            result = attempts.pop(0)
            if isinstance(result, Exception):
                raise result
            return BytesIO(result)

        with patch("cli.adguard.urllib.request.urlopen", urlopen):
            self.assertEqual(self.client._call("/rewrite/list"), {"ok": True})
        self.assertEqual(self.sleeps, [1, 2])

    def test_a_404_that_never_clears_names_the_host(self):
        missing = urllib.error.HTTPError("url", 404, "Not Found", {}, None)
        with patch("cli.adguard.urllib.request.urlopen", side_effect=missing):
            with self.assertRaises(adguard.APIError) as caught:
                self.client._call("/rewrite/list")
        self.assertIn("127.0.0.1", str(caught.exception))
        self.assertEqual(len(self.sleeps), len(adguard.RETRY_DELAYS), "one wait per retry")


if __name__ == "__main__":
    unittest.main()
