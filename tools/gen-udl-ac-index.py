#!/usr/bin/env python3
"""
Generate udl-ac-index.json — the single catalog the Nextpad++ UDL Admin reads.

For each udl-list.json entry it resolves the UDL + AC files, reads the UDL's
<UserLang name> as the language key (= EditorView.currentLanguage = the
"<language>.d/" install folder), computes sha256 + byte size per file, and emits
a flat list of per-language entries with a 1:N autoComplete[] array.

Run from anywhere:  python3 tools/gen-udl-ac-index.py
Writes:             <repo>/udl-ac-index.json
"""
import json, os, sys, hashlib, datetime
import xml.etree.ElementTree as ET

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # repo root (tools/..)

def sha256_and_size(abspath):
    h = hashlib.sha256()
    with open(abspath, "rb") as f:
        data = f.read()
    h.update(data)
    return h.hexdigest(), len(data)

def file_asset(rel):
    """Return {file, sha256, bytes} for a repo-relative path, or None if absent."""
    ap = os.path.join(REPO, rel)
    if not os.path.isfile(ap):
        return None
    digest, size = sha256_and_size(ap)
    return {"file": rel.replace(os.sep, "/"), "sha256": digest, "bytes": size}

def find_in_dir(folder, stem):
    """Resolve '<folder>/<stem>.xml' case-insensitively; return repo-relative path or None."""
    exact = os.path.join(folder, stem + ".xml")
    if os.path.isfile(os.path.join(REPO, exact)):
        return exact
    want = (stem + ".xml").lower()
    d = os.path.join(REPO, folder)
    if os.path.isdir(d):
        for n in os.listdir(d):
            if n.lower() == want:
                return os.path.join(folder, n)
    return None

def userlang_name(udl_rel):
    """Read <UserLang name="..."> from a UDL file; None on failure."""
    try:
        for _ev, el in ET.iterparse(os.path.join(REPO, udl_rel), events=("start",)):
            if el.tag.split("}")[-1] == "UserLang":
                return el.get("name")
    except Exception:
        return None
    return None

def main():
    src = json.load(open(os.path.join(REPO, "udl-list.json"), encoding="utf-8"))
    langs = []
    warnings = []

    for e in src.get("UDLs", []):
        idname  = e.get("id-name") or ""
        display = e.get("display-name") or idname
        if not idname:
            continue

        # ── UDL ──────────────────────────────────────────────────────────────
        udl_rel = find_in_dir("UDLs", idname)
        udl = None
        language = display          # fallback if we can't read the UDL's own name
        repo_url = e.get("repository") or ""
        if udl_rel:
            udl = file_asset(udl_rel)
            nm = userlang_name(udl_rel)
            if nm:
                language = nm       # the authoritative key = <UserLang name>
        elif repo_url.startswith("http"):
            udl = {"url": repo_url}             # externally hosted UDL
        else:
            warnings.append(f"{idname}: no UDL file and no repository URL")

        # ── AutoComplete (1:N array; community catalog is 0..1 today) ─────────
        ac_list = []
        acv = e.get("autoCompletion")
        if acv is True:
            acv = idname                         # 'true' → same stem as the UDL id
        if isinstance(acv, str) and acv:
            if acv.startswith("http"):
                ac_list.append({"url": acv,
                                "author": e.get("autoCompletionAuthor", ""),
                                "source": "community"})
            else:
                ac_rel = find_in_dir("autoCompletion", acv)
                if ac_rel:
                    asset = file_asset(ac_rel)
                    asset.update({"author": e.get("autoCompletionAuthor", ""),
                                  "source": "community"})
                    ac_list.append(asset)
                else:
                    warnings.append(f"{idname}: autoCompletion '{acv}' has no file")

        # ── optional sample / functionList ──────────────────────────────────
        sample = None
        sv = e.get("sample")
        if isinstance(sv, str) and sv and os.path.isfile(os.path.join(REPO, "UDL-samples", sv)):
            sample = "UDL-samples/" + sv
        fl = None
        flv = e.get("functionList")
        if flv is True:
            flv = idname
        if isinstance(flv, str) and flv:
            fl_rel = find_in_dir("functionList", flv)
            if fl_rel:
                fl = file_asset(fl_rel)

        langs.append({
            "id":          idname,
            "language":    language,
            "displayName": display,
            "source":      "community",
            "author":      e.get("author", ""),
            "description": e.get("description", ""),
            "version":     e.get("version", ""),
            "udl":         udl,
            "autoComplete": ac_list,
            "sample":      sample,
            "functionList": fl,
        })

    langs.sort(key=lambda x: (x["language"].lower(), x["id"].lower()))
    index = {
        "name": "nextpad-udl-ac-index",
        "version": 1,
        "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(langs),
        "languages": langs,
    }
    out = os.path.join(REPO, "udl-ac-index.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=1, ensure_ascii=False)
        f.write("\n")

    with_udl = sum(1 for l in langs if l["udl"])
    with_ac  = sum(1 for l in langs if l["autoComplete"])
    print(f"wrote {out}")
    print(f"  {len(langs)} languages | {with_udl} with UDL | {with_ac} with AC | {len(warnings)} warnings")
    for w in warnings[:15]:
        print("  ! " + w)
    if len(warnings) > 15:
        print(f"  … and {len(warnings) - 15} more")

if __name__ == "__main__":
    main()
