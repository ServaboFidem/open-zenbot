"""Layer 1 of the magick MCP server's guard: a fixed, rule-based gate on every call, before the server sees it.

magick-mcp runs `magick <command>` for any command string, in any working directory. ImageMagick reads and
writes any path it is given, and some inputs read further files (`text:`, `@file`, SVG/MVG/MSL content,
`-profile`), so unguarded it would ignore Settings > Allowed Folders. This gate holds it to the files server's
rules:

- every path a command reads is inside an allowed folder (Full Access or Read-Only); every path it writes is
  inside a Full Access one (the vault, ZEN_FS_ROOT, a Full Access folder) and not inside a Read-Only one;
  nothing in the harness's own settings, ledger or music folders either way. Symlinks are resolved first.
- the command is split exactly as the server splits it (on whitespace; no quotes, no shell: magick-mcp hands
  the pieces straight to `magick`), so the gate and ImageMagick see the same arguments.
- options are an allow-list with their argument counts; anything else is refused, `-draw` and `-script`
  included. A value that looks like a file where none is expected is refused.
- `@file` arguments, `|pipe` names and every `coder:` prefix except image generators (`xc:`, `label:`...) and
  raster formats (`png:`...) are refused. Input files must be raster images by their first bytes (SVG, MVG,
  MSL, PDF and text can make ImageMagick read other files); outputs must be raster formats (others can run
  delegate programs).
- the gate runs `magick` itself (MagickRunner), not magick-mcp: the server clears the environment of the
  `magick` it starts, so nothing the harness set could reach it. Run here, `magick` gets the harness's
  policy.xml (policy_xml(): ImageMagick's own deny-lists and resource limits, a second wall behind this
  gate) through MAGICK_CONFIGURE_PATH, from vault-settings/magick/, where bots cannot write.
- `func_save` and `func_execute` names are plain slugs (the server builds a file path from them, unchecked).
  `func_save` commands are checked for everything but paths; `func_execute` is run by the gate itself: it
  reads the saved function, fills in `$input`, checks every command's rules before any runs, then checks
  each command's paths just before running exactly that command (a later command may read what an earlier
  one wrote). What runs is the argument list that was checked: nothing changes between the check and the run.

Layer 2 (the decider judging whether an allowed command fits the task) comes later. This layer only refuses."""

from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import shutil
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .allowed_folders import Folders  # what the gate checks paths against, read fresh on every call


class Refused(RuntimeError):
    """A magick call the harness will not run; the message is for the bot."""


Next = Callable[[str, dict[str, Any]], Awaitable[str]]
Run = Callable[[list[str], "Path | None"], Awaitable[str]]  # (magick's arguments, working folder) -> its output

SUBCOMMANDS = {"identify", "mogrify", "montage", "composite", "compare"}
DENIED_SUBCOMMANDS = {"magick", "convert", "import", "conjure", "stream", "display", "animate", "script"}

