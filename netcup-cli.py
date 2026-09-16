#!/usr/bin/env python3
"""Netcup VPS CLI — manage Netcup VPS via the SCP REST API."""

import atexit
import ipaddress
import json
import os
import re
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
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Write atomically with 0o600 from the start — no chmod race
    tmp = CREDS_FILE.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(data, indent=2).encode())
    finally:
        os.close(fd)
    tmp.replace(CREDS_FILE)


def get_access_token() -> str:
    creds = load_creds()
    resp = requests.post(TOKEN_ENDPOINT, data={
        "client_id":     CLIENT_ID,
        "grant_type":    "refresh_token",
        "refresh_token": creds["refresh_token"],
    })
    if not resp.ok:
        try:
            msg = resp.json().get("error_description", resp.json().get("error", "unknown"))
        except Exception:
            msg = str(resp.status_code)
        console.print(f"[red]Token refresh failed (re-run login):[/red] {msg}")
        sys.exit(1)
    tok = resp.json()
    if "refresh_token" in tok:
        creds["refresh_token"] = tok["refresh_token"]
        save_creds(creds)
    return tok["access_token"]


def _headers(patch: bool = False) -> dict:
    h = {"Authorization": f"Bearer {get_access_token()}", "Accept": "application/json"}
    if patch:
        h["Content-Type"] = "application/merge-patch+json"
    return h

# ── API helpers ───────────────────────────────────────────────────────────────

def api_get(path: str, params: dict | None = None):
    r = requests.get(f"{API_BASE}{path}", headers=_headers(), params=params)
    _check(r); return r.json()

def api_post(path: str, body: dict):
    r = requests.post(f"{API_BASE}{path}", headers=_headers(), json=body)
    _check(r); return r.json() if r.content else {}

def api_patch(path: str, body: dict):
    r = requests.patch(f"{API_BASE}{path}", headers=_headers(patch=True), json=body)
    _check(r); return r.json() if r.content else {}

def api_put(path: str, body: dict):
    r = requests.put(f"{API_BASE}{path}", headers=_headers(), json=body)
    _check(r); return r.json() if r.content else {}

def api_delete(path: str):
    r = requests.delete(f"{API_BASE}{path}", headers=_headers())
    _check(r); return r.json() if r.content else {}

def _check(r: requests.Response):
    if not r.ok:
        try:    msg = r.json()
        except: msg = r.text
        console.print(f"[red]API {r.status_code}:[/red] {msg}")
        sys.exit(1)

# ── helpers ───────────────────────────────────────────────────────────────────

def _servers() -> list[dict]:
    data = api_get("/servers")
    return data if isinstance(data, list) else data.get("data", [])

def resolve(name_or_id: str) -> int:
    try:
        return int(name_or_id)
    except ValueError:
        pass
    for s in _servers():
        if name_or_id in (s.get("name"), s.get("nickname"), s.get("hostname")):
            return s["id"]
    console.print(f"[red]Server not found:[/red] {name_or_id}")
    sys.exit(1)

def _validate_path_segment(value: str, name: str) -> None:
    """Reject values that could cause API path traversal."""
    if "/" in value or ".." in value or "\x00" in value:
        console.print(f"[red]Invalid {name}:[/red] must not contain '/', '..', or null bytes.")
        sys.exit(1)

def _validate_mac(mac: str) -> None:
    if not re.fullmatch(r"([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", mac):
        console.print(f"[red]Invalid MAC address:[/red] {mac}")
        sys.exit(1)

def _validate_ipv4(ip: str) -> None:
    try:
        ipaddress.IPv4Address(ip)
    except ValueError:
        console.print(f"[red]Invalid IPv4 address:[/red] {ip}")
        sys.exit(1)

def _validate_ipv6_prefix(prefix: str) -> None:
    try:
        ipaddress.ip_network(prefix, strict=False)
    except ValueError:
        console.print(f"[red]Invalid IPv6 prefix:[/red] {prefix}")
        sys.exit(1)

def _state(s: dict) -> str:
    return s.get("serverLiveInfo", {}).get("state", "?")

def _ipv4(s: dict) -> str:
    addrs = s.get("ipv4Addresses", [])
    return addrs[0]["ip"] if addrs else ""

def _ipv6(s: dict) -> str:
    for a in s.get("ipv6Addresses", []):
        p = a.get("networkPrefix", "")
        if p and not p.startswith("fe80"):
            return f"{p}/{a.get('networkPrefixLength','')}"
    return ""

def wait_task(task_uuid: str, label: str = "Task") -> dict:
    """Poll /tasks/{uuid} until finished."""
    console.print(f"[dim]{label} {task_uuid[:8]}…[/dim] ", end="")
    while True:
        t = api_get(f"/tasks/{task_uuid}")
        state = t.get("state", "PENDING")
        pct   = t.get("progressInPercent", 0)
        if state == "FINISHED":
            print(f" [done]")
            console.print(f"[green]✓[/green] {label} finished.")
            return t
        if state == "FAILED":
            print("")
            console.print(f"[red]✗ {label} failed:[/red] {t}")
            sys.exit(1)
        print(f"\r[dim]{label} {task_uuid[:8]}…[/dim] {pct}%  ", end="", flush=True)
        time.sleep(3)

