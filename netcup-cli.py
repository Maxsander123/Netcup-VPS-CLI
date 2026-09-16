#!/usr/bin/env python3
"""Netcup VPS CLI — manage Netcup VPS via the SCP REST API."""

import json
import sys
import time
from pathlib import Path

import click
import requests
from rich.console import Console
from rich.table import Table
from rich import box
from rich.prompt import Confirm

AUTH_BASE       = "https://www.servercontrolpanel.de/realms/scp/protocol/openid-connect"
API_BASE        = "https://www.servercontrolpanel.de/scp-core/api/v1"
DEVICE_ENDPOINT = f"{AUTH_BASE}/auth/device"
TOKEN_ENDPOINT  = f"{AUTH_BASE}/token"
CLIENT_ID       = "scp"

CONFIG_DIR = Path.home() / ".config" / "netcup-cli"
CREDS_FILE = CONFIG_DIR / "credentials.json"

console = Console()

# ── credentials ───────────────────────────────────────────────────────────────

def load_creds() -> dict:
    if not CREDS_FILE.exists():
        console.print("[red]Not logged in.[/red] Run [bold]netcup-cli login[/bold] first.")
        sys.exit(1)
    return json.loads(CREDS_FILE.read_text())


def save_creds(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CREDS_FILE.write_text(json.dumps(data, indent=2))
    CREDS_FILE.chmod(0o600)


def get_access_token() -> str:
    creds = load_creds()
    resp = requests.post(TOKEN_ENDPOINT, data={
        "client_id":     CLIENT_ID,
        "grant_type":    "refresh_token",
        "refresh_token": creds["refresh_token"],
    })
    if not resp.ok:
        console.print(f"[red]Token refresh failed (re-run login):[/red] {resp.text}")
        sys.exit(1)
    tok = resp.json()
    if "refresh_token" in tok:
        creds["refresh_token"] = tok["refresh_token"]
        save_creds(creds)
    return tok["access_token"]


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_access_token()}", "Accept": "application/json"}

# ── API ───────────────────────────────────────────────────────────────────────

def api_get(path: str, params: dict | None = None):
    r = requests.get(f"{API_BASE}{path}", headers=_headers(), params=params)
    _check(r); return r.json()


def api_post(path: str, body: dict):
    r = requests.post(f"{API_BASE}{path}", headers=_headers(), json=body)
    _check(r); return r.json() if r.content else {}


def api_patch(path: str, body: dict):
    h = _headers(); h["Content-Type"] = "application/merge-patch+json"
    r = requests.patch(f"{API_BASE}{path}", headers=h, json=body)
    _check(r); return r.json() if r.content else {}


def api_put(path: str, body: dict):
    r = requests.put(f"{API_BASE}{path}", headers=_headers(), json=body)
    _check(r); return r.json() if r.content else {}


def api_delete(path: str):
    r = requests.delete(f"{API_BASE}{path}", headers=_headers())
    _check(r)


def _check(r: requests.Response):
    if not r.ok:
        try:
            msg = r.json()
        except Exception:
            msg = r.text
        console.print(f"[red]API {r.status_code}:[/red] {msg}")
        sys.exit(1)


def _servers() -> list[dict]:
    data = api_get("/servers")
    return data if isinstance(data, list) else data.get("data", [])


def resolve(name_or_id: str) -> int:
    """Resolve name/nickname/hostname → numeric server id."""
    try:
        return int(name_or_id)
    except ValueError:
        pass
    for s in _servers():
        if name_or_id in (s.get("name"), s.get("nickname"), s.get("hostname")):
            return s["id"]
    console.print(f"[red]Server not found:[/red] {name_or_id}")
    sys.exit(1)


def _state(s: dict) -> str:
    return s.get("serverLiveInfo", {}).get("state", "?")


def _ipv4(s: dict) -> str:
    addrs = s.get("ipv4Addresses", [])
    return addrs[0]["ip"] if addrs else ""


def _ipv6(s: dict) -> str:
    for a in s.get("ipv6Addresses", []):
        prefix = a.get("networkPrefix", "")
        if prefix and not prefix.startswith("fe80"):
            return prefix
    return ""

# ── CLI ───────────────────────────────────────────────────────────────────────

@click.group()
@click.version_option("1.1.0", prog_name="netcup-cli")
def cli():
    """Netcup VPS CLI — control your Netcup VPS from the terminal."""

