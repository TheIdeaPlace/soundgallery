"""Tests for tools/gallery.py: the first gate a proposed scheme goes through, for every app."""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import sys
import wave
import zipfile
from pathlib import Path

import pytest
from theideaplace_appkit import soundgallery, soundschemes

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import gallery  # noqa: E402

QM = gallery.APPS["quickmail"]
TCP = gallery.APPS["thechatplace"]
# A scheme's events in each app: one custom sound, one built-in, one silent.
EVENTS = {
    "quickmail": {"NewMail": "custom:drop.wav", "SendFailed": "builtin:wood/failed", "MessageDeleted": "none"},
    "thechatplace": {"ReplyFinished": "custom:drop.wav", "NeedsYou": "builtin:softbell/needsyou", "ToolRan": "none"},
}


def write_wav(path: Path, seconds: float = 0.2, rate: int = 8000, width: int = 2, channels: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00" * width * channels * int(seconds * rate))


def make_scheme(root: Path, sid: str = "rain", in_app: str = "quickmail", **overrides) -> Path:
    """A scheme folder at root/<in_app>/schemes/<sid>, with drop.wav if an event uses it. Other keywords
    are scheme.json fields."""
    folder = root / in_app / "schemes" / sid
    meta = {
        "name": "Rain",
        "author": "Pat",
        "description": "Soft drops.",
        "license": "CC0-1.0",
        "events": dict(EVENTS[in_app]),
    }
    meta.update(overrides)
    folder.mkdir(parents=True)
    (folder / "scheme.json").write_text(json.dumps(meta), encoding="utf-8")
    events = meta.get("events")
    if isinstance(events, dict) and "custom:drop.wav" in events.values():
        write_wav(folder / "sounds" / "drop.wav")
    return folder


def problems_for(root: Path) -> list[str]:
    return gallery.validate_all(root)[1]


def test_a_good_scheme_passes(tmp_path: Path) -> None:
    make_scheme(tmp_path)
    found, problems = gallery.validate_all(tmp_path)
    assert problems == []
    assert [s.id for s in found["quickmail"]] == ["rain"]
    assert found["quickmail"][0].files == ["drop.wav"]
    assert found["thechatplace"] == []


def test_a_chat_place_scheme_passes(tmp_path: Path) -> None:
    make_scheme(tmp_path, in_app="thechatplace")
    found, problems = gallery.validate_all(tmp_path)
    assert problems == []
    assert [s.id for s in found["thechatplace"]] == ["rain"]
    assert found["thechatplace"][0].app is TCP


def test_every_app_app_kit_knows_has_a_folder_in_the_gallery() -> None:
    assert set(gallery.APPS) == {"quickmail", "thechatplace"}
    for app_id in gallery.APPS:
        assert (gallery.ROOT / app_id / "schemes").is_dir()


def test_the_contributing_guide_lists_every_apps_events_and_sounds() -> None:
    guide = (gallery.ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    for app in gallery.APPS.values():
        assert f"### {app.name}'s events" in guide
        section = guide.split(f"### {app.name}'s events", 1)[1].split("###", 1)[0]
        for event in app.events:
            assert f"| `{event.key}` |" in section and f"| `{event.sound}` |" in section, (app.id, event.key)


def test_the_schemes_in_the_repo_pass() -> None:
    _, problems = gallery.validate_all()
    assert problems == []


def test_the_same_name_in_two_apps_is_fine(tmp_path: Path) -> None:
    make_scheme(tmp_path, in_app="quickmail")
    make_scheme(tmp_path, in_app="thechatplace")
    assert problems_for(tmp_path) == []


def test_a_folder_for_an_app_the_gallery_does_not_know_is_refused(tmp_path: Path) -> None:
    (tmp_path / "weatherfast" / "schemes" / "rain").mkdir(parents=True)
    assert any("isn't an app the gallery knows" in p for p in problems_for(tmp_path))


def test_a_stray_file_in_schemes_is_refused_but_a_readme_is_fine(tmp_path: Path) -> None:
    (tmp_path / "thechatplace" / "schemes").mkdir(parents=True)
    (tmp_path / "thechatplace" / "schemes" / "README.md").write_text("notes")
    assert problems_for(tmp_path) == []
    (tmp_path / "thechatplace" / "schemes" / ".gitkeep").write_text("")
    assert any("only scheme folders belong here" in p for p in problems_for(tmp_path))


@pytest.mark.parametrize("sid", ["Rain", "-rain", "rain_drops", "a" * 41, "rain.drops"])
def test_a_bad_folder_name_is_refused(tmp_path: Path, sid: str) -> None:
    make_scheme(tmp_path, sid=sid)
    assert any("folder name" in p for p in problems_for(tmp_path))


@pytest.mark.parametrize("value, fragment", [
    ("builtin:chimes/arrived", "unknown pack"),
    ("builtin:wood/nope", "unknown built-in sound"),
    ("builtin:/arrived", "unknown pack"),
    ("builtin:wood/tool", "unknown built-in sound"),   # one of The Chat Place's, not QuickMail's
    ("custom:../evil.wav", "unsafe"),
    ("custom:C:\\evil.wav", "unsafe"),
    ("custom:COM1.wav", "unsafe"),
    ("custom:song.ogg", "unsafe"),
    ("custom:drop.wav ", "unsafe"),
    ("custom:drop\u202ewav.exe", "unsafe"),
    ("custom:song.mp3", "MP3"),
    ("https://example.com/x.wav", "is not none"),
    (42, "is not text"),
])
def test_a_bad_sound_value_is_refused(tmp_path: Path, value, fragment: str) -> None:
    make_scheme(tmp_path, events={"NewMail": value, "SendFailed": "builtin:glass/failed"})
    assert any(fragment in p for p in problems_for(tmp_path)), problems_for(tmp_path)


def test_a_built_in_sound_without_a_pack_and_in_any_case_passes(tmp_path: Path) -> None:
    make_scheme(tmp_path, events={"NewMail": "builtin:arrived", "SendFailed": "BUILTIN:SoftBell/Failed"})
    assert problems_for(tmp_path) == []


def test_an_unknown_event_is_refused(tmp_path: Path) -> None:
    make_scheme(tmp_path, events={"Exploded": "builtin:glass/failed"})
    assert any("unknown event" in p for p in problems_for(tmp_path))


def test_a_quickmail_event_in_a_chat_place_scheme_is_refused(tmp_path: Path) -> None:
    make_scheme(tmp_path, in_app="thechatplace", events={"NewMail": "builtin:glass/arrived"})
    problems = problems_for(tmp_path)
    assert any("thechatplace/schemes/rain" in p and "unknown event 'NewMail'" in p for p in problems), problems


def test_a_chat_place_sound_in_a_quickmail_scheme_is_refused(tmp_path: Path) -> None:
    make_scheme(tmp_path, events={"NewMail": "builtin:glass/otherreply"})
    assert any("unknown built-in sound 'otherreply'" in p for p in problems_for(tmp_path))


def test_a_scheme_with_no_sound_is_refused(tmp_path: Path) -> None:
    make_scheme(tmp_path, events={"NewMail": "none"})
    assert any("no event has a sound" in p for p in problems_for(tmp_path))


@pytest.mark.parametrize("field, value, fragment", [
    ("name", "", "'name' is required"),
    ("name", "x" * 61, "longer than 60"),
    ("name", "Two\nlines", "one line"),
    ("name", "Bad\ud800half", "one line"),
    ("author", "", "'author' is required"),
    ("description", "x" * 301, "longer than 300"),
    ("license", "GPL-3.0", "license must be one of"),
    ("license", "", "'license' is required"),
])
def test_bad_metadata_is_refused(tmp_path: Path, field: str, value: str, fragment: str) -> None:
    make_scheme(tmp_path, **{field: value})
    assert any(fragment in p for p in problems_for(tmp_path)), problems_for(tmp_path)


def test_an_unknown_field_is_refused(tmp_path: Path) -> None:
    make_scheme(tmp_path, homepage="https://example.com")
    assert any("unknown fields" in p for p in problems_for(tmp_path))


def test_format_and_app_from_an_exported_file_are_fine_when_they_agree(tmp_path: Path) -> None:
    make_scheme(tmp_path, in_app="thechatplace", format=1, app="thechatplace")
    make_scheme(tmp_path, format=1, app=None)   # null means QuickMail, as QuickMail 0.8.69 wrote no app
    assert problems_for(tmp_path) == []


def test_an_app_of_null_means_quickmail(tmp_path: Path) -> None:
    make_scheme(tmp_path, in_app="thechatplace", app=None)
    assert any("'app' says it's for QuickMail" in p for p in problems_for(tmp_path))


def test_a_scheme_in_the_wrong_apps_folder_is_refused(tmp_path: Path) -> None:
    make_scheme(tmp_path, **{"app": "thechatplace"})
    assert any("'app' says it's for The Chat Place" in p for p in problems_for(tmp_path))


def test_a_newer_format_is_refused(tmp_path: Path) -> None:
    make_scheme(tmp_path, format=2)
    assert any("'format' must be 1" in p for p in problems_for(tmp_path))


def test_json_every_app_would_refuse_is_refused(tmp_path: Path) -> None:
    folder = make_scheme(tmp_path)
    (folder / "scheme.json").write_text('{"name": NaN}', encoding="utf-8")
    assert any("not valid UTF-8 JSON" in p for p in problems_for(tmp_path))


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    folder = make_scheme(tmp_path)
    (folder / "sounds" / "drop.wav").unlink()
    assert any("isn't there" in p for p in problems_for(tmp_path))


def test_an_unused_file_is_refused(tmp_path: Path) -> None:
    folder = make_scheme(tmp_path)
    write_wav(folder / "sounds" / "extra.wav")
    assert any("no event uses it" in p for p in problems_for(tmp_path))


def test_an_unexpected_file_in_the_scheme_is_refused(tmp_path: Path) -> None:
    folder = make_scheme(tmp_path)
    (folder / "install.ps1").write_text("echo hi")
    assert any("unexpected 'install.ps1'" in p for p in problems_for(tmp_path))


def test_a_sound_that_does_not_decode_is_refused(tmp_path: Path) -> None:
    folder = make_scheme(tmp_path)
    (folder / "sounds" / "drop.wav").write_bytes(b"RIFF\x00\x00\x00\x00WAVEnot really")
    assert any("16-bit PCM WAV" in p for p in problems_for(tmp_path))


def test_a_disguised_file_is_refused(tmp_path: Path) -> None:
    folder = make_scheme(tmp_path)
    (folder / "sounds" / "drop.wav").write_bytes(b"MZ\x90\x00" + b"\x00" * 200)
    assert any("16-bit PCM WAV" in p for p in problems_for(tmp_path))


def test_an_mp3_renamed_to_wav_is_refused(tmp_path: Path) -> None:
    folder = make_scheme(tmp_path)
    # An MPEG audio frame header, as an MP3 starts, with an ID3 tag in front.
    (folder / "sounds" / "drop.wav").write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x64" + b"\x00" * 400)
    assert any("16-bit PCM WAV" in p for p in problems_for(tmp_path))


def test_an_mp3_file_is_refused(tmp_path: Path) -> None:
    folder = make_scheme(tmp_path, events={"NewMail": "custom:drop.mp3"})
    (folder / "sounds").mkdir(exist_ok=True)
    (folder / "sounds" / "drop.mp3").write_bytes(b"\xff\xfb\x90\x64" + b"\x00" * 400)
    problems = problems_for(tmp_path)
    assert any("names an MP3 file 'drop.mp3'" in p for p in problems), problems
    assert any("sounds/drop.mp3: no event uses it" in p for p in problems), problems


@pytest.mark.parametrize("width, channels, rate", [(1, 1, 8000), (3, 1, 8000), (4, 1, 8000), (2, 3, 8000), (2, 1, 4000)])
def test_a_wav_every_app_cannot_play_is_refused(tmp_path: Path, width: int, channels: int, rate: int) -> None:
    folder = make_scheme(tmp_path)
    write_wav(folder / "sounds" / "drop.wav", width=width, channels=channels, rate=rate)
    assert any("sounds/drop.wav" in p for p in problems_for(tmp_path)), (width, channels, rate)


def test_an_8_bit_wav_is_refused(tmp_path: Path) -> None:
    folder = make_scheme(tmp_path)
    write_wav(folder / "sounds" / "drop.wav", width=1)
    assert any("16-bit" in p for p in problems_for(tmp_path)), problems_for(tmp_path)


def test_a_stereo_wav_passes(tmp_path: Path) -> None:
    folder = make_scheme(tmp_path)
    write_wav(folder / "sounds" / "drop.wav", channels=2, rate=44100)
    assert problems_for(tmp_path) == []


def test_an_extensible_wav_is_refused(tmp_path: Path) -> None:
    folder = make_scheme(tmp_path)
    path = folder / "sounds" / "drop.wav"
    data = bytearray(path.read_bytes())
    data[20:22] = struct.pack("<H", 0xFFFE)   # WAVE_FORMAT_EXTENSIBLE in place of PCM
    path.write_bytes(bytes(data))
    assert any("16-bit PCM WAV" in p for p in problems_for(tmp_path))


def test_a_long_sound_is_refused(tmp_path: Path) -> None:
    folder = make_scheme(tmp_path)
    write_wav(folder / "sounds" / "drop.wav", seconds=31, rate=8000)
    assert any("longer than 30 seconds" in p for p in problems_for(tmp_path))


def test_a_large_sound_is_refused(tmp_path: Path) -> None:
    folder = make_scheme(tmp_path)
    write_wav(folder / "sounds" / "drop.wav", seconds=29, rate=48000)   # about 2.8 MB: under the cap
    assert problems_for(tmp_path) == []
    with open(folder / "sounds" / "drop.wav", "ab") as f:
        f.write(b"\x00" * (gallery.MAX_SOUND_BYTES))
    assert any("larger than 5 MB" in p for p in problems_for(tmp_path))


def test_two_schemes_with_one_name_are_refused(tmp_path: Path) -> None:
    make_scheme(tmp_path, sid="rain")
    make_scheme(tmp_path, sid="rain-two", name="RAIN")
    assert any("already used" in p for p in problems_for(tmp_path))


def test_names_compare_as_every_app_ignores_case(tmp_path: Path) -> None:
    # Simple uppercase, one character for one: "straße" and "STRASSE" are different names in every app.
    make_scheme(tmp_path, sid="one", name="straße")
    make_scheme(tmp_path, sid="two", name="STRASSE")
    assert problems_for(tmp_path) == []


def test_too_many_schemes_for_one_app_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gallery, "MAX_SCHEMES", 2)
    for n in range(3):
        make_scheme(tmp_path, sid=f"s{n}", in_app="thechatplace", name=f"S{n}")
    make_scheme(tmp_path, sid="s0", name="S0")
    problems = problems_for(tmp_path)
    assert [p for p in problems if "more than 2 schemes" in p] == [
        "thechatplace/schemes: more than 2 schemes; The Chat Place reads only the first 2"]


def build(tmp_path: Path) -> Path:
    out = tmp_path / "site"
    assert gallery.build(out, tmp_path) == []
    return out


def test_build_writes_a_file_quickmail_reads_and_an_index_that_matches_it(tmp_path: Path) -> None:
    make_scheme(tmp_path)
    out = build(tmp_path) / "quickmail"

    index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    assert index["format"] == 1
    assert index["app"] == "quickmail"
    entry = index["schemes"][0]
    file = entry["file"]
    data = (out / file).read_bytes()
    assert file == f"rain-{hashlib.sha256(data).hexdigest()[:16]}.qmsounds"
    assert entry["size"] == len(data)
    assert entry["sha256"] == hashlib.sha256(data).hexdigest()
    assert entry["eventsWithSound"] == 2
    assert entry["description"] == "Soft drops."

    with zipfile.ZipFile(out / file) as zf:
        assert zf.namelist() == ["scheme.json", "sounds/drop.wav"]
        body = json.loads(zf.read("scheme.json"))
    assert body["format"] == 1 and body["app"] == "quickmail" and body["name"] == "Rain"
    assert body["description"] == "Soft drops."
    assert body["events"]["NewMail"] == "custom:drop.wav"
    assert list(body["events"]) == list(QM.keys)   # every event, in the app's order
    assert body["events"]["MessageSent"] == "none"


def test_the_built_file_reads_back_in_its_app_and_is_refused_by_the_other(tmp_path: Path) -> None:
    make_scheme(tmp_path, in_app="thechatplace", events={**EVENTS["thechatplace"], "MessageSent": "builtin:send"})
    out = build(tmp_path) / "thechatplace"
    entry = json.loads((out / "index.json").read_text(encoding="utf-8"))["schemes"][0]
    assert entry["file"].endswith(".tcpsounds")
    data = (out / entry["file"]).read_bytes()

    back = soundschemes.read_scheme_file(data, TCP)
    assert back.app == "thechatplace" and back.name == "Rain" and back.author == "Pat"
    assert back.license == "CC0-1.0" and back.description == "Soft drops."
    assert back.dropped == 0 and set(back.sounds) == {"drop.wav"}
    assert back.events["ReplyFinished"] == "custom:drop.wav"
    assert back.events["MessageSent"] == "builtin:glass/send"   # a pack is pinned, so it sounds the same anywhere
    with pytest.raises(soundschemes.SchemeFileProblem, match="for The Chat Place, so QuickMail can't use it"):
        soundschemes.read_scheme_file(data, QM)


def test_an_app_with_no_schemes_still_gets_an_index_and_a_page(tmp_path: Path) -> None:
    make_scheme(tmp_path)
    out = build(tmp_path)
    index = json.loads((out / "thechatplace" / "index.json").read_text(encoding="utf-8"))
    assert index == {"format": 1, "app": "thechatplace", "schemes": []}
    page = (out / "thechatplace" / "index.html").read_text(encoding="utf-8")
    assert "<title>The Chat Place sound schemes</title>" in page and "No schemes yet." in page


def test_an_empty_gallery_still_builds_every_app(tmp_path: Path) -> None:
    out = build(tmp_path)
    for app_id in gallery.APPS:
        assert json.loads((out / app_id / "index.json").read_text(encoding="utf-8"))["schemes"] == []


def test_build_is_repeatable(tmp_path: Path) -> None:
    make_scheme(tmp_path)
    make_scheme(tmp_path, in_app="thechatplace")
    gallery.build(tmp_path / "a", tmp_path)
    gallery.build(tmp_path / "b", tmp_path)
    for app_id in gallery.APPS:
        a, b = tmp_path / "a" / app_id, tmp_path / "b" / app_id
        assert sorted(p.name for p in a.iterdir()) == sorted(p.name for p in b.iterdir())
        for p in a.iterdir():
            assert p.read_bytes() == (b / p.name).read_bytes()


def test_build_refuses_to_publish_anything_if_one_scheme_fails(tmp_path: Path) -> None:
    make_scheme(tmp_path)
    make_scheme(tmp_path, sid="broken", in_app="thechatplace", name="Broken", license="GPL-3.0")
    out = tmp_path / "site"
    assert gallery.build(out, tmp_path) != []
    assert not out.exists()


def test_build_refuses_a_file_that_would_not_read_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_scheme(tmp_path)
    monkeypatch.setattr(gallery, "build_file", lambda scheme: b"not a zip")
    out = tmp_path / "site"
    problems = gallery.build(out, tmp_path)
    assert any("doesn't read back in QuickMail" in p for p in problems), problems
    assert not out.exists()


def test_the_page_escapes_scheme_text(tmp_path: Path) -> None:
    make_scheme(tmp_path, description="<script>alert(1)</script>")
    page = (build(tmp_path) / "quickmail" / "index.html").read_text(encoding="utf-8")
    assert "<script>" not in page and "&lt;script&gt;" in page


@pytest.mark.parametrize("app_id, title, where", [
    ("quickmail", "QuickMail sound schemes", "Settings, Sounds, Browse schemes"),
    ("thechatplace", "The Chat Place sound schemes", "File, Sounds, Browse Gallery"),
])
def test_each_app_page_says_where_to_browse_in_that_app(tmp_path: Path, app_id: str, title: str, where: str) -> None:
    page = (build(tmp_path) / app_id / "index.html").read_text(encoding="utf-8")
    assert f"<title>{title}</title>" in page and f"<h1>{title}</h1>" in page
    assert where in page


def key_pair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    return key, private, public


def test_sign_produces_a_signature_over_the_exact_index(tmp_path: Path) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec

    key, pem, _ = key_pair()
    make_scheme(tmp_path)
    out = build(tmp_path) / "quickmail"
    unsigned = (out / "index.json").read_bytes()
    gallery.sign(out, pem)

    signed = (out / "index.signed").read_bytes()
    line, _, index = signed.partition(b"\n")
    assert index == unsigned
    assert not (out / "index.json").exists()
    signature = base64.b64decode(line)
    key.public_key().verify(signature, index, ec.ECDSA(hashes.SHA256()))
    with pytest.raises(InvalidSignature):
        key.public_key().verify(signature, index + b" ", ec.ECDSA(hashes.SHA256()))


def test_each_signed_index_verifies_in_its_app_and_not_in_the_other(tmp_path: Path) -> None:
    _, private, public = key_pair()
    make_scheme(tmp_path)
    make_scheme(tmp_path, in_app="thechatplace", name="Chat Rain")
    out = build(tmp_path)
    assert gallery.sign_all(out, private) == sorted(gallery.APPS)

    tcp = soundgallery.read_index((out / "thechatplace" / "index.signed").read_bytes(), TCP, public)
    assert [(e.id, e.name, e.author, e.license, e.events_with_sound) for e in tcp] == [("rain", "Chat Rain", "Pat", "CC0-1.0", 2)]
    data = (out / "thechatplace" / tcp[0].file).read_bytes()
    assert soundgallery.check_download(tcp[0], data) == data

    qm = soundgallery.read_index((out / "quickmail" / "index.signed").read_bytes(), QM, public)
    assert [e.name for e in qm] == ["Rain"] and qm[0].file.endswith(".qmsounds")

    # One key signs every app's list; `app` is what stops QuickMail taking The Chat Place's.
    with pytest.raises(soundgallery.Untrusted):
        soundgallery.read_index((out / "thechatplace" / "index.signed").read_bytes(), QM, public)


def test_an_empty_apps_signed_index_verifies(tmp_path: Path) -> None:
    _, private, public = key_pair()
    out = build(tmp_path)
    gallery.sign_all(out, private)
    assert soundgallery.read_index((out / "thechatplace" / "index.signed").read_bytes(), TCP, public) == []


def test_sign_refuses_a_key_of_the_wrong_kind(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP384R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    (tmp_path / "index.json").write_text("{}")
    with pytest.raises(SystemExit):
        gallery.sign(tmp_path, pem)


def test_the_command_line_signs_every_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, private, _ = key_pair()
    assert gallery.main(["build", "--out", str(tmp_path)]) == 0
    monkeypatch.setenv("GALLERY_SIGNING_KEY", private.decode("ascii"))
    assert gallery.main(["sign", "--out", str(tmp_path)]) == 0
    for app_id in gallery.APPS:
        assert (tmp_path / app_id / "index.signed").exists()
        assert not (tmp_path / app_id / "index.json").exists()


def test_a_link_is_refused(tmp_path: Path) -> None:
    folder = make_scheme(tmp_path)
    target = tmp_path / "outside.wav"
    write_wav(target)
    (folder / "sounds" / "drop.wav").unlink()
    try:
        (folder / "sounds" / "drop.wav").symlink_to(target)
    except OSError:
        pytest.skip("this machine can't make symbolic links")
    assert any("contains a link" in p for p in problems_for(tmp_path))


def test_names_that_differ_only_in_case_are_refused(tmp_path: Path) -> None:
    folder = make_scheme(tmp_path, events={"NewMail": "custom:drop.wav", "SendFailed": "custom:Drop.wav"})
    if (folder / "sounds" / "Drop.wav").exists():
        pytest.skip("this file system ignores case, so the two can't both exist")
    write_wav(folder / "sounds" / "Drop.wav")
    assert any("differ only in case" in p for p in problems_for(tmp_path))


def test_length_counts_as_the_apps_do(tmp_path: Path) -> None:
    make_scheme(tmp_path, name="\U0001F327" * 31)   # 31 emoji: 31 characters here, 62 in the apps
    assert any("longer than 60" in p for p in problems_for(tmp_path))


def test_an_index_too_big_for_the_apps_fails_the_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_scheme(tmp_path)
    monkeypatch.setattr(gallery, "MAX_INDEX_BYTES", 100)
    problems = gallery.build(tmp_path / "site", tmp_path)
    assert any("QuickMail reads at most" in p for p in problems)
    assert not (tmp_path / "site").exists()


@pytest.mark.parametrize("name, app_id, expected", [
    ("Essentials", "quickmail", "Essentials.qmsounds"),
    ("Rain at Night", "quickmail", "Rain at Night.qmsounds"),
    ("Rain at Night", "thechatplace", "Rain at Night.tcpsounds"),
    ("Calm / quiet: v2", "quickmail", "Calm - quiet- v2.qmsounds"),
    ("../../evil", "thechatplace", "evil.tcpsounds"),
    ("***", "quickmail", "Sound scheme.qmsounds"),
])
def test_downloads_get_a_friendly_name(name: str, app_id: str, expected: str) -> None:
    assert gallery.download_name(name, gallery.APPS[app_id]) == expected


def test_the_page_offers_the_friendly_name_but_links_the_hashed_file(tmp_path: Path) -> None:
    make_scheme(tmp_path, in_app="thechatplace")
    out = build(tmp_path) / "thechatplace"
    page = (out / "index.html").read_text(encoding="utf-8")
    index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    assert f'href="{index["schemes"][0]["file"]}" download="Rain.tcpsounds"' in page


def test_the_command_line_build_puts_each_app_in_its_own_folder_with_a_front_page(tmp_path: Path) -> None:
    assert gallery.main(["build", "--out", str(tmp_path)]) == 0
    assert any(p.suffix == ".qmsounds" for p in (tmp_path / "quickmail").iterdir())
    for app_id, app in gallery.APPS.items():
        assert json.loads((tmp_path / app_id / "index.json").read_text(encoding="utf-8"))["app"] == app_id
        assert (tmp_path / app_id / "index.html").exists()
    home = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert '<a href="quickmail/">QuickMail</a>' in home
    assert '<a href="thechatplace/">The Chat Place</a>' in home


# -- more of what a scheme folder can get wrong -------------------------------------------------------


def test_a_folder_name_with_a_trailing_newline_is_refused() -> None:
    assert gallery.ID_RE.fullmatch("rain")
    assert not gallery.ID_RE.fullmatch("rain\n")
    problems: list[str] = []
    assert gallery.load_scheme(Path("unused") / "rain\n", QM, problems) is None
    assert any("folder name" in p for p in problems)


def _link_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError:
        pytest.skip("this machine can't make symbolic links")


def test_a_linked_scheme_folder_is_refused(tmp_path: Path) -> None:
    real = make_scheme(tmp_path / "elsewhere")
    (tmp_path / "quickmail" / "schemes").mkdir(parents=True)
    _link_or_skip(tmp_path / "quickmail" / "schemes" / "rain", real)
    assert any("contains a link" in p for p in problems_for(tmp_path))


def test_a_linked_sounds_folder_is_refused(tmp_path: Path) -> None:
    folder = make_scheme(tmp_path)
    outside = tmp_path / "outside"
    write_wav(outside / "drop.wav")
    (folder / "sounds" / "drop.wav").unlink()
    (folder / "sounds").rmdir()
    _link_or_skip(folder / "sounds", outside)
    assert any("contains a link" in p for p in problems_for(tmp_path))


def test_a_scheme_json_over_64_kb_is_refused(tmp_path: Path) -> None:
    make_scheme(tmp_path, description="x" * (64 * 1024))
    assert any("larger than 64 KB" in p for p in problems_for(tmp_path))


@pytest.mark.parametrize("value", [True, None, "1", 1.0])
def test_a_format_that_is_not_the_number_1_is_refused(tmp_path: Path, value) -> None:
    make_scheme(tmp_path, format=value)
    assert any("'format' must be 1" in p for p in problems_for(tmp_path)), value


@pytest.mark.parametrize("value", [None, [], "NewMail", 1])
def test_events_that_are_not_an_object_are_refused(tmp_path: Path, value) -> None:
    make_scheme(tmp_path, events=value)
    assert any("'events' must be an object" in p for p in problems_for(tmp_path)), value


def test_build_fails_if_an_app_would_leave_out_an_index_entry() -> None:
    entries = [{"id": "rain", "name": "Rain", "author": "Pat", "license": "CC0-1.0", "description": "",
                "file": "rain-0000000000000000.tcpsounds", "size": 1, "sha256": "0" * 64}]
    good = json.dumps({"format": 1, "app": "quickmail", "schemes": entries}).encode()
    assert "would leave out rain" in gallery.index_problem(QM, good, entries)   # the wrong extension
    entries[0]["file"] = "rain-0000000000000000.qmsounds"
    good = json.dumps({"format": 1, "app": "quickmail", "schemes": entries}).encode()
    assert gallery.index_problem(QM, good, entries) is None
    assert "wouldn't read it" in gallery.index_problem(TCP, good, entries)   # another app's list


# -- what a scheme pull request may change ------------------------------------------------------------


@pytest.mark.parametrize("path", [
    "quickmail/schemes/rain/scheme.json",
    "quickmail/schemes/rain/README.md",
    "quickmail/schemes/rain/sounds/drop.wav",
    "thechatplace/schemes/rain-2/sounds/Soft knock 2.wav",
    "thechatplace/schemes/README.md",
    "quickmail/schemes/README.md",
])
def test_a_scheme_pull_request_may_change_scheme_files(path: str) -> None:
    assert gallery.scheme_path_ok(path)
    assert gallery.path_problems([path]) == []


@pytest.mark.parametrize("path", [
    "tests/schemes/conftest.py",
    "tools/schemes/x",
    "weatherfast/schemes/x/scheme.json",
    "quickmail/schemes/x/run.sh",
    "quickmail/schemes/x/sounds/sub/a.wav",
    "quickmail/schemes/x/sounds/a.mp3",
    "quickmail/schemes/x/sounds/COM1.wav",
    "quickmail/schemes/x/scheme.json/extra",
    "quickmail/schemes/x\n/scheme.json",
    "quickmail/schemes/x/sounds/a\n.wav",
    "quickmail/schemes/Rain/scheme.json",
    "quickmail/schemes/conftest.py",
    "quickmail/schemes/x",
    "quickmail/conftest.py",
    "quickmail/schemes",
    "conftest.py",
    "pytest.ini",
    ".github/workflows/validate.yml",
    "tools/gallery.py",
    "tools/requirements.txt",
])
def test_a_scheme_pull_request_may_not_change_anything_else(path: str) -> None:
    assert not gallery.scheme_path_ok(path)
    problems = gallery.path_problems(["quickmail/schemes/rain/scheme.json", path])
    assert problems[0].startswith("A scheme pull request may only change files in a scheme folder")
    assert problems[1:] == [f"  {path!r}"]


def _git(repo: Path, *args: str) -> str:
    import subprocess
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=repo,
                          check=True, capture_output=True, text=True).stdout.strip()


def test_check_paths_sees_additions_renames_and_deletions(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_x.py").write_text("x")
    (repo / "quickmail" / "schemes" / "rain").mkdir(parents=True)
    (repo / "quickmail" / "schemes" / "rain" / "scheme.json").write_text("{}")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")

    (repo / "quickmail" / "schemes" / "rain" / "README.md").write_text("notes")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fine")
    fine = _git(repo, "rev-parse", "HEAD")
    assert gallery.path_problems(gallery.changed_paths(base, fine, repo)) == []

    # A rename out of the scheme folder lists both paths; a deletion outside it is caught too.
    _git(repo, "mv", "quickmail/schemes/rain/scheme.json", "tests/conftest.py")
    _git(repo, "rm", "-q", "tests/test_x.py")
    _git(repo, "commit", "-q", "-m", "bad")
    bad = _git(repo, "rev-parse", "HEAD")
    paths = gallery.changed_paths(base, bad, repo)
    assert sorted(paths) == ["quickmail/schemes/rain/README.md", "quickmail/schemes/rain/scheme.json",
                             "tests/conftest.py", "tests/test_x.py"]
    assert gallery.path_problems(paths)[1:] == ["  'tests/conftest.py'", "  'tests/test_x.py'"]


def test_check_paths_on_the_command_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(gallery, "changed_paths", lambda base, head: ["quickmail/schemes/rain/scheme.json"])
    assert gallery.main(["check-paths", "a", "b"]) == 0
    monkeypatch.setattr(gallery, "changed_paths", lambda base, head: ["tests/schemes/conftest.py"])
    assert gallery.main(["check-paths", "a", "b"]) == 1
    assert "'tests/schemes/conftest.py'" in capsys.readouterr().out