# ── CLI root ──────────────────────────────────────────────────────────────────

@click.group()
@click.version_option("1.4.0", prog_name="netcup-cli")
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
@click.option("--no-status", is_flag=True, help="Skip live status fetch (faster).")
def list_servers(as_json, no_status):
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
        state = "—"
        color = "dim"
        if not no_status:
            try:
                detail = api_get(f"/servers/{s['id']}")
                state  = _state(detail).upper()
                color  = "green" if state == "RUNNING" else "red" if state in ("STOPPED", "OFF") else "yellow"
            except SystemExit:
                state = "?"
        table.add_row(
            str(s.get("id", "")),
            str(s.get("hostname", "")),
            str(s.get("nickname", "")),
            str(s.get("template", {}).get("name", "")),
            f"[{color}]{state}[/{color}]",
        )
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

    def row(k, v): table.add_row(k, str(v) if v is not None else "—")

    row("ID",           data.get("id", ""))
    row("Name",         data.get("name", ""))
    row("Hostname",     data.get("hostname", ""))
    row("Nickname",     data.get("nickname", ""))
    row("Template",     data.get("template", {}).get("name", ""))
    row("Architecture", data.get("architecture", ""))
    table.add_row("State", f"[{color}]{state}[/{color}]")
    row("vCPU",         live.get("cpuCount", ""))
    row("RAM (MiB)",    live.get("currentServerMemoryInMiB", ""))
    uptime = live.get("uptimeInSeconds", 0)
    row("Uptime",       f"{uptime // 3600}h {(uptime % 3600) // 60}m")
    row("Location",     data.get("site", {}).get("city", ""))
    row("IPv4",         _ipv4(data))
    row("IPv6 prefix",  _ipv6(data))
    row("Snapshots",    data.get("snapshotCount", 0))
    row("Rescue active",data.get("rescueSystemActive", False))
    row("Autostart",    live.get("autostart", ""))
    row("Machine type", live.get("machineType", ""))
    row("UEFI",         live.get("uefi", ""))
    for i, d in enumerate(live.get("disks", [])):
        row(f"Disk {i} ({d.get('dev','?')})",
            f"{d.get('allocationInMiB','?')} / {d.get('capacityInMiB','?')} MiB used")
    console.print(table)

# ── ips ───────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("server")
def ips(server):
    """Show all IP addresses for SERVER."""
    sid  = resolve(server)
    data = api_get(f"/servers/{sid}")

    # enrich with rDNS from interfaces
    iface_map: dict[str, dict] = {}
    try:
        for iface in api_get(f"/servers/{sid}/interfaces"):
            for a in iface.get("ipv4Addresses", []):
                iface_map[a.get("ip", "")] = {
                    "rdns": a.get("rdns", ""),
                    "gw":   a.get("gateway", ""),
                }
    except SystemExit:
        pass

    table = Table(title=f"IPs — {server}", box=box.ROUNDED)
    table.add_column("Type",    style="cyan", no_wrap=True)
    table.add_column("Address", style="green")
    table.add_column("Gateway", style="dim")
    table.add_column("rDNS",    style="dim")

    for a in data.get("ipv4Addresses", []):
        ip  = a.get("ip", "")
        gw  = iface_map.get(ip, {}).get("gw", a.get("gateway", ""))
        rdns = iface_map.get(ip, {}).get("rdns", "")
        table.add_row("IPv4", ip, gw, str(rdns) if rdns else "")

    for a in data.get("ipv6Addresses", []):
        prefix = a.get("networkPrefix", "")
        length = a.get("networkPrefixLength", "")
        table.add_row("IPv6 prefix", f"{prefix}/{length}", a.get("gateway", ""), "")

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
@click.option("-f", "--force", is_flag=True)
def stop(server, force):
    """Graceful ACPI shutdown of SERVER."""
    if not force and not Confirm.ask(f"Shutdown [bold]{server}[/bold]?"): return
    sid = resolve(server)
    api_post(f"/servers/{sid}/power", {"state": "OFF", "option": "POWEROFF"})
    console.print("[green]✓[/green] Shutdown command sent.")


@cli.command()
@click.argument("server")
@click.option("-f", "--force", is_flag=True)
def reset(server, force):
    """Hard reset SERVER."""
    if not force and not Confirm.ask(f"[red]Hard reset[/red] [bold]{server}[/bold]?"): return
    sid = resolve(server)
    api_post(f"/servers/{sid}/power", {"state": "ON", "option": "RESET"})
    console.print("[green]✓[/green] Reset command sent.")


@cli.command()
@click.argument("server")
@click.option("-f", "--force", is_flag=True)
def poweroff(server, force):
    """Cut power to SERVER immediately."""
    if not force and not Confirm.ask(f"[red]Hard power-off[/red] [bold]{server}[/bold]?"): return
    sid = resolve(server)
    api_post(f"/servers/{sid}/power", {"state": "OFF", "option": "POWEROFF"})
    console.print("[green]✓[/green] Power-off command sent.")


