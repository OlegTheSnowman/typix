"""Makes the add-on importable as the package 'typix' without running its
__init__.py, which needs NVDA. Test files import this first."""
import builtins
import os
import sys
import types

ADDON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "globalPlugins", "typix")
if "typix" not in sys.modules:
    package = types.ModuleType("typix")
    package.__path__ = [ADDON]
    sys.modules["typix"] = package
sys.path.insert(0, os.path.join(ADDON, "libs"))
# NVDA installs these at runtime.
builtins.__dict__.setdefault("_", lambda s: s)
builtins.__dict__.setdefault("ngettext", lambda a, b, n: a if n == 1 else b)
