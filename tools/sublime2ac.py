#!/usr/bin/env python3
"""
sublime2ac.py — convert Sublime Text completions/snippets to Notepad++
autoCompletion XML (Nextpad++ AC format).

Handles:
  * .sublime-completions  (JSON-with-comments: { "scope", "completions":[...] })
  * .sublime-snippet       (XML: <snippet><content><tabTrigger><scope><description>)

Groups all triggers by resolved language (scope → language) and writes one
autoCompletion-Sublime/<language>.xml per language, merging duplicates and
synthesising <Overload>/<Param> from snippet call signatures.

Usage:
  python3 tools/sublime2ac.py <input-dir-or-file ...> --out autoCompletion-Sublime [--language NAME]
"""
import sys, os, re, json, argparse, html
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

# ── scope → Nextpad++ language key (built-in internal names / UDL names) ──────
SCOPE_LANG = {
    "source.python": "python", "source.c++": "cpp", "source.cpp": "cpp", "source.c": "c",
    "source.js": "javascript.js", "source.jsx": "javascript.js", "source.ts": "typescript",
    "source.tsx": "typescript", "text.html.basic": "html", "text.html": "html",
    "source.css": "css", "source.scss": "css", "source.less": "css",
    "source.php": "php", "source.shell.bash": "bash", "source.shell": "bash",
    "source.ruby": "ruby", "source.go": "go", "source.rust": "rust", "source.java": "java",
    "source.cs": "cs", "source.lua": "lua", "source.perl": "perl", "source.sql": "sql",
    "source.yaml": "yaml", "source.json": "json", "source.makefile": "makefile",
    "source.r": "r", "source.swift": "swift", "source.objc": "objc", "source.objc++": "objc",
    "source.haskell": "haskell", "source.lisp": "lisp", "source.clojure": "lisp",
    "source.dart": "dart", "source.powershell": "powershell", "source.batchfile": "batch",
    "source.dosbatch": "batch", "source.ini": "ini", "source.toml": "toml",
    "source.tex": "tex", "source.latex": "latex", "source.matlab": "matlab",
    "source.vhdl": "vhdl", "source.verilog": "verilog", "source.asm": "asm",
    "source.nasm": "asm", "source.cobol": "cobol", "source.fortran": "fortran",
    "source.pascal": "pascal", "source.tcl": "tcl", "source.coffee": "coffeescript",
}

def _candidate_lang(scope, override):
    if override:
        return override
    if not scope:
        return None
    s = re.split(r"[,\s]", scope.strip())[0].strip()        # first selector only
    if s in SCOPE_LANG:
        return SCOPE_LANG[s]
    parts = s.split(".")
    for i in range(len(parts), 0, -1):                      # longest-prefix match
        k = ".".join(parts[:i])
        if k in SCOPE_LANG:
            return SCOPE_LANG[k]
    # loose fallback only for real language roots (source.X / text.X)
    if parts and parts[0] in ("source", "text") and len(parts) >= 2:
        return parts[1]
    return None

def scope_to_lang(scope, override, allow_map):
    """Resolve to a real NPP language. allow_map: lc-name → canonical (None = no gate)."""
    cand = _candidate_lang(scope, override)
    if not cand:
        return None
    if allow_map is None:
        return cand
    return allow_map.get(cand.lower())                      # None if not a known language

# ── JSON-with-comments cleaner (Sublime allows // /* */ and trailing commas) ──
def strip_jsonc(text):
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1]); i += 2; continue
            if c == '"':
                in_str = False
            i += 1; continue
        if c == '"':
            in_str = True; out.append(c); i += 1; continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n": i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"): i += 1
            i += 2; continue
        out.append(c); i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))       # trailing commas

# ── snippet content → (is_func, [params]) ────────────────────────────────────
def split_params(s):
    out, cur, depth, i = [], "", 0, 0
    while i < len(s):
        c = s[i]
        if c == "$" and i + 1 < len(s) and s[i + 1] == "{":
            j = s.find("}", i)
            if j < 0: cur += s[i:]; break
            cur += s[i:j + 1]; i = j + 1; continue
        if c in "([{": depth += 1; cur += c
        elif c in ")]}": depth -= 1; cur += c
        elif c == "," and depth == 0: out.append(cur); cur = ""
        else: cur += c
        i += 1
    if cur.strip(): out.append(cur)
    return out

def clean_param(p, idx):
    p = re.sub(r"\$\{\d+:([^}]*)\}", r"\1", p)               # ${1:label} → label
    p = re.sub(r"\$\{(\d+)\}", lambda m: "arg" + m.group(1), p)
    p = re.sub(r"\$(\d+)", lambda m: "arg" + m.group(1), p)
    p = p.replace("$0", "").replace("\\$", "$").strip()
    return p or ("arg%d" % idx)

def parse_signature(name, contents):
    if not contents:
        return (False, [])
    body = contents.strip()
    m = re.match(r"^\s*" + re.escape(name) + r"\s*\((.*)", body, re.S)
    inner = m.group(1) if m else None
    if inner is None:
        idx = body.find("(")
        if idx < 0 or idx > len(name) + 2:
            return (False, [])
        inner = body[idx + 1:]
    depth, ps = 1, ""
    for ch in inner:
        if ch == "(": depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0: break
        ps += ch
    raw = split_params(ps)
    params = [clean_param(p, i + 1) for i, p in enumerate(raw) if p.strip()]
    return (True, params)