@cli.command()
@click.argument("server")
@click.option("-f", "--force", is_flag=True)
def powercycle(server, force):
    """Power cycle SERVER (hard off then on)."""
    if not force and not Confirm.ask(f"Power cycle [bold]{server}[/bold]?"): return
    sid = resolve(server)
    api_post(f"/servers/{sid}/power", {"state": "ON", "option": "POWERCYCLE"})
    console.print("[green]✓[/green] Power cycle command sent.")

# ── disks ─────────────────────────────────────────────────────────────────────

@cli.group()
def disks():
    """Disk management."""


@disks.command("list")
@click.argument("server")
@click.option("--json", "as_json", is_flag=True)
def disks_list(server, as_json):
    """List disks for SERVER."""
    sid  = resolve(server)
    data = api_get(f"/servers/{sid}/disks")
    data = data if isinstance(data, list) else [data]
    if as_json:
        click.echo(json.dumps(data, indent=2)); return

    table = Table(title=f"Disks — {server}", box=box.ROUNDED)
    table.add_column("Device",        style="cyan")
    table.add_column("Driver",        style="dim")
    table.add_column("Used (MiB)",    justify="right", style="yellow")
    table.add_column("Total (MiB)",   justify="right", style="green")
    table.add_column("Used %",        justify="right")

    for d in data:
        alloc = d.get("allocationInMiB", 0)
        cap   = d.get("capacityInMiB", 0)
        pct   = f"{alloc/cap*100:.1f}%" if cap else "?"
        table.add_row(
            d.get("name", ""),
            d.get("storageDriver", ""),
            str(alloc),
            str(cap),
            pct,
        )
    console.print(table)


@disks.command("get")
@click.argument("server")
@click.argument("disk")
@click.option("--json", "as_json", is_flag=True)
def disks_get(server, disk, as_json):
    """Show details for DISK on SERVER."""
    _validate_path_segment(disk, "disk name")
    sid  = resolve(server)
    data = api_get(f"/servers/{sid}/disks/{disk}")
    if as_json:
        click.echo(json.dumps(data, indent=2)); return
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Key",   style="dim", width=20)
    table.add_column("Value", style="bold")
    for k, v in data.items():
        table.add_row(k, str(v))
    console.print(table)

# ── snapshots ─────────────────────────────────────────────────────────────────

@cli.group()
def snapshot():
    """Snapshot management."""


@snapshot.command("list")
@click.argument("server")
@click.option("--json", "as_json", is_flag=True)
def snap_list(server, as_json):
    """List snapshots for SERVER."""
    sid  = resolve(server)
    data = api_get(f"/servers/{sid}/snapshots")
    data = data if isinstance(data, list) else []
    if as_json:
        click.echo(json.dumps(data, indent=2)); return
    if not data:
        console.print("[dim]No snapshots.[/dim]"); return

    table = Table(title=f"Snapshots — {server}", box=box.ROUNDED)
    table.add_column("Name",     style="cyan", no_wrap=True)
    table.add_column("UUID",     style="dim")
    table.add_column("State",    justify="center")
    table.add_column("Created",  style="dim")
    table.add_column("Online")
    table.add_column("Exported")
    table.add_column("Size (KiB)", justify="right")

    for s in data:
        state = str(s.get("state", "?")).upper()
        color = "green" if state == "AVAILABLE" else "yellow"
        table.add_row(
            s.get("name", ""),
            s.get("uuid", "")[:12] + "…",
            f"[{color}]{state}[/{color}]",
            str(s.get("creationTime", ""))[:19],
            "yes" if s.get("online") else "no",
            "yes" if s.get("exported") else "no",
            str(s.get("exportedSizeInKiB", "")),
        )
    console.print(table)


@snapshot.command("create")
@click.argument("server")
@click.argument("name")
@click.option("--disk", default="vda", show_default=True, help="Disk to snapshot.")
@click.option("--wait", is_flag=True, help="Wait until snapshot is ready.")
def snap_create(server, name, disk, wait):
    """Create snapshot NAME for SERVER."""
    sid    = resolve(server)
    result = api_post(f"/servers/{sid}/snapshots", {"diskName": disk, "name": name})
    task_id = result.get("uuid", result.get("id", ""))
    console.print(f"[green]✓[/green] Snapshot creation started. Task: {task_id}")
    if wait and task_id:
        wait_task(task_id, "Snapshot")


@snapshot.command("delete")
@click.argument("server")
@click.argument("name")
@click.option("-f", "--force", is_flag=True)
@click.option("--wait", is_flag=True)
def snap_delete(server, name, force, wait):
    """Delete snapshot NAME from SERVER."""
    _validate_path_segment(name, "snapshot name")
    if not force and not Confirm.ask(f"[red]Delete[/red] snapshot [bold]{name}[/bold] on {server}?"): return
    sid    = resolve(server)
    result = api_delete(f"/servers/{sid}/snapshots/{name}")
    task_id = (result or {}).get("uuid", (result or {}).get("id", ""))
    console.print(f"[green]✓[/green] Delete started.")
    if wait and task_id:
        wait_task(task_id, "Delete snapshot")

