"""Auto-increment the version in manifest.ini.
Reads version = X.Y.Z, increments Z (0-9), rolls to Y+1.0 at 9, etc.
Usage: python tools/bump_version.py"""

import re
import sys
import os


def bump(version_str):
    parts = version_str.strip().split('.')
    if len(parts) != 3:
        print(f"Error: expected X.Y.Z format, got '{version_str}'")
        sys.exit(1)
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    patch += 1
    if patch > 9:
        patch = 0
        minor += 1
    if minor > 9:
        minor = 0
        major += 1
    return f"{major}.{minor}.{patch}"


def main():
    manifest = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'manifest.ini')
    if not os.path.isfile(manifest):
        print("Error: manifest.ini not found")
        sys.exit(1)
    with open(manifest, 'r', encoding='utf-8') as f:
        content = f.read()
    m = re.search(r'^version\s*=\s*(.+)$', content, re.MULTILINE)
    if not m:
        print("Error: no version line found in manifest.ini")
        sys.exit(1)
    old = m.group(1).strip()
    new = bump(old)
    content = content.replace(f"version = {old}", f"version = {new}")
    with open(manifest, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"{old} -> {new}")


if __name__ == '__main__':
    main()
