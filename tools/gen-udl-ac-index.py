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

def add_sublime_udl_entries(langs):
    """One entry per Sublime UDL — kept SEPARATE from community (UDLs are NOT
    merged). ACs are attached later by cross_attach_acs."""
    n = 0
    for f in sorted(glob.glob(os.path.join(REPO, "UDLs-Sublime", "*.xml"))):
        rel = os.path.relpath(f, REPO)
        try:
            ul = ET.parse(f).getroot().find("UserLang")
            name = ul.get("name") if ul is not None else None
        except Exception:
            name = None
        if not name:
            continue
        langs.append({
            "id": "sublime_" + sanitize_id(name), "language": name, "displayName": name,
            "source": "sublime", "author": "", "description": "Imported from Sublime Text (best-effort UDL).",
            "version": "", "udl": file_asset(rel), "autoComplete": [],
            "sample": None, "functionList": None,
        }); n += 1
    return n

def add_ac_only_entries(langs):
    """For languages that have AC files but NO UDL entry (e.g. built-in languages
    like java/cpp), create an AC-only entry so the language is installable."""
    covered = set(e["language"].lower() for e in langs)
    seen = {}                                               # lc → display language
    for f in glob.glob(os.path.join(REPO, "autoCompletion-Sublime", "*.xml")):
        stem = os.path.splitext(os.path.basename(f))[0]; seen.setdefault(stem.lower(), stem)
    for f in glob.glob(os.path.join(REPO, "autoCompletion-stock", "*.xml")):
        stem = os.path.splitext(os.path.basename(f))[0]
        lang = STOCK_LANG.get(stem.lower(), stem.lower()); seen.setdefault(lang.lower(), lang)
    n = 0
    for lc, lang in sorted(seen.items()):
        if lc in covered:
            continue
        builtin = lc in STOCK_CAPTION
        langs.append({
            "id": ("builtin_ac_" if builtin else "sublime_ac_") + sanitize_id(lang),
            "language": lang, "displayName": STOCK_CAPTION.get(lc, lang),
            "source": "notepad++" if builtin else "sublime", "author": "",
            "description": "Built-in Notepad++ language." if builtin else "Autocompletion imported from Sublime Text.",
            "version": "", "udl": None, "autoComplete": [], "sample": None, "functionList": None,
        }); covered.add(lc); n += 1
    return n

def cross_attach_acs(langs):
    """Attach ALL ACs for each entry's language from the three AC folders
    (Notepad++ community, Sublime, Notepad++ stock built-in), deduped by file.
    This is the AC-level 1:N: a single UDL can carry several ACs that merge."""
    pool = {}                                               # lc → [(rel, source, builtin)]
    for folder, source, builtin in [("autoCompletion", "notepad++", False),
                                     ("autoCompletion-Sublime", "sublime", False),
                                     ("autoCompletion-stock", "notepad++", True)]:
        for f in sorted(glob.glob(os.path.join(REPO, folder, "*.xml"))):
            stem = os.path.splitext(os.path.basename(f))[0]
            lang = STOCK_LANG.get(stem.lower(), stem.lower()) if folder == "autoCompletion-stock" else stem
            pool.setdefault(lang.lower(), []).append((os.path.relpath(f, REPO), source, builtin))
    for e in langs:
        have = set(a.get("file") for a in e["autoComplete"] if a.get("file"))
        for rel, source, builtin in pool.get(e["language"].lower(), []):
            if rel in have:
                continue
            a = ac_asset(rel, source)
            if not a:
                continue
            if builtin:
                a["builtin"] = True
            e["autoComplete"].append(a); have.add(rel)
        # Notepad++ (built-in, then community) before Sublime, for a readable details pane.
        e["autoComplete"].sort(key=lambda a: (0 if a.get("source") == "notepad++" else 1,
                                              0 if a.get("builtin") else 1))

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

    add_sublime_udl_entries(langs)   # keep every Sublime UDL as its own entry
    add_ac_only_entries(langs)       # built-in / UDL-less languages that have ACs
    cross_attach_acs(langs)          # AC-level 1:N — every UDL of a language gets all its ACs

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