# ── logs ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("server")
@click.option("-n", "--lines", default=20, show_default=True, help="Number of log entries.")
@click.option("--json", "as_json", is_flag=True)
def logs(server, lines, as_json):
    """Show activity log for SERVER."""
    sid  = resolve(server)
    data = api_get(f"/servers/{sid}/logs")
    data = data if isinstance(data, list) else []
    data = data[:lines]
    if as_json:
        click.echo(json.dumps(data, indent=2)); return
    if not data:
        console.print("[dim]No log entries.[/dim]"); return

    table = Table(title=f"Logs — {server}", box=box.ROUNDED)
    table.add_column("Date",    style="dim", no_wrap=True)
    table.add_column("Type",    style="cyan", no_wrap=True)
    table.add_column("User",    style="dim")
    table.add_column("Message", style="bold")

    for e in data:
        ltype = str(e.get("type", "")).upper()
        color = "red" if ltype == "ERROR" else "yellow" if ltype == "WARNING" else "cyan"
        user  = e.get("executingUser", {})
        if isinstance(user, dict):
            user_str = f"{user.get('firstname','')} {user.get('lastname','')}".strip() or user.get("username", "")
        else:
            user_str = str(user)
        table.add_row(
            str(e.get("date", ""))[:19],
            f"[{color}]{ltype}[/{color}]",
            user_str,
            str(e.get("message", "")),
        )
    console.print(table)

# ── iso ───────────────────────────────────────────────────────────────────────

@cli.group()
def iso():
    """ISO management."""


@iso.command("status")
@click.argument("server")
@click.option("--json", "as_json", is_flag=True)
def iso_status(server, as_json):
    """Show ISO status for SERVER."""
    sid  = resolve(server)
    data = api_get(f"/servers/{sid}/iso")
    if as_json:
        click.echo(json.dumps(data, indent=2)); return
    attached = data.get("isoAttached", False)
    iso_name = data.get("iso") or "—"
    status   = "[green]attached[/green]" if attached else "[dim]none[/dim]"
    console.print(f"ISO attached: {status}")
    if attached:
        console.print(f"ISO: [bold]{iso_name}[/bold]")

# ── tasks ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("task_uuid")
@click.option("--wait", is_flag=True, help="Poll until finished.")
@click.option("--json", "as_json", is_flag=True)
def task(task_uuid, wait, as_json):
    """Show or wait for a background TASK_UUID."""
    if wait:
        wait_task(task_uuid, "Task"); return
    data = api_get(f"/tasks/{task_uuid}")
    if as_json:
        click.echo(json.dumps(data, indent=2)); return
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Key",   style="dim", width=22)
    table.add_column("Value", style="bold")
    for k, v in data.items():
        if not isinstance(v, (dict, list)):
            table.add_row(k, str(v))
    console.print(table)

# ── network ───────────────────────────────────────────────────────────────────

@cli.group()
def network():
    """Network interface management."""


@network.command("list")
@click.argument("server")
@click.option("--json", "as_json", is_flag=True)
def net_list(server, as_json):
    """List network interfaces for SERVER."""
    sid    = resolve(server)
    ifaces = api_get(f"/servers/{sid}/interfaces")
    ifaces = ifaces if isinstance(ifaces, list) else []
    if as_json:
        click.echo(json.dumps(ifaces, indent=2)); return

    # traffic data lives in serverLiveInfo.interfaces, keyed by mac
    traffic: dict[str, dict] = {}
    try:
        live_ifaces = api_get(f"/servers/{sid}").get("serverLiveInfo", {}).get("interfaces", [])
        for li in live_ifaces:
            traffic[li.get("mac", "")] = li
    except SystemExit:
        pass

    table = Table(title=f"Interfaces — {server}", box=box.ROUNDED)
    table.add_column("MAC",         style="cyan",  no_wrap=True)
    table.add_column("Driver",      style="dim")
    table.add_column("Speed (Mb)",  justify="right")
    table.add_column("IPv4",        style="green")
    table.add_column("IPv6 prefix", style="dim")
    table.add_column("RX/mo (MiB)", justify="right", style="dim")
    table.add_column("TX/mo (MiB)", justify="right", style="dim")

    for iface in ifaces:
        mac   = iface.get("mac", "")
        ipv4s = ", ".join(a["ip"] for a in iface.get("ipv4Addresses", []) if a.get("ip"))
        ipv6s = ", ".join(
            a["networkPrefix"] for a in iface.get("ipv6Addresses", [])
            if a.get("networkPrefix") and not a.get("linkLocal")
        )
        t = traffic.get(mac, {})
        table.add_row(
            mac,
            iface.get("driver", ""),
            str(iface.get("speedInMBits", "")),
            ipv4s or "—",
            ipv6s or "—",
            str(t.get("rxMonthlyInMiB", "—")),
            str(t.get("txMonthlyInMiB", "—")),
        )
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
    _validate_mac(mac)
    if not force and not Confirm.ask(f"Remove [bold]{mac}[/bold] from {server}?"): return
    sid = resolve(server)
    api_delete(f"/servers/{sid}/interfaces/{mac}")
    console.print(f"[green]✓[/green] Interface {mac} removed.")

