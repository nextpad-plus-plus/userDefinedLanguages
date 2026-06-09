#!/usr/bin/env python3
"""
sublime2udl.py — BEST-EFFORT conversion of a Sublime language grammar to a
Notepad++ User Defined Language (UDL).

Handles .sublime-syntax (YAML) and .tmLanguage (XML plist). A Sublime grammar is
a context state machine; a UDL is a flat keyword/comment/delimiter highlighter,
so this only EXTRACTS what maps cleanly:
  * name + file extensions
  * keyword lists, binned by TextMate scope (control/storage/type/constant/function)
  * line + block comment tokens
  * string delimiters (" and ')
It cannot capture context rules, interpolation, heredocs, etc. Output is a basic
keyword/comment/string highlighter — review before shipping.

Usage: python3 tools/sublime2udl.py <grammar-file> --out UDLs-Sublime [--name NAME]
"""
import sys, os, re, argparse, plistlib
from xml.sax.saxutils import quoteattr, escape

try:
    import yaml
except Exception:
    yaml = None

# scope-prefix → UDL keyword group (1..8)
SCOPE_GROUP = [
    ("keyword.control",      1),
    ("storage.type",         3),
    ("storage.modifier",     4),
    ("storage",              2),
    ("keyword.operator.word",2),
    ("keyword",              2),
    ("constant.language",    5),
    ("support.constant",     5),
    ("support.type",         3),
    ("support.class",        3),
    ("support.function",     6),
    ("entity.name.function", 6),
    ("variable.language",    5),
]
BLOCK_CLOSE = {"/*": "*/", "<!--": "-->", "(*": "*)", "{-": "-}", '"""': '"""', "'''": "'''", "/+": "+/"}

def regex_to_literal(rx):
    """Crude unescape of a literal-ish regex token (for comment/string markers)."""
    if not rx: return ""
    s = rx.strip()
    s = re.sub(r"^\^", "", s)
    # take the leading literal run (stop at the first real metacharacter group)
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            out.append(s[i + 1]); i += 2; continue
        if c in "([{|?*+":          # a real quantifier/group → stop
            break
        if c in ")]}$":
            break
        out.append(c); i += 1
    return "".join(out).strip()

def keywords_from_match(rx):
    """Pull alternation words from \\b(a|b|c)\\b / (?:a|b) style matches."""
    if not rx: return []
    words = set()
    for grp in re.findall(r"\((?:\?:)?([^()]*)\)", rx):
        if "|" not in grp: continue
        for tok in grp.split("|"):
            tok = tok.strip().strip("\\b").strip()
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\-]*", tok):
                words.add(tok)
    return sorted(words)

class Grammar:
    def __init__(self):
        self.name = None; self.exts = []; self.rules = []   # rules: (scope, match)

def load_sublime_syntax(path):
    if not yaml:
        sys.stderr.write("  ! PyYAML not available — cannot read .sublime-syntax\n"); return None
    data = yaml.safe_load(open(path, encoding="utf-8"))
    if not isinstance(data, dict): return None
    g = Grammar()
    g.name = data.get("name")
    g.exts = data.get("file_extensions") or []
    def walk(rules):
        if not isinstance(rules, list): return
        for r in rules:
            if not isinstance(r, dict): continue
            sc = r.get("scope") or ""
            m  = r.get("match") or ""
            if sc and m: g.rules.append((sc, m))
    for ctx in (data.get("contexts") or {}).values():
        walk(ctx)
    return g

