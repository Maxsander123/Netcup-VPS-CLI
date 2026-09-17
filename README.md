# netcup-cli

A terminal client for [Netcup](https://www.netcup.eu/) VPS management. It talks directly to the Netcup SCP REST API via OAuth2 and gives you full control over your servers — power, networking, snapshots, reinstalls, rescue mode, and more — without touching a browser.

---

## Features

| Command group | What you can do |
|---|---|
| **Auth** | Login / logout via OAuth2 device flow |
| **Server info** | List servers, view details, show live status, list IPs |
| **Power** | Start, stop (ACPI), hard reset, hard power-off, power cycle |
| **Disks** | List disks, inspect a single disk |
| **Snapshots** | List, create, delete |
| **Network** | List interfaces (with traffic stats), add/remove interfaces |
| **Reverse DNS** | Get/set/delete rDNS for IPv4 and IPv6 |
| **ISO** | Attach, detach, check status |
| **OS install** | List available images, reinstall/install with cloud-init support |
| **Rescue** | Activate/deactivate rescue system |
| **SSH keys** | List, add, delete account SSH keys |
| **VNC** | Open web VNC console in browser |
| **SSH** | Direct SSH to server (auto-detects IPv4) |
| **Activity logs** | View server activity log / history |
| **Tasks** | Inspect or wait on background tasks |
| **Self-update** | Update to the latest release |

---

## Installation

### Option A — Debian/Ubuntu (.deb)

Download the latest `.deb` from [GitHub Releases](https://github.com/Maxsander123/Netcup-VPS-CLI/releases/latest) and install:

```bash
sudo apt install ./netcup-cli_VERSION_all.deb
```

### Option B — macOS

```bash
bash <(curl -fsSL https://github.com/Maxsander123/Netcup-VPS-CLI/releases/latest/download/install-macos.sh)
```

### Update

Once installed, update in-place from any platform:

```bash
netcup-cli update
```

On Debian/Ubuntu this downloads the latest `.deb` and runs `sudo apt install`. On macOS / venv installs it replaces `netcup-cli.py` in-place.

---

## Authentication

netcup-cli uses the **OAuth2 Device Code flow** against the Netcup SCP Keycloak instance. No passwords are stored.

```bash
netcup-cli login
```

This prints a URL. Open it in your browser, complete authentication, then return to the terminal. The refresh token is stored at:

```
~/.config/netcup-cli/credentials.json   (chmod 600)
```

Access tokens are obtained automatically on every command by exchanging the stored refresh token. To remove credentials:

```bash
netcup-cli logout
```

---

## Command Reference

All commands accept `SERVER` as either a numeric server ID, hostname, or nickname.

---

### `login`

Authenticate via OAuth2 device flow.

```
netcup-cli login
```

Opens a browser authorization URL. After approval, credentials are saved to `~/.config/netcup-cli/credentials.json`.

---

### `logout`

Remove stored credentials.

```
netcup-cli logout
```

---

### `list`

List all VPS in your account.

```
netcup-cli list [--json] [--no-status]
```

| Flag | Description |
|---|---|
| `--json` | Output raw JSON |
| `--no-status` | Skip live status fetch (faster, avoids extra API calls) |

Example:

```bash
netcup-cli list
netcup-cli list --no-status
```

---

### `info`

Show full details for a server: hardware, IPs, disks, uptime, rescue state.

```
netcup-cli info SERVER [--json]
```

Example:

```bash
netcup-cli info my-server
netcup-cli info 787734 --json
```

---

### `status`

Show compact live state (RUNNING / STOPPED / etc.) for a server.

```
netcup-cli status SERVER
```

Example:

```bash
netcup-cli status my-server
```

---

### `ips`

Show all IPv4 and IPv6 addresses with gateway and rDNS information.

```
netcup-cli ips SERVER
```

Example:

```bash
netcup-cli ips my-server
```

---

### `start`

Power on a server.

```
netcup-cli start SERVER
```

---

### `stop`

Send an ACPI shutdown signal (graceful).

```
netcup-cli stop SERVER [-f]
```

| Flag | Description |
|---|---|
| `-f`, `--force` | Skip confirmation prompt |

Example:

```bash
netcup-cli stop my-server
netcup-cli stop my-server -f
```

---

### `reset`

Hard reset a server (equivalent to pressing the physical reset button).

```
netcup-cli reset SERVER [-f]
```

| Flag | Description |
|---|---|
| `-f`, `--force` | Skip confirmation prompt |

---

### `poweroff`

Cut power immediately (hard off, no graceful shutdown).

```
netcup-cli poweroff SERVER [-f]
```

| Flag | Description |
|---|---|
| `-f`, `--force` | Skip confirmation prompt |

---

### `powercycle`

Hard off followed by power on.

```
netcup-cli powercycle SERVER [-f]
```

| Flag | Description |
|---|---|
| `-f`, `--force` | Skip confirmation prompt |

---

### `disks`

#### `disks list`

List all disks for a server.

```
netcup-cli disks list SERVER [--json]
```

Example:

```bash
netcup-cli disks list my-server
```

#### `disks get`

Show details for a specific disk.

```
netcup-cli disks get SERVER DISK [--json]
```

`DISK` is the device name (e.g. `vda`).

Example:

```bash
netcup-cli disks get my-server vda
```

---

### `snapshot`

#### `snapshot list`

List all snapshots for a server.

```
netcup-cli snapshot list SERVER [--json]
```

#### `snapshot create`

Create a snapshot.

```
netcup-cli snapshot create SERVER NAME [--disk vda] [--wait]
```

| Flag | Description |
|---|---|
| `--disk` | Disk to snapshot (default: `vda`) |
| `--wait` | Poll until snapshot is ready |

Example:

```bash
netcup-cli snapshot create my-server before-upgrade --wait
```

#### `snapshot delete`

Delete a snapshot.

```
netcup-cli snapshot delete SERVER NAME [-f] [--wait]
```

| Flag | Description |
|---|---|
| `-f`, `--force` | Skip confirmation prompt |
| `--wait` | Poll until deletion is complete |

Example:

```bash
netcup-cli snapshot delete my-server before-upgrade -f
```

---

### `network`

#### `network list`

List network interfaces with IPs and monthly traffic statistics.

```
netcup-cli network list SERVER [--json]
```

#### `network add`

Add a network interface to a server.

```
netcup-cli network add SERVER --vlan VLAN_ID [--driver virtio]
```

| Flag | Description |
|---|---|
| `--vlan` | VLAN ID (required) |
| `--driver` | NIC driver (default: `virtio`) |

> **Note:** The server must be in SHUTOFF state before adding or removing a network interface.

Example:

```bash
netcup-cli network add my-server --vlan 100
```

#### `network remove`

Remove a network interface by MAC address.

```
netcup-cli network remove SERVER MAC [-f]
```

| Flag | Description |
|---|---|
| `-f`, `--force` | Skip confirmation prompt |

> **Note:** The server must be in SHUTOFF state before adding or removing a network interface.

Example:

```bash
netcup-cli network remove my-server 52:54:00:ab:cd:ef
```

---

### `rdns`

#### `rdns get`

Get reverse DNS for an IPv4 address.

```
netcup-cli rdns get IP
```

Example:

```bash
netcup-cli rdns get 1.2.3.4
```

#### `rdns set`

Set reverse DNS for an IPv4 address.

```
netcup-cli rdns set IP HOSTNAME
```

Example:

```bash
netcup-cli rdns set 1.2.3.4 mail.example.com
```

#### `rdns delete`

Delete reverse DNS for an IPv4 address.

```
netcup-cli rdns delete IP [-f]
```

| Flag | Description |
|---|---|
| `-f`, `--force` | Skip confirmation prompt |

#### `rdns get6`

Get reverse DNS for an IPv6 prefix.

```
netcup-cli rdns get6 PREFIX
```

Example:

```bash
netcup-cli rdns get6 2a03:4000:1::/48
```

#### `rdns set6`

Set reverse DNS for an IPv6 prefix.

```
netcup-cli rdns set6 PREFIX HOSTNAME
```

Example:

```bash
netcup-cli rdns set6 2a03:4000:1::/48 mail.example.com
```

#### `rdns delete6`

Delete reverse DNS for an IPv6 prefix.

```
netcup-cli rdns delete6 PREFIX [-f]
```

| Flag | Description |
|---|---|
| `-f`, `--force` | Skip confirmation prompt |

---

### `iso`

#### `iso status`

Show whether an ISO is currently attached to a server.

```
netcup-cli iso status SERVER [--json]
```

#### `iso attach`

Attach an ISO image to a server.

```
netcup-cli iso attach SERVER NAME
```

`NAME` is the ISO filename as known to Netcup (e.g. `ubuntu-24.04-live-server-amd64.iso`).

Example:

```bash
netcup-cli iso attach my-server ubuntu-24.04-live-server-amd64.iso
```

#### `iso detach`

Detach the currently attached ISO.

```
netcup-cli iso detach SERVER
```

---

### `install`

#### `install images`

List available OS (or app) images for a server.

```
netcup-cli install images SERVER [--apps] [--json]
```

| Flag | Description |
|---|---|
| `--apps` | Show app images instead of OS images |

Example:

```bash
netcup-cli install images my-server
netcup-cli install images my-server --apps
```

#### `install run`

Install or reinstall a server with an OS image. **All data on the server will be erased.**

```
netcup-cli install run SERVER --image-id ID [OPTIONS]
```

| Flag | Description |
|---|---|
| `--image-id ID` | Image flavour ID from `install images` (required) |
| `--hostname` | Hostname to set after install |
| `--locale` | Locale, e.g. `en_US.UTF-8` |
| `--timezone` | Timezone, e.g. `Europe/Berlin` |
| `--user` | Additional non-root user to create |
| `--user-pass` | Password for the additional user |
| `--ssh-key ID` | SSH key ID to inject (repeatable; get IDs from `sshkeys list`) |
| `--ssh-password / --no-ssh-password` | Allow/disallow SSH password auth (default: allow) |
| `--script TEXT` | Inline cloud-init bash script |
| `--cloud-init-file PATH` | Path to a cloud-init YAML or bash script file |
| `--full-disk / --no-full-disk` | Use full disk for root partition (default: yes) |
| `--email` | Send confirmation email after install |
| `--wait` | Poll until installation completes |
| `-f`, `--force` | Skip confirmation prompt |

> `--script` and `--cloud-init-file` are mutually exclusive.

Examples:

```bash
# List images first
netcup-cli install images my-server

# Basic install
netcup-cli install run my-server --image-id 112 --hostname myserver --wait

# Install with SSH key injection and cloud-init
netcup-cli install run my-server --image-id 112 \
  --ssh-key 42 \
  --cloud-init-file user-data.yaml \
  --wait
```

---

### `rescue`

#### `rescue status`

Show whether the rescue system is active and print the SSH password if it is.

```
netcup-cli rescue status SERVER [--json]
```

#### `rescue activate`

Activate the rescue system. The server reboots into a minimal rescue environment accessible via SSH.

```
netcup-cli rescue activate SERVER [--wait] [-f]
```

| Flag | Description |
|---|---|
| `--wait` | Poll until activation completes |
| `-f`, `--force` | Skip confirmation prompt |

After activation, run `netcup-cli rescue status SERVER` to get the SSH password.

Example:

```bash
netcup-cli rescue activate my-server --wait
netcup-cli rescue status my-server
```

#### `rescue deactivate`

Deactivate rescue mode and reboot the server back into the normal OS.

```
netcup-cli rescue deactivate SERVER [--wait] [-f]
```

| Flag | Description |
|---|---|
| `--wait` | Poll until deactivation completes |
| `-f`, `--force` | Skip confirmation prompt |

---

### `sshkeys`

#### `sshkeys list`

List SSH keys stored in your Netcup account.

```
netcup-cli sshkeys list [--json]
```

Use the ID column with `install run --ssh-key` to inject keys during OS installs.

#### `sshkeys add`

Add an SSH public key to your account.

```
netcup-cli sshkeys add NAME [--pubkey TEXT] [--file PATH]
```

| Flag | Description |
|---|---|
| `--pubkey` | Public key string (e.g. `ssh-ed25519 AAAA...`) |
| `--file` | Path to a `.pub` file |

`--pubkey` and `--file` are mutually exclusive.

Examples:

```bash
netcup-cli sshkeys add "My Laptop" --file ~/.ssh/id_ed25519.pub
netcup-cli sshkeys add "My Laptop" --pubkey "ssh-ed25519 AAAA..."
```

#### `sshkeys delete`

Delete an SSH key from your account.

```
netcup-cli sshkeys delete KEY_ID [-f]
```

| Flag | Description |
|---|---|
| `-f`, `--force` | Skip confirmation prompt |

Example:

```bash
netcup-cli sshkeys delete 42
```

---

### `vnc`

Open the Netcup SCP web VNC console for a server in your default browser. You must be logged in to `servercontrolpanel.de` in that browser session.

```
netcup-cli vnc SERVER [--url-only]
```

| Flag | Description |
|---|---|
| `--url-only` | Print the URL instead of opening the browser |

Examples:

```bash
netcup-cli vnc my-server
netcup-cli vnc 787734 --url-only
```

---

### `ssh`

Open an SSH connection to a server. The first public IPv4 address is detected automatically from the API.

```
netcup-cli ssh SERVER [-u USER] [-p PORT] [--ip IP]
```

| Flag | Description |
|---|---|
| `-u`, `--user` | SSH username (default: `root`) |
| `-p`, `--port` | SSH port (default: `22`) |
| `--ip` | Override auto-detected IP |

> `netcup-cli ssh` auto-detects the first public IPv4 from the API. Use `--ip` if you need to connect via a different address or if no public IPv4 is assigned.

Examples:

```bash
netcup-cli ssh my-server
netcup-cli ssh my-server --user mia --port 2222
netcup-cli ssh my-server --ip 10.0.0.5
```

---

### `logs`

Show the activity log for a server.

```
netcup-cli logs SERVER [-n LINES] [--json]
```

| Flag | Description |
|---|---|
| `-n`, `--lines` | Number of log entries to show (default: 20) |
| `--json` | Raw JSON output |

Example:

```bash
netcup-cli logs my-server -n 50
```

---

### `history`

Alias for `logs`.

```
netcup-cli history SERVER [-n LINES] [--json]
```

Example:

```bash
netcup-cli history my-server
netcup-cli history my-server -n 50
```

---

### `rename`

Set the nickname for a server.

```
netcup-cli rename SERVER NICKNAME
```

Example:

```bash
netcup-cli rename 787734 my-server
```

---

### `set-hostname`

Set the hostname for a server.

```
netcup-cli set-hostname SERVER HOSTNAME
```

Example:

```bash
netcup-cli set-hostname my-server web01.example.com
```

---

### `task`

Inspect or wait on a background task by UUID. Task UUIDs are returned by long-running operations like snapshot creation and OS installs.

```
netcup-cli task TASK_UUID [--wait] [--json]
```

| Flag | Description |
|---|---|
| `--wait` | Poll until the task finishes |
| `--json` | Raw JSON output |

Example:

```bash
netcup-cli task a1b2c3d4-... --wait
```

---

### `update`

Update netcup-cli to the latest release from GitHub.

- **Debian/Ubuntu (.deb install):** downloads the latest `.deb` and runs `sudo apt install`.
- **macOS / venv install:** replaces `netcup-cli.py` in-place (no sudo needed).

```
netcup-cli update
```

---

### `commands`

Print all available commands and their one-line descriptions.

```
netcup-cli commands
```

---

### `gen-manpages`

Generate man pages for all commands into a local directory.

```
netcup-cli gen-manpages [--dir PATH]
```

| Flag | Description |
|---|---|
| `--dir` | Output directory (default: `~/.local/share/man/man1`) |

Requires `click-man` (`pip install click-man`). Man pages are also generated automatically on first run and on version upgrades.

---

## Notes

- **VLAN add/remove requires the server to be SHUTOFF.** Running `network add` or `network remove` on a powered-on server will be rejected by the API.
- **SSH key IDs** for use with `install run --ssh-key` can be retrieved via `netcup-cli sshkeys list`.
- **`netcup-cli ssh`** auto-detects the first public IPv4 address from the API. If the server has no public IPv4, use `--ip` to specify the address manually.
- **`--cloud-init-file` and `--script` are mutually exclusive.** You can pass an inline script with `--script` or a file path with `--cloud-init-file`, but not both.

---

## License

[PolyForm Noncommercial 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/)
