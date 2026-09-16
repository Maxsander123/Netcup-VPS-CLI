#!/usr/bin/env python3
"""Netcup VPS CLI — manage Netcup VPS via the SCP REST API."""

import json
import os
import sys
import time
from pathlib import Path

import click
import requests
from rich.console import Console
from rich.table import Table
from rich import box
from rich.prompt import Prompt, Confirm

# ── Constants ────────────────────────────────────────────────────────────────

AUTH_BASE = "https://www.servercontrolpanel.de/realms/scp/protocol/openid-connect"
API_BASE  = "https://www.servercontrolpanel.de/scp-core/api/v1"
DEVICE_ENDPOINT = f"{AUTH_BASE}/auth/device"
TOKEN_ENDPOINT  = f"{AUTH_BASE}/token"
CLIENT_ID = "scp-cli"

CONFIG_DIR  = Path.home() / ".config" / "netcup-cli"
CREDS_FILE  = CONFIG_DIR / "credentials.json"

console = Console()

# ── Credential helpers ───────────────────────────────────────────────────────

def load_creds() -> dict:
    if not CREDS_FILE.exists():
        console.print("[red]Not logged in.[/red] Run [bold]netcup-cli login[/bold] first.")
        sys.exit(1)
    return json.loads(CREDS_FILE.read_text())


