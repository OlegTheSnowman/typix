import config
import json
import logging

from .sound_loader import CATEGORIES

log = logging.getLogger(__name__)

SECTION = "typix"
OLD_SECTION = "typing_soundscape"

CONF_SPEC = {
    "enabled": "boolean(default=True)",
    "panning_enabled": "boolean(default=True)",
    "panning_width": "float(default=0.8, min=0.0, max=1.0)",
    "master_volume": "float(default=1.0, min=0.0, max=1.0)",
    "current_pack": "string(default='default')",
    "debounce_ms": "integer(default=30, min=0, max=200)",
    "spellcheck_enabled": "boolean(default=False)",
    # Global character rules: JSON dict of char -> {"pan": float, "pitch": float}
    "global_char_rules_json": "string(default='{}')",
    # Text replacements: JSON dict of shortcut word -> replacement text
    "expansions_json": "string(default='{}')",
    "capitalize_sentences": "boolean(default=False)",
    "capitalize_i": "boolean(default=False)",
    # Legacy key, read once by _migrate_panning_to_rules
    "global_char_panning_json": "string(default='{}')",
    "categories": {cat: "boolean(default=True)" for cat in CATEGORIES},
}


def init_config():
    config.conf.spec[SECTION] = CONF_SPEC
    _migrate_section_name()
    _migrate_panning_to_rules()


def _migrate_section_name():
    """Moves settings out of the section used before the rename to Typix.

    Copies only when the base profile has no Typix section yet, so a
    leftover old section can never overwrite current settings. The old
    section is then dropped from each loaded profile file: ConfigManager
    has no __delitem__, and before this the failed delete left the old
    section in place, so its values were copied back on every start."""
    try:
        if OLD_SECTION not in config.conf:
            return
        if SECTION not in config.conf.profiles[0]:
            old = config.conf[OLD_SECTION]
            new = config.conf[SECTION]
            for key in CONF_SPEC:
                if key != "categories" and key in old:
                    new[key] = old[key]
            if "categories" in old:
                for cat in CATEGORIES:
                    if cat in old["categories"]:
                        new["categories"][cat] = old["categories"][cat]
            log.info("Typix: migrated settings from the typing_soundscape section")
        for profile in config.conf.profiles:
            profile.pop(OLD_SECTION, None)
    except Exception:
        log.exception("Typix: settings migration from typing_soundscape failed")


def _migrate_panning_to_rules():
    """Converts the old {char: pan} map into {char: {"pan": pan}} rules."""
    try:
        section = config.conf[SECTION]
        old = json.loads(section["global_char_panning_json"] or "{}")
        if old and get_json("global_char_rules_json") == {}:
            set_json("global_char_rules_json", {char: {"pan": float(pan)} for char, pan in old.items()})
            section["global_char_panning_json"] = "{}"
            log.info("Typix: migrated global_char_panning_json to global_char_rules_json")
    except Exception:
        log.exception("Typix: character panning migration failed")


def get_config(key):
    try:
        return config.conf[SECTION][key]
    except KeyError:
        return None


def set_config(key, value):
    config.conf[SECTION][key] = value


def get_json(key):
    """A dict stored as a JSON string setting: global_char_rules_json
    (char -> {"pan", "pitch"}) or expansions_json (shortcut -> text).
    Stored in NVDA's config, so each NVDA profile can have its own."""
    try:
        value = json.loads(get_config(key) or "{}")
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def set_json(key, value):
    set_config(key, json.dumps(value, ensure_ascii=False))