# ── rDNS ──────────────────────────────────────────────────────────────────────

@cli.group()
def rdns():
    """Reverse DNS management (IPv4 and IPv6)."""


@rdns.command("get")
@click.argument("ip")
def rdns_get(ip):
    """Get rDNS for IPv4 IP."""
    _validate_ipv4(ip)
    data = api_get(f"/rdns/ipv4/{ip}")
    console.print(data.get("rdns") or "[dim]no rDNS set[/dim]")


@rdns.command("set")
@click.argument("ip")
@click.argument("hostname")
def rdns_set(ip, hostname):
    """Set rDNS for IPv4 IP."""
    _validate_ipv4(ip)
    api_put(f"/rdns/ipv4/{ip}", {"hostname": hostname})
    console.print(f"[green]✓[/green] rDNS {ip} → {hostname}")


@rdns.command("delete")
@click.argument("ip")
@click.option("-f", "--force", is_flag=True)
def rdns_delete(ip, force):
    """Delete rDNS for IPv4 IP."""
    _validate_ipv4(ip)
    if not force and not Confirm.ask(f"Delete rDNS for [bold]{ip}[/bold]?"): return
    api_delete(f"/rdns/ipv4/{ip}")
    console.print(f"[green]✓[/green] rDNS for {ip} deleted.")


@rdns.command("get6")
@click.argument("prefix")
def rdns_get6(prefix):
    """Get rDNS for IPv6 PREFIX."""
    _validate_ipv6_prefix(prefix)
    data = api_get(f"/rdns/ipv6/{prefix}")
    console.print(data.get("rdns") or "[dim]no rDNS set[/dim]")


@rdns.command("set6")
@click.argument("prefix")
@click.argument("hostname")
def rdns_set6(prefix, hostname):
    """Set rDNS for IPv6 PREFIX."""
    _validate_ipv6_prefix(prefix)
    api_put(f"/rdns/ipv6/{prefix}", {"hostname": hostname})
    console.print(f"[green]✓[/green] rDNS {prefix} → {hostname}")


@rdns.command("delete6")
@click.argument("prefix")
@click.option("-f", "--force", is_flag=True)
def rdns_delete6(prefix, force):
    """Delete rDNS for IPv6 PREFIX."""
    _validate_ipv6_prefix(prefix)
    if not force and not Confirm.ask(f"Delete rDNS for [bold]{prefix}[/bold]?"): return
    api_delete(f"/rdns/ipv6/{prefix}")
    console.print(f"[green]✓[/green] rDNS for {prefix} deleted.")

# ── rename ────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("server")
@click.argument("nickname")
def rename(server, nickname):
    """Set nickname for SERVER."""
    sid = resolve(server)
    api_patch(f"/servers/{sid}", {"nickname": nickname})
    console.print(f"[green]✓[/green] Nickname → [bold]{nickname}[/bold]")


@cli.command()
@click.argument("server")
@click.argument("hostname")
def set_hostname(server, hostname):
    """Set hostname for SERVER."""
    sid = resolve(server)
    api_patch(f"/servers/{sid}", {"hostname": hostname})
    console.print(f"[green]✓[/green] Hostname → [bold]{hostname}[/bold]")

# ── reinstall / OS install ────────────────────────────────────────────────────

def _get_user_id() -> int:
    """Decode user ID from JWT access token payload."""
    import base64 as _b64
    token = get_access_token()
    try:
        payload_b64 = token.split('.')[1]
        payload_b64 += '=' * (4 - len(payload_b64) % 4)
        payload = json.loads(_b64.urlsafe_b64decode(payload_b64))
    except Exception:
        console.print("[red]Failed to decode JWT payload.[/red]")
        sys.exit(1)
    uid = payload.get("userId") or payload.get("user_id")
    if uid:
        try:
            return int(uid)
        except (ValueError, TypeError):
            pass
    # fallback: iterate known claim names
    for key in ("sub", "id", "userId", "user_id"):
        val = payload.get(key)
        if val and str(val).isdigit():
            try:
                return int(val)
            except (ValueError, TypeError):
                continue
    console.print(f"[red]Could not determine user ID from JWT.[/red] Claims: {list(payload.keys())}")
    sys.exit(1)


@cli.group()
def install():
    """OS installation and reinstallation."""