# option -> the kinds of the arguments that follow it: "in" (an input file), "out" (an output file), "dir"
# (an output folder), "font", "profile", "text" (never a file, '@...' refused), "color", "define", "set",
# "tile", or "value" (geometry, numbers, names). -opt and +opt share a row unless PLUS says otherwise.
_V, _T, _C = "value", "text", "color"
OPTIONS: dict[str, tuple[str, ...]] = {
    **{o: () for o in (
        "strip", "flip", "flop", "trim", "auto-orient", "negate", "normalize", "auto-level", "auto-gamma", "equalize",
        "monochrome", "enhance", "despeckle", "flatten", "append", "coalesce", "magnify", "minify", "verbose", "ping",
        "transpose", "transverse", "mosaic", "separate", "combine", "reverse", "respect-parentheses", "antialias",
        "quiet", "regard-warnings", "adjoin", "deconstruct", "identify", "clamp", "contrast", "unique-colors",
        "version", "synchronize", "taint", "screen", "matte")},
    **{o: (_V,) for o in (
        "resize", "thumbnail", "scale", "sample", "adaptive-resize", "resample", "density", "units", "crop", "shave",
        "chop", "extent", "border", "gravity", "geometry", "compose", "rotate", "frame", "shadow", "mode", "page",
        "repage", "extract", "sharpen", "unsharp", "blur", "gaussian-blur", "charcoal", "sketch", "sepia-tone",
        "posterize", "swirl", "implode", "wave", "vignette", "modulate", "brightness-contrast", "contrast-stretch",
        "level", "gamma", "colorspace", "type", "depth", "colors", "quality", "sampling-factor", "interlace",
        "pointsize", "strokewidth", "threshold", "white-threshold", "black-threshold", "fuzz", "alpha", "channel",
        "delay", "loop", "dispose", "layers", "size", "auto-threshold", "kuwahara", "edge", "emboss", "shade",
        "raise", "spread", "noise", "splice", "roll", "affine", "compress", "endian", "filter", "interpolate",
        "virtual-pixel", "precision", "grayscale", "dither", "clone", "delete", "insert", "duplicate", "swap",
        "orient", "lat", "median", "paint", "solarize", "fx", "cycle", "blue-shift", "adaptive-blur",
        "adaptive-sharpen", "motion-blur", "radial-blur", "rotational-blur", "selective-blur", "mean-shift",
        "segment", "linear-stretch", "sigmoidal-contrast", "tint", "colorize", "intensity", "metric")},
    **{o: (_C,) for o in ("fill", "stroke", "background", "bordercolor", "mattecolor", "transparent", "opaque",
                          "highlight-color", "lowlight-color")},
    **{o: (_T,) for o in ("label", "title", "comment", "format", "print", "caption", "family")},
    "define": ("define",), "font": ("font",), "profile": ("profile",), "tile": ("tile",),
    "write": ("out",), "read": ("in",), "path": ("dir",), "texture": ("in",),
    "annotate": (_V, _T), "set": ("set", _T),
    **{o: (_V, _V) for o in ("distort", "evaluate", "function", "statistic", "morphology", "limit")},
}
PLUS: dict[str, tuple[str, ...]] = {"profile": (_T,), "clone": (), "delete": (), "swap": (), "repage": (),
                                    "noise": (_V,), "dither": (), "set": (_T,), "define": (_T,)}
NO_PATH_CHECK = {"fx"}  # its expressions use '/' for division

# pseudo-images ImageMagick draws itself: fine as inputs (label:/caption: only with plain text, never '@file')
GENERATORS = {"xc", "canvas", "gradient", "radial-gradient", "pattern", "plasma", "label", "caption", "null",
              "logo", "rose", "wizard", "granite", "netscape", "fractal", "hald"}
# raster formats: as a `png:` prefix or an output extension. ImageMagick decodes and encodes these itself.
RASTER = {"png", "png8", "png24", "png32", "png48", "png64", "png00", "jpg", "jpeg", "gif", "webp", "tif", "tiff",
          "bmp", "bmp2", "bmp3", "ico", "heic", "avif", "jxl", "ppm", "pgm", "pbm", "pnm", "tga", "psd", "jp2", "qoi",
          "miff"}
OUT_ONLY = {"pdf", "info", "txt"}  # written without delegates, never read back as inputs here
DEFINE_PREFIXES = ("jpeg:", "png:", "webp:", "heic:", "avif:", "tiff:", "gif:", "jxl:", "bmp:", "quantum:", "trim:",
                   "distort:", "compose:", "filter:", "morphology:", "colorspace:", "icc:", "pdf:", "connected-components:")
SLUG = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CODER = re.compile(r"^([A-Za-z0-9][A-Za-z0-9-]+):(.*)$")  # two or more characters: never a drive letter
_DRIVE_RELATIVE = re.compile(r"^[A-Za-z]:(?![/\\])")      # C: or C:x, relative to that drive's own cwd
_FRAMES = re.compile(r"\[[^\]]*\]$")                       # img.gif[0], big.jpg[640x480]: read hints
_GEOMETRY = re.compile(r"^[0-9x+\-%.!<>^@]*$")
_UNSAFE = set("\"'`$;&|<>{}~")  # shell metacharacters reach delegate command lines; '~' would expand to home