def save_creds(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CREDS_FILE.write_text(json.dumps(data, indent=2))
    CREDS_FILE.chmod(0o600)


def refresh_access_token(refresh_token: str) -> dict:
    resp = requests.post(TOKEN_ENDPOINT, data={
        "client_id":     CLIENT_ID,
        "grant_type":    "refresh_token",
        "refresh_token": refresh_token,
    })
    if resp.status_code != 200:
        console.print(f"[red]Token refresh failed:[/red] {resp.text}")
        sys.exit(1)
    return resp.json()


def get_access_token() -> str:
    creds = load_creds()
    tokens = refresh_access_token(creds["refresh_token"])
    # persist updated refresh token if the server rotated it
    if "refresh_token" in tokens:
        creds["refresh_token"] = tokens["refresh_token"]
        save_creds(creds)
    return tokens["access_token"]


def headers(token: str | None = None) -> dict:
    tok = token or get_access_token()
    return {"Authorization": f"Bearer {tok}", "Accept": "application/json"}

# ── API helpers ──────────────────────────────────────────────────────────────

def api_get(path: str, params: dict | None = None) -> dict | list:
    resp = requests.get(f"{API_BASE}{path}", headers=headers(), params=params)
    _raise(resp)
    return resp.json()


def api_post(path: str, body: dict) -> dict | list:
    resp = requests.post(f"{API_BASE}{path}", headers=headers(), json=body)
    _raise(resp)
    return resp.json() if resp.content else {}


def api_patch(path: str, body: dict) -> dict | list:
    h = headers()
    h["Content-Type"] = "application/merge-patch+json"
    resp = requests.patch(f"{API_BASE}{path}", headers=h, json=body)
    _raise(resp)
    return resp.json() if resp.content else {}


def api_put(path: str, body: dict) -> dict | list:
    resp = requests.put(f"{API_BASE}{path}", headers=headers(), json=body)
    _raise(resp)
    return resp.json() if resp.content else {}


def api_delete(path: str) -> None:
    resp = requests.delete(f"{API_BASE}{path}", headers=headers())
    _raise(resp)


def _raise(resp: requests.Response) -> None:
    if not resp.ok:
        try:
            msg = resp.json()
        except Exception:
            msg = resp.text
        console.print(f"[red]API error {resp.status_code}:[/red] {msg}")
        sys.exit(1)


def resolve_server(name_or_id: str) -> str:
    """Accept a server name/nickname and return the server id."""
    servers = api_get("/servers")
    items = servers if isinstance(servers, list) else servers.get("data", servers.get("servers", []))
    for s in items:
        sid = s.get("id", s.get("name", ""))
        nick = s.get("nickname", "")
        host = s.get("hostname", "")
        if name_or_id in (sid, nick, host):
            return sid
    return name_or_id  # fall through — let the API return an error

# ── CLI root ─────────────────────────────────────────────────────────────────

@click.group()
@click.version_option("1.0.0", prog_name="netcup-cli")
def cli():
    """Netcup VPS CLI — control your Netcup VPS from the terminal."""

# ── login / logout ───────────────────────────────────────────────────────────

@cli.command()
def login():
    """Authenticate via OAuth2 Device Code flow."""
    resp = requests.post(DEVICE_ENDPOINT, data={"client_id": CLIENT_ID})
    if not resp.ok:
        console.print(f"[red]Failed to start device flow:[/red] {resp.text}")
        sys.exit(1)

    data = resp.json()
    verification_uri = data.get("verification_uri_complete", data.get("verification_uri"))
    user_code       = data.get("user_code")
    device_code     = data["device_code"]
    interval        = data.get("interval", 5)

    console.print(f"\n[bold yellow]Open this URL in your browser:[/bold yellow]")
    console.print(f"  {verification_uri}\n")
    if "verification_uri_complete" not in data:
        console.print(f"  Enter code: [bold]{user_code}[/bold]\n")

    console.print("Waiting for browser authentication", end="", flush=True)
    while True:
        time.sleep(interval)
        token_resp = requests.post(TOKEN_ENDPOINT, data={
            "client_id":   CLIENT_ID,
            "grant_type":  "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
        })
        tok = token_resp.json()
        if token_resp.ok:
            console.print(" [green]✓[/green]")
            save_creds({"refresh_token": tok["refresh_token"]})
            console.print("[green]Logged in successfully.[/green]")
            return
        err = tok.get("error", "")
        if err == "authorization_pending":
            console.print(".", end="", flush=True)
        elif err == "slow_down":
            interval += 5
        else:
            console.print(f"\n[red]Auth error:[/red] {tok}")
            sys.exit(1)


@cli.command()
def logout():
    """Remove stored credentials."""
    if CREDS_FILE.exists():
        CREDS_FILE.unlink()
        console.print("[green]Logged out.[/green]")
    else:
        console.print("Not logged in.")

# ── list ─────────────────────────────────────────────────────────────────────

@cli.command("list")
@click.option("--limit", default=50, show_default=True, help="Max results.")
@click.option("--json", "as_json", is_flag=True, help="Raw JSON output.")
def list_servers(limit, as_json):
    """List all VPS in your account."""
    data = api_get("/servers", params={"limit": limit})
    items = data if isinstance(data, list) else data.get("data", data.get("servers", []))

    if as_json:
        click.echo(json.dumps(items, indent=2))
        return

    table = Table(title="Netcup VPS", box=box.ROUNDED, highlight=True)
    table.add_column("ID / Name",    style="cyan",  no_wrap=True)
    table.add_column("Nickname",     style="bold")
    table.add_column("Status",       justify="center")
    table.add_column("IPv4",         style="green")
    table.add_column("IPv6",         style="dim")

    for s in items:
        status = s.get("status", s.get("state", "?")).upper()
        color  = "green" if status == "RUNNING" else "red" if status in ("STOPPED", "OFF") else "yellow"
        table.add_row(
            s.get("id", s.get("name", "?")),
            s.get("nickname", s.get("hostname", "")),
            f"[{color}]{status}[/{color}]",
            s.get("ipv4", s.get("ipv4Address", "")),
            s.get("ipv6", s.get("ipv6Address", ""))[:39] if s.get("ipv6", s.get("ipv6Address", "")) else "",
        )

    console.print(table)

# ── info ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("server")
@click.option("--json", "as_json", is_flag=True, help="Raw JSON output.")
def info(server, as_json):
    """Show details for SERVER (id, name, or nickname)."""
    sid  = resolve_server(server)
    data = api_get(f"/servers/{sid}")

    if as_json:
        click.echo(json.dumps(data, indent=2))
        return

    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Key",   style="dim", width=20)
    table.add_column("Value", style="bold")

    for k, v in data.items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v)
        table.add_row(k, str(v))

    console.print(table)