@install.command("images")
@click.argument("server")
@click.option("--apps", is_flag=True, help="Show app images instead of OS images.")
@click.option("--json", "as_json", is_flag=True)
def install_images(server, apps, as_json):
    """List available OS images for SERVER."""
    sid    = resolve(server)
    params = {"app": "true"} if apps else {}
    data   = api_get(f"/servers/{sid}/imageflavours", params=params)
    data   = data if isinstance(data, list) else []

    if as_json:
        click.echo(json.dumps(data, indent=2)); return
    if not data:
        console.print("[dim]No images available.[/dim]"); return

    table = Table(title=f"{'App ' if apps else 'OS '}Images — {server}", box=box.ROUNDED)
    table.add_column("ID",     style="dim",  justify="right")
    table.add_column("OS / Image",  style="bold")
    table.add_column("Flavour", style="cyan")
    table.add_column("Description", style="dim")

    for img in data:
        table.add_row(
            str(img.get("id", "")),
            img.get("image", {}).get("name", ""),
            img.get("alias", img.get("name", "")),
            img.get("text", ""),
        )
    console.print(table)
    console.print("\nInstall with: [bold]netcup-cli install run SERVER --image-id ID[/bold]")


@install.command("run")
@click.argument("server")
@click.option("--image-id",   required=True, type=int, help="Image flavour ID (from 'install images').")
@click.option("--hostname",   default="", help="Set hostname after install.")
@click.option("--locale",     default="", help="e.g. en_US.UTF-8")
@click.option("--timezone",   default="", help="e.g. Europe/Berlin")
@click.option("--user",       default="", help="Additional non-root user to create.")
@click.option("--user-pass",  default="", help="Password for the additional user.")
@click.option("--ssh-key",    "ssh_keys", multiple=True, type=int, help="SSH key ID(s) to inject (repeatable).")
@click.option("--ssh-password/--no-ssh-password", default=True, show_default=True,
              help="Allow SSH password authentication.")
@click.option("--script",     default="", help="Custom cloud-init bash script.")
@click.option("--full-disk/--no-full-disk", default=True, show_default=True,
              help="Use full disk for root partition.")
@click.option("--email", is_flag=True, help="Send confirmation email after install.")
@click.option("--wait", is_flag=True, help="Wait for install to complete.")
@click.option("-f", "--force", is_flag=True, help="Skip confirmation.")
def install_run(server, image_id, hostname, locale, timezone, user, user_pass,
                ssh_keys, ssh_password, script, full_disk, email, wait, force):
    """Install/reinstall SERVER with an OS image.

    Example:

        netcup-cli install images head-server        # list images
        netcup-cli install run head-server --image-id 112 --hostname myserver

    WARNING: This will ERASE all data on the server!
    """
    sid = resolve(server)

    if script:
        console.print("[yellow bold]WARNING:[/yellow bold] --script runs arbitrary bash as root on the new system. Review its contents before proceeding.")

    if not force:
        console.print(f"[red bold]WARNING:[/red bold] All data on [bold]{server}[/bold] will be erased!")
        if not Confirm.ask(f"Install image [bold]{image_id}[/bold] on [bold]{server}[/bold]?"):
            return

    body: dict = {
        "imageFlavourId":          image_id,
        "rootPartitionFullDiskSize": full_disk,
        "sshPasswordAuthentication": ssh_password,
        "emailToExecutingUser":     email,
    }
    if hostname:  body["hostname"]                = hostname
    if locale:    body["locale"]                  = locale
    if timezone:  body["timezone"]                = timezone
    if user:      body["additionalUserUsername"]  = user
    if user_pass: body["additionalUserPassword"]  = user_pass
    if ssh_keys:  body["sshKeyIds"]               = list(ssh_keys)
    if script:    body["customScript"]            = script

    result  = api_post(f"/servers/{sid}/image", body)
    task_id = result.get("uuid", result.get("id", ""))
    console.print(f"[green]✓[/green] Installation started. Task: [bold]{task_id}[/bold]")
    if wait and task_id:
        wait_task(task_id, "Install")

# ── rescue system ─────────────────────────────────────────────────────────────

@cli.group()
def rescue():
    """Rescue system management."""


@rescue.command("status")
@click.argument("server")
@click.option("--json", "as_json", is_flag=True)
def rescue_status(server, as_json):
    """Show rescue system status for SERVER."""
    sid  = resolve(server)
    data = api_get(f"/servers/{sid}/rescuesystem")
    if as_json:
        click.echo(json.dumps(data, indent=2)); return
    active = data.get("active", False)
    pw     = data.get("password")
    status = "[green]ACTIVE[/green]" if active else "[dim]inactive[/dim]"
    console.print(f"Rescue system: {status}")
    if active and pw:
        console.print(f"SSH password:  [bold yellow]{pw}[/bold yellow]")


@rescue.command("activate")
@click.argument("server")
@click.option("--wait", is_flag=True)
@click.option("-f", "--force", is_flag=True)
def rescue_activate(server, wait, force):
    """Activate rescue system for SERVER.

    The server will reboot into the rescue system.
    SSH login with the password shown in 'rescue status'.
    """
    if not force and not Confirm.ask(f"Activate rescue system on [bold]{server}[/bold]?\n"
                                      "  (server will reboot)"):
        return
    sid    = resolve(server)
    result = api_post(f"/servers/{sid}/rescuesystem", {})
    task_id = result.get("uuid", "")
    console.print(f"[green]✓[/green] Rescue activation started.")
    if wait and task_id:
        wait_task(task_id, "Rescue activate")
    console.print("Run [bold]netcup-cli rescue status {server}[/bold] to get the SSH password.")


