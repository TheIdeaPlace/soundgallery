"""Validate, build and sign The Idea Place sound gallery. Each app has its own events, so each has its
own folder of schemes, <app>/schemes/, published at /soundgallery/<app>/. The apps, their events and
their built-in sounds come from app-kit (sounds/apps.json), as do the rules for a scheme file
(sounds/SCHEMES.md), so the gallery and the apps can't drift apart.

    python tools/gallery.py validate            check every scheme in every app's schemes/ folder
    python tools/gallery.py build --out site    validate, then write site/<app>/<id>-<hash><extension>,
                                                site/<app>/index.json and site/<app>/index.html for each
                                                app, and site/index.html listing the apps
    python tools/gallery.py sign --out site     sign each site/<app>/index.json into index.signed, with the key
                                                in GALLERY_SIGNING_KEY (CI signs with openssl instead, the same way)
    python tools/gallery.py check-paths BASE HEAD
                                                fail unless every file changed between the two commits is in a
                                                scheme folder: what a proposed scheme may change

This is the first gate, not the only one. It adds the gallery's own rules (SCHEMES.md, "The gallery's own
rules") to what every app checks on import, so a scheme that passes here imports cleanly in its app. Each
built file is read back with app-kit's reader before it's listed. The apps check everything again, and
check the signature and the hash.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

from theideaplace_appkit import soundschemes
from theideaplace_appkit.soundschemes import (SILENT, AppCatalog, SchemeFile, SchemeFileProblem, custom_name_ok,
                                              fold, load_json, parse_ref, utf16_length, wav_problem)

ROOT = Path(__file__).resolve().parent.parent
# Every app app-kit knows, by id, which is also its folder and its address: <id>/schemes/, /soundgallery/<id>/.
APPS: dict[str, AppCatalog] = soundschemes.apps()

# Where in each app a person browses the gallery, and imports a file downloaded by hand, for its page.
BROWSE = {"quickmail": "Settings, Sounds, Browse schemes", "thechatplace": "File, Sounds, Browse Gallery"}
IMPORT = {"quickmail": "Import scheme", "thechatplace": "Import Scheme"}

LICENSES = {"CC0-1.0", "CC-BY-4.0"}
# Always used with fullmatch: with match, $ would let "rain\n" through.
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
MAX_NAME = soundschemes.MAX_NAME
MAX_AUTHOR = soundschemes.MAX_AUTHOR
MAX_LICENSE = soundschemes.MAX_LICENSE
MAX_DESCRIPTION = soundschemes.MAX_DESCRIPTION
MAX_SOUND_BYTES = soundschemes.MAX_SOUND_BYTES
MAX_JSON_BYTES = soundschemes.MAX_JSON_BYTES
MAX_SCHEME_BYTES = soundschemes.MAX_FILE_BYTES
# A scheme file's sounds have to leave room for scheme.json, or app-kit's writer leaves some out.
MAX_SOUNDS_BYTES = MAX_SCHEME_BYTES - MAX_JSON_BYTES
MAX_INDEX_BYTES = 256 * 1024   # what every app reads (SCHEMES.md, index.signed)
MAX_SCHEMES = 200              # every app reads only the first 200
# The fields a gallery scheme.json may have. format and app are allowed so the scheme.json from an exported
# scheme file can be used as it is, but they have to agree with the folder it's in.
FIELDS = {"format", "app", "name", "author", "description", "license", "events"}


@dataclass
class Scheme:
    id: str
    folder: Path
    app: AppCatalog
    name: str = ""
    author: str = ""
    description: str = ""
    license: str = ""
    events: dict[str, str] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)


def has_control(text: str) -> bool:
    """Control characters (category Cc), or an unpaired surrogate, which no app would keep."""
    return any(ord(c) < 32 or 127 <= ord(c) < 160 or 0xD800 <= ord(c) <= 0xDFFF for c in text)


def check_value(value: object, app: AppCatalog) -> tuple[str | None, str | None]:
    """(custom file or None, problem or None) for one event's stored sound. app-kit's parse_ref decides;
    the rest only says why, in words a contributor can act on."""
    if not isinstance(value, str):
        return None, "is not text"
    ref = parse_ref(value, app)
    if ref is not None:
        return (ref.name if ref.kind == "custom" else None), None
    lower = value.lower()
    if lower.startswith("builtin:"):
        pack, slash, name = value[len("builtin:"):].rpartition("/")
        if slash and pack.lower() not in soundschemes.PACKS:
            return None, f"names an unknown pack '{pack}'"
        return None, f"names an unknown built-in sound '{name}'; {app.name}'s are {', '.join(app.built_in_sounds)}"
    if lower.startswith("custom:"):
        file = value[len("custom:"):]
        if file.lower().endswith(".mp3") and custom_name_ok(file, allow_mp3=True):
            return None, f"names an MP3 file '{file}'; the gallery takes 16-bit PCM WAV files only"
        return None, f"names an unsafe or unsupported file '{file}'"
    return None, f"is not none, builtin:<sound>, builtin:<pack>/<sound> or custom:<file> ('{value}')"


def load_scheme(folder: Path, app: AppCatalog, problems: list[str]) -> Scheme | None:
    sid = folder.name
    where = f"{app.id}/schemes/{sid}"
    if not ID_RE.fullmatch(sid):
        problems.append(f"{where}: the folder name must be lowercase letters, digits and dashes, up to 40 characters")
        return None
    scheme = Scheme(id=sid, folder=folder, app=app)

    # Before anything is read: a link could point anywhere on the machine building the gallery, and is
    # easy to miss in review.
    if folder.is_symlink() or any(p.is_symlink() for p in folder.rglob("*")):
        problems.append(f"{where}: contains a link; commit the files themselves")
        return None

    allowed = {"scheme.json", "sounds", "README.md"}
    for entry in folder.iterdir():
        if entry.name not in allowed:
            problems.append(f"{where}: unexpected '{entry.name}'; a scheme holds scheme.json, sounds/ and optionally README.md")

    meta_path = folder / "scheme.json"
    if not meta_path.is_file():
        problems.append(f"{where}: no scheme.json")
        return None
    if meta_path.stat().st_size > MAX_JSON_BYTES:
        problems.append(f"{where}/scheme.json: larger than 64 KB")
        return None
    try:
        meta = load_json(meta_path.read_bytes())   # strict JSON, as every app reads it
    except (UnicodeDecodeError, ValueError) as ex:
        problems.append(f"{where}/scheme.json: not valid UTF-8 JSON ({ex})")
        return None
    if not isinstance(meta, dict):
        problems.append(f"{where}/scheme.json: must be a JSON object")
        return None

    def text(key: str, limit: int, required: bool = True) -> str:
        value = meta.get(key, "")
        if not isinstance(value, str):
            problems.append(f"{where}/scheme.json: '{key}' must be text")
            return ""
        value = value.strip()
        if required and not value:
            problems.append(f"{where}/scheme.json: '{key}' is required")
        if utf16_length(value) > limit:
            problems.append(f"{where}/scheme.json: '{key}' is longer than {limit} characters")
        if has_control(value):
            problems.append(f"{where}/scheme.json: '{key}' must be one line with no control characters")
        return value

    scheme.name = text("name", MAX_NAME)
    scheme.author = text("author", MAX_AUTHOR)
    scheme.description = text("description", MAX_DESCRIPTION, required=False)
    scheme.license = text("license", MAX_LICENSE)
    if scheme.license and scheme.license not in LICENSES:
        problems.append(f"{where}/scheme.json: license must be one of {', '.join(sorted(LICENSES))}")

    unknown = set(meta) - FIELDS
    if unknown:
        problems.append(f"{where}/scheme.json: unknown fields {', '.join(sorted(unknown))}")
    if "format" in meta and (type(meta["format"]) is not int or meta["format"] != soundschemes.FORMAT):
        problems.append(f"{where}/scheme.json: 'format' must be {soundschemes.FORMAT}, or left out")
    # As every app reads it: no app, or null, means QuickMail, since QuickMail 0.8.69 wrote none.
    owner = "quickmail" if meta.get("app") is None else meta["app"]
    if "app" in meta and owner != app.id:
        problems.append(f"{where}/scheme.json: 'app' says it's for {soundschemes.app_name(owner)}, but this "
                        f"is {app.name}'s folder; move the scheme to its app's folder, or leave 'app' out")

    events = meta.get("events")
    if not isinstance(events, dict):
        problems.append(f"{where}/scheme.json: 'events' must be an object of event to sound")
        return None
    for key, value in events.items():
        if key not in app.keys:
            problems.append(f"{where}/scheme.json: unknown event '{key}'; {app.name}'s events are {', '.join(app.keys)}")
            continue
        _, problem = check_value(value, app)
        if problem:
            problems.append(f"{where}/scheme.json: {key} {problem}")
            continue
        scheme.events[key] = value
    scheme.files = soundschemes.files_used(app, scheme.events)   # each once, in the app's event order
    if all(parse_ref(v, app) == SILENT for v in scheme.events.values()):
        problems.append(f"{where}/scheme.json: no event has a sound")

    sounds = folder / "sounds"
    folded: dict[str, str] = {}
    for name in scheme.files:
        other = folded.setdefault(fold(name), name)
        if other != name:
            problems.append(f"{where}: '{name}' and '{other}' differ only in case; Windows treats them as one file")
    if sounds.exists() and not sounds.is_dir():
        problems.append(f"{where}: 'sounds' must be a folder")
    present = sorted(p.name for p in sounds.iterdir()) if sounds.is_dir() else []
    for name in present:
        path = sounds / name
        if not path.is_file():
            problems.append(f"{where}/sounds: '{name}' is not a file")
        elif name not in scheme.files:
            problems.append(f"{where}/sounds/{name}: no event uses it; remove it")
    total = 0
    for name in scheme.files:
        path = sounds / name
        if not path.is_file():
            problems.append(f"{where}: an event uses sounds/{name}, which isn't there")
            continue
        size = path.stat().st_size
        total += size
        if size > MAX_SOUND_BYTES:
            problems.append(f"{where}/sounds/{name}: larger than 5 MB")
            continue
        problem = wav_problem(path.read_bytes())
        if problem:
            problems.append(f"{where}/sounds/{name}: {problem} The gallery takes 16-bit PCM WAV files, mono or "
                            "stereo, up to 30 seconds and 5 MB.")
    if total > MAX_SOUNDS_BYTES:
        problems.append(f"{where}: its sounds add up to more than 80 MB, less 64 KB for scheme.json")
    return scheme


def validate(schemes_dir: Path, app: AppCatalog) -> tuple[list[Scheme], list[str]]:
    """One app's schemes, and every problem with them."""
    problems: list[str] = []
    schemes: list[Scheme] = []
    if not schemes_dir.is_dir():
        return schemes, problems
    folders = sorted(p for p in schemes_dir.iterdir() if p.is_dir())
    if len(folders) > MAX_SCHEMES:
        problems.append(f"{app.id}/schemes: more than {MAX_SCHEMES} schemes; {app.name} reads only the first {MAX_SCHEMES}")
    names: dict[str, str] = {}
    for folder in folders:
        before = len(problems)
        scheme = load_scheme(folder, app, problems)
        if scheme is None or len(problems) > before:
            continue
        key = fold(scheme.name)
        if key in names:
            problems.append(f"{app.id}/schemes/{scheme.id}: the name '{scheme.name}' is already used by "
                            f"{app.id}/schemes/{names[key]}")
            continue
        names[key] = scheme.id
        schemes.append(scheme)
    for f in schemes_dir.iterdir():
        if f.is_file() and f.name != "README.md":
            problems.append(f"{app.id}/schemes/{f.name}: only scheme folders belong here")
    return schemes, problems


