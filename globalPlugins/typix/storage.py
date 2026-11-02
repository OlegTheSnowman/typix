"""The one way Typix reads and writes its JSON files: pack manifests, the
custom dictionary, the bundled-pack marker and typing statistics."""

import json
import logging
import os

log = logging.getLogger(__name__)


def load_json(path, default=None):
    """The file's data, or default when it is missing or unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        log.error(f"Typix: could not read {path}: {e}")
        return default


def save_json(path, data):
    """Writes atomically, so a crash mid-write can't leave a broken file.
    Returns whether it worked."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except OSError as e:
        log.error(f"Typix: could not write {path}: {e}")
        return False