def raster_kind(head: bytes) -> str | None:
    """The image format by its first bytes, for the formats an input may be; None for anything else."""
    for sig, kind in ((b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpeg"), (b"GIF87a", "gif"), (b"GIF89a", "gif"),
                      (b"II*\x00", "tiff"), (b"MM\x00*", "tiff"), (b"BM", "bmp"), (b"\x00\x00\x01\x00", "ico"),
                      (b"8BPS", "psd"), (b"qoif", "qoi"), (b"\xff\x0a", "jxl"), (b"\x00\x00\x00\x0cJXL ", "jxl"),
                      (b"\x00\x00\x00\x0cjP  \r\n\x87\n", "jp2"), (b"id=ImageMagick", "miff")):
        if head.startswith(sig):
            return kind
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"avif", b"avis", b"hevc"):
        return "heif"
    if head[:1] == b"P" and head[1:2] in b"123456" and head[2:3] in (b"\n", b" ", b"\r", b"\t"):
        return "pnm"
    return None


def _is_font(head: bytes) -> bool:
    return head[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf", b"wOFF", b"wOF2")


def _is_icc(head: bytes) -> bool:
    return head[36:40] == b"acsp"


def functions_dir() -> Path:
    """Where magick-mcp keeps saved functions: Rust's `dirs::data_dir()/magick-mcp/functions`."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        xdg = os.environ.get("XDG_DATA_HOME", "")
        base = Path(xdg) if xdg and Path(xdg).is_absolute() else Path.home() / ".local" / "share"
    return base / "magick-mcp" / "functions"


def _real(p: Path) -> Path:
    try:
        return Path(os.path.realpath(p))
    except (OSError, ValueError):
        return p


def _inside(p: Path, roots: list[Path]) -> bool:
    return any(p == r or p.is_relative_to(r) for r in roots)


@dataclass
class _Roots:
    full: list[Path]
    read_only: list[Path]
    private: list[Path]


class MagickGuard:
    """The gate. `folders()` is read on every call. `run(args, cwd)` runs `magick` with the checked arguments:
    MagickRunner, which applies the harness's policy.xml. magick-mcp never runs a command itself (it clears
    the environment, so no policy could reach its `magick`); it lists the tools and keeps saved functions."""

    def __init__(self, folders: Callable[[], Folders], run: Run, functions: Path | None = None) -> None:
        self.folders = folders
        self.run = run
        self.functions = functions or functions_dir()

    # -- the MCP surface ----------------------------------------------------

    async def __call__(self, tool: str, args: dict[str, Any], call: Next) -> str:
        if tool == "func_list":
            return await call(tool, args)
        if tool == "check":  # the server would look on PATH; the harness runs the ImageMagick in Settings
            return await self.run(["-version"], None)
        if tool == "magick":
            base = self._workspace(args.get("workspace"))
            return await self.run(self.check(_str(args.get("command")), base), base)
        if tool == "func_save":
            self._name(args.get("name"))
            commands = args.get("commands")
            if not isinstance(commands, list) or not commands or not all(isinstance(c, str) for c in commands):
                raise Refused("commands must be a non-empty list of command strings")
            for c in commands:  # $input stands in as a plain file name: the real one is checked when it runs
                self.check(c.replace("$input", "input.png"), None, saving=True)
            return await call(tool, args)
        if tool == "func_execute":
            return await self._execute(args)
        raise Refused(f"the harness does not know the magick tool {tool!r}, so it does not run it")

    async def _execute(self, args: dict[str, Any]) -> str:
        name = self._name(args.get("name"))
        base = self._workspace(args.get("workspace"))
        try:
            saved = json.loads((self.functions / f"{name}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise Refused(f"there is no saved magick function named {name}") from None
        commands = saved.get("commands") if isinstance(saved, dict) else None
        if not isinstance(commands, list) or not commands or not all(isinstance(c, str) for c in commands):
            raise Refused(f"the saved function {name} is not a list of commands")
        value = args.get("input")
        ready = []
        for c in commands:
            if "$input" in c:
                if not isinstance(value, str) or not value.strip():
                    raise Refused(f"{name} uses $input; give an input")
                c = c.replace("$input", value)  # as the server does it: a plain string replace
            self.check(c, base, saving=True)  # every rule but the paths, for all of them before any runs
            ready.append(c)
        outs = []
        for c in ready:  # paths one command at a time: a later one may read what an earlier one wrote
            outs.append(await self.run(self.check(c, base), base))
        return "\n".join(o for o in outs if o)

    def _name(self, name: Any) -> str:
        if not isinstance(name, str) or not SLUG.match(name):
            raise Refused("a function name is 1 to 64 letters, digits, '-' or '_'")
        return name

    def _workspace(self, ws: Any) -> Path | None:
        if ws in (None, ""):
            return None
        if not isinstance(ws, str) or not Path(ws).is_absolute() or _DRIVE_RELATIVE.match(ws):
            raise Refused("workspace must be the full path of a folder")
        p = _real(Path(ws))
        if not p.is_dir():
            raise Refused(f"workspace {ws} is not a folder")
        self._roots = self._load()
        self._may_read(p, ws)
        return p

    # -- the command --------------------------------------------------------

    def check(self, command: str, base: Path | None, *, saving: bool = False) -> list[str]:
        """The arguments to run, or Refused unless every one is allowed. `base` resolves relative paths (no
        base: full paths only). `saving` checks a command for a saved function: every rule but the paths, which
        are checked when it runs, with `$input` filled in."""
        self._roots = self._load()
        self._base, self._saving = base, saving
        tokens = command.split()  # exactly the server's split_whitespace
        if not tokens:
            raise Refused("an empty command")
        first = tokens[0].lower()
        if first in DENIED_SUBCOMMANDS:
            raise Refused(f"'{tokens[0]}' is not allowed: leave out 'magick' (the server adds it); the tools allowed"
                          f" first are {', '.join(sorted(SUBCOMMANDS))}, or none")
        sub = first if first in SUBCOMMANDS else None
        rest = tokens[1:] if sub else tokens
        positional: list[str] = []
        outputs: list[str] = []
        dirs_out: list[str] = []
        fmt: str | None = None
        i = 0
        while i < len(rest):
            t = rest[i]
            if t in ("(", ")"):
                i += 1
                continue
            if t[:1] in "-+" and len(t) > 1 and not (t[1].isdigit() or t[1] == "."):
                name = t[1:].lower()
                kinds = PLUS.get(name, OPTIONS.get(name)) if t[0] == "+" else OPTIONS.get(name)
                if kinds is None:
                    raise Refused(f"the harness does not allow the option {t}")
                vals = rest[i + 1:i + 1 + len(kinds)]
                if len(vals) < len(kinds):
                    raise Refused(f"{t} needs {len(kinds)} value(s)")
                for kind, v in zip(kinds, vals):
                    self._value(t, name, kind, v, outputs, dirs_out)
                if name == "format" and kinds:
                    fmt = vals[0]
                i += 1 + len(kinds)
                continue
            positional.append(t)
            i += 1
        if sub == "identify":
            for t in positional:
                self._input(t)
        elif sub == "mogrify":
            if fmt is not None and fmt.lower() not in RASTER:
                raise Refused(f"mogrify -format {fmt}: write a raster format ({', '.join(sorted(RASTER))})")
            for t in positional:
                for p in self._input(t):
                    if not dirs_out:  # in place, unless -path names the output folder
                        self._may_write(p, t)
        else:
            if len(positional) < 2:
                raise Refused("name an input (a file or a generator like xc:white), then the output file last")
            for t in positional[:-1]:
                self._input(t)
            outputs.append(positional[-1])
        for t in outputs:
            self._output(t)
        for t in dirs_out:
            self._file(t, write=True, what="-path folder", is_dir=True)
        return tokens

    def _value(self, opt: str, name: str, kind: str, v: str, outs: list[str], dirs: list[str]) -> None:
        if v.startswith(("@", "|")):
            raise Refused(f"{opt} {v}: '@' and '|' values read files or run commands")
        if kind == "in":
            self._input(v)
        elif kind == "out":
            outs.append(v)
        elif kind == "dir":
            dirs.append(v)
        elif kind == "profile":
            self._file(v, write=False, what="color profile", sniff=_is_icc)
        elif kind == "font":
            if any(s in v for s in "/\\.") or _DRIVE_RELATIVE.match(v):  # a file, not a font name
                self._file(v, write=False, what="font", sniff=_is_font)
        elif kind == "tile":
            if not _GEOMETRY.match(v):
                raise Refused(f"{opt} {v}: only a tile layout like 3x2 is allowed; use -texture for an image")
        elif kind == "color":
            if any(s in v for s in ":/\\"):
                raise Refused(f"{opt} {v} is not a color")
        elif kind == "define":
            if not v.lower().startswith(DEFINE_PREFIXES):
                raise Refused(f"-define {v}: only format settings ({', '.join(p.rstrip(':') for p in DEFINE_PREFIXES)})")
        elif kind == "set":
            key = v.lower()
            if key == "profile" or key.startswith(("filename:", "icc", "icm", "iptc", "xmp", "8bim")):
                raise Refused(f"-set {v}: that key reads a file or names outputs at run time")
        elif kind == _V and name not in NO_PATH_CHECK:
            if any(s in v for s in "/\\") or _DRIVE_RELATIVE.match(v) or (self._base and (self._base / v).exists()):
                raise Refused(f"{opt} {v}: that looks like a file, not a value; name files as inputs or outputs")

    def _coder(self, t: str) -> tuple[str | None, str]:
        m = _CODER.match(t)
        return (m.group(1).lower(), m.group(2)) if m else (None, t)

    def _input(self, t: str) -> list[Path]:
        if t.startswith(("@", "|")) or t == "-":
            raise Refused(f"{t}: the harness refuses '@' lists, '|' pipes and standard input")
        coder, rest = self._coder(t)
        if coder in GENERATORS:
            if "@" in rest or "%[" in rest:
                raise Refused(f"{t}: '@' would read a file; write the text itself")
            return []
        if coder is not None and coder not in RASTER:
            raise Refused(f"{t}: '{coder}:' inputs are not allowed (they can read other files or the network)")
        name = _FRAMES.sub("", rest)
        if "%" in name:
            raise Refused(f"{t}: '%' in an input name reads a numbered series; name each file")
        return self._file(name, write=False, what="input", sniff=lambda h: raster_kind(h) is not None, glob_ok=True)

    def _output(self, t: str) -> None:
        if t.startswith(("@", "|")):
            raise Refused(f"{t}: the harness refuses '@' files and '|' pipes")
        if t in ("-", "null:", "info:", "info:-"):
            return  # standard output (the server returns it), or nothing
        coder, rest = self._coder(t)
        kind = coder if coder is not None else Path(rest).suffix.lower().lstrip(".")
        if kind not in RASTER | OUT_ONLY:
            raise Refused(f"{t}: write a raster image ({', '.join(sorted(RASTER))}), pdf or txt"
                          + ("" if kind else "; give the file an extension"))
        if "%[" in rest or "[" in rest:
            raise Refused(f"{t}: names built at run time are not allowed; name the file")
        self._file(rest, write=True, what="output")

    def _file(self, raw: str, *, write: bool, what: str, sniff: Callable[[bytes], bool] | None = None,
              glob_ok: bool = False, is_dir: bool = False) -> list[Path]:
        if not raw:
            raise Refused(f"an empty {what} name")
        if any(c in _UNSAFE for c in raw):
            raise Refused(f"{raw}: quotes and {''.join(sorted(_UNSAFE))} are not allowed in file names")
        if _DRIVE_RELATIVE.match(raw):
            raise Refused(f"{raw}: a drive-relative path; give the full path")
        if ".." in Path(raw).parts:
            raise Refused(f"{raw}: '..' is not allowed in paths")
        wild = any(c in raw for c in "*?")
        if wild and (write or not glob_ok):
            raise Refused(f"{raw}: wildcards are only allowed in input names")
        if self._saving:
            return []  # paths are checked when the function runs
        p = Path(raw)
        if not p.is_absolute():
            if self._base is None:
                raise Refused(f"{raw}: give a full path, or a workspace for relative ones")
            p = self._base / p
        if wild:
            found = [Path(m) for m in glob.glob(str(p)) if Path(m).is_file()]
            if not found:
                raise Refused(f"{raw}: no files match")
            return [self._one(m, raw, write=False, what=what, sniff=sniff) for m in found]
        return [self._one(p, raw, write=write, what=what, sniff=sniff, is_dir=is_dir)]

    def _one(self, p: Path, raw: str, *, write: bool, what: str, sniff: Callable[[bytes], bool] | None,
             is_dir: bool = False) -> Path:
        if write:
            real = _real(p) if p.exists() else _real(p.parent) / p.name
            self._may_write(real, raw)
            if is_dir and not real.is_dir():
                raise Refused(f"{raw}: not a folder")
            return real
        real = _real(p)
        self._may_read(real, raw)
        if not real.is_file():
            raise Refused(f"{raw}: no such {what} file")
        if sniff is not None:
            try:
                with open(real, "rb") as f:
                    head = f.read(64)
            except OSError:
                raise Refused(f"{raw}: cannot be read") from None
            if not sniff(head):
                raise Refused(f"{raw}: not a raster image ImageMagick may read here (SVG, MVG, MSL, PDF and text can"
                              " make it read other files)" if what == "input" else f"{raw}: not a {what} file")
        return real

    # -- folders ------------------------------------------------------------

    def _load(self) -> _Roots:
        f = self.folders()
        return _Roots([_real(Path(x)) for x in f.full], [_real(Path(x)) for x in f.read_only],
                      [_real(Path(x)) for x in f.private])

    def _may_read(self, p: Path, raw: str) -> None:
        r = self._roots
        if _inside(p, r.private):
            raise Refused(f"{raw}: that is the harness's own folder; bots never reach it")
        if not _inside(p, [*r.full, *r.read_only]):
            raise Refused(f"{raw}: outside the allowed folders (Settings > Allowed Folders)")

    def _may_write(self, p: Path, raw: str) -> None:
        self._may_read(p, raw)
        if _inside(p, self._roots.read_only):
            raise Refused(f"{raw}: inside a Read-Only folder; write to a Full Access folder")
        if not _inside(p, self._roots.full):
            raise Refused(f"{raw}: not in a Full Access folder")


def _str(v: Any) -> str:
    if not isinstance(v, str):
        raise Refused("command must be a string")
    return v


# -- policy.xml: ImageMagick's own limits, under the gate ----------------------------------------------------

# coder names as ImageMagick knows them, beyond the gate's lower-case lists (aliases one module registers)
_ALIASES = {"jpg": ["JPEG"], "tif": ["TIFF"], "tiff": ["TIF", "TIFF64", "PTIF"], "ico": ["ICON"], "heic": ["HEIF"],
            "pnm": ["PAM"], "jp2": ["J2K", "JPC"]}
RESOURCES = {"memory": "1GiB", "map": "2GiB", "disk": "4GiB", "width": "16KP", "height": "16KP", "area": "256MP",
             "list-length": "512", "time": "120"}
TIMEOUT = 150.0  # seconds; past the policy's own time limit, so ImageMagick normally stops itself first


def _coders(names: set[str]) -> str:
    out = {n.upper() for n in names} | {a for n in names for a in _ALIASES.get(n, [])}
    return "{" + ",".join(sorted(out)) + "}"


def policy_xml() -> str:
    """The policy the harness runs `magick` under. ImageMagick reads it after its built-in defaults; for each
    domain the last matching rule wins, so each deny-all comes before what it lets back in. It stands behind
    the gate, not in place of it: paths are left to the gate (Windows paths and ImageMagick's glob patterns
    do not mix), and it shuts what the gate keeps out anyway: other coders, delegates (outside programs),
    `@file` reads, and the script modules."""
    rows = [f'  <policy domain="resource" name="{k}" value="{v}"/>' for k, v in RESOURCES.items()]
    rows += [
        '  <policy domain="system" name="max-memory-request" value="256MiB"/>',
        '  <policy domain="delegate" rights="none" pattern="*"/>',
        '  <policy domain="filter" rights="none" pattern="*"/>',
        '  <policy domain="path" rights="none" pattern="@*"/>',
        '  <policy domain="module" rights="none" pattern="{MSL,MVG,PS,SVG,URL,XPS,EPHEMERAL,TEXT,HTML}"/>',
        '  <policy domain="coder" rights="none" pattern="*"/>',
        f'  <policy domain="coder" rights="read|write" pattern="{_coders(RASTER | {"null"})}"/>',
        f'  <policy domain="coder" rights="read" pattern="{_coders(GENERATORS - {"null"} | {"icc", "icm"})}"/>',
        f'  <policy domain="coder" rights="write" pattern="{_coders(OUT_ONLY)}"/>',
    ]
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            "<!-- Written by the zen-agents harness (src/harness/magick_guard.py) at each magick call; edits are"
            " overwritten. -->\n<policymap>\n" + "\n".join(rows) + "\n</policymap>\n")


# what `magick` keeps of the harness's environment: enough to start and find temp space and fonts; no keys
_KEEP_ENV = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR")


class MagickRunner:
    """Runs `magick` with checked arguments, as magick-mcp would (no shell, the same argument list, the
    workspace as the working folder, stdout back, a failure raised with stdout and stderr), with two changes:
    MAGICK_CONFIGURE_PATH points at `policy_dir`, where policy.xml is (re)written before every run, and the
    environment is the few variables in _KEEP_ENV. Keep `policy_dir` where bots cannot write (vault-settings)."""

    def __init__(self, policy_dir: Path, binary: Callable[[], list[str] | None] | None = None,
                 timeout: float = TIMEOUT) -> None:
        self.policy_dir = policy_dir
        self.binary = binary or (lambda: [b] if (b := shutil.which("magick")) else None)
        self.timeout = timeout

    def write_policy(self) -> Path:
        self.policy_dir.mkdir(parents=True, exist_ok=True)
        p = self.policy_dir / "policy.xml"
        text = policy_xml()
        try:
            if p.read_text(encoding="utf-8") == text:
                return p
        except OSError:
            pass
        p.write_text(text, encoding="utf-8")
        return p

    async def __call__(self, args: list[str], cwd: Path | None) -> str:
        exe = self.binary()
        if not exe:
            raise RuntimeError("ImageMagick is not set up: the owner sets its path in Settings > Programs,"
                               " or runs scripts/install-deps")
        self.write_policy()
        env = {k: v for k in _KEEP_ENV if (v := os.environ.get(k))}
        env["MAGICK_CONFIGURE_PATH"] = str(self.policy_dir)
        proc = await asyncio.create_subprocess_exec(
            *exe, *args, cwd=str(cwd or self.policy_dir), env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"magick ran past {self.timeout:.0f}s and was stopped") from None
        stdout, stderr = out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
        if proc.returncode:
            raise RuntimeError(f"magick exited {proc.returncode}\nstdout: {stdout}\nstderr: {stderr}")
        return stdout
