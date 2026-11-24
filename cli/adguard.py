"""reconcile an adguard instance against dns.toml.

this talks to adguard's own http api rather than editing
/var/lib/adguardhome/AdGuardHome.yaml, which adguard owns and rewrites
whenever anything changes in its web ui. nothing here needs a restart, and
nothing here can corrupt a live config.

only names inside dns.toml's zone are managed. a rewrite you added by hand for
something else is left exactly where it is.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from . import Fault, env, manifest
from .manifest import Credentials, address_of

TIMEOUT = 10

# a resolver just pushed by `pi sync` may still be mid-restart, or on a first
# install mid-startup, when the dns pass reaches it a moment later; these are
# the retry delays for a connection-level failure, not for an answer
RETRY_DELAYS = (1, 2, 4, 8)


class APIError(Fault):
    """adguard answered, but not with what was asked for"""


@dataclass
class Client:
    host: str
    user: str
    password: str
    port: int

    def _call(self, path: str, body: dict | None = None) -> object:
        url = f"http://{self.host}:{self.port}/control{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        if data:
            req.add_header("Content-Type", "application/json")
        token = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")

        raw = self._open(req, path)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def _open(self, req: urllib.request.Request, path: str) -> bytes:
        """urlopen, retrying a connection-level failure.

        a sync just restarted this adguard, or seeded it for the first time,
        a moment ago; a refused or unreachable connection right after that is
        the service still coming up, not a real failure, so it is worth a
        few short waits before giving up. an http error is adguard actually
        answering and is never retried.
        """
        for delay in (*RETRY_DELAYS, None):
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
                    return res.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    raise APIError(
                        f"adguard on {self.host} rejected the login for {self.user!r}; "
                        "check ADGUARD_PASSWORD in .env"
                    ) from exc
                raise APIError(f"{path}: {exc.code} {exc.reason}") from exc
            except urllib.error.URLError as exc:
                if delay is None:
                    raise APIError(f"{path}: {exc.reason}") from exc
                time.sleep(delay)
        raise AssertionError("unreachable")  # the loop above always returns or raises

    def rewrites(self) -> list[dict[str, str]]:
        return self._call("/rewrite/list") or []

    def add_rewrite(self, domain: str, answer: str) -> None:
        self._call("/rewrite/add", {"domain": domain, "answer": answer})

    def delete_rewrite(self, domain: str, answer: str) -> None:
        self._call("/rewrite/delete", {"domain": domain, "answer": answer})

    def dns_info(self) -> dict:
        return self._call("/dns_info") or {}

    def set_dns(self, upstream: list[str], bootstrap: list[str]) -> None:
        # a partial body: adguard merges it into the rest of the dns config,
        # so cache sizes, dnssec and the like are left alone
        self._call("/dns_config", {"upstream_dns": upstream, "bootstrap_dns": bootstrap})

    def status(self) -> dict:
        return self._call("/status") or {}


@dataclass
class Plan:
    """what would change, worked out before anything is written"""

    host: str
    add: list[tuple[str, str, str]] = field(default_factory=list)
    remove: list[tuple[str, str]] = field(default_factory=list)
    keep: list[tuple[str, str, str]] = field(default_factory=list)
    upstream_from: list[str] = field(default_factory=list)
    upstream_to: list[str] = field(default_factory=list)
    foreign: int = 0

    @property
    def upstream_changes(self) -> bool:
        return self.upstream_from != self.upstream_to

    @property
    def empty(self) -> bool:
        return not self.add and not self.remove and not self.upstream_changes


def desired() -> dict[str, tuple[str, str]]:
    """domain -> (answer, why), the same answer set for every resolver"""
    out = {}
    for record in manifest.records():
        out[record.domain] = (address_of(record.host), record.host)
    return out


def plan(client: Client, host: str) -> Plan:
    cfg = manifest.dns()
    zone = cfg["zone"]
    want = desired()
    result = Plan(host=host)

    live: dict[str, str] = {}
    for entry in client.rewrites():
        domain, answer = entry.get("domain", ""), entry.get("answer", "")
        if not domain.endswith(f".{zone}"):
            result.foreign += 1
            continue
        live[domain] = answer

    for domain, (answer, why) in sorted(want.items()):
        if live.get(domain) == answer:
            result.keep.append((domain, answer, why))
        else:
            if domain in live:
                # a changed address is a delete plus an add; adguard has no
                # update, and two rewrites for one name would both answer
                result.remove.append((domain, live[domain]))
            result.add.append((domain, answer, why))

    for domain, answer in sorted(live.items()):
        if domain not in want:
            result.remove.append((domain, answer))

    result.upstream_from = list(client.dns_info().get("upstream_dns") or [])
    result.upstream_to = list(cfg["resolvers"][host])
    return result


def apply(client: Client, result: Plan) -> None:
    # delete first: a stale answer for a name we are about to re-point would
    # otherwise still be live alongside the new one
    for domain, answer in result.remove:
        client.delete_rewrite(domain, answer)
    for domain, answer, _ in result.add:
        client.add_rewrite(domain, answer)
    if result.upstream_changes:
        client.set_dns(result.upstream_to, list(manifest.dns()["bootstrap"]))


def password() -> str:
    """the adguard web login, the same one on every resolver. see .env.example"""
    if value := env("ADGUARD_PASSWORD").strip():
        return value
    raise Fault(
        "no adguard password, so dns cannot be reconciled.\n"
        "copy .env.example to .env and put the adguard web login in it"
    )


def client_for(creds: Credentials) -> Client:
    cfg = manifest.dns()
    return Client(host=creds.address, user=cfg["user"], password=password(), port=cfg["port"])