def load_tmlanguage(path):
    with open(path, "rb") as f:
        data = plistlib.load(f)
    g = Grammar()
    g.name = data.get("name")
    fts = data.get("fileTypes") or []
    g.exts = fts
    def walk(pats):
        if not isinstance(pats, list): return
        for p in pats:
            if not isinstance(p, dict): continue
            nm = p.get("name") or ""
            if p.get("match"): g.rules.append((nm, p["match"]))
            if p.get("begin"): g.rules.append((p.get("name") or p.get("contentName") or "", p["begin"]))
            if p.get("patterns"): walk(p["patterns"])
            for v in (p.get("repository") or {}).values():
                if isinstance(v, dict) and v.get("patterns"): walk(v["patterns"])
    walk(data.get("patterns"))
    for v in (data.get("repository") or {}).values():
        if isinstance(v, dict) and v.get("patterns"): walk(v["patterns"])
    return g

def group_for_scope(scope):
    for prefix, g in SCOPE_GROUP:
        if scope.startswith(prefix): return g
    return None

def build_udl(g, force_name=None):
    name = force_name or g.name or "Unnamed"
    ext = " ".join(e.lstrip(".") for e in g.exts)

    kw = {n: set() for n in range(1, 9)}
    line_comment = ""; block_open = ""; block_close = ""
    dq = sq = False
    for scope, m in g.rules:
        grp = group_for_scope(scope)
        if grp:
            for w in keywords_from_match(m): kw[grp].add(w)
        if scope.startswith("comment.line") and not line_comment:
            lit = regex_to_literal(m)
            if lit: line_comment = lit
        elif scope.startswith("comment.block") and not block_open:
            lit = regex_to_literal(m)
            if lit:
                block_open = lit
                block_close = BLOCK_CLOSE.get(lit, "")
        if "string.quoted.double" in scope: dq = True
        if "string.quoted.single" in scope: sq = True

    # Comments keyword: 00<line> 03<blockOpen> 04<blockClose>
    comments = []
    if line_comment: comments.append("00" + line_comment)
    else: comments.append("00")
    comments += ["01", "02"]
    comments.append(("03" + block_open) if block_open else "03")
    comments.append(("04" + block_close) if block_close else "04")
    comments_str = " ".join(comments)

    # Delimiters: (open,esc,close) triples 00..23 (8 max). " then ' if present.
    delim = ["" for _ in range(24)]
    slot = 0
    for present, ch in ((dq, '"'), (sq, "'")):
        if present and slot < 8:
            delim[slot * 3] = ch; delim[slot * 3 + 1] = "\\"; delim[slot * 3 + 2] = ch
            slot += 1
    delim_str = " ".join("%02d%s" % (i, delim[i]) for i in range(24))

    L = ['<?xml version="1.0" encoding="UTF-8"?>', "<NotepadPlus>",
         '    <UserLang name=%s ext=%s udlVersion="2.1">' % (quoteattr(name), quoteattr(ext)),
         "        <Settings>",
         '            <Global caseIgnored="no" allowFoldOfComments="no" foldCompact="no" forcePureLC="0" decimalSeparator="0" />',
         '            <Prefix Keywords1="no" Keywords2="no" Keywords3="no" Keywords4="no" Keywords5="no" Keywords6="no" Keywords7="no" Keywords8="no" />',
         "        </Settings>",
         "        <KeywordLists>",
         '            <Keywords name="Comments">%s</Keywords>' % escape(comments_str),
         '            <Keywords name="Numbers, prefix1"></Keywords>',
         '            <Keywords name="Numbers, prefix2"></Keywords>',
         '            <Keywords name="Numbers, extras1"></Keywords>',
         '            <Keywords name="Numbers, extras2"></Keywords>',
         '            <Keywords name="Numbers, suffix1"></Keywords>',
         '            <Keywords name="Numbers, suffix2"></Keywords>',
         '            <Keywords name="Numbers, range"></Keywords>',
         '            <Keywords name="Operators1"></Keywords>',
         '            <Keywords name="Operators2"></Keywords>',
         '            <Keywords name="Folders in code1, open"></Keywords>',
         '            <Keywords name="Folders in code1, middle"></Keywords>',
         '            <Keywords name="Folders in code1, close"></Keywords>',
         '            <Keywords name="Folders in code2, open"></Keywords>',
         '            <Keywords name="Folders in code2, middle"></Keywords>',
         '            <Keywords name="Folders in code2, close"></Keywords>',
         '            <Keywords name="Folders in comment, open"></Keywords>',
         '            <Keywords name="Folders in comment, middle"></Keywords>',
         '            <Keywords name="Folders in comment, close"></Keywords>',
         '            <Keywords name="Delimiters">%s</Keywords>' % escape(delim_str)]
    for n in range(1, 9):
        L.append('            <Keywords name="Keywords%d">%s</Keywords>' % (n, escape(" ".join(sorted(kw[n])))))
    L.append("        </KeywordLists>")
    # Minimal style block (dark) so the language highlights.
    styles = [("DEFAULT", "FFFFFF"), ("COMMENTS", "00FF00"), ("LINE COMMENTS", "00FF00"),
              ("NUMBERS", "FF8040"), ("KEYWORDS1", "0080FF"), ("KEYWORDS2", "FFFF00"),
              ("KEYWORDS3", "00FF80"), ("KEYWORDS4", "FF80FF"), ("KEYWORDS5", "80C0FF"),
              ("KEYWORDS6", "FFC000"), ("KEYWORDS7", "C0C0C0"), ("KEYWORDS8", "C0C0C0"),
              ("OPERATORS", "FFFFFF"), ("FOLDER IN CODE1", "FFFFFF"),
              ("DELIMITERS1", "FF8000"), ("DELIMITERS2", "FF8000")]
    L.append("        <Styles>")
    for nm, fg in styles:
        L.append('            <WordsStyle name="%s" fgColor="%s" bgColor="2E2E2E" colorStyle="1" fontStyle="0" nesting="0" />' % (nm, fg))
    L += ["        </Styles>", "    </UserLang>", "</NotepadPlus>", ""]
    total_kw = sum(len(kw[n]) for n in range(1, 9))
    return name, "\n".join(L), total_kw

