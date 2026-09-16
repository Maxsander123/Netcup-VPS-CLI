# netcup-cli

A full-featured CLI tool to manage Netcup VPS servers via the [SCP REST API](https://www.servercontrolpanel.de).

## Features

| Category | Commands |
|---|---|
| **Servers** | `list`, `info`, `power` (on/off/reset/acpi), `status` |
| **OS Install** | `install images`, `install run` |
| **Console** | `vnc` (opens noVNC in browser) |
| **Networking** | `net list`, `net add`, `net remove` |
| **rDNS** | `rdns get/set/delete`, `rdns get6/set6/delete6` |
| **Snapshots** | `snap list`, `snap create`, `snap restore`, `snap delete` |
| **Backups** | `backup list`, `backup restore` |
| **Disks** | `disks list`, `disks get` |
| **SSH Keys** | `key list`, `key add`, `key delete` |
| **Rescue** | `rescue enable`, `rescue disable`, `rescue status` |
| **Logs** | `logs` |
| **Tasks** | `task list`, `task get` |

## Update

```bash
netcup-cli update
```

Checks GitHub for a newer release, downloads the `.deb`, and installs it via `sudo apt install`.

## Installation

### Option A — apt (Ubuntu/Debian, recommended)

```bash
echo "deb [trusted=yes] https://maxsander123.github.io/Netcup-VPS-CLI/ ./" \
  | sudo tee /etc/apt/sources.list.d/netcup-cli.list
sudo apt update
sudo apt install netcup-cli
```

### Option B — macOS

```bash
bash <(curl -fsSL https://github.com/Maxsander123/Netcup-VPS-CLI/releases/latest/download/install-macos.sh)
```

Requires Python 3.8+ (`brew install python3` if needed).

### Option C — install.sh (Debian/Ubuntu, no root required)

```bash
git clone https://github.com/Maxsander123/Netcup-VPS-CLI.git
cd Netcup-VPS-CLI
bash install.sh
source ~/.bashrc
```

### Requirements

- Python 3.8+
- `python3-venv` + `python3-pip` (Debian/Ubuntu) or Homebrew Python (macOS)

## Usage

```bash
# Log in via Netcup SCP OAuth (opens a browser URL to authenticate)
netcup-cli login

# List your VPS servers
netcup-cli list

# Show server details
netcup-cli info my-server

# Power controls
netcup-cli power my-server on
netcup-cli power my-server off
netcup-cli power my-server reset

# Open VNC console in browser
netcup-cli vnc my-server

# List available OS images
netcup-cli install images my-server

# Reinstall OS
netcup-cli install run my-server --image-id 112 --hostname myserver

# Manage SSH keys
netcup-cli key list
netcup-cli key add "My Key" --pubkey "ssh-ed25519 AAAA..."

# Snapshots
netcup-cli snap list my-server
netcup-cli snap create my-server --name before-update

# rDNS
netcup-cli rdns set 1.2.3.4 mail.example.com
netcup-cli rdns set6 2a01:4f8::/29 ipv6.example.com
```

## Man pages

Man pages are generated automatically on first run and regenerated on version upgrades:

```bash
man netcup-cli
man netcup-cli-power
man netcup-cli-vnc
# etc.
```

## Authentication

`netcup-cli login` uses the **OAuth2 Device Code Flow** — no password is stored locally. A refresh token is saved at `~/.config/netcup-cli/credentials.json` (mode 0600).

## Security

- Credentials file created with mode `0600` atomically (no race window)
- Config directory created with mode `0700`
- All user-supplied path segments, IP addresses, and MAC addresses are validated before being interpolated into API URLs
- VNC temp HTML file created with mode `0600` and deleted on exit
- JWT decode errors are caught and reported cleanly

## License

[PolyForm Noncommercial License 1.0.0](LICENSE) — free to use, share, and modify for any non-commercial purpose. Commercial use is not permitted.
