"""Moves top-level blocks of a module into new sibling modules, keeping every name working.

    python3 tools/split_module.py <spec.json>

spec: {"origin": "src/ui.py", "style": "import" | "attr",
       "modules": [{"name": "ui_chat", "doc": "…", "ranges": [[77, 340], …]}, …]}

- Each top-level statement whose first line falls in a range moves (with the comments right above it).
- Names the moved code uses from the origin: always `<origin>.name` for state the code rebinds
  (`global`), otherwise `from .<origin> import name` ("import") or `<origin>.name` ("attr").
- Names from another new module: `from .<mod> import name` (modules are ordered: a module may only
  import from modules listed before it; later ones are reached as `<mod>.name`).
- The origin gets `from .<mod> import (...)` at its end for every moved name, so `origin.name` keeps
  working for the rest of the add-on and the tests.
"""

import ast
import json
import os
import re
import sys
import symtable


def top_level(tree, lines):
    """[(stmt, first_line, last_line)] with decorators and comments directly above."""
    out = []
    prev_end = 0
    for stmt in tree.body:
        start = min([stmt.lineno] + [d.lineno for d in getattr(stmt, "decorator_list", [])])
        first = start
        while first - 1 > prev_end and lines[first - 2].lstrip().startswith("#"):
            first -= 1
        # a section comment separated by one blank line belongs to the statement below it too
        if first - 2 > prev_end and not lines[first - 2].strip() and lines[first - 3].lstrip().startswith("#"):
            first -= 1
            while first - 1 > prev_end and lines[first - 2].lstrip().startswith("#"):
                first -= 1
        out.append((stmt, first, stmt.end_lineno))
        prev_end = stmt.end_lineno
    return out


def bound_names(stmt):
    names = set()
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names.add(stmt.name)
    elif isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        for t in targets:
            for n in ast.walk(t):
                if isinstance(n, ast.Name):
                    names.add(n.id)
    elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
        for a in stmt.names:
            names.add((a.asname or a.name).split(".")[0])
    return names


def scopes_by_line(table):
    """{(name_of_scope, lineno): table} for functions/classes, recursively."""
    out = {}
    for child in table.get_children():
        out[(child.get_name(), child.get_lineno())] = child
        out.update(scopes_by_line(child))
    return out