# ── ips ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("server")
def ips(server):
    """Show IP addresses assigned to SERVER."""
    sid  = resolve_server(server)
    data = api_get(f"/servers/{sid}")

    table = Table(title=f"IPs — {server}", box=box.ROUNDED)
    table.add_column("Type",    style="cyan")
    table.add_column("Address", style="green")

    ipv4 = data.get("ipv4", data.get("ipv4Address", ""))
    ipv6 = data.get("ipv6", data.get("ipv6Address", ""))

    if ipv4:
        table.add_row("IPv4", ipv4)
    if ipv6:
        table.add_row("IPv6", ipv6)

    # also pull from interfaces if available
    try:
        ifaces = api_get(f"/servers/{sid}/interfaces")
        iface_list = ifaces if isinstance(ifaces, list) else ifaces.get("data", [])
        for iface in iface_list:
            for addr in iface.get("ipAddresses", iface.get("ips", [])):
                table.add_row(
                    f"NIC {iface.get('mac','')} IPv{'6' if ':' in str(addr) else '4'}",
                    str(addr)
                )
    except SystemExit:
        pass

    console.print(table)

# ── power controls ───────────────────────────────────────────────────────────

def _power(server: str, state: str, option: str, confirm_msg: str) -> None:
    sid = resolve_server(server)
    if not Confirm.ask(confirm_msg):
        return
    api_post(f"/servers/{sid}/power", {"state": state, "option": option})
    console.print(f"[green]✓[/green] Power command sent: {option}")


@cli.command()
@click.argument("server")
def start(server):
    """Start (power on) SERVER."""
    sid = resolve_server(server)
    api_post(f"/servers/{sid}/power", {"state": "ON"})
    console.print(f"[green]✓[/green] Start command sent.")


@cli.command()
@click.argument("server")
@click.option("-f", "--force", is_flag=True, help="Skip confirmation.")
def stop(server, force):
    """Gracefully shut down SERVER (ACPI)."""
    if not force and not Confirm.ask(f"Graceful shutdown [bold]{server}[/bold]?"):
        return
    sid = resolve_server(server)
    api_post(f"/servers/{sid}/power", {"state": "OFF", "option": "POWEROFF"})
    console.print(f"[green]✓[/green] Shutdown command sent.")


@cli.command()
@click.argument("server")
@click.option("-f", "--force", is_flag=True, help="Skip confirmation.")
def reset(server, force):
    """Hard reset SERVER (like pressing the reset button)."""
    if not force and not Confirm.ask(f"[red]Hard reset[/red] [bold]{server}[/bold]?"):
        return
    sid = resolve_server(server)
    api_post(f"/servers/{sid}/power", {"state": "ON", "option": "RESET"})
    console.print(f"[green]✓[/green] Reset command sent.")


@cli.command()
@click.argument("server")
@click.option("-f", "--force", is_flag=True, help="Skip confirmation.")
def poweroff(server, force):
    """Hard power off SERVER (cuts power immediately)."""
    if not force and not Confirm.ask(f"[red]Hard power-off[/red] [bold]{server}[/bold]?"):
        return
    sid = resolve_server(server)
    api_post(f"/servers/{sid}/power", {"state": "OFF", "option": "POWEROFF"})
    console.print(f"[green]✓[/green] Power-off command sent.")


@cli.command()
@click.argument("server")
@click.option("-f", "--force", is_flag=True, help="Skip confirmation.")
def powercycle(server, force):
    """Power cycle SERVER (hard off → on)."""
    if not force and not Confirm.ask(f"Power cycle [bold]{server}[/bold]?"):
        return
    sid = resolve_server(server)
    api_post(f"/servers/{sid}/power", {"state": "ON", "option": "POWERCYCLE"})
    console.print(f"[green]✓[/green] Power cycle command sent.")

# ── network / interfaces ─────────────────────────────────────────────────────

@cli.group()
def network():
    """Network interface management."""


@network.command("list")
@click.argument("server")
@click.option("--json", "as_json", is_flag=True)
def net_list(server, as_json):
    """List network interfaces for SERVER."""
    sid   = resolve_server(server)
    data  = api_get(f"/servers/{sid}/interfaces")
    items = data if isinstance(data, list) else data.get("data", [])

    if as_json:
        click.echo(json.dumps(items, indent=2))
        return

    table = Table(title=f"Interfaces — {server}", box=box.ROUNDED)
    table.add_column("MAC",    style="cyan")
    table.add_column("Driver", style="dim")
    table.add_column("VLAN")
    table.add_column("IPs")

    for iface in items:
        ips_str = ", ".join(
            str(a) for a in iface.get("ipAddresses", iface.get("ips", []))
        )
        table.add_row(
            iface.get("mac", iface.get("macAddress", "")),
            iface.get("driver", ""),
            str(iface.get("vlan", iface.get("vlanId", ""))),
            ips_str or "—",
        )

    console.print(table)