# ── login / logout ────────────────────────────────────────────────────────────

@cli.command()
def login():
    """Authenticate via OAuth2 Device Code flow."""
    resp = requests.post(DEVICE_ENDPOINT, data={"client_id": CLIENT_ID})
    if not resp.ok:
        console.print(f"[red]Failed:[/red] {resp.text}"); sys.exit(1)
    data        = resp.json()
    uri         = data.get("verification_uri_complete", data.get("verification_uri"))
    device_code = data["device_code"]
    interval    = data.get("interval", 5)

    console.print(f"\n[bold yellow]Open this URL in your browser:[/bold yellow]\n  {uri}\n")
    if "verification_uri_complete" not in data:
        console.print(f"  Code: [bold]{data.get('user_code')}[/bold]\n")

    print("Waiting for browser authentication", end="", flush=True)
    while True:
        time.sleep(interval)
        r = requests.post(TOKEN_ENDPOINT, data={
            "client_id":   CLIENT_ID,
            "grant_type":  "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
        })
        tok = r.json()
        if r.ok:
            print("")
            save_creds({"refresh_token": tok["refresh_token"]})
            console.print("[green]Logged in successfully.[/green]")
            return
        err = tok.get("error", "")
        if err == "authorization_pending":
            print(".", end="", flush=True)
        elif err == "slow_down":
            interval += 5
        else:
            console.print(f"\n[red]Auth error:[/red] {tok}"); sys.exit(1)


@cli.command()
def logout():
    """Remove stored credentials."""
    if CREDS_FILE.exists():
        CREDS_FILE.unlink()
        console.print("[green]Logged out.[/green]")
    else:
        console.print("Not logged in.")

# ── list ──────────────────────────────────────────────────────────────────────

@cli.command("list")
@click.option("--json", "as_json", is_flag=True, help="Raw JSON output.")
def list_servers(as_json):
    """List all VPS in your account."""
    servers = _servers()

    if as_json:
        click.echo(json.dumps(servers, indent=2)); return

    table = Table(title="Netcup VPS", box=box.ROUNDED, highlight=True)
    table.add_column("ID",       style="dim",  no_wrap=True)
    table.add_column("Hostname", style="bold", no_wrap=True)
    table.add_column("Nickname", style="cyan")
    table.add_column("Template", style="dim")
    table.add_column("Status",   justify="center")

    for s in servers:
        sid    = str(s.get("id", ""))
        host   = str(s.get("hostname", ""))
        nick   = str(s.get("nickname", ""))
        tmpl   = str(s.get("template", {}).get("name", ""))
        # fetch live state (one request per server — skip if many)
        try:
            detail = api_get(f"/servers/{s['id']}")
            state  = _state(detail).upper()
        except SystemExit:
            state = "?"
        color = "green" if state == "RUNNING" else "red" if state in ("STOPPED", "OFF") else "yellow"
        table.add_row(sid, host, nick, tmpl, f"[{color}]{state}[/{color}]")

    console.print(table)

# ── info ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("server")
@click.option("--json", "as_json", is_flag=True)
def info(server, as_json):
    """Show details for SERVER (id, hostname, or nickname)."""
    sid  = resolve(server)
    data = api_get(f"/servers/{sid}")

    if as_json:
        click.echo(json.dumps(data, indent=2)); return

    live  = data.get("serverLiveInfo", {})
    state = live.get("state", "?").upper()
    color = "green" if state == "RUNNING" else "red"

    table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    table.add_column("Key",   style="dim",  width=26)
    table.add_column("Value", style="bold")

    table.add_row("ID",           str(data.get("id", "")))
    table.add_row("Name",         data.get("name", ""))
    table.add_row("Hostname",     data.get("hostname", ""))
    table.add_row("Nickname",     data.get("nickname", ""))
    table.add_row("Template",     data.get("template", {}).get("name", ""))
    table.add_row("Architecture", data.get("architecture", ""))
    table.add_row("State",        f"[{color}]{state}[/{color}]")
    table.add_row("vCPU",         str(live.get("cpuCount", "")))
    table.add_row("RAM (MiB)",    str(live.get("currentServerMemoryInMiB", "")))
    table.add_row("Uptime (s)",   str(live.get("uptimeInSeconds", "")))
    table.add_row("Location",     data.get("site", {}).get("city", ""))
    table.add_row("IPv4",         _ipv4(data))
    table.add_row("IPv6 prefix",  _ipv6(data))

    disks = live.get("disks", [])
    for i, d in enumerate(disks):
        table.add_row(f"Disk {i} (MiB)", f"{d.get('allocationInMiB','?')} used / {d.get('capacityInMiB','?')} total")

    console.print(table)