def main(spec_path):
    spec = json.load(open(spec_path))
    origin_path = spec["origin"]
    origin = os.path.splitext(os.path.basename(origin_path))[0]
    style = spec.get("style", "import")
    src = open(origin_path, encoding="utf-8").read()
    lines = src.splitlines()
    tree = ast.parse(src)
    table = symtable.symtable(src, origin_path, "exec")
    scopes = scopes_by_line(table)
    stmts = top_level(tree, lines)

    owner = {}   # stmt index -> module name (origin if not moved)
    for i, (stmt, first, last) in enumerate(stmts):
        owner[i] = origin
        for mod in spec["modules"]:
            if any(a <= stmt.lineno <= b or a <= first <= b for a, b in mod["ranges"]):
                owner[i] = mod["name"]
    order = [m["name"] for m in spec["modules"]]
    defined = {name: set() for name in [origin] + order}
    imports_of_origin = []   # (stmt, names)
    for i, (stmt, _f, _l) in enumerate(stmts):
        if isinstance(stmt, (ast.Import, ast.ImportFrom)) and owner[i] == origin:
            imports_of_origin.append((stmt, bound_names(stmt)))
            continue
        defined[owner[i]] |= bound_names(stmt)
    import_names = set().union(*[n for _s, n in imports_of_origin]) if imports_of_origin else set()

    # names declared global anywhere (rebound state)
    rebound = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            rebound |= set(node.names)

    def scope_for(node_stack):
        """symtable of the innermost function/class around a node (None = module)."""
        for parent in reversed(node_stack):
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                key = (parent.name if not isinstance(parent, ast.Lambda) else "lambda", parent.lineno)
                if key in scopes:
                    return scopes[key]
        return None

    def is_global_in(scope, name):
        if scope is None:
            return True
        try:
            sym = scope.lookup(name)
        except KeyError:
            return True   # not in this scope: free → looked up outside (class bodies don't count)
        return sym.is_global() or (sym.is_free() and _free_is_module(scope, name))

    def _free_is_module(scope, name):
        return False

    new_sources = {}
    exports = {}
    for mod in order:
        edits = []          # (line, col, end_col, replacement)  single-line Name nodes
        drop_globals = []   # (stmt node) to rewrite
        need_origin = set()
        need_from = {}      # module -> names
        need_imports = set()
        mod_stmts = [(s, f, l) for i, (s, f, l) in enumerate(stmts) if owner[i] == mod]
        exports[mod] = sorted(defined[mod])

        for stmt, _f, _l in mod_stmts:
            stack = []

            def visit(node):
                stack.append(node)
                if isinstance(node, ast.Name):
                    name = node.id
                    scope = scope_for(stack[:-1])
                    comp_bound = set()
                    for parent in stack[:-1]:
                        if isinstance(parent, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                            for gen in parent.generators:
                                comp_bound |= {n.id for n in ast.walk(gen.target) if isinstance(n, ast.Name)}
                    if name in comp_bound:
                        pass
                    elif is_global_in(scope, name) and name not in defined[mod]:
                        if name in import_names:
                            need_imports.add(name)
                        elif name in defined[origin]:
                            if name in rebound or style == "attr":
                                edits.append((node.lineno, node.col_offset, node.end_col_offset,
                                              "%s.%s" % (origin, name)))
                                need_origin.add(name)
                            else:
                                need_from.setdefault(origin, set()).add(name)
                        else:
                            for other in order:
                                if other != mod and name in defined[other]:
                                    if order.index(other) < order.index(mod) and name not in rebound:
                                        need_from.setdefault(other, set()).add(name)
                                    else:
                                        edits.append((node.lineno, node.col_offset, node.end_col_offset,
                                                      "%s.%s" % (other, name)))
                                        need_from.setdefault("__mod__", set()).add(other)
                                    break
                if isinstance(node, ast.Global):
                    drop_globals.append(node)
                for child in ast.iter_child_nodes(node):
                    visit(child)
                stack.pop()
            visit(stmt)

        # apply edits on a copy of the lines
        out_lines = list(lines)
        for line, col, end, rep in sorted(edits, reverse=True):
            text = out_lines[line - 1]
            out_lines[line - 1] = text[:col] + rep + text[end:]
        for g in drop_globals:
            keep = [n for n in g.names if n in defined[mod]]
            text = out_lines[g.lineno - 1]
            indent = re.match(r"\s*", text).group(0)
            out_lines[g.lineno - 1] = (indent + "global " + ", ".join(keep)) if keep else None
        body = []
        idx = [i for i in range(len(stmts)) if owner[i] == mod]
        runs = []
        for i in idx:   # consecutive statements keep the original text between them
            if runs and runs[-1][-1] == i - 1:
                runs[-1].append(i)
            else:
                runs.append([i])
        for run in runs:
            first, last = stmts[run[0]][1], stmts[run[-1]][2]
            chunk = [l for l in out_lines[first - 1:last] if l is not None]
            body.append("\n".join(chunk))
        header = ['# SPDX-License-Identifier: GPL-3.0-or-later', '"""%s"""' % mod_doc(spec, mod), ""]
        for stmt, names in imports_of_origin:
            used = names & need_imports
            if not used:
                continue
            if isinstance(stmt, ast.Import):
                for a in stmt.names:
                    if (a.asname or a.name).split(".")[0] in used:
                        header.append("import " + a.name + (" as " + a.asname if a.asname else ""))
            else:
                picked = [a for a in stmt.names if (a.asname or a.name) in used]
                header.append("from %s%s import %s" % ("." * stmt.level, stmt.module or "",
                              ", ".join(a.name + (" as " + a.asname if a.asname else "") for a in picked)))
        mods = set(need_from.pop("__mod__", set()))
        if need_origin:
            mods.add(origin)
        if mods:
            header.append("from . import " + ", ".join(sorted(mods)))
        for other in [origin] + order:
            if other in need_from and need_from[other]:
                header.append(wrap("from .%s import " % other, sorted(need_from[other])))
        new_sources[mod] = "\n".join(header) + "\n\n\n" + "\n\n\n".join(body).rstrip() + "\n"

    # origin without the moved statements, re-exporting them at the end
    keep_lines = list(lines)
    for i, (stmt, first, last) in enumerate(stmts):
        if owner[i] != origin:
            for n in range(first - 1, last):
                keep_lines[n] = None
    tail = ["# Moved into their own modules; re-exported so `%s.<name>` keeps working." % origin]
    for mod in order:
        tail.append(wrap("from .%s import " % mod, exports[mod]) + "  # noqa: E402,F401")
    if spec.get("reexport_at", "end") == "top":
        last_import = max(l for (s_, f_, l) in stmts if isinstance(s_, (ast.Import, ast.ImportFrom)))
        keep_lines[last_import - 1] = keep_lines[last_import - 1] + "\n" + "\n".join(tail)
        text = "\n".join(l for l in keep_lines if l is not None)
        new_origin = re.sub(r"\n{4,}", "\n\n\n", text).rstrip() + "\n"
    else:
        text = "\n".join(l for l in keep_lines if l is not None)
        text = re.sub(r"\n{4,}", "\n\n\n", text).rstrip() + "\n"
        new_origin = text + "\n\n" + "\n".join(tail) + "\n"

    folder = os.path.dirname(origin_path)
    for mod, source in new_sources.items():
        open(os.path.join(folder, mod + ".py"), "w", encoding="utf-8").write(source)
    open(origin_path, "w", encoding="utf-8").write(new_origin)
    for mod in order:
        print(mod, len(new_sources[mod].splitlines()), "lines")
    print(origin, len(new_origin.splitlines()), "lines")


def mod_doc(spec, mod):
    return next(m.get("doc", mod) for m in spec["modules"] if m["name"] == mod)


def wrap(prefix, names, width=110):
    line = prefix + ", ".join(names)
    if len(line) <= width:
        return line
    out, cur = [prefix + "("], "    "
    for n in names:
        piece = n + ", "
        if len(cur) + len(piece) > width:
            out.append(cur.rstrip())
            cur = "    "
        cur += piece
    out.append(cur.rstrip().rstrip(","))
    out.append(")")
    return "\n".join(out)


if __name__ == "__main__":
    main(sys.argv[1])