def validate_all(root: Path = ROOT) -> tuple[dict[str, list[Scheme]], list[str]]:
    """Every app's schemes, by app id, and every problem with them."""
    problems: list[str] = []
    found: dict[str, list[Scheme]] = {}
    for app_id, app in APPS.items():
        found[app_id], app_problems = validate(root / app_id / "schemes", app)
        problems += app_problems
    # A schemes/ folder for an app app-kit doesn't know would never be published: say so, rather than
    # quietly leaving it out.
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and p.name not in APPS):
        if (folder / "schemes").exists():
            problems.append(f"{folder.name}/schemes: '{folder.name}' isn't an app the gallery knows; "
                            f"the apps are {', '.join(APPS)}")
    return found, problems


def build_file(scheme: Scheme) -> bytes:
    """The scheme file its app imports, written by app-kit, so it carries `app` and `description` and is the
    same bytes every time."""
    sounds = {name: (scheme.folder / "sounds" / name).read_bytes() for name in scheme.files}
    body = SchemeFile(scheme.app.id, scheme.name, dict(scheme.events), author=scheme.author,
                      license=scheme.license, description=scheme.description or None, sounds=sounds)
    return soundschemes.write_scheme_file(body, scheme.app, built_in=False, scheme_pack=None)


def read_back_problem(scheme: Scheme, data: bytes) -> str | None:
    """Why the built file wouldn't import whole in its app, or None. The last check before it's listed:
    whatever passed validation has to come back with its name and every sound."""
    try:
        back = soundschemes.read_scheme_file(data, scheme.app)
    except SchemeFileProblem as ex:
        return str(ex)
    if back.dropped or back.name != scheme.name or set(back.sounds) != set(scheme.files):
        return "its name or a sound didn't come back"
    return None