# ── ips ───────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("server")
def ips(server):
    """Show all IP addresses for SERVER."""
    sid  = resolve(server)
    data = api_get(f"/servers/{sid}")

    table = Table(title=f"IPs — {server}", box=box.ROUNDED)
    table.add_column("Type",    style="cyan", no_wrap=True)
    table.add_column("Address", style="green")
    table.add_column("Gateway", style="dim")
    table.add_column("rDNS",    style="dim")

    # pull rDNS from interfaces endpoint which has more detail
    iface_map: dict[str, dict] = {}
    try:
        ifaces = api_get(f"/servers/{sid}/interfaces")
        ifaces = ifaces if isinstance(ifaces, list) else []
        for iface in ifaces:
            for a in iface.get("ipv4Addresses", []):
                iface_map[a["ip"]] = {"rdns": a.get("rdns", ""), "gw": a.get("gateway", "")}
    except SystemExit:
        pass

    for a in data.get("ipv4Addresses", []):
        ip  = a.get("ip", "")
        gw  = a.get("gateway", "")
        rdns = iface_map.get(ip, {}).get("rdns", "")
        table.add_row("IPv4", ip, gw, str(rdns) if rdns else "")

    for a in data.get("ipv6Addresses", []):
        prefix = a.get("networkPrefix", "")
        gw     = a.get("gateway", "")
        table.add_row("IPv6 prefix", f"{prefix}/{a.get('networkPrefixLength','')}", gw, "")

    console.print(table)

# ── power controls ────────────────────────────────────────────────────────────

@cli.command()
@click.argument("server")
def start(server):
    """Power on SERVER."""
    sid = resolve(server)
    api_post(f"/servers/{sid}/power", {"state": "ON"})
    console.print("[green]✓[/green] Start command sent.")


@cli.command()
@click.argument("server")
@click.option("-f", "--force", is_flag=True, help="Skip confirmation.")
def stop(server, force):
    """Graceful ACPI shutdown of SERVER."""
    if not force and not Confirm.ask(f"Shutdown [bold]{server}[/bold]?"):
        return
    sid = resolve(server)
    api_post(f"/servers/{sid}/power", {"state": "OFF", "option": "POWEROFF"})
    console.print("[green]✓[/green] Shutdown command sent.")


@cli.command()
@click.argument("server")
@click.option("-f", "--force", is_flag=True, help="Skip confirmation.")
def reset(server, force):
    """Hard reset SERVER (like pressing the reset button)."""
    if not force and not Confirm.ask(f"[red]Hard reset[/red] [bold]{server}[/bold]?"):
        return
    sid = resolve(server)
    api_post(f"/servers/{sid}/power", {"state": "ON", "option": "RESET"})
    console.print("[green]✓[/green] Reset command sent.")


@cli.command()
@click.argument("server")
@click.option("-f", "--force", is_flag=True, help="Skip confirmation.")
def poweroff(server, force):
    """Cut power to SERVER immediately."""
    if not force and not Confirm.ask(f"[red]Hard power-off[/red] [bold]{server}[/bold]?"):
        return
    sid = resolve(server)
    api_post(f"/servers/{sid}/power", {"state": "OFF", "option": "POWEROFF"})
    console.print("[green]✓[/green] Power-off command sent.")


@cli.command()
@click.argument("server")
@click.option("-f", "--force", is_flag=True, help="Skip confirmation.")
def powercycle(server, force):
    """Power cycle SERVER (hard off then on)."""
    if not force and not Confirm.ask(f"Power cycle [bold]{server}[/bold]?"):
        return
    sid = resolve(server)
    api_post(f"/servers/{sid}/power", {"state": "ON", "option": "POWERCYCLE"})
    console.print("[green]✓[/green] Power cycle command sent.")

# ── network ───────────────────────────────────────────────────────────────────

@cli.group()
def network():
    """Network interface management."""


