"""what exists, and how to reach it.

apps.toml says which apps there are and where they run, hosts/<name>/host.toml
describes one pi, dns.toml owns the fleet's names. nothing is hardcoded here.

credentials come from the remote-hosts file rmt already uses, so there is one
place on this machine holding ssh passwords and it is not this repository.
fleet addresses come from `tailscale status`, so no pi's ip is ever written
down. dns.toml's [addresses] is the one exception: things off the tailnet
have no name to look up, so the answer is given literally.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from . import ROOT

HOSTS_FILE = Path(os.environ.get("REMOTE_HOSTS_CONFIG", Path.home() / ".config/remote/hosts.toml"))

# "# rootpsw:" above a host carries the sudo password without making rmt treat
# the host as password-auth, which is what the "password" key means to it
SECRET_COMMENT = re.compile(r"^#\s*rootpsw:\s*(.+)$")
KEY = re.compile(r'^(\w+)\s*=\s*"(.*)"$')


def load(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


# ── apps ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class App:
    name: str
    label: str
    host: str
    domain: str
    api: int
    web: int
    needs: str = ""
    env: dict[str, str] = field(default_factory=dict)
    files: tuple[dict[str, str], ...] = ()

    @property
    def web_dir(self) -> Path:
        return ROOT / self.name / "web"

    @property
    def server_dir(self) -> Path:
        return ROOT / self.name / "server"

    @property
    def api_url(self) -> str:
        return f"http://127.0.0.1:{self.api}"

    @property
    def web_url(self) -> str:
        return f"http://localhost:{self.web}"

    @property
    def artefacts(self) -> list[str]:
        """everything of this app's that has to end up in build/"""
        return [self.name, *(f["as"] for f in self.files)]


@cache
def apps() -> tuple[App, ...]:
    raw = load(ROOT / "apps.toml")["app"]
    out = []
    seen: dict[int, str] = {}
    for a in raw:
        for port, kind in ((a["api"], "api"), (a["web"], "web")):
            if port in seen:
                raise ValueError(
                    f"apps.toml: {a['name']} {kind} port {port} collides with {seen[port]}"
                )
            seen[port] = f"{a['name']} {kind}"
        out.append(
            App(
                name=a["name"],
                label=a["label"],
                host=a["host"],
                domain=a["domain"],
                api=a["api"],
                web=a["web"],
                needs=a.get("needs", ""),
                env=a.get("env", {}),
                files=tuple(a.get("files", [])),
            )
        )
    return tuple(out)


def apps_on(host: str) -> tuple[App, ...]:
    return tuple(a for a in apps() if a.host == host)


def app(name: str) -> App:
    for a in apps():
        if a.name == name:
            return a
    raise LookupError(f"unknown app {name!r}, have: {', '.join(a.name for a in apps())}")


# ── hosts ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Host:
    name: str
    glyph: str
    plane: str
    blurb: str
    colors: dict[str, str]
    services: tuple[str, ...]

    @property
    def dir(self) -> Path:
        return ROOT / "hosts" / self.name

    @property
    def installable(self) -> bool:
        return (self.dir / "install").is_file()


@cache
def hosts() -> tuple[Host, ...]:
    out = []
    for path in sorted((ROOT / "hosts").glob("*/host.toml")):
        raw = load(path)
        out.append(
            Host(
                name=raw["name"],
                glyph=raw["glyph"],
                plane=raw["plane"],
                blurb=raw["blurb"],
                colors=raw["colors"],
                services=tuple(raw.get("services", [])),
            )
        )
    return tuple(out)


def host_names() -> list[str]:
    return [h.name for h in hosts()]


def host(name: str) -> Host:
    for h in hosts():
        if h.name == name:
            return h
    raise LookupError(f"unknown host {name!r}, have: {', '.join(host_names())}")


def select(chosen: list[str]) -> list[str]:
    """the hosts named on the command line, in fleet order; all of them by default"""
    names = host_names()
    if not chosen:
        return names
    if unknown := sorted(set(chosen) - set(names)):
        raise LookupError(f"unknown host: {', '.join(unknown)} (have: {', '.join(names)})")
    return [n for n in names if n in chosen]


