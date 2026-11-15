"""The magick server's layer-1 gate (src/harness/magick_guard.py): every call is checked against Settings >
Allowed Folders before magick-mcp sees it, split the way the server splits it, with options, coders and file
kinds on allow-lists. func_execute is run by the gate itself, command by command."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.harness.magick_guard import Folders, MagickGuard, Refused, raster_kind
from src.harness.mcp_client import McpRegistry, ServerConfig, load_config

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 56
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><image href="file:///etc/passwd"/></svg>'
ICC = b"\x00" * 36 + b"acsp" + b"\x00" * 24


@pytest.fixture
def w(tmp_path: Path):
    zen = tmp_path / ".zen-agents"
    vault, settings, ledger = zen / "vault", zen / "vault-settings", zen / "vault-ledger"
    work, ref, outside = tmp_path / "work", tmp_path / "ref", tmp_path / "outside"
    for d in (vault / "docs", settings, ledger, work, ref, outside):
        d.mkdir(parents=True)
    for d in (vault / "docs", work, ref, outside):
        (d / "a.png").write_bytes(PNG)
    (settings / "keys.png").write_bytes(PNG)
    (work / "evil.png").write_bytes(SVG)  # an SVG named .png: ImageMagick goes by content
    (work / "p.icc").write_bytes(ICC)
    folders = Folders(full=[vault, work], read_only=[ref], private=[settings, ledger])
    fns = tmp_path / "functions"
    fns.mkdir()
    ran = Runner()
    return SimpleNamespace(guard=MagickGuard(lambda: folders, ran, functions=fns), ran=ran, vault=vault,
                           settings=settings, work=work, ref=ref, outside=outside, fns=fns)


class Server:
    """Stands in for magick-mcp: records what reaches it. A magick command never should."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, tool: str, args: dict[str, Any]) -> str:
        assert tool != "magick", "the gate runs magick itself"
        self.calls.append((tool, args))
        return f"ran {tool}"