@network.command("list")
@click.argument("server")
@click.option("--json", "as_json", is_flag=True)
def net_list(server, as_json):
    """List network interfaces for SERVER."""
    sid   = resolve(server)
    ifaces = api_get(f"/servers/{sid}/interfaces")
    ifaces = ifaces if isinstance(ifaces, list) else []

    if as_json:
        click.echo(json.dumps(ifaces, indent=2)); return

    table = Table(title=f"Interfaces — {server}", box=box.ROUNDED)
    table.add_column("MAC",         style="cyan",  no_wrap=True)
    table.add_column("Driver",      style="dim")
    table.add_column("Speed (Mb)",  justify="right")
    table.add_column("IPv4",        style="green")
    table.add_column("IPv6 prefix", style="dim")

    for iface in ifaces:
        mac    = iface.get("mac", "")
        driver = iface.get("driver", "")
        speed  = str(iface.get("speedInMBits", ""))
        ipv4s  = ", ".join(a["ip"] for a in iface.get("ipv4Addresses", []) if a.get("ip"))
        ipv6s  = ", ".join(
            a["networkPrefix"] for a in iface.get("ipv6Addresses", [])
            if a.get("networkPrefix") and not a.get("linkLocal")
        )
        table.add_row(mac, driver, speed, ipv4s or "—", ipv6s or "—")

    console.print(table)


@network.command("add")
@click.argument("server")
@click.option("--vlan", required=True, type=int, help="VLAN ID.")
@click.option("--driver", default="virtio", show_default=True)
def net_add(server, vlan, driver):
    """Add a network interface to SERVER."""
    sid    = resolve(server)
    result = api_post(f"/servers/{sid}/interfaces", {"vlan_id": vlan, "driver": driver})
    console.print(f"[green]✓[/green] Interface added: {result}")


@network.command("remove")
@click.argument("server")
@click.argument("mac")
@click.option("-f", "--force", is_flag=True)
def net_remove(server, mac, force):
    """Remove interface by MAC from SERVER."""
    if not force and not Confirm.ask(f"Remove [bold]{mac}[/bold] from {server}?"):
        return
    sid = resolve(server)
    api_delete(f"/servers/{sid}/interfaces/{mac}")
    console.print(f"[green]✓[/green] Interface {mac} removed.")

# ── rDNS ──────────────────────────────────────────────────────────────────────

@cli.group()
def rdns():
    """Reverse DNS management."""


@rdns.command("get")
@click.argument("ip")
def rdns_get(ip):
    """Get rDNS for IP."""
    data = api_get(f"/rdns/ipv4/{ip}")
    console.print(data)


@rdns.command("set")
@click.argument("ip")
@click.argument("hostname")
def rdns_set(ip, hostname):
    """Set rDNS for IP."""
    api_put(f"/rdns/ipv4/{ip}", {"hostname": hostname})
    console.print(f"[green]✓[/green] rDNS {ip} → {hostname}")


@rdns.command("delete")
@click.argument("ip")
@click.option("-f", "--force", is_flag=True)
def rdns_delete(ip, force):
    """Delete rDNS for IP."""
    if not force and not Confirm.ask(f"Delete rDNS for [bold]{ip}[/bold]?"):
        return
    api_delete(f"/rdns/ipv4/{ip}")
    console.print(f"[green]✓[/green] rDNS for {ip} deleted.")

# ── rename ────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("server")
@click.argument("nickname")
def rename(server, nickname):
    """Set nickname for SERVER."""
    sid = resolve(server)
    api_patch(f"/servers/{sid}", {"nickname": nickname})
    console.print(f"[green]✓[/green] Nickname → [bold]{nickname}[/bold]")

# ── reinstall ─────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("server")
def reinstall(server):
    """Reinstall SERVER with a new OS.

    Opens the Netcup SCP web interface for this server directly.
    (The reinstall endpoint is not publicly available in the REST API.)
    """
    sid  = resolve(server)
    data = api_get(f"/servers/{sid}")
    name = data.get("name", str(sid))
    url  = f"https://www.servercontrolpanel.de/SCP/VServer#server={name}&action=reinstall"
    console.print(
        f"[yellow]The reinstall endpoint is not exposed in the public REST API.[/yellow]\n"
        f"Open the SCP web UI for this server:\n\n"
        f"  [bold cyan]{url}[/bold cyan]\n"
    )

# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli()