@rescue.command("deactivate")
@click.argument("server")
@click.option("--wait", is_flag=True)
@click.option("-f", "--force", is_flag=True)
def rescue_deactivate(server, wait, force):
    """Deactivate rescue system and reboot SERVER normally."""
    if not force and not Confirm.ask(f"Deactivate rescue and reboot [bold]{server}[/bold]?"):
        return
    sid    = resolve(server)
    result = api_delete(f"/servers/{sid}/rescuesystem")
    task_id = (result or {}).get("uuid", "")
    console.print(f"[green]✓[/green] Rescue deactivation started.")
    if wait and task_id:
        wait_task(task_id, "Rescue deactivate")

# ── SSH keys ──────────────────────────────────────────────────────────────────

@cli.group()
def sshkeys():
    """SSH key management."""


@sshkeys.command("list")
@click.option("--json", "as_json", is_flag=True)
def sshkeys_list(as_json):
    """List SSH keys in your account."""
    uid  = _get_user_id()
    data = api_get(f"/users/{uid}/ssh-keys")
    data = data if isinstance(data, list) else data.get("data", [])
    if as_json:
        click.echo(json.dumps(data, indent=2)); return
    if not data:
        console.print("[dim]No SSH keys found.[/dim]"); return

    table = Table(title="SSH Keys", box=box.ROUNDED)
    table.add_column("ID",          style="dim",  justify="right")
    table.add_column("Name",        style="bold")
    table.add_column("Fingerprint", style="cyan")
    table.add_column("Type",        style="dim")

    for k in data:
        table.add_row(
            str(k.get("id", "")),
            k.get("name", k.get("label", "")),
            k.get("fingerprint", ""),
            k.get("type", k.get("algorithm", "")),
        )
    console.print(table)
    console.print("\nUse [bold]--ssh-key ID[/bold] with [bold]netcup-cli install run[/bold] to inject keys.")

# ── VNC console ───────────────────────────────────────────────────────────────

VNC_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>VNC — {hostname}</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ background: #1a1a1a; display: flex; flex-direction: column;
           height: 100vh; font-family: monospace; color: #ccc; }}
    #toolbar {{ background: #111; padding: 6px 12px; display: flex;
                align-items: center; gap: 12px; font-size: 13px; flex-shrink: 0; }}
    #toolbar span {{ color: #888; }}
    #toolbar strong {{ color: #fff; }}
    #status {{ margin-left: auto; color: #f90; }}
    #screen {{ flex: 1; overflow: hidden; }}
    #screen canvas {{ width: 100% !important; height: 100% !important; }}
  </style>
</head>
<body>
  <div id="toolbar">
    <span>netcup-cli VNC</span>
    <strong>{hostname}</strong>
    <span id="status">Connecting…</span>
  </div>
  <div id="screen"></div>
  <script type="module">
    import RFB from 'https://cdn.jsdelivr.net/npm/@novnc/novnc@1.5.0/core/rfb.js'
      // integrity="sha384-O+Gv1O92p9jznqgsBMGcJcBNwGeXVT9WFlO9lmqcqTM937WM9jCVpjbiD8MYWOex" crossorigin="anonymous"

    const wsUrl = '{ws_url}';
    const status = document.getElementById('status');

    let rfb;
    try {{
      rfb = new RFB(document.getElementById('screen'), wsUrl);
      rfb.scaleViewport = true;
      rfb.resizeSession = true;
      rfb.addEventListener('connect',    () => status.textContent = '● Connected');
      rfb.addEventListener('disconnect', (e) => {{
        status.style.color = '#f44';
        status.textContent = '✗ Disconnected' + (e.detail.clean ? '' : ' (error)');
      }});
    }} catch (e) {{
      status.style.color = '#f44';
      status.textContent = '✗ ' + e.message;
    }}
  </script>
