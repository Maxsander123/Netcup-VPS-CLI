#!/usr/bin/env bash
# Install netcup-cli on Ubuntu/Debian
set -e

INSTALL_DIR="/usr/local/lib/netcup-cli"
BIN_LINK="/usr/local/bin/netcup-cli"

# Require root
if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo bash install.sh"
    exit 1
fi

# Dependencies
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv

# Install into a dedicated venv
python3 -m venv "$INSTALL_DIR"
"$INSTALL_DIR/bin/pip" install --quiet -r requirements.txt

# Copy script
cp netcup-cli.py "$INSTALL_DIR/netcup-cli.py"
chmod +x "$INSTALL_DIR/netcup-cli.py"

# Create wrapper
cat > "$BIN_LINK" <<'EOF'
#!/usr/bin/env bash
exec /usr/local/lib/netcup-cli/bin/python /usr/local/lib/netcup-cli/netcup-cli.py "$@"
EOF
chmod +x "$BIN_LINK"

echo "Installed. Run: netcup-cli --help"
