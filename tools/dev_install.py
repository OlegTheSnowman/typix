"""Turn the installed Typix into a developer copy that reloads itself.

Run once:  python tools/dev_install.py
Then press NVDA+Ctrl+F3 (reload plugins) or restart NVDA once. From then on,
saving any .py file under globalPlugins/typix copies it into the installed
add-on and reloads it, and NVDA says "Typix reloaded".

Undo:      python tools/dev_install.py --remove
Installing a normal Typix package also removes it, because NVDA replaces the
whole add-on folder.

This only touches the add-on in this user's NVDA configuration
(%APPDATA%/nvda/addons/typix). It never changes the repository or anything
build.bat packs, so a store package can't carry the reloader. It copies .py
files only: sounds, libraries and translations stay as installed.
"""

import os
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(REPO, "globalPlugins", "typix")
ADDON = os.path.join(os.environ["APPDATA"], "nvda", "addons", "typix")
PLUGINS = os.path.join(ADDON, "globalPlugins")
RELOADER = os.path.join(PLUGINS, "typixDevReload.py")


def main():
    if not os.path.isdir(os.path.join(PLUGINS, "typix")):
        sys.exit("Typix is not installed in %s. Install it in NVDA first." % ADDON)
    if "--remove" in sys.argv:
        if os.path.exists(RELOADER):
            os.remove(RELOADER)
        print("Removed the reloader. Restart NVDA or press NVDA+Ctrl+F3.")
        return
    copied = 0
    for root, dirs, names in os.walk(SOURCE):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in names:
            if name.endswith(".py"):
                src = os.path.join(root, name)
                dest = os.path.join(PLUGINS, "typix", os.path.relpath(src, SOURCE))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(src, dest)
                copied += 1
    with open(os.path.join(REPO, "tools", "dev_reload_plugin.py"), encoding="utf-8") as f:
        text = f.read().replace("__SOURCE__", SOURCE)
    with open(RELOADER, "w", encoding="utf-8") as f:
        f.write(text)
    print("Copied %d files and installed the reloader in %s." % (copied, ADDON))
    print("Press NVDA+Ctrl+F3 once to start it.")


if __name__ == "__main__":
    main()