# ── completion record: {name, func, params, descr} ───────────────────────────
def from_completions_file(path):
    try:
        data = json.loads(strip_jsonc(open(path, encoding="utf-8").read()))
    except Exception as e:
        sys.stderr.write("  ! parse failed %s: %s\n" % (os.path.basename(path), e)); return None, []
    scope = data.get("scope") if isinstance(data, dict) else None
    recs = []
    for item in (data.get("completions") or []):
        if isinstance(item, str):
            recs.append({"name": item, "func": False, "params": [], "descr": ""})
        elif isinstance(item, dict):
            trig = item.get("trigger") or ""
            name = trig.split("\t", 1)[0].strip()
            if not name: continue
            hint = trig.split("\t", 1)[1].strip() if "\t" in trig else ""
            isf, params = parse_signature(name, item.get("contents") or "")
            descr = item.get("details") or item.get("annotation") or hint or ""
            descr = re.sub(r"<[^>]+>", "", descr).strip()    # strip HTML from details
            recs.append({"name": name, "func": isf, "params": params, "descr": descr})
    return scope, recs

def from_snippet_file(path):
    try:
        root = ET.parse(path).getroot()
    except Exception as e:
        sys.stderr.write("  ! parse failed %s: %s\n" % (os.path.basename(path), e)); return None, []
    def t(tag):
        el = root.find(tag); return (el.text or "").strip() if el is not None and el.text else ""
    scope, trig, content, descr = t("scope"), t("tabTrigger"), t("content"), t("description")
    if not trig: return scope, []
    isf, params = parse_signature(trig, content)
    return scope, [{"name": trig, "func": isf, "params": params, "descr": descr}]

# ── emit one AC XML per language ──────────────────────────────────────────────
def emit(language, recs, outdir):
    by_name = {}                                            # merge dups, union overloads
    order = []
    for r in recs:
        k = r["name"]
        if k not in by_name:
            by_name[k] = {"name": k, "func": r["func"], "overloads": []}
            order.append(k)
        e = by_name[k]
        if r["func"]: e["func"] = True
        if r["func"]:
            e["overloads"].append({"params": r["params"], "descr": r["descr"]})
    order.sort(key=str.lower)

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<NotepadPlus>",
             '    <AutoComplete language=%s>' % quoteattr(language),
             '        <Environment ignoreCase="no" startFunc="(" stopFunc=")" paramSeparator="," additionalWordChar="" />']
    for k in order:
        e = by_name[k]
        if e["func"] and e["overloads"]:
            lines.append('        <KeyWord name=%s func="yes">' % quoteattr(e["name"]))
            for ov in e["overloads"]:
                d = (' descr=%s' % quoteattr(ov["descr"])) if ov["descr"] else ""
                lines.append('            <Overload retVal=""%s>' % d)
                for p in ov["params"]:
                    lines.append('                <Param name=%s />' % quoteattr(p))
                lines.append('            </Overload>')
            lines.append('        </KeyWord>')
        else:
            lines.append('        <KeyWord name=%s />' % quoteattr(e["name"]))
    lines += ["    </AutoComplete>", "</NotepadPlus>", ""]
    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, language + ".xml")
    open(out, "w", encoding="utf-8").write("\n".join(lines))
    return out, len(order)

def gather_files(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            for dp, _dn, fn in os.walk(p):
                for f in fn:
                    if f.endswith(".sublime-completions") or f.endswith(".sublime-snippet"):
                        files.append(os.path.join(dp, f))
        elif os.path.isfile(p):
            files.append(p)
    return files

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--language", default=None, help="force target language (overrides scope)")
    ap.add_argument("--allow", default=None, help="file of allowed language names (1/line); reject others")
    args = ap.parse_args()

    allow_map = None
    if args.allow:
        allow_map = {}
        for line in open(args.allow, encoding="utf-8"):
            n = line.strip()
            if n: allow_map[n.lower()] = n                  # lc → canonical casing

    by_lang = {}
    rejected = {}
    for f in gather_files(args.inputs):
        scope, recs = (from_completions_file(f) if f.endswith(".sublime-completions")
                       else from_snippet_file(f))
        if not recs: continue
        lang = scope_to_lang(scope, args.language, allow_map)
        if not lang:
            rejected[scope or "(no scope)"] = rejected.get(scope or "(no scope)", 0) + 1
            continue
        by_lang.setdefault(lang, []).extend(recs)

    for lang, recs in sorted(by_lang.items()):
        out, n = emit(lang, recs, args.out)
        print("  %-22s %4d keywords → %s" % (lang, n, os.path.relpath(out)))
    if rejected:
        top = sorted(rejected.items(), key=lambda x: -x[1])[:20]
        sys.stderr.write("  rejected (not a known language — extend SCOPE_LANG if real):\n")
        for sc, c in top:
            sys.stderr.write("    %-40s ×%d\n" % (sc, c))
    print("done: %d language file(s)" % len(by_lang))

if __name__ == "__main__":
    main()