def index_problem(app: AppCatalog, index: bytes, entries: list[dict]) -> str | None:
    """Why the apps wouldn't list every entry in this index, or None. Signed with a throwaway key and read
    with app-kit's reader, as an app reads the real one, so an entry an app would quietly leave out fails
    the build instead."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from theideaplace_appkit import soundgallery
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    signed = signed_index(key.sign(index, ec.ECDSA(hashes.SHA256())), index)
    try:
        listed = soundgallery.read_index(signed, app, public)
    except soundgallery.GalleryProblem as ex:
        return f"{app.name} wouldn't read it ({ex.__class__.__name__}: {getattr(ex, 'detail', '') or ex})"
    missing = sorted({e["id"] for e in entries} - {e.id for e in listed})
    if missing:
        return f"{app.name} would leave out {', '.join(missing)}"
    return None


def build_app(app: AppCatalog, schemes: list[Scheme]) -> tuple[dict[str, bytes], list[str]]:
    """Everything published in one app's folder, by file name: the scheme files, index.json and
    index.html. Its schemes have already passed validation. Returns ({}, problems) if any file fails."""
    files: dict[str, bytes] = {}
    entries = []
    for scheme in schemes:
        data = build_file(scheme)
        if len(data) > MAX_SCHEME_BYTES:
            return {}, [f"{app.id}/schemes/{scheme.id}: the built file is larger than 80 MB"]
        problem = read_back_problem(scheme, data)
        if problem:
            return {}, [f"{app.id}/schemes/{scheme.id}: the built file doesn't read back in {app.name} ({problem})"]
        sha = hashlib.sha256(data).hexdigest()
        # Named by content: a changed scheme is a new file, so no cache can pair it with an old listing.
        file = f"{scheme.id}-{sha[:16]}{app.extension}"
        files[file] = data
        entries.append({
            "id": scheme.id,
            "name": scheme.name,
            "author": scheme.author,
            "description": scheme.description,
            "license": scheme.license,
            "file": file,
            "size": len(data),
            "sha256": sha,
            "eventsWithSound": sum(1 for v in scheme.events.values() if parse_ref(v, app) != SILENT),
        })
    entries.sort(key=lambda e: fold(e["name"]))
    # `app` is what stops one app taking another's list, since one key signs them all.
    index = json.dumps({"format": 1, "app": app.id, "schemes": entries}, indent=2, ensure_ascii=False).encode("utf-8")
    if len(index) > MAX_INDEX_BYTES:
        return {}, [f"{app.id}/index.json would be {len(index)} bytes; {app.name} reads at most {MAX_INDEX_BYTES}"]
    problem = index_problem(app, index, entries)
    if problem:
        return {}, [f"{app.id}/index.json: {problem}"]
    files["index.json"] = index
    files["index.html"] = page(app, entries).encode("utf-8")
    return files, []


def build(out: Path, root: Path = ROOT) -> list[str]:
    """Validate every app, then write site/<app>/ for each, and the front page. Nothing is written unless
    every scheme of every app passes and builds."""
    found, problems = validate_all(root)
    if problems:
        return problems
    planned: dict[str, dict[str, bytes]] = {}
    for app_id, app in APPS.items():
        planned[app_id], problems = build_app(app, found[app_id])
        if problems:
            return problems
    for app_id, files in planned.items():
        folder = out / app_id
        folder.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (folder / name).write_bytes(data)
    (out / "index.html").write_text(home_page(), encoding="utf-8")
    return []


def download_name(name: str, app: AppCatalog) -> str:
    """A friendly file name for downloading a scheme by hand, such as "Rain at Night.qmsounds".

    The file itself keeps its hashed name, which the apps rely on so that no cache can pair an old file
    with a new listing; this is only the name a browser saves it under."""
    safe = "".join(c if c.isalnum() or c in " -_()" else "-" for c in name).strip(" -")
    return (safe or "Sound scheme") + app.extension


def page(app: AppCatalog, entries: list[dict]) -> str:
    """One app's gallery page."""
    rows = "\n".join(
        f'<li><a href="{escape(e["file"])}" download="{escape(download_name(e["name"], app))}">{escape(e["name"])}</a>, '
        f'by {escape(e["author"])}. {escape(e["description"])} Licence: {escape(e["license"])}.</li>'
        for e in entries) or "<li>No schemes yet.</li>"
    title = escape(f"{app.name} sound schemes")
    if app.id in BROWSE:
        how = (f"The easiest way to add one is from {escape(app.name)}: {escape(BROWSE[app.id])}. You can also\n"
               f"download a scheme here and use {escape(IMPORT[app.id])} in the same place.")
    else:
        how = f"Download a scheme here and import it in {escape(app.name)}."
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title></head>
<body><main>
<h1>{title}</h1>
<p>{how}</p>
<ul>
{rows}
</ul>
<p><a href="../">Schemes for the other apps</a>. <a href="https://github.com/TheIdeaPlace/soundgallery">Propose a scheme of your own</a>.</p>
</main></body></html>
"""


def home_page() -> str:
    """The gallery's front page: one link per app."""
    links = "\n".join(f'<li><a href="{escape(app_id)}/">{escape(app.name)}</a></li>' for app_id, app in APPS.items())
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>The Idea Place sound gallery</title></head>
<body><main>
<h1>The Idea Place sound gallery</h1>
<p>Sound schemes for The Idea Place apps. Each app has its own events, so each has its own schemes.</p>
<ul>
{links}
</ul>
<p><a href="https://github.com/TheIdeaPlace/soundgallery">Propose a scheme of your own</a>.</p>
</main></body></html>
"""


def signed_index(signature_der: bytes, index: bytes) -> bytes:
    """index.signed: the base64 signature on one line, then the exact bytes it signs. One file, so a cache
    can never hand an app one index with another's signature."""
    return base64.b64encode(signature_der) + b"\n" + index


