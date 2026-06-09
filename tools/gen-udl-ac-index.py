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
import json, os, sys, hashlib, datetime, glob, re
import xml.etree.ElementTree as ET

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # repo root (tools/..)

def sanitize_id(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_") or "x"

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

def ac_entry_count(rel):
    """Number of <KeyWord> entries in an AC file (0 on failure)."""
    try:
        n = 0
        for _ev, el in ET.iterparse(os.path.join(REPO, rel), events=("start",)):
            if el.tag.split("}")[-1] == "KeyWord":
                n += 1
        return n
    except Exception:
        return 0

def ac_asset(rel, source, author=""):
    """An AC asset dict {file, sha256, bytes, entries, source[, author]} or None."""
    a = file_asset(rel)
    if not a:
        return None
    a["entries"] = ac_entry_count(rel)
    a["source"] = source
    if author:
        a["author"] = author
    return a

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

def merge_sublime(langs):
    """Unify by language: merge Sublime AC/UDL into the existing (Notepad++)
    entry of the same language so a language aggregates ALL its AC sources on one
    row. A Sublime UDL for a language Notepad++ already covers is dropped (the
    Notepad++ UDL wins) but its AC is still added. Sublime-only languages become
    new entries."""
    by_lang = {}
    for e in langs:                                          # Notepad++ entries first → they win the UDL
        by_lang.setdefault(e["language"].lower(), e)

    udl_subl = {}
    for f in sorted(glob.glob(os.path.join(REPO, "UDLs-Sublime", "*.xml"))):
        rel = os.path.relpath(f, REPO)
        try:
            ul = ET.parse(f).getroot().find("UserLang")
            name = ul.get("name") if ul is not None else None
        except Exception:
            name = None
        if name:
            udl_subl.setdefault(name.lower(), (name, rel))
    ac_subl = {}
    for f in sorted(glob.glob(os.path.join(REPO, "autoCompletion-Sublime", "*.xml"))):
        rel = os.path.relpath(f, REPO)
        lang = os.path.splitext(os.path.basename(f))[0]
        ac_subl.setdefault(lang.lower(), (lang, rel))

    handled_ac, new_langs = set(), 0
    for lc, (name, rel) in sorted(udl_subl.items()):
        sub_ac = ac_asset(ac_subl[lc][1], "sublime") if lc in ac_subl else None
        if lc in by_lang:                                   # language already exists → add Sublime AC, drop dup UDL
            if sub_ac:
                by_lang[lc]["autoComplete"].append(sub_ac); handled_ac.add(lc)
        else:                                               # new Sublime-only language
            entry = {
                "id": "sublime_" + sanitize_id(name), "language": name, "displayName": name,
                "source": "sublime", "author": "", "description": "Imported from Sublime Text (best-effort UDL).",
                "version": "", "udl": file_asset(rel),
                "autoComplete": [sub_ac] if sub_ac else [], "sample": None, "functionList": None,
            }
            if sub_ac:
                handled_ac.add(lc)
            langs.append(entry); by_lang[lc] = entry; new_langs += 1
    for lc, (lang, rel) in sorted(ac_subl.items()):
        if lc in handled_ac:
            continue
        a = ac_asset(rel, "sublime")
        if not a:
            continue
        if lc in by_lang:                                   # enhance a Notepad++/built-in language
            by_lang[lc]["autoComplete"].append(a)
        else:                                               # AC-only entry (e.g. built-in language)
            langs.append({
                "id": "sublime_ac_" + sanitize_id(lang), "language": lang, "displayName": lang,
                "source": "sublime", "author": "", "description": "Autocompletion imported from Sublime Text.",
                "version": "", "udl": None, "autoComplete": [a], "sample": None, "functionList": None,
            })
            new_langs += 1
    return new_langs

STOCK_LANG = {"javascript": "javascript.js", "coffee": "coffeescript", "baanc": "baanc"}
STOCK_CAPTION = {
    "actionscript": "ActionScript", "autoit": "AutoIt", "baanc": "BaanC", "batch": "Batch",
    "c": "C", "cmake": "CMake", "cobol": "COBOL", "coffeescript": "CoffeeScript", "cpp": "C++",
    "cs": "C#", "css": "CSS", "gdscript": "GDScript", "go": "Go", "html": "HTML", "java": "Java",
    "javascript.js": "JavaScript", "lisp": "Lisp", "lua": "Lua", "nsis": "NSIS", "perl": "Perl",
    "php": "PHP", "powershell": "PowerShell", "python": "Python", "raku": "Raku",
    "rc": "Resource file", "rust": "Rust", "sas": "SAS", "sql": "SQL", "tex": "TeX",
    "typescript": "TypeScript", "vb": "Visual Basic", "vhdl": "VHDL", "xml": "XML",
}

def add_stock_acs(langs):
    """Augment EXISTING entries with the bundled stock built-in AC as a
    builtin:true 'notepad++' source (so e.g. java shows Notepad++ + Sublime).
    Never creates rows for built-ins that have nothing installable."""
    by_lang = {}
    for e in langs:
        by_lang.setdefault(e["language"].lower(), e)
    n = 0
    for f in sorted(glob.glob(os.path.join(REPO, "autoCompletion-stock", "*.xml"))):
        stem = os.path.splitext(os.path.basename(f))[0]
        lc = STOCK_LANG.get(stem.lower(), stem.lower()).lower()
        if lc not in by_lang:
            continue                                        # nothing installable for this built-in → skip
        a = ac_asset(os.path.relpath(f, REPO), "notepad++")
        if not a:
            continue
        a["builtin"] = True                                 # bundled → shown but not downloaded on install
        e = by_lang[lc]
        e["autoComplete"].insert(0, a)                      # Notepad++ base first
        if not e["udl"]:                                    # a built-in language row
            e["source"] = "notepad++"
            if lc in STOCK_CAPTION:
                e["displayName"] = STOCK_CAPTION[lc]
        n += 1
    return n

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
                ac_list.append({"url": acv, "source": "notepad++"})
            else:
                ac_rel = find_in_dir("autoCompletion", acv)
                if ac_rel:
                    asset = ac_asset(ac_rel, "notepad++")
                    if asset:
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
            "source":      "notepad++",
            "author":      e.get("author", ""),
            "description": e.get("description", ""),
            "version":     e.get("version", ""),
            "udl":         udl,
            "autoComplete": ac_list,
            "sample":      sample,
            "functionList": fl,
        })

    merge_sublime(langs)
    add_stock_acs(langs)

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
    n_sub = sum(1 for l in langs if l["source"] == "sublime")
    print(f"wrote {out}")
    print(f"  {len(langs)} languages | {with_udl} with UDL | {with_ac} with AC | {n_sub} sublime | {len(warnings)} warnings")
    for w in warnings[:15]:
        print("  ! " + w)
    if len(warnings) > 15:
        print(f"  … and {len(warnings) - 15} more")

if __name__ == "__main__":
    main()
