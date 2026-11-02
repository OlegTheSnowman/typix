"""Translation workflow in the shape of NVDA's add-on template.

    python tools/i18n.py update
        Extracts every translatable string, with its "Translators:" comment,
        into locale/typix.pot, then merges the template into each
        locale/<lang>/LC_MESSAGES/nvda.po. Run it after changing UI text,
        then hand the .po files to translators.

    python tools/i18n.py build <stage_dir>
        Called by build.bat. Compiles each nvda.po to nvda.mo and writes
        locale/<lang>/manifest.ini with the translated summary and
        description, both into the build stage. Translators only ever
        edit nvda.po.

Needs Babel (pip install babel); translators need nothing but Poedit or
any .po editor.
"""

import glob
import os
import sys
from datetime import datetime

from babel.messages.catalog import Catalog
from babel.messages.extract import extract_from_file
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po, write_po

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALE = os.path.join(ROOT, "locale")
POT = os.path.join(LOCALE, "typix.pot")
SOURCES = os.path.join(ROOT, "globalPlugins", "typix", "*.py")
KEYWORDS = {
    "_": None,
    "ngettext": (1, 2),
    "pgettext": ((1, "c"), 2),
    "npgettext": ((1, "c"), 2, 3),
}
# Manifest fields translators see in the .po, as NVDA's template does.
MANIFEST_FIELDS = {
    "summary": "Summary/title for this add-on, shown on installation and in the Add-on Store",
    "description": "Long description for this add-on, shown on installation and in the Add-on Store",
}


def read_manifest():
    """The English manifest.ini as a dict. It has no section header and
    uses triple quotes, so it is parsed by hand like NVDA does."""
    text = open(os.path.join(ROOT, "manifest.ini"), encoding="utf-8").read()
    values, key, buf = {}, None, None
    for line in text.splitlines():
        if buf is not None:
            if line.endswith('"""'):
                buf.append(line[:-3])
                values[key] = "\n".join(buf)
                buf = None
            else:
                buf.append(line)
            continue
        if "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if value.startswith('"""'):
            value = value[3:]
            if value.endswith('"""') and len(value) >= 3:
                values[key] = value[:-3]
            else:
                buf = [value]
        else:
            values[key] = value.strip('"')
    return values


def translated(message):
    """True when a message has a usable, non-fuzzy translation. Plural
    messages hold a tuple of strings, all of which must be filled."""
    if message.fuzzy:
        return False
    strings = message.string if isinstance(message.string, tuple) else (message.string,)
    return all(strings)


def po_files():
    return sorted(glob.glob(os.path.join(LOCALE, "*", "LC_MESSAGES", "nvda.po")))


def update():
    manifest = read_manifest()
    pot = Catalog(
        project="'typix'", version=f"'{manifest['version']}'",
        msgid_bugs_address="'nvda-translations@groups.io'",
        copyright_holder="OlegTheSnowman", creation_date=datetime.now(),
        fuzzy=False,
    )
    for path in sorted(glob.glob(SOURCES)):
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        for lineno, message, comments, context in extract_from_file(
            "python", path, KEYWORDS, comment_tags=("Translators:",), strip_comment_tags=False,
        ):
            pot.add(message, locations=[(rel, lineno)], auto_comments=comments, context=context)
    for field, note in MANIFEST_FIELDS.items():
        pot.add(manifest[field], locations=[("manifest.ini", 0)], auto_comments=[f"Translators: {note}"])
    with open(POT, "wb") as f:
        write_po(f, pot, width=0, omit_header=False, include_lineno=True)
    print(f"{POT}: {len(pot)} messages")

    for po_path in po_files():
        with open(po_path, "rb") as f:
            catalog = read_po(f)
        catalog.update(pot, update_header_comment=False)
        catalog.version = pot.version
        with open(po_path, "wb") as f:
            write_po(f, catalog, width=0, include_previous=True)
        done = sum(1 for m in catalog if m.id and translated(m))
        fuzzy = sum(1 for m in catalog if m.id and m.fuzzy)
        print(f"{po_path}: {done} of {len(catalog)} translated, {fuzzy} fuzzy")


def build(stage):
    manifest = read_manifest()
    for po_path in po_files():
        lang = os.path.basename(os.path.dirname(os.path.dirname(po_path)))
        with open(po_path, "rb") as f:
            catalog = read_po(f, locale=lang)
        out_dir = os.path.join(stage, "locale", lang)
        os.makedirs(os.path.join(out_dir, "LC_MESSAGES"), exist_ok=True)
        # Leave out anything not fully translated, so NVDA shows the English
        # instead; Babel would otherwise fill a missing plural form with
        # the singular, giving "3 sound".
        for message in list(catalog):
            if message.id and not translated(message):
                catalog.delete(message.id, context=message.context)
        with open(os.path.join(out_dir, "LC_MESSAGES", "nvda.mo"), "wb") as f:
            write_mo(f, catalog)
        lines = []
        for field in MANIFEST_FIELDS:
            message = catalog.get(manifest[field])
            text = message.string if message and translated(message) else manifest[field]
            lines.append(f'{field} = """{text}"""' if field == "description" else f'{field} = "{text}"')
        with open(os.path.join(out_dir, "manifest.ini"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        print(f"built locale/{lang}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "update":
        update()
    elif len(sys.argv) == 3 and sys.argv[1] == "build":
        build(sys.argv[2])
    else:
        sys.exit(__doc__)