class Runner:
    """Stands in for MagickRunner: records the argument lists, and writes the last one as a PNG, like magick."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None]] = []

    async def __call__(self, args: list[str], cwd: Path | None) -> str:
        self.calls.append((args, str(cwd) if cwd else None))
        out = args[-1]
        if cwd and ":" not in out and out != "-" and args[0] != "mogrify":
            (cwd / out).write_bytes(PNG)
        return "ran magick"


async def run(w, command: str, workspace: Path | None = None) -> None:
    srv = Server()
    args = {"command": command, **({"workspace": str(workspace)} if workspace else {})}
    assert await w.guard("magick", args, srv) == "ran magick"
    # magick runs with the very arguments checked, split as magick-mcp splits them; the server sees nothing
    assert w.ran.calls[-1] == (command.split(), os.path.realpath(workspace) if workspace else None)
    assert srv.calls == []


async def refused(w, command: str, match: str, workspace: Path | None = None) -> None:
    srv, before = Server(), len(w.ran.calls)
    with pytest.raises(Refused, match=match):
        await w.guard("magick", {"command": command, **({"workspace": str(workspace)} if workspace else {})}, srv)
    assert srv.calls == [] and len(w.ran.calls) == before


async def test_plain_edits_inside_allowed_folders_pass(w):
    await run(w, "a.png -resize 50% -quality 85 -strip out.jpg", w.work)
    await run(w, f"{w.ref / 'a.png'} -auto-orient -resize 800x800> {w.work / 'b.webp'}")  # read Read-Only, write Full
    await run(w, f"{w.vault / 'docs' / 'a.png'} -gravity center -crop 100x100+0+0 +repage {w.vault / 'docs' / 'c.png'}")
    await run(w, "-size 200x100 xc:white -fill red -pointsize 20 -annotate +10+50 Hello label.png", w.work)
    await run(w, "a.png[0] ( +clone -resize 50% ) -append -define jpeg:extent=200kb both.jpg", w.work)
    await run(w, "a.png -profile p.icc -morphology Erode Disk:2 -rotate -90 m.png", w.work)
    await run(w, "identify -format %wx%h a.png", w.work)
    await run(w, f"compare -metric AE a.png {w.ref / 'a.png'} null:", w.work)


async def test_paths_follow_allowed_folders_and_access(w):
    await refused(w, f"{w.outside / 'a.png'} {w.work / 'o.png'}", "outside the allowed folders")
    await refused(w, f"{w.work / 'a.png'} {w.outside / 'o.png'}", "outside the allowed folders")
    await refused(w, f"{w.work / 'a.png'} {w.ref / 'o.png'}", "Read-Only")
    await refused(w, f"{w.settings / 'keys.png'} {w.work / 'o.png'}", "harness's own")
    await refused(w, f"{w.work / 'a.png'} {w.settings / 'o.png'}", "harness's own")
    await refused(w, "a.png o.png", "give a full path")  # no workspace: nothing is relative to the harness
    await refused(w, "a.png ../outside/o.png", r"'\.\.'", w.work)
    await refused(w, "a.png o.png", "outside the allowed folders", w.outside)  # the workspace must be allowed too
    await refused(w, f"{w.work / 'a.png'} C:o.png", "drive-relative")
    await refused(w, "gone.png o.png", "no such input", w.work)


async def test_a_symlink_out_of_an_allowed_folder_is_followed_and_refused(w):
    try:
        os.symlink(w.outside / "a.png", w.work / "link.png")
        os.symlink(w.outside, w.work / "out-dir", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need privileges here")
    await refused(w, "link.png o.png", "outside the allowed folders", w.work)
    await refused(w, "a.png out-dir/o.png", "outside the allowed folders", w.work)


async def test_inputs_that_read_other_things_are_refused(w):
    for cmd, why in [("text:/etc/passwd o.png", "'text:' inputs"), ("msl:x.msl o.png", "'msl:' inputs"),
                     ("https://example.com/a.png o.png", "'https:' inputs"), ("ephemeral:a.png o.png", "'ephemeral:'"),
                     ("svg:a.png o.png", "'svg:' inputs"), ("@list.txt o.png", "'@' lists"), ("|cat o.png", "'@' lists"),
                     ("- o.png", "standard input"), ("label:@secret.txt o.png", "'@' would read"),
                     ("evil.png o.png", "not a raster image"), ("img-%d.png[1-3] o.png", "numbered series")]:
        await refused(w, cmd, why, w.work)


async def test_options_are_an_allow_list(w):
    for cmd, why in [("a.png -draw image_over_0,0_0,0_x o.png", "option -draw"),
                     ("-script x.msl", "option -script"), ("a.png -wobble 3 o.png", "option -wobble"),
                     ("a.png -resize", "needs 1"),
                     ("a.png -rotate /etc/passwd o.png", "looks like a file"),
                     ("a.png -rotate a.png o.png", "looks like a file"),
                     ("a.png -profile a.png o.png", "not a color profile"),
                     ("a.png -font evil.png o.png", "not a font"),
                     ("a.png -tile a.png o.png", "tile layout"),
                     ("a.png -define delegate:x=y o.png", "only format settings"),
                     ("a.png -set profile p.icc o.png", "reads a file"),
                     ("a.png -set filename:x %w o.png", "reads a file"),
                     ("a.png -annotate +0+0 @secret.txt o.png", "'@' and '|'"),
                     ("a.png -fill pattern:x o.png", "not a color"),
                     ("a.png -write msl:o.msl o.png", "write a raster")]:
        await refused(w, cmd, why, w.work)


async def test_outputs_are_raster_files_named_plainly(w):
    for cmd, why in [("a.png o.mp4", "write a raster"), ("a.png o.svg", "write a raster"), ("a.png o", "extension"),
                     ("a.png o-%[w].png", "built at run time"), ('a.png o";calc;".png', "not allowed in file names"),
                     ("a.png ~/o.png", "not allowed in file names"), ("a.png *.png", "wildcards"),
                     ("a.png mpr:x", "write a raster"), ("a.png", "name an input")]:
        await refused(w, cmd, why, w.work)
    for cmd in ("a.png -", "a.png info:"):  # standard output comes back to the bot
        await run(w, cmd, w.work)
    await run(w, "a.png png:o", w.work)  # the coder names the format


async def test_leading_tool_names_that_are_not_allowed(w):
    for cmd in ("magick a.png o.png", "convert a.png o.png", "import o.png", "conjure x.msl", "display a.png"):
        await refused(w, cmd, "is not allowed", w.work)


async def test_wildcards_expand_and_every_match_is_checked(w):
    (w.ref / "b.png").write_bytes(PNG)
    await run(w, f"montage {w.ref / '*.png'} -tile 2x -geometry +2+2 {w.work / 'sheet.png'}")
    await refused(w, "*.png sheet.png", "not a raster image", w.work)  # evil.png matches
    await refused(w, "*.gif sheet.png", "no files match", w.work)


async def test_mogrify_writes_in_place_unless_given_a_folder(w):
    await run(w, "mogrify -resize 50% a.png", w.work)
    await run(w, f"mogrify -path {w.work} -format jpg a.png", w.ref)  # read from Read-Only, write elsewhere
    await refused(w, "mogrify -resize 50% a.png", "Read-Only", w.ref)
    await refused(w, f"mogrify -path {w.outside} a.png", "outside", w.ref)
    await refused(w, "mogrify -format mp4 a.png", "raster format", w.work)


async def test_saved_functions_get_slug_names_and_every_command_is_checked(w):
    srv = Server()
    for bad in ("../../evil", "a/b", "", "x" * 65, None):
        with pytest.raises(Refused, match="function name"):
            await w.guard("func_save", {"name": bad, "commands": ["$input o.png"]}, srv)
    with pytest.raises(Refused, match="option -draw"):  # rules apply at save time; paths wait for the run
        await w.guard("func_save", {"name": "thumb", "commands": ["$input -draw x o.png"]}, srv)
    save = {"name": "thumb", "commands": ["$input -thumbnail 128x128 t.png"]}
    await w.guard("func_save", save, srv)
    assert srv.calls == [("func_save", save)]

    # the gate runs func_execute itself, one magick run per command, never the server's func_execute
    (w.fns / "thumb.json").write_text(json.dumps({"name": "thumb", "commands": [
        "$input -thumbnail 128x128 t.png", "t.png -strip t2.png"]}), encoding="utf-8")  # reads what the first wrote
    srv = Server()
    out = await w.guard("func_execute", {"name": "thumb", "input": "a.png", "workspace": str(w.work)}, srv)
    ws = os.path.realpath(w.work)
    assert w.ran.calls == [(["a.png", "-thumbnail", "128x128", "t.png"], ws), (["t.png", "-strip", "t2.png"], ws)]
    assert out == "ran magick\nran magick" and srv.calls == []

    srv, before = Server(), len(w.ran.calls)
    with pytest.raises(Refused, match="outside the allowed folders"):
        await w.guard("func_execute", {"name": "thumb", "input": str(w.outside / "a.png"), "workspace": str(w.work)}, srv)
    (w.fns / "bad.json").write_text(json.dumps({"commands": ["$input o.png", "$input -draw x o2.png"]}), encoding="utf-8")
    with pytest.raises(Refused, match="option -draw"):  # a rule broken anywhere: nothing runs
        await w.guard("func_execute", {"name": "bad", "input": "a.png", "workspace": str(w.work)}, srv)
    with pytest.raises(Refused, match="give an input"):
        await w.guard("func_execute", {"name": "thumb", "workspace": str(w.work)}, srv)
    with pytest.raises(Refused, match="no saved magick function"):
        await w.guard("func_execute", {"name": "nope", "input": "a.png", "workspace": str(w.work)}, srv)
    with pytest.raises(Refused, match="function name"):
        await w.guard("func_execute", {"name": "../thumb", "input": "a.png"}, srv)
    assert srv.calls == [] and len(w.ran.calls) == before


async def test_other_tools(w):
    srv = Server()
    assert await w.guard("func_list", {}, srv) == "ran func_list"
    assert await w.guard("check", {}, srv) == "ran magick"  # the ImageMagick in Settings, not the server's PATH
    assert w.ran.calls[-1] == (["-version"], None) and srv.calls == [("func_list", {})]
    with pytest.raises(Refused, match="does not know"):
        await w.guard("shell", {"command": "x"}, srv)
    with pytest.raises(Refused, match="must be a string"):
        await w.guard("magick", {"command": ["a.png", "o.png"]}, srv)
    with pytest.raises(Refused, match="full path of a folder"):
        await w.guard("magick", {"command": "a.png o.png", "workspace": "work"}, srv)


FAKE_MAGICK = '''import json, os, sys
if "fail" in sys.argv:
    print("half done"); print("magick: no decode delegate", file=sys.stderr); sys.exit(1)
if "slow" in sys.argv:
    import time; time.sleep(30)
policy = os.path.join(os.environ.get("MAGICK_CONFIGURE_PATH", ""), "policy.xml")
print(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "env": sorted(os.environ),
                  "conf": os.environ.get("MAGICK_CONFIGURE_PATH"), "policy": open(policy).read()}))
'''


async def test_the_runner_starts_magick_under_the_policy_with_a_bare_environment(tmp_path: Path, monkeypatch):
    import sys

    from src.harness.magick_guard import MagickRunner, policy_xml

    script = tmp_path / "fake_magick.py"
    script.write_text(FAKE_MAGICK, encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-for-magick")
    monkeypatch.setenv("ZEN_GUI_KEY", "nor-this")
    conf, work = tmp_path / "vault-settings" / "magick", tmp_path / "work"
    work.mkdir()
    runner = MagickRunner(conf, binary=lambda: [sys.executable, str(script)], timeout=5)
    got = json.loads(await runner(["a.png", "-resize", "50%", "o.png"], work))
    assert got["argv"] == ["a.png", "-resize", "50%", "o.png"]  # an argument list, no shell
    assert os.path.realpath(got["cwd"]) == os.path.realpath(work)
    assert got["conf"] == str(conf) and got["policy"] == policy_xml()
    leaked = {k.upper() for k in got["env"]} - {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR",
                                                  "MAGICK_CONFIGURE_PATH"}
    assert not leaked, leaked  # no keys, nothing else
    (conf / "policy.xml").write_text("<policymap/>", encoding="utf-8")  # an edit is put back at the next run
    await runner(["x"], work)
    assert (conf / "policy.xml").read_text(encoding="utf-8") == policy_xml()

    with pytest.raises(RuntimeError, match=r"exited 1[\s\S]*half done[\s\S]*no decode delegate"):
        await runner(["fail"], work)
    with pytest.raises(RuntimeError, match="stopped"):
        await MagickRunner(conf, binary=lambda: [sys.executable, str(script)], timeout=0.5)(["slow"], work)
    with pytest.raises(RuntimeError, match="Settings > Harness Tools"):
        await MagickRunner(conf, binary=lambda: None)(["x"], work)


def _installed_magick() -> str | None:
    from src.harness.tool_paths import IMAGEMAGICK, find

    return find(IMAGEMAGICK)


REAL_MAGICK = _installed_magick()


@pytest.mark.skipif(REAL_MAGICK is None, reason="ImageMagick 7 is not installed (not on PATH or in its usual folders)")
async def test_real_magick_through_the_gate_and_under_the_policy(tmp_path: Path):
    """End to end with the installed ImageMagick, run by full path as the harness does: the gate passes plain
    edits, and the policy (not only the gate) stops the coders that read other files: a control run without
    the policy reads them."""
    import subprocess

    from src.harness.magick_guard import MagickRunner

    assert REAL_MAGICK is not None
    work, conf = tmp_path / "work", tmp_path / "vault-settings" / "magick"
    work.mkdir()
    (work / "secret.txt").write_text("TOP SECRET\n", encoding="utf-8")
    (work / "evil.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"/>', encoding="utf-8")
    runner = MagickRunner(conf, binary=lambda: [REAL_MAGICK])
    guard = MagickGuard(lambda: Folders(full=[work]), runner, functions=tmp_path / "fns")
    srv = Server()
    for cmd in ("-size 64x64 xc:steelblue base.png", "base.png -resize 50% -strip small.jpg",
                "-background white -pointsize 18 label:Hi label.png", "base.png doc.pdf"):
        await guard("magick", {"command": cmd, "workspace": str(work)}, srv)
    assert (await guard("magick", {"command": "identify -format %wx%h small.jpg", "workspace": str(work)}, srv)) == "32x32"
    assert raster_kind((work / "label.png").read_bytes()[:64]) == "png" and (work / "doc.pdf").read_bytes()[:5] == b"%PDF-"

    for args in (["text:secret.txt", "leak1.png"], ["evil.svg", "leak2.png"]):  # straight to the runner, past the gate
        with pytest.raises(RuntimeError, match="not authorized by the security policy"):
            await runner(args, work)
        assert subprocess.run([REAL_MAGICK, *args], cwd=work, capture_output=True).returncode == 0  # without the policy
    assert srv.calls == []


def test_the_policy_shuts_what_the_gate_keeps_out():
    import xml.etree.ElementTree as ET

    from src.harness.magick_guard import GENERATORS, OUT_ONLY, RASTER, policy_xml

    rows = [(p.get("domain"), p.get("rights"), p.get("pattern"), p.get("name"))
            for p in ET.fromstring(policy_xml()).iter("policy")]
    assert ("delegate", "none", "*", None) in rows  # no outside programs
    assert ("path", "none", "@*", None) in rows     # no @file reads
    coder = [(r, set(p.strip("{}").split(","))) for d, r, p, _ in rows if d == "coder"]
    assert coder[0] == ("none", {"*"})  # deny all first: for each domain the last matching rule wins
    allowed = {name for _, names in coder[1:] for name in names}
    assert {n.upper() for n in RASTER | GENERATORS | OUT_ONLY} <= allowed  # everything the gate lets through
    assert not allowed & {"TEXT", "MSL", "MVG", "SVG", "URL", "HTTPS", "EPHEMERAL", "PS", "EPS", "MPR"}
    writable = {n for r, names in coder[1:] if "write" in r for n in names}
    assert not writable & {n.upper() for n in GENERATORS - {"null"}}
    assert {"time", "memory", "width", "height", "area", "disk"} <= {n for d, _, _, n in rows if d == "resource"}


def test_raster_sniffing():
    assert raster_kind(PNG) == "png"
    assert raster_kind(b"\xff\xd8\xff\xe0") == "jpeg"
    assert raster_kind(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    assert raster_kind(b"P6\n1 1\n255\n") == "pnm"
    for not_raster in (SVG, b"%PDF-1.7", b"push graphic-context", b"<?xml", b"hello", b"PK\x03\x04"):
        assert raster_kind(not_raster) is None


async def test_the_registry_runs_a_named_guard_and_refuses_when_it_has_none(tmp_path: Path):
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({"mcpServers": {"m": {"command": "x", "guard": "magick"}, "plain": {"command": "y"}}}),
                 encoding="utf-8")
    cfgs = {c.name: c for c in load_config(f)}
    assert cfgs["m"].guard == "magick" and cfgs["plain"].guard == ""
    assert ServerConfig(name="x").guard == ""

    sent: list[tuple[str, dict]] = []

    class Session:
        async def call_tool(self, tool: str, args: dict) -> Any:
            sent.append((tool, args))
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="done")], is_error=False)

    def registry(guards) -> McpRegistry:
        reg = McpRegistry([cfgs["m"], cfgs["plain"]], guards=guards)
        for s in reg._servers.values():
            s.session = Session()
        return reg

    with pytest.raises(RuntimeError, match="no 'magick' guard"):  # named but missing: fail closed
        await registry({}).call("m", "magick", {"command": "a.png o.png"})
    assert sent == []
    assert await registry({}).call("plain", "anything", {}) == "done"  # unguarded servers are untouched

    async def gate(tool, args, call):
        if args.get("command") == "no":
            raise Refused("refused")
        return await call(tool, {**args, "seen": True})

    reg = registry({"magick": gate})
    assert await reg.call("m", "magick", {"command": "yes"}) == "done"
    with pytest.raises(Refused):
        await reg.call("m", "magick", {"command": "no"})
    assert sent == [("anything", {}), ("magick", {"command": "yes", "seen": True})]


def test_the_shipped_magick_entry_is_guarded():
    servers = {c.name: c for c in load_config(Path(__file__).parent.parent / "mcp.json")}
    assert servers["magick"].guard == "magick"