def sign(out: Path, key_pem: bytes) -> None:
    """Writes index.signed, signed with ECDSA P-256 / SHA-256 (DER), and removes the unsigned index.json."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = serialization.load_pem_private_key(key_pem, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise SystemExit("the signing key must be an ECDSA P-256 private key")
    index = (out / "index.json").read_bytes()
    signature = key.sign(index, ec.ECDSA(hashes.SHA256()))
    (out / "index.signed").write_bytes(signed_index(signature, index))
    (out / "index.json").unlink()


def sign_all(out: Path, key_pem: bytes) -> list[str]:
    """Signs every app folder's index.json, as publish.yml's loop over site/*/index.json does. Returns the
    folders signed."""
    signed = []
    for index in sorted(out.glob("*/index.json")):
        sign(index.parent, key_pem)
        signed.append(index.parent.name)
    return signed


def scheme_path_ok(path: str) -> bool:
    """Whether a proposed scheme may change this file: <app>/schemes/README.md, or scheme.json, README.md or
    sounds/<safe file name> in <app>/schemes/<id>/, for an app the gallery knows. Anything else could change
    what runs in CI (a conftest.py that pytest loads, a tool, a workflow), so a scheme pull request can't."""
    parts = path.split("/")
    if len(parts) < 3 or parts[0] not in APPS or parts[1] != "schemes":
        return False
    if parts[2:] == ["README.md"]:
        return True
    if not ID_RE.fullmatch(parts[2]):
        return False
    rest = parts[3:]
    if rest in (["scheme.json"], ["README.md"]):
        return True
    return len(rest) == 2 and rest[0] == "sounds" and custom_name_ok(rest[1])


