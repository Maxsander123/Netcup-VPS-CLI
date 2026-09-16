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
@click.version_option("1.2.0", prog_name="netcup-cli")
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
    data = api_get(f"/rdns/ipv4/{ip}")
    console.print(data.get("rdns") or "[dim]no rDNS set[/dim]")


@rdns.command("set")
@click.argument("ip")
@click.argument("hostname")
def rdns_set(ip, hostname):
    """Set rDNS for IPv4 IP."""
    api_put(f"/rdns/ipv4/{ip}", {"hostname": hostname})
    console.print(f"[green]✓[/green] rDNS {ip} → {hostname}")


@rdns.command("delete")
@click.argument("ip")
@click.option("-f", "--force", is_flag=True)
def rdns_delete(ip, force):
    """Delete rDNS for IPv4 IP."""
    if not force and not Confirm.ask(f"Delete rDNS for [bold]{ip}[/bold]?"): return
    api_delete(f"/rdns/ipv4/{ip}")
    console.print(f"[green]✓[/green] rDNS for {ip} deleted.")


@rdns.command("get6")
@click.argument("prefix")
def rdns_get6(prefix):
    """Get rDNS for IPv6 PREFIX."""
    data = api_get(f"/rdns/ipv6/{prefix}")
    console.print(data.get("rdns") or "[dim]no rDNS set[/dim]")


@rdns.command("set6")
@click.argument("prefix")
@click.argument("hostname")
def rdns_set6(prefix, hostname):
    """Set rDNS for IPv6 PREFIX."""
    api_put(f"/rdns/ipv6/{prefix}", {"hostname": hostname})
    console.print(f"[green]✓[/green] rDNS {prefix} → {hostname}")


@rdns.command("delete6")
@click.argument("prefix")
@click.option("-f", "--force", is_flag=True)
def rdns_delete6(prefix, force):
    """Delete rDNS for IPv6 PREFIX."""
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

# ── reinstall ─────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("server")
def reinstall(server):
    """Open SCP web UI for OS reinstall.

    The reinstall endpoint is not in the public REST API.
    """
    sid  = resolve(server)
    data = api_get(f"/servers/{sid}")
    name = data.get("name", str(sid))
    url  = f"https://www.servercontrolpanel.de/SCP/VServer#server={name}&action=reinstall"
    console.print(
        f"[yellow]Reinstall is not available via the public REST API.[/yellow]\n"
        f"Open the SCP web UI:\n\n  [bold cyan]{url}[/bold cyan]\n"
    )

# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli()
