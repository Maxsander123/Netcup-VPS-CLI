#!/usr/bin/env bash
# Build a .deb package for netcup-cli
set -e

VERSION="$(grep -m1 'VERSION = ' netcup-cli.py | cut -d'"' -f2)"
PKG="netcup-cli_${VERSION}_all"
ROOT="$(pwd)/deb-build/${PKG}"

echo "Building netcup-cli v${VERSION} ..."

rm -rf deb-build
mkdir -p "${ROOT}/DEBIAN"
mkdir -p "${ROOT}/usr/bin"
mkdir -p "${ROOT}/usr/share/netcup-cli"
mkdir -p "${ROOT}/usr/share/man/man1"

# Control file
cat > "${ROOT}/DEBIAN/control" <<EOF
Package: netcup-cli
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.8), python3-venv, python3-pip
Maintainer: Maxsander123 <https://github.com/Maxsander123>
Homepage: https://github.com/Maxsander123/Netcup-VPS-CLI
Description: CLI tool to manage Netcup VPS via the SCP REST API
 Supports power controls, networking, OS reinstallation, VNC console,
 SSH key management, snapshots, backups, and more.
EOF

# postinst: create venv and install Python deps
cat > "${ROOT}/DEBIAN/postinst" <<'EOF'
#!/bin/bash
set -e
python3 -m venv /usr/lib/netcup-cli
/usr/lib/netcup-cli/bin/pip install --quiet "click>=8.1" "requests>=2.31" "rich>=13.0" "click-man>=0.1" 2>&1 | tail -3
EOF
chmod 0755 "${ROOT}/DEBIAN/postinst"

# prerm: remove venv
cat > "${ROOT}/DEBIAN/prerm" <<'EOF'
#!/bin/bash
set -e
rm -rf /usr/lib/netcup-cli
EOF
chmod 0755 "${ROOT}/DEBIAN/prerm"

# Script
cp netcup-cli.py "${ROOT}/usr/share/netcup-cli/netcup-cli.py"

# Wrapper
cat > "${ROOT}/usr/bin/netcup-cli" <<'EOF'
#!/bin/bash
exec /usr/lib/netcup-cli/bin/python /usr/share/netcup-cli/netcup-cli.py "$@"
EOF
chmod 0755 "${ROOT}/usr/bin/netcup-cli"

dpkg-deb --build --root-owner-group "${ROOT}"
echo ""
echo "Built: deb-build/${PKG}.deb"