def changed_paths(base: str, head: str, cwd: Path = ROOT) -> list[str]:
    """Every path added, changed, deleted or renamed between two commits. --no-renames lists a rename as
    both its old and its new path, so moving a file out of a scheme folder is caught too; -z keeps odd
    names (a newline in one) exactly as they are."""
    out = subprocess.run(["git", "-c", "core.quotePath=false", "diff", "--name-only", "--no-renames", "-z", base, head],
                         cwd=cwd, check=True, capture_output=True).stdout
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def path_problems(paths: list[str]) -> list[str]:
    bad = [p for p in paths if not scheme_path_ok(p)]
    if not bad:
        return []
    return [f"A scheme pull request may only change files in a scheme folder: scheme.json, README.md and "
            f"sounds/<file>.wav in <app>/schemes/<id>/, where <app> is {' or '.join(APPS)}. It also changes:"] + \
           [f"  {p!r}" for p in bad]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    c = sub.add_parser("check-paths")
    c.add_argument("base")
    c.add_argument("head")
    b = sub.add_parser("build")
    b.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("sign")
    s.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "validate":
        found, problems = validate_all()
        for p in problems:
            print(f"::error::{p}" if os.environ.get("GITHUB_ACTIONS") else p)
        if not problems:
            print("; ".join(f"{APPS[a].name}: {len(s)} scheme(s)" for a, s in found.items()) + ". All passed.")
        return 1 if problems else 0
    if args.command == "check-paths":
        paths = changed_paths(args.base, args.head)
        problems = path_problems(paths)
        if problems:
            print(("::error::" if os.environ.get("GITHUB_ACTIONS") else "") + problems[0])
            print("\n".join(problems[1:]))
            return 1
        print(f"{len(paths)} changed file(s), all in scheme folders.")
        return 0
    if args.command == "build":
        problems = build(args.out)
        for p in problems:
            print(p)
        return 1 if problems else 0
    key = os.environ.get("GALLERY_SIGNING_KEY", "").encode("ascii")
    if not key:
        print("GALLERY_SIGNING_KEY is not set", file=sys.stderr)
        return 1
    if not sign_all(args.out, key):
        print(f"no index.json in any app folder under {args.out}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