</body>
</html>
"""


@cli.command()
@click.argument("server")
@click.option("--url-only", is_flag=True, help="Print WebSocket URL instead of opening browser.")
@click.option("--ws-url", is_flag=True, help="Print raw WebSocket URL.")
def vnc(server, url_only, ws_url):
    """Open the VNC console for SERVER in the browser via noVNC.

    Generates a local HTML page with noVNC embedded and opens it.
    The access token is embedded so no SCP login is needed.

    Example:

        netcup-cli vnc head-server
        netcup-cli vnc 787734 --url-only
    """
    import tempfile

    sid   = resolve(server)
    data  = api_get(f"/servers/{sid}")
    host  = data.get("hostname", data.get("name", str(sid)))
    token = get_access_token()

    ws = f"wss://www.servercontrolpanel.de/scp-core/api/v1/servers/{sid}/vnc?token={token}"

    if ws_url or url_only:
        console.print(ws)
        return

    html = VNC_HTML_TEMPLATE.format(hostname=host, ws_url=ws)

    # Create temp file with 0o600 from the start — token is embedded in HTML
    vnc_dir = Path.home() / ".config" / "netcup-cli"
    vnc_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(suffix=".html", prefix="netcup-vnc-", dir=str(vnc_dir))
    try:
        os.write(fd, html.encode())
    finally:
        os.close(fd)
    os.chmod(tmp_path, 0o600)

    console.print(f"VNC console for [bold]{host}[/bold]")
    console.print(f"[dim]Temp page: {tmp_path}[/dim]")
    _open_browser(f"file://{tmp_path}")
    # Clean up after browser has had time to load the page
    atexit.register(lambda p=tmp_path: Path(p).unlink(missing_ok=True))


def _open_browser(url: str) -> None:
    """Open URL in the user's default browser."""
    import subprocess, shutil

    console.print(f"Opening: [bold cyan]{url}[/bold cyan]")

    for opener in ("xdg-open", "sensible-browser", "x-www-browser", "firefox", "chromium-browser", "google-chrome"):
        if shutil.which(opener):
            subprocess.Popen([opener, url],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return

    console.print("[yellow]No browser found. Open this URL manually:[/yellow]")
    console.print(f"  {url}")

# ── help / man pages ──────────────────────────────────────────────────────────

@cli.command("commands")
@click.pass_context
def list_commands(ctx):
    """Show all available commands with descriptions."""
    root = ctx.find_root()
    cli_cmd = root.command

    console.print(f"\n[bold]netcup-cli[/bold] — Netcup VPS CLI v1.4.0\n")

    def print_group(cmd, prefix=""):
        if hasattr(cmd, 'commands'):
            for name, sub in sorted(cmd.commands.items()):
                full = f"{prefix}{name}" if not prefix else f"{prefix} {name}"
                desc = (sub.help or "").split("\n")[0][:60]
                console.print(f"  [cyan]{full:<30}[/cyan] [dim]{desc}[/dim]")
                if hasattr(sub, 'commands') and sub.commands:
                    print_group(sub, full)

    print_group(cli_cmd)
    console.print("\nRun [bold]netcup-cli COMMAND --help[/bold] for details.")
    console.print("Run [bold]man netcup-cli-COMMAND[/bold] for the man page.\n")


@cli.command("gen-manpages")
@click.option("--dir", "outdir", default=str(Path.home() / ".local/share/man/man1"),
              show_default=True, help="Output directory.")
def gen_manpages(outdir):
    """Generate man pages for all commands into OUTDIR."""
    try:
        from click_man.core import generate_man_page
    except ImportError:
        console.print("[red]click-man not installed.[/red] Run: pip install click-man")
        sys.exit(1)

    import click as _click
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)

    def gen(cmd, file_prefix, info_name, parent_ctx=None):
        ctx = _click.Context(cmd, info_name=info_name, parent=parent_ctx)
        fname = f"{file_prefix}.1"
        (out / fname).write_text(generate_man_page(ctx, version="1.4.0"))
        console.print(f"  [green]✓[/green] {fname}")
        if hasattr(cmd, 'commands'):
            for sub_name, sub_cmd in cmd.commands.items():
                gen(sub_cmd, f"{file_prefix}-{sub_name}", sub_name, ctx)

    gen(cli, "netcup-cli", "netcup-cli")
    console.print(f"\n[green]Man pages written to {out}[/green]")
    console.print(f"Use: [bold]MANPATH={out.parent} man netcup-cli[/bold]")

# ── auto man pages ────────────────────────────────────────────────────────────

VERSION = "1.4.0"
_MAN_DIR     = Path.home() / ".local" / "share" / "man" / "man1"
_MAN_STAMP   = CONFIG_DIR / ".manpage_version"


def _auto_gen_manpages() -> None:
    """Silently regenerate man pages when version changed or pages are missing."""
    try:
        current = _MAN_STAMP.read_text().strip() if _MAN_STAMP.exists() else ""
        main_page = _MAN_DIR / "netcup-cli.1"
        if current == VERSION and main_page.exists():
            return  # already up to date

        from click_man.core import generate_man_page
        import click as _click

        _MAN_DIR.mkdir(parents=True, exist_ok=True)

        def gen(cmd, file_prefix, info_name, parent_ctx=None):
            ctx = _click.Context(cmd, info_name=info_name, parent=parent_ctx)
            (_MAN_DIR / f"{file_prefix}.1").write_text(
                generate_man_page(ctx, version=VERSION)
            )
            if hasattr(cmd, 'commands'):
                for sub_name, sub_cmd in cmd.commands.items():
                    gen(sub_cmd, f"{file_prefix}-{sub_name}", sub_name, ctx)

        gen(cli, "netcup-cli", "netcup-cli")
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _MAN_STAMP.write_text(VERSION)
    except Exception:
        pass  # never break the CLI over man page generation


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _auto_gen_manpages()
    cli()
