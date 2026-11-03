"""Function-anchor check: does each backported change land in the same
function (per file) as the upstream change?

Catches the classic misplacement where two functions share identical context
lines and a hunk is applied to the wrong one (e.g. eval v1 ctnetlink case:
lockdep assert put in nf_conntrack_eventmask_report() instead of
nf_ct_expect_event_report()). Pure git, no LLM.
"""

import os
import re
import subprocess
import tempfile

# Force git's C function detection even on trees without .gitattributes (CBR 7.9).
_ATTR = tempfile.NamedTemporaryFile("w", delete=False, suffix=".gitattributes")
_ATTR.write("*.c diff=cpp\n*.h diff=cpp\n")
_ATTR.close()

_HUNK = re.compile(r"^@@ [^@]* @@ ?(.*)$")
_IDENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_TYPE = re.compile(r"^(?:typedef\s+)?(struct|union|enum)\s+([A-Za-z_][A-Za-z0-9_]*)")


def _anchor(header):
    """Reduce a git hunk header context to a stable name ("foo", "struct bar")."""
    h = header.strip()
    if not h:
        return "<file scope>"
    m = _TYPE.match(h)
    if m and "(" not in h:
        return f"{m.group(1)} {m.group(2)}"
    m = _IDENT.search(h)
    if m:
        return m.group(1)
    if h.startswith("#"):  # macro block, include area
        return "<preprocessor>"
    return h[:60]


def anchors_of(diff_text):
    """{file: set(anchor)} for a -U0 unified diff."""
    out, cur = {}, None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            p = line[4:].strip()
            cur = None if p == "/dev/null" else re.sub(r"^b/", "", p)
            if cur:
                out.setdefault(cur, set())
        elif cur and line.startswith("@@"):
            m = _HUNK.match(line)
            if m:
                out[cur].add(_anchor(m.group(1)))
    return out


def _git(repo, *args):
    return subprocess.run(
        ["git", "-c", f"core.attributesFile={_ATTR.name}", "-C", repo, *args],
        capture_output=True, text=True, check=True).stdout


def upstream_anchors(repo, sha):
    return anchors_of(_git(repo, "show", "-U0", "--format=", sha))


def worktree_anchors(repo):
    return anchors_of(_git(repo, "diff", "-U0"))


def compare(up, bp):
    """Return human-readable problems; empty list means anchors agree."""
    problems = []
    for f in sorted(set(up) | set(bp)):
        if not f.endswith((".c", ".h")):
            continue
        u, b = up.get(f, set()), bp.get(f, set())
        if f not in bp:
            problems.append(f"{f}: upstream changes this file but the backport does not "
                            f"(upstream functions: {', '.join(sorted(u))})")
            continue
        if f not in up:
            problems.append(f"{f}: backport changes this file but upstream does not")
            continue
        for a in sorted(b - u):
            problems.append(f"{f}: backport changes `{a}`, which upstream does not touch")
        for a in sorted(u - b):
            problems.append(f"{f}: upstream changes `{a}`, but the backport does not")
    return problems


_NOISE = re.compile(r"^(EXPORT_SYMBOL\w*|module_\w+|MODULE_\w+|late_initcall|subsys_initcall|"
                    r"device_initcall|core_initcall|__setup|<file scope>|<preprocessor>)$")


def _defined_in(repo, ref, path, name):
    """Is `name` defined (function at column 0, or struct/union/enum) in ref:path?"""
    try:
        src = _git(repo, "show", f"{ref}:{path}")
    except subprocess.CalledProcessError:
        return False
    if name.startswith(("struct ", "union ", "enum ")):
        return re.search(rf"^{re.escape(name)}\s*\{{", src, re.M) is not None
    return re.search(rf"^\S.*\b{re.escape(name)}\s*\(", src, re.M) is not None


def misplacements(repo, target_ref, up, bp):
    """High-confidence wrong-function placements.

    Flag only the swap pattern: in one file the backport touches X that upstream
    doesn't, upstream touches Y that the backport doesn't, and Y is defined in
    that same file of the target tree (so the right place existed).
    Returns (errors, notes): errors should block, notes go to the reviewer.
    """
    errors, notes = [], []
    for f in sorted(set(up) & set(bp)):
        if not f.endswith((".c", ".h")):
            continue
        extra = {a for a in bp[f] - up[f] if not _NOISE.match(a)}
        missing = {a for a in up[f] - bp[f] if not _NOISE.match(a)}
        present = {y for y in missing if _defined_in(repo, target_ref, f, y)}
        if extra and present:
            errors.append(f"{f}: the backport changes {', '.join(f'`{x}`' for x in sorted(extra))}, "
                          f"but upstream changes {', '.join(f'`{y}`' for y in sorted(present))} instead, "
                          f"which also exists in this tree. A hunk was probably applied to the wrong "
                          f"function (similar context lines).")
        else:
            for x in sorted(extra):
                notes.append(f"{f}: also changes `{x}` (not touched upstream)")
            for y in sorted(missing):
                notes.append(f"{f}: upstream change in `{y}` has no counterpart"
                             + ("" if y in present else " (not defined in this tree)"))
    for f in sorted(set(up) - set(bp)):
        if f.endswith((".c", ".h")):
            notes.append(f"{f}: upstream changes this file; the backport does not")
    for f in sorted(set(bp) - set(up)):
        if f.endswith((".c", ".h")):
            notes.append(f"{f}: changed by the backport only (not in upstream)")
    return errors, notes