def load_grammar(path):
    if path.endswith(".sublime-syntax"):
        return load_sublime_syntax(path)
    if path.endswith(".tmLanguage"):
        return load_tmlanguage(path)
    return None

def convert_one(path, out_dir, force_name, min_keywords):
    g = load_grammar(path)
    if not g or not (force_name or g.name):
        return None
    name, xml, n = build_udl(g, force_name)
    if n < min_keywords:
        return ("skip", name, n)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, re.sub(r"[/:]", "_", name) + ".xml")
    open(out, "w", encoding="utf-8").write(xml)
    return ("write", name, n)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("grammar", help="a grammar file OR a directory to batch-convert")
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default=None)
    ap.add_argument("--min-keywords", type=int, default=0, help="skip UDLs with fewer keywords")
    args = ap.parse_args()

    if os.path.isdir(args.grammar):
        # .tmLanguage first, then .sublime-syntax → modern format wins on name collision.
        files = []
        for dp, _dn, fn in os.walk(args.grammar):
            for f in fn:
                if f.endswith(".tmLanguage"): files.append((0, os.path.join(dp, f)))
                elif f.endswith(".sublime-syntax"): files.append((1, os.path.join(dp, f)))
        files.sort()
        wrote = skipped = errored = 0
        for _ord, path in files:
            try:
                r = convert_one(path, args.out, None, args.min_keywords)
            except Exception:
                errored += 1; continue
            if r is None: errored += 1
            elif r[0] == "write": wrote += 1
            else: skipped += 1
        print("syntax→UDL: wrote %d, skipped %d (<%d kw), unparseable %d"
              % (wrote, skipped, args.min_keywords, errored))
    else:
        r = convert_one(args.grammar, args.out, args.name, args.min_keywords)
        if r and r[0] == "write":
            print("  %-24s %4d keywords" % (r[1], r[2]))
        else:
            sys.stderr.write("  - not written: %s\n" % args.grammar)

if __name__ == "__main__":
    main()