# ── dns ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Record:
    """one adguard rewrite: a name, what it answers with, and why"""

    domain: str
    target: str  # a fleet host, or a literal address when `literal`
    source: str  # "app", "record" or "address"
    literal: bool = False

    @property
    def why(self) -> str:
        """the one-word reason shown next to this name in `pi sync`"""
        return self.source if self.literal else self.target


@cache
def dns() -> dict:
    return load(ROOT / "dns.toml")


def resolvers() -> list[str]:
    """the hosts that run an adguard, in fleet order"""
    declared = dns()["resolvers"]
    return [name for name in host_names() if name in declared]


def records() -> tuple[Record, ...]:
    """every name the fleet's resolvers should answer for.

    the app names come from apps.toml so a new app is one entry in one file;
    dns.toml only lists what is not an app: [records] for a fleet host, whose
    address is looked up, and [addresses] for anything off the tailnet, whose
    address is written out because there is nothing to look it up in.
    """
    zone = dns()["zone"]
    out = [Record(a.domain, a.host, "app") for a in apps()]
    out += [Record(f"{name}.{zone}", target, "record") for name, target in dns()["records"].items()]
    out += [
        Record(f"{name}.{zone}", addr, "address", literal=True)
        for name, addr in dns().get("addresses", {}).items()
    ]

    names = {r.domain for r in out}
    if len(names) != len(out):
        dupes = sorted(d for d in names if sum(r.domain == d for r in out) > 1)
        raise ValueError(f"dns.toml: {', '.join(dupes)} declared twice")

    known = set(host_names())
    for r in out:
        if r.literal:
            try:
                ipaddress.ip_address(r.target)
            except ValueError:
                raise ValueError(
                    f"dns.toml: {r.domain} answers with {r.target!r}, which is not an address"
                ) from None
        elif r.target not in known:
            raise ValueError(f"dns.toml: {r.domain} points at unknown host {r.target!r}")

    return tuple(sorted(out, key=lambda r: r.domain))


# ── credentials and addresses ───────────────────────────────────────────────


@dataclass(frozen=True)
class Credentials:
    name: str
    user: str
    address: str
    password: str = ""
    rootpsw: str = ""

    @property
    def target(self) -> str:
        return f"{self.user}@{self.address}"

    @property
    def sudo(self) -> str:
        return self.rootpsw or self.password


@cache
def credentials() -> dict[str, Credentials]:
    if not HOSTS_FILE.exists():
        return {}

    out: dict[str, Credentials] = {}
    current: dict[str, str] = {}
    pending: dict[str, str] = {}

    def flush():
        if name := current.get("name"):
            out[name] = Credentials(
                name=name,
                user=current.get("user", ""),
                address=current.get("host", name),
                password=current.get("password", ""),
                rootpsw=current.get("rootpsw", ""),
            )

    for raw in HOSTS_FILE.read_text().splitlines():
        text = raw.strip()
        if m := SECRET_COMMENT.match(text):
            pending["rootpsw"] = m.group(1).strip()
            continue
        if text == "[[hosts]]":
            flush()
            current = dict(pending)
            pending = {}
            continue
        if m := KEY.match(text):
            current[m.group(1)] = m.group(2)
    flush()
    return out


def credentials_for(name: str) -> Credentials:
    creds = credentials().get(name)
    if creds is None:
        raise LookupError(f"no entry for {name} in {HOSTS_FILE}")
    return creds


@cache
def tailnet() -> dict[str, str]:
    """hostname -> tailnet ipv4, so dns.toml never has to name an address"""
    if shutil.which("tailscale") is None:
        return {}
    try:
        raw = subprocess.run(
            ["tailscale", "status", "--json"], capture_output=True, timeout=10, check=True
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return {}

    status = json.loads(raw)
    peers = {"self": status.get("Self") or {}, **(status.get("Peer") or {})}
    out: dict[str, str] = {}
    for peer in peers.values():
        name = peer.get("HostName")
        v4 = next((ip for ip in peer.get("TailscaleIPs") or [] if ":" not in ip), None)
        if name and v4:
            out[name] = v4
    return out


def address_of(name: str) -> str:
    """the tailnet address a dns rewrite should answer with"""
    addr = tailnet().get(name)
    if not addr:
        raise LookupError(
            f"tailscale does not know a host called {name!r}; "
            "run `tailscale status` and check the machine name"
        )
    return addr


def sshpass_available() -> bool:
    return shutil.which("sshpass") is not None
