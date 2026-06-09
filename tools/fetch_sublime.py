#!/usr/bin/env python3
"""
fetch_sublime.py — download + extract Package Control packages for a label into
a staging dir, so sublime2ac.py / sublime2udl.py can convert them.

Usage:
  python3 tools/fetch_sublime.py --channel /tmp/pc_channel.json \
      --label "auto-complete" --limit 40 --stage /tmp/stage_ac
"""
import json, os, sys, io, zipfile, argparse, urllib.request, re

def sanitize(name): return re.sub(r"[^A-Za-z0-9._-]", "_", name)

def pkgs_for_label(channel, label):
    out = []
    for _repo, pkgs in (channel.get("packages_cache") or {}).items():
        for p in pkgs:
            if label in (p.get("labels") or []):
                out.append(p)
    out.sort(key=lambda p: (p.get("name") or "").lower())
    return out

def download_zip(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "nextpad-fetch"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    zipfile.ZipFile(io.BytesIO(data)).extractall(dest)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--stage", required=True)
    args = ap.parse_args()

    channel = json.load(open(args.channel))
    pkgs = pkgs_for_label(channel, args.label)
    print("label %r: %d packages; fetching up to %d" % (args.label, len(pkgs), args.limit))
    os.makedirs(args.stage, exist_ok=True)

    ok = fail = 0
    manifest = {}
    for p in pkgs[:args.limit]:
        name = p.get("name") or ""
        rels = p.get("releases") or []
        url = rels[0].get("url") if rels else None
        if not url:
            continue
        dest = os.path.join(args.stage, sanitize(name))
        try:
            download_zip(url, dest)
            ok += 1
            manifest[name] = {"homepage": p.get("homepage", ""),
                              "authors": p.get("authors", []), "url": url}
        except Exception as e:
            fail += 1
            sys.stderr.write("  ! %s: %s\n" % (name, str(e)[:80]))
    json.dump(manifest, open(os.path.join(args.stage, "_manifest.json"), "w"), indent=1)
    print("fetched %d ok, %d failed → %s" % (ok, fail, args.stage))

if __name__ == "__main__":
    main()
