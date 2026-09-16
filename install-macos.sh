#!/usr/bin/env bash
# Install netcup-cli on macOS (no root required)
set -e

INSTALL_DIR="$HOME/.local/lib/netcup-cli"
BIN_DIR="$HOME/.local/bin"
MAN_DIR="$HOME/.local/share/man/man1"
REPO="Maxsander123/Netcup-VPS-CLI"

if ! command -v python3 &>/dev/null; then
    echo "python3 not found. Install with: brew install python3"
    exit 1
fi

if ! python3 -c "import sys; assert sys.version_info >= (3,8)" 2>/dev/null; then
    echo "Python 3.8+ required. Current: $(python3 --version)"
    exit 1
fi

echo "Installing netcup-cli..."

# Download latest netcup-cli.py from GitHub release
SCRIPT_URL="https://github.com/${REPO}/releases/latest/download/netcup-cli.py"
curl -fsSL "$SCRIPT_URL" -o /tmp/netcup-cli-download.py

python3 -m venv "$INSTALL_DIR"
"$INSTALL_DIR/bin/pip" install --quiet "click>=8.1" "requests>=2.31" "rich>=13.0" "click-man>=0.1"

cp /tmp/netcup-cli-download.py "$INSTALL_DIR/netcup-cli.py"
rm /tmp/netcup-cli-download.py

mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/netcup-cli" <<EOF
#!/usr/bin/env bash
exec "$INSTALL_DIR/bin/python" "$INSTALL_DIR/netcup-cli.py" "\$@"
EOF
chmod +x "$BIN_DIR/netcup-cli"

mkdir -p "$MAN_DIR"
"$INSTALL_DIR/bin/python" "$INSTALL_DIR/netcup-cli.py" gen-manpages --dir "$MAN_DIR" 2>/dev/null || true

# Add to PATH if needed
for RC in ~/.zshrc ~/.bash_profile ~/.bashrc; do
    [ -f "$RC" ] || continue
    grep -q 'HOME/.local/bin' "$RC" 2>/dev/null || \
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$RC"
    grep -q 'HOME/.local/share/man' "$RC" 2>/dev/null || \
        echo 'export MANPATH="$HOME/.local/share/man:$MANPATH"' >> "$RC"
done

echo ""
echo "Done! Run:"
echo "  source ~/.zshrc   # or ~/.bash_profile"
echo "  netcup-cli login"
echo "  netcup-cli --help"