@network.command("add")
@click.argument("server")
@click.option("--vlan", required=True, type=int, help="VLAN ID.")
@click.option("--driver", default="virtio", show_default=True, help="NIC driver.")
def net_add(server, vlan, driver):
    """Add a network interface to SERVER."""
    sid = resolve_server(server)
    result = api_post(f"/servers/{sid}/interfaces", {"vlan_id": vlan, "driver": driver})
    console.print(f"[green]✓[/green] Interface added: {result}")


@network.command("remove")
@click.argument("server")
@click.argument("mac")
@click.option("-f", "--force", is_flag=True)
def net_remove(server, mac, force):
    """Remove a network interface by MAC from SERVER."""
    if not force and not Confirm.ask(f"Remove interface [bold]{mac}[/bold] from {server}?"):
        return
    sid = resolve_server(server)
    api_delete(f"/servers/{sid}/interfaces/{mac}")
    console.print(f"[green]✓[/green] Interface {mac} removed.")

# ── rDNS ─────────────────────────────────────────────────────────────────────

@cli.group()
def rdns():
    """Reverse DNS management."""


@rdns.command("get")
@click.argument("ip")
def rdns_get(ip):
    """Get rDNS record for IP."""
    data = api_get(f"/rdns/ipv4/{ip}")
    console.print(data)


@rdns.command("set")
@click.argument("ip")
@click.argument("hostname")
def rdns_set(ip, hostname):
    """Set rDNS record for IP."""
    api_put(f"/rdns/ipv4/{ip}", {"hostname": hostname})
    console.print(f"[green]✓[/green] rDNS for {ip} set to {hostname}")


@rdns.command("delete")
@click.argument("ip")
@click.option("-f", "--force", is_flag=True)
def rdns_delete(ip, force):
    """Delete rDNS record for IP."""
    if not force and not Confirm.ask(f"Delete rDNS for [bold]{ip}[/bold]?"):
        return
    api_delete(f"/rdns/ipv4/{ip}")
    console.print(f"[green]✓[/green] rDNS for {ip} deleted.")

# ── rename ───────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("server")
@click.argument("nickname")
def rename(server, nickname):
    """Set nickname for SERVER."""
    sid = resolve_server(server)
    api_patch(f"/servers/{sid}", {"nickname": nickname})
    console.print(f"[green]✓[/green] Nickname set to [bold]{nickname}[/bold]")

# ── reinstall ────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("server")
@click.option("--image", help="Image/template ID or name to install.")
@click.option("--json", "as_json", is_flag=True, help="Print available images as JSON.")
def reinstall(server, image, as_json):
    """Reinstall SERVER with a new OS image.

    Run without --image to list available images first.
    """
    sid = resolve_server(server)

    # Try to fetch available images
    images_data = None
    for path in (f"/servers/{sid}/images", "/images"):
        try:
            images_data = api_get(path)
            break
        except SystemExit:
            continue

    if images_data is None:
        console.print("[yellow]Could not fetch image list from API.[/yellow]")
        console.print("Provide --image with the template name and try again.")
        return

    images = images_data if isinstance(images_data, list) else images_data.get("data", [])

    if not image:
        if as_json:
            click.echo(json.dumps(images, indent=2))
            return

        table = Table(title="Available Images", box=box.ROUNDED)
        table.add_column("ID",   style="cyan")
        table.add_column("Name", style="bold")
        table.add_column("OS",   style="dim")

        for img in images:
            table.add_row(
                str(img.get("id", img.get("name", "?"))),
                img.get("name", img.get("description", "")),
                img.get("os", img.get("osFamily", "")),
            )

        console.print(table)
        console.print("\nRun with [bold]--image <ID>[/bold] to install.")
        return

    if not Confirm.ask(
        f"[red bold]Reinstall[/red bold] [bold]{server}[/bold] with image [bold]{image}[/bold]?\n"
        "  [red]All data will be lost![/red]"
    ):
        return

    result = api_post(f"/servers/{sid}/images", {"image": image})
    console.print(f"[green]✓[/green] Reinstall started: {result}")

# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli()
