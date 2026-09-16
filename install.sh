#!/usr/bin/env bash
# Install netcup-cli on Ubuntu/Debian (no root required)
set -e

INSTALL_DIR="$HOME/.local/lib/netcup-cli"
BIN_LINK="$HOME/.local/bin/netcup-cli"
MAN_DIR="$HOME/.local/share/man/man1"

if ! command -v python3 &>/dev/null; then
    echo "python3 not found. Install with: sudo apt-get install python3 python3-venv"
    exit 1
fi

echo "Installing netcup-cli..."

python3 -m venv "$INSTALL_DIR"
"$INSTALL_DIR/bin/pip" install --quiet -r requirements.txt click-man

cp netcup-cli.py "$INSTALL_DIR/netcup-cli.py"

mkdir -p "$HOME/.local/bin"
cat > "$BIN_LINK" <<EOF
#!/usr/bin/env bash
exec "$INSTALL_DIR/bin/python" "$INSTALL_DIR/netcup-cli.py" "\$@"
EOF
chmod +x "$BIN_LINK"

mkdir -p "$MAN_DIR"
"$INSTALL_DIR/bin/python" "$INSTALL_DIR/netcup-cli.py" gen-manpages --dir "$MAN_DIR" 2>/dev/null || true

for RC in ~/.bashrc ~/.zshrc; do
    [ -f "$RC" ] || continue
    grep -q 'HOME/.local/bin' "$RC" 2>/dev/null || \
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$RC"
    grep -q 'HOME/.local/share/man' "$RC" 2>/dev/null || \
        echo 'export MANPATH="$HOME/.local/share/man:$MANPATH"' >> "$RC"
done

echo ""
echo "Done! Run:"
echo "  source ~/.bashrc"
echo "  netcup-cli login"
echo "  netcup-cli --help"
echo "  man netcup-cli"
