# Typix developer reload. Never shipped: tools/dev_install.py copies this
# file into the installed add-on on the developer's own machine only, and
# build.bat never packs anything from tools/.
#
# Every second it looks at the .py files in the source folder below. When
# one changes and has stopped changing, it checks that every changed file
# compiles, copies them into the installed add-on and reloads NVDA's global
# plugins, so a change is live without restarting NVDA. A file with a
# syntax error is not copied; NVDA says where the error is instead.
#
# It can't help when Typix itself fails to import after a reload for a
# reason other than syntax: look in the NVDA log, fix it, save again.

import os
import shutil

import core
import globalPluginHandler
import ui
import wx
from logHandler import log

SOURCE = r"__SOURCE__"
TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "typix")
INTERVAL_MS = 1000


def _snapshot():
    """Relative path -> (mtime, size) of every .py file in SOURCE."""
    files = {}
    for root, dirs, names in os.walk(SOURCE):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in names:
            if name.endswith(".py"):
                path = os.path.join(root, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                files[os.path.relpath(path, SOURCE)] = (st.st_mtime_ns, st.st_size)
    return files


class _Timer(wx.Timer):
    def __init__(self, callback):
        super().__init__()
        self._callback = callback

    def Notify(self):
        self._callback()


def _reload():
    globalPluginHandler.reloadGlobalPlugins()
    log.info("Typix dev: reloaded global plugins")
    ui.message("Typix reloaded")


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    def __init__(self):
        super().__init__()
        self._known = _snapshot()
        self._pending = None
        self._failed = None
        self._timer = _Timer(self._tick)
        self._timer.Start(INTERVAL_MS)
        log.info("Typix dev: watching %s", SOURCE)

    def terminate(self):
        self._timer.Stop()
        super().terminate()

    def _tick(self):
        try:
            self._check()
        except Exception:
            log.exception("Typix dev: watcher failed")

    def _check(self):
        now = _snapshot()
        if now == self._known or now == self._failed:
            self._pending = None
            return
        if now != self._pending:
            # Still being written, or just noticed: wait one more tick.
            self._pending = now
            return
        self._pending = None
        changed = [p for p, stamp in now.items() if self._known.get(p) != stamp]
        for rel in changed:
            path = os.path.join(SOURCE, rel)
            try:
                with open(path, "rb") as f:
                    compile(f.read(), path, "exec")
            except SyntaxError as e:
                log.error("Typix dev: syntax error, not reloading", exc_info=True)
                ui.message("Typix not reloaded: syntax error in %s line %s" % (rel, e.lineno))
                # Wait for the next save before trying again. Nothing is
                # copied, so the files that did compile go with that one.
                self._failed = now
                return
        for rel in changed:
            dest = os.path.join(TARGET, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(os.path.join(SOURCE, rel), dest)
        log.info("Typix dev: copied %s", ", ".join(changed))
        self._known = now
        self._timer.Stop()
        core.callLater(0, _reload)
