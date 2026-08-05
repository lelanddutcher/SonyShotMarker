#!/usr/bin/env python3
"""Package the Premiere panel for release.

Produces two artifacts from plugin/premiere-uxp:

  Sony-Shot-Marker-<version>.ccx  - the Adobe plugin package. A .ccx is a plain zip of the
                                   plugin folder, so this is also what you hand to the UXP
                                   Developer Tool or upload to Adobe Developer Distribution.
  Sony-Shot-Marker-<version>.zip  - the same payload plus the install scripts, for people
                                   side-loading without Creative Cloud.

Both are built deterministically (sorted entries, fixed timestamps) so the same source
always produces byte-identical archives - which means a release can be verified by hash.

    python3 plugin/package_plugin.py [--out dist/plugin]
"""
import argparse
import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "premiere-uxp")

# Everything the panel needs at runtime, and nothing else. The icon generator, notes and
# any editor litter stay out of the shipped package.
PAYLOAD = ["manifest.json", "index.html", "styles.css", "src", "icons"]
EXTRAS = ["install_premiere_macos.sh", "install_premiere_windows.ps1", "README.md"]

FIXED_DATE = (2026, 1, 1, 0, 0, 0)   # deterministic archives; see module docstring


def collect(root, names):
    """Return sorted (absolute path, archive-relative path) pairs."""
    out = []
    for name in names:
        p = os.path.join(root, name)
        if os.path.isfile(p):
            out.append((p, name))
        elif os.path.isdir(p):
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames.sort()
                for fn in sorted(filenames):
                    if fn == ".DS_Store":
                        continue
                    full = os.path.join(dirpath, fn)
                    out.append((full, os.path.relpath(full, root)))
    return sorted(out, key=lambda t: t[1].replace(os.sep, "/"))


def write_zip(path, entries, prefix=""):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for full, rel in entries:
            arc = (prefix + rel).replace(os.sep, "/")
            info = zipfile.ZipInfo(arc, date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(full, "rb") as fh:
                z.writestr(info, fh.read())
    return path


ALLOWED_CONTROL = {0x09, 0x0a, 0x0d}   # tab, LF, CR


def verify_printable_ascii(entries):
    """Refuse to ship a byte UXP's parser will reject.

    UXP fails on these in the worst possible way: "SyntaxError: Invalid or unexpected
    token" at a position that looks perfectly fine, the module never defines itself, and
    every control in the panel goes dead with nothing on screen to explain it. Two
    different bytes have caused exactly that here:

      * a NUL (0x00) inside a string literal. JavaScript allows NUL in strings, so Node
        parses the file and the test suite passes green while Premiere refuses to load it.
        That is why this checks the low bytes too, not only the high ones.
      * an em dash in a comment, because UXP does not decode sources as UTF-8.

    Cheap check, expensive bug. It runs on every package.
    """
    bad = []
    for full, rel in entries:
        if not rel.endswith((".js", ".html", ".css", ".json")):
            continue
        data = open(full, "rb").read()
        for i, b in enumerate(data):
            if (0x20 <= b <= 0x7e) or b in ALLOWED_CONTROL:
                continue
            line = data[:i].count(b"\n") + 1
            col = i - (data.rfind(b"\n", 0, i) + 1) + 1
            bad.append("%s:%d:%d (byte 0x%02x)" % (rel, line, col, b))
            break
    if bad:
        raise SystemExit(
            "non-printable bytes in shipped plugin sources (UXP will refuse these):\n  "
            + "\n  ".join(bad))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(HERE, "..", "dist", "plugin"),
                    help="output directory (default: dist/plugin)")
    args = ap.parse_args(argv)

    manifest_path = os.path.join(SRC, "manifest.json")
    if not os.path.isfile(manifest_path):
        raise SystemExit("no manifest.json under %s" % SRC)
    manifest = json.load(open(manifest_path))
    version = manifest["version"]

    payload = collect(SRC, PAYLOAD)
    if not payload:
        raise SystemExit("nothing to package")
    verify_printable_ascii(payload)

    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    stem = "Sony-Shot-Marker-%s" % version

    # .ccx: the plugin folder's contents at the archive root, which is what Adobe expects.
    ccx = write_zip(os.path.join(out, stem + ".ccx"), payload)

    # .zip: the same payload under a named folder, plus the manual install scripts.
    extras = [(os.path.join(HERE, n), n) for n in EXTRAS if os.path.isfile(os.path.join(HERE, n))]
    bundle = [(f, "Sony Shot Marker/" + r) for f, r in payload] + extras
    zip_path = write_zip(os.path.join(out, stem + ".zip"), bundle)

    # The DaVinci Resolve panel is one self-contained file plus its installers.
    resolve_src = os.path.join(HERE, "resolve", "SonyShotMarker.py")
    resolve_zip = None
    if os.path.isfile(resolve_src):
        resolve_entries = [(resolve_src, "Sony Shot Marker for Resolve/SonyShotMarker.py")]
        for extra in ("install_resolve_macos.sh", "install_resolve_windows.ps1"):
            p = os.path.join(HERE, extra)
            if os.path.isfile(p):
                resolve_entries.append((p, "Sony Shot Marker for Resolve/" + extra))
        resolve_zip = write_zip(
            os.path.join(out, "Sony-Shot-Marker-Resolve-%s.zip" % version), resolve_entries)

    print("Sony Shot Marker %s  (%s, host %s %s+)" % (
        version, manifest["id"], manifest["host"]["app"], manifest["host"]["minVersion"]))
    for p in (ccx, zip_path, resolve_zip):
        if p:
            print("  %-52s %8.1f KB" % (os.path.relpath(p, os.getcwd()),
                                        os.path.getsize(p) / 1024.0))
    print("  Premiere: %d files, all ASCII-clean" % len(payload))
    print("  Note: for an Adobe Developer Distribution submission, use the UXP Developer")
    print("        Tool's Package button - it emits %s_premierepro.ccx, the same payload"
          % manifest["id"])
    print("        under the name Adobe's validator expects.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
