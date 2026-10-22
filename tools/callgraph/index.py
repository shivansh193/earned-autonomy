"""Build a call graph of a Python application plus selected dependency packages.

The analysis is intentionally simple and explainable:

* modules are parsed with ``ast``; imports (absolute and relative) are resolved to
  qualified names;
* a call ``f()`` / ``mod.f()`` / ``Cls.m()`` / ``obj.m()`` becomes an edge when the
  callee can be resolved; ``obj`` is resolved when its type is known from an
  assignment (``obj = Cls(...)``), a ``with Cls(...) as obj``, a parameter
  annotation, or ``self``;
* module-level aliases such as ``decode = _jwt_global_obj.decode`` are followed;
* FastAPI dependency injection (``Depends(f)``, ``Annotated[T, Depends(f)]`` and
  aliases of those) adds an edge from the function to the dependency;
* FastAPI routes are found from ``@router.<method>(path)`` decorators and the full
  path is rebuilt from ``APIRouter(prefix=...)`` and ``include_router(..., prefix=...)``.

Calls whose receiver type is unknown are kept by method name only, so the
reachability report can say "possibly reachable" instead of silently dropping them.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


@dataclass
class Function:
    qualname: str
    module: str
    cls: str | None
    node: ast.AST
    calls: set[str] = field(default_factory=set)
    # method names called on receivers of unknown type: name -> line
    unresolved_methods: dict[str, int] = field(default_factory=dict)
    lineno: int = 0


@dataclass
class ClassInfo:
    qualname: str
    module: str
    bases: list[str] = field(default_factory=list)
    methods: dict[str, str] = field(default_factory=dict)
    constants: dict[str, ast.expr] = field(default_factory=dict)


@dataclass
class Module:
    name: str
    path: Path
    is_package: bool
    tree: ast.Module
    imports: dict[str, str] = field(default_factory=dict)
    defs: dict[str, str] = field(default_factory=dict)
    assigns: dict[str, ast.expr] = field(default_factory=dict)
    annotated_aliases: dict[str, ast.expr] = field(default_factory=dict)

    @property
    def package(self) -> str:
        return self.name if self.is_package else self.name.rpartition(".")[0]


@dataclass
class Route:
    method: str
    path: str
    handler: str
    router: str  # qualname of the router variable

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}"


@dataclass
class Index:
    modules: dict[str, Module] = field(default_factory=dict)
    functions: dict[str, Function] = field(default_factory=dict)
    classes: dict[str, ClassInfo] = field(default_factory=dict)
    routes: list[Route] = field(default_factory=list)
    defines: dict[str, str] = field(default_factory=dict)
    parse_errors: list[dict[str, str]] = field(default_factory=list)

    # ------------------------------------------------------------------ names
    def canonical(self, name: str, _seen: set[str] | None = None) -> str:
        """Follow re-exports and module-level aliases to the defining qualname."""
        seen = _seen if _seen is not None else set()
        if name in seen:
            return name
        seen.add(name)
        if name in self.functions or name in self.classes or name in self.modules:
            return name
        parts = name.split(".")
        for i in range(len(parts) - 1, 0, -1):
            mod_name = ".".join(parts[:i])
            mod = self.modules.get(mod_name)
            if mod is None:
                continue
            head, rest = parts[i], parts[i + 1 :]
            target: str | None = None
            if head in mod.defs:
                target = mod.defs[head]
            elif head in mod.imports:
                target = mod.imports[head]
            elif head in mod.assigns:
                target = self._resolve_alias_expr(mod, mod.assigns[head])
            if target is None:
                return name
            full = ".".join([target, *rest])
            if rest and target in self.classes:
                method = self.lookup_method(target, rest[0])
                if method:
                    full = ".".join([method, *rest[1:]])
            return self.canonical(full, seen)
        return name

    def _resolve_alias_expr(self, mod: Module, expr: ast.expr) -> str | None:
        scope = Scope(self, mod, None, {})
        resolved = scope.resolve(expr)
        if resolved is None:
            return None
        kind, value = resolved
        return value if kind == "name" else None

    def lookup_method(self, cls: str, name: str, _depth: int = 0) -> str | None:
        info = self.classes.get(cls)
        if info is None or _depth > 20:
            return None
        if name in info.methods:
            return info.methods[name]
        for base in info.bases:
            found = self.lookup_method(self.canonical(base), name, _depth + 1)
            if found:
                return found
        return None

    def class_constant(self, cls: str, name: str, _depth: int = 0) -> ast.expr | None:
        info = self.classes.get(cls)
        if info is None or _depth > 20:
            return None
        if name in info.constants:
            return info.constants[name]
        for base in info.bases:
            found = self.class_constant(self.canonical(base), name, _depth + 1)
            if found is not None:
                return found
        return None


class Scope:
    """Name resolution inside one function (or module level when func is None)."""

    def __init__(
        self, index: Index, module: Module, cls: str | None, local_types: dict[str, str]
    ) -> None:
        self.index = index
        self.module = module
        self.cls = cls
        self.local_types = local_types

    def resolve(self, expr: ast.expr) -> tuple[str, str] | None:
        """Return ("name", qualname) for a module/function/class reference,
        ("instance", class_qualname) for an object of a known class, or None."""
        if isinstance(expr, ast.Name):
            if expr.id == "self" and self.cls:
                return ("instance", self.cls)
            if expr.id in self.local_types:
                return ("instance", self.local_types[expr.id])
            mod = self.module
            if expr.id in mod.defs:
                return ("name", mod.defs[expr.id])
            if expr.id in mod.imports:
                return self._qualname_ref(self.index.canonical(mod.imports[expr.id]))
            if expr.id in mod.assigns:
                value = mod.assigns[expr.id]
                if isinstance(value, ast.Call):
                    inst = self._instance_of_call(value)
                    if inst:
                        return ("instance", inst)
                elif isinstance(value, (ast.Name, ast.Attribute)) and value is not expr:
                    inner = Scope(self.index, mod, None, {}).resolve(value)
                    if inner:
                        return inner
            return None
        if isinstance(expr, ast.Attribute):
            base = self.resolve(expr.value)
            if base is None:
                return None
            kind, value = base
            if kind == "instance":
                method = self.index.lookup_method(value, expr.attr)
                return ("name", method) if method else None
            if value in self.index.classes:
                method = self.index.lookup_method(value, expr.attr)
                if method:
                    return ("name", method)
            return self._qualname_ref(self.index.canonical(f"{value}.{expr.attr}"))
        if isinstance(expr, ast.Call):
            inst = self._instance_of_call(expr)
            if inst:
                return ("instance", inst)
        return None

    def _qualname_ref(self, qual: str) -> tuple[str, str]:
        """A qualname that names a module-level object (``settings = Settings()``)
        resolves to an instance of its class."""
        mod_name, _, attr = qual.rpartition(".")
        mod = self.index.modules.get(mod_name)
        if mod is not None and attr in mod.assigns and attr not in mod.defs:
            value = mod.assigns[attr]
            if isinstance(value, ast.Call):
                inst = Scope(self.index, mod, None, {})._instance_of_call(value)
                if inst:
                    return ("instance", inst)
        return ("name", qual)

    def _instance_of_call(self, call: ast.Call) -> str | None:
        target = self.resolve(call.func)
        if target and target[0] == "name" and target[1] in self.index.classes:
            return target[1]
        return None

    def annotation_type(self, ann: ast.expr | None) -> str | None:
        """Class qualname from an annotation, unwrapping Annotated[...] and aliases."""
        if ann is None:
            return None
        if isinstance(ann, ast.Subscript):
            base = self.resolve(ann.value)
            if base and base[1].endswith("Annotated"):
                first = ann.slice.elts[0] if isinstance(ann.slice, ast.Tuple) else ann.slice
                return self.annotation_type(first)
            return None
        if isinstance(ann, ast.Name) and ann.id in self.module.annotated_aliases:
            return self.annotation_type(self.module.annotated_aliases[ann.id])
        resolved = self.resolve(ann)
        if resolved and resolved[0] == "name":
            qual = resolved[1]
            if qual in self.index.classes:
                return qual
            alias = self._annotated_alias(qual)
            if alias is not None:
                mod, expr = alias
                return Scope(self.index, mod, None, {}).annotation_type(expr)
        return None

    def _annotated_alias(self, qualname: str) -> tuple[Module, ast.expr] | None:
        mod_name, _, attr = qualname.rpartition(".")
        mod = self.index.modules.get(mod_name)
        if mod and attr in mod.annotated_aliases:
            return mod, mod.annotated_aliases[attr]
        return None

    def dependencies_in(self, expr: ast.expr | None) -> list[str]:
        """Functions injected via Depends(...) inside an annotation or default."""
        if expr is None:
            return []
        found: list[str] = []
        for node in ast.walk(expr):
            if isinstance(node, ast.Call) and _call_name(node.func) == "Depends" and node.args:
                dep = self.resolve(node.args[0])
                if dep and dep[0] == "name":
                    found.append(dep[1])
            elif isinstance(node, ast.Name):
                alias = None
                if node.id in self.module.annotated_aliases:
                    alias = (self.module, self.module.annotated_aliases[node.id])
                else:
                    resolved = self.resolve(node)
                    if resolved and resolved[0] == "name":
                        alias = self._annotated_alias(resolved[1])
                if alias is not None:
                    mod, aexpr = alias
                    found.extend(Scope(self.index, mod, None, {}).dependencies_in(aexpr))
        return found


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


# ---------------------------------------------------------------------- loading
def _module_name(root: Path, path: Path) -> tuple[str, bool]:
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts = parts[:-1]
    return ".".join(parts), is_package


def _iter_py(package_dir: Path):
    for dirpath, dirnames, filenames in os.walk(package_dir):
        dirnames[:] = [
            d for d in dirnames if d not in {"__pycache__", "tests", "test", ".venv", "alembic"}
        ]
        for fn in filenames:
            if fn.endswith(".py"):
                yield Path(dirpath) / fn


def _load_module(index: Index, root: Path, path: Path) -> None:
    name, is_package = _module_name(root, path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        # Never skip silently: an unparsed module can hide a reachable path.
        index.parse_errors.append({"module": name, "path": str(path), "error": str(exc)})
        return
    index.modules[name] = Module(name=name, path=path, is_package=is_package, tree=tree)


def _absolute_import(mod: Module, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    base = mod.package.split(".") if mod.package else []
    if node.level > 1:
        base = base[: len(base) - (node.level - 1)]
    if node.module:
        base.append(node.module)
    return ".".join(base)


def _collect_definitions(index: Index, mod: Module) -> None:
    for node in mod.tree.body:
        _collect_stmt(index, mod, node)


def _collect_stmt(index: Index, mod: Module, node: ast.stmt) -> None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.asname:
                mod.imports[alias.asname] = alias.name
            else:
                top = alias.name.split(".")[0]
                mod.imports[top] = top
    elif isinstance(node, ast.ImportFrom):
        source = _absolute_import(mod, node)
        for alias in node.names:
            if alias.name == "*":
                continue
            mod.imports[alias.asname or alias.name] = f"{source}.{alias.name}"
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        qual = f"{mod.name}.{node.name}"
        mod.defs[node.name] = qual
        index.functions[qual] = Function(qual, mod.name, None, node, lineno=node.lineno)
    elif isinstance(node, ast.ClassDef):
        qual = f"{mod.name}.{node.name}"
        mod.defs[node.name] = qual
        info = ClassInfo(qualname=qual, module=mod.name)
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                mqual = f"{qual}.{item.name}"
                info.methods[item.name] = mqual
                index.functions[mqual] = Function(mqual, mod.name, qual, item, lineno=item.lineno)
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                if item.value is not None:
                    info.constants[item.target.id] = item.value
            elif isinstance(item, ast.Assign):
                for t in item.targets:
                    if isinstance(t, ast.Name):
                        info.constants[t.id] = item.value
        index.classes[qual] = info
        info._bases_nodes = node.bases  # type: ignore[attr-defined]
    elif isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if value is None:
            return
        for t in targets:
            if isinstance(t, ast.Name):
                if _is_annotated(value):
                    mod.annotated_aliases[t.id] = value
                else:
                    mod.assigns[t.id] = value
    elif isinstance(node, ast.If):
        # e.g. `if settings.X: ...` / `if TYPE_CHECKING:` — take both branches
        for child in [*node.body, *node.orelse]:
            _collect_stmt(index, mod, child)
    elif isinstance(node, ast.Try):
        for child in [*node.body, *node.orelse, *node.finalbody]:
            _collect_stmt(index, mod, child)
        for handler in node.handlers:
            for child in handler.body:
                _collect_stmt(index, mod, child)


def _is_annotated(value: ast.expr) -> bool:
    if isinstance(value, ast.Subscript):
        name = _call_name(value.value)
        return name == "Annotated"
    return False


def _resolve_bases(index: Index) -> None:
    for info in index.classes.values():
        mod = index.modules[info.module]
        scope = Scope(index, mod, None, {})
        for b in getattr(info, "_bases_nodes", []):
            resolved = scope.resolve(b)
            if resolved and resolved[0] == "name":
                info.bases.append(resolved[1])


# ------------------------------------------------------------------- calls/edges
class _CallVisitor(ast.NodeVisitor):
    def __init__(self, index: Index, func: Function) -> None:
        self.index = index
        self.func = func
        mod = index.modules[func.module]
        self.scope = Scope(index, mod, func.cls, {})

    def run(self) -> None:
        node = self.func.node
        assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        defaults = [None] * (len(node.args.posonlyargs) + len(node.args.args) - len(node.args.defaults))
        defaults += list(node.args.defaults)
        defaults += list(node.args.kw_defaults)
        for arg, default in zip(args, defaults, strict=False):
            typ = self.scope.annotation_type(arg.annotation)
            if typ:
                self.scope.local_types[arg.arg] = typ
            for dep in self.scope.dependencies_in(arg.annotation):
                self._edge(dep)
            for dep in self.scope.dependencies_in(default):
                self._edge(dep)
        for deco in node.decorator_list:
            if isinstance(deco, ast.Call):
                for kw in deco.keywords:
                    if kw.arg == "dependencies":
                        for dep in self.scope.dependencies_in(kw.value):
                            self._edge(dep)
        for stmt in node.body:
            self.visit(stmt)

    def _edge(self, target: str) -> None:
        target = self.index.canonical(target)
        if target in self.index.classes:
            init = self.index.lookup_method(target, "__init__")
            self.func.calls.add(init or target)
        else:
            self.func.calls.add(target)

    # nested defs are analysed as part of the enclosing function
    def visit_Assign(self, node: ast.Assign) -> None:
        self.generic_visit(node)
        typ = self._type_of(node.value)
        for t in node.targets:
            if isinstance(t, ast.Name) and typ:
                self.scope.local_types[t.id] = typ

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.generic_visit(node)
        if isinstance(node.target, ast.Name):
            typ = self.scope.annotation_type(node.annotation) or (
                self._type_of(node.value) if node.value else None
            )
            if typ:
                self.scope.local_types[node.target.id] = typ

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            typ = self._type_of(item.context_expr)
            if typ and isinstance(item.optional_vars, ast.Name):
                self.scope.local_types[item.optional_vars.id] = typ
        for stmt in node.body:
            self.visit(stmt)

    visit_AsyncWith = visit_With  # type: ignore[assignment]

    def _type_of(self, expr: ast.expr) -> str | None:
        resolved = self.scope.resolve(expr)
        if resolved and resolved[0] == "instance":
            return resolved[1]
        return None

    def visit_Call(self, node: ast.Call) -> None:
        resolved = self.scope.resolve(node.func)
        if resolved and resolved[0] == "name":
            self._edge(resolved[1])
        elif isinstance(node.func, ast.Attribute):
            self.func.unresolved_methods.setdefault(node.func.attr, node.lineno)
        if _call_name(node.func) == "Depends" and node.args:
            dep = self.scope.resolve(node.args[0])
            if dep and dep[0] == "name":
                self._edge(dep[1])
        self.generic_visit(node)

    # A function used as a value (passed to asyncio.to_thread, a callback, or kept in a
    # module-level dispatch table the function reads) may be called later. Count it as a
    # call: over-reporting reachability is the safe direction for security findings.
    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self._reference(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load):
            self._reference(node)
        self.generic_visit(node)

    def _reference(self, expr: ast.expr) -> None:
        resolved = self.scope.resolve(expr)
        if resolved and resolved[0] == "name":
            target = self.index.canonical(resolved[1])
            if target in self.index.functions:
                self.func.calls.add(target)
                return
        if isinstance(expr, ast.Name) and expr.id not in self.scope.local_types:
            mod = self.index.modules[self.func.module]
            value = mod.assigns.get(expr.id)
            if value is None or not isinstance(value, (ast.Dict, ast.List, ast.Tuple, ast.Set)):
                return
            table = Scope(self.index, mod, None, {})
            for sub in ast.walk(value):
                if isinstance(sub, (ast.Name, ast.Attribute)) and isinstance(sub.ctx, ast.Load):
                    r = table.resolve(sub)
                    if r and r[0] == "name":
                        target = self.index.canonical(r[1])
                        if target in self.index.functions or target in self.index.classes:
                            self._edge(target)


# ------------------------------------------------------------------------ routes
def _const_str(index: Index, mod: Module, expr: ast.expr | None) -> str:
    if expr is None:
        return ""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, ast.JoinedStr):
        out = []
        for v in expr.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            elif isinstance(v, ast.FormattedValue):
                out.append(_const_str(index, mod, v.value))
        return "".join(out)
    text = ast.unparse(expr)
    if text in index.defines:
        return index.defines[text]
    scope = Scope(index, mod, None, {})
    if isinstance(expr, ast.Attribute):
        base = scope.resolve(expr.value)
        if base:
            kind, value = base
            cls = value if kind == "instance" else value if value in index.classes else None
            if cls:
                const = index.class_constant(cls, expr.attr)
                if const is not None:
                    return _const_str(index, index.modules[index.classes[cls].module], const)
    if isinstance(expr, ast.Name) and expr.id in mod.assigns:
        return _const_str(index, mod, mod.assigns[expr.id])
    return "{" + text + "}"


def _router_var(index: Index, mod: Module, expr: ast.expr) -> str | None:
    """Qualname of a router/app variable referenced by expr (e.g. `items.router`)."""
    if isinstance(expr, ast.Name):
        if expr.id in mod.assigns:
            return f"{mod.name}.{expr.id}"
        if expr.id in mod.imports:
            return index.canonical(mod.imports[expr.id])
    if isinstance(expr, ast.Attribute):
        base = Scope(index, mod, None, {}).resolve(expr.value)
        if base and base[0] == "name":
            return f"{base[1]}.{expr.attr}"
    return None


def _collect_routes(index: Index) -> None:
    own_prefix: dict[str, str] = {}
    parents: dict[str, list[tuple[str, str]]] = {}  # child -> [(parent, prefix)]
    for mod in index.modules.values():
        for name, value in mod.assigns.items():
            if isinstance(value, ast.Call) and _call_name(value.func) in {"APIRouter", "FastAPI"}:
                prefix = ""
                for kw in value.keywords:
                    if kw.arg == "prefix":
                        prefix = _const_str(index, mod, kw.value)
                own_prefix[f"{mod.name}.{name}"] = prefix
        for node in ast.walk(mod.tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "include_router"
                and node.args
            ):
                parent = _router_var(index, mod, node.func.value)
                child = _router_var(index, mod, node.args[0])
                if parent and child:
                    prefix = ""
                    for kw in node.keywords:
                        if kw.arg == "prefix":
                            prefix = _const_str(index, mod, kw.value)
                    parents.setdefault(child, []).append((parent, prefix))

    def full_prefixes(router: str, depth: int = 0) -> list[str]:
        mine = own_prefix.get(router, "")
        ups = parents.get(router, [])
        if not ups or depth > 20:
            return [mine]
        return [up + inc + mine for p, inc in ups for up in full_prefixes(p, depth + 1)]

    for func in index.functions.values():
        node = func.node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        mod = index.modules[func.module]
        for deco in node.decorator_list:
            if not (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)):
                continue
            method = deco.func.attr
            if method not in HTTP_METHODS and method != "api_route":
                continue
            router = _router_var(index, mod, deco.func.value)
            if router is None or router not in own_prefix:
                continue
            path = _const_str(index, mod, deco.args[0]) if deco.args else ""
            for prefix in full_prefixes(router):
                index.routes.append(Route(method.upper(), prefix + path, func.qualname, router))


# ------------------------------------------------------------------------- entry
def build_index(
    app_root: Path,
    app_packages: list[str],
    dep_roots: list[Path] | None = None,
    dep_packages: list[str] | None = None,
    defines: dict[str, str] | None = None,
) -> Index:
    """Index ``app_packages`` under ``app_root`` and ``dep_packages`` found under
    any of ``dep_roots`` (e.g. a site-packages directory)."""
    index = Index(defines=dict(defines or {}))
    for pkg in app_packages:
        pkg_dir = app_root / pkg
        if pkg_dir.is_dir():
            for path in _iter_py(pkg_dir):
                _load_module(index, app_root, path)
        elif (app_root / f"{pkg}.py").is_file():
            _load_module(index, app_root, app_root / f"{pkg}.py")
    for pkg in dep_packages or []:
        for root in dep_roots or []:
            pkg_dir = root / pkg
            if pkg_dir.is_dir():
                for path in _iter_py(pkg_dir):
                    _load_module(index, root, path)
                break
            if (root / f"{pkg}.py").is_file():
                _load_module(index, root, root / f"{pkg}.py")
                break
    for mod in list(index.modules.values()):
        _collect_definitions(index, mod)
    _resolve_bases(index)
    for func in list(index.functions.values()):
        _CallVisitor(index, func).run()
    _collect_routes(index)
    return index
