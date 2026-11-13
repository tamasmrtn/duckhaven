"""Read a dashboard file, strictly, and say exactly where it is wrong.

``yaml.safe_load`` is safe but forgiving in ways a shared, reviewed file cannot
afford. It keeps the last of two duplicate keys, reads ``no`` and ``on`` as
booleans (so the country code ``NO`` becomes ``False``), reads ``1:30`` as 90,
and follows anchors and aliases. This loader refuses all of that. It also caps
the file's size and nesting, and remembers where every value sits, so each
diagnostic carries the line and column to fix.

Every problem comes back as a :class:`Diagnostic`: a path into the document, a
line and column when the source was YAML, a message, and a hint when there is a
likely fix.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass

import yaml
from pydantic import BaseModel, ValidationError
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from api.services.dashboards import spec as spec_module
from api.services.dashboards.spec import (
    SPEC_VERSION,
    SUPPORTED_SPEC_VERSIONS,
    DashboardSpec,
    check,
)

MAX_BYTES = 256 * 1024
MAX_DEPTH = 12

_Path = tuple[str | int, ...]


@dataclass(frozen=True)
class Diagnostic:
    path: str
    line: int | None
    col: int | None
    message: str
    hint: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class DashboardInvalid(ValueError):
    """The document is not a valid dashboard. ``diagnostics`` says why and where."""

    code = "invalid_dashboard"

    def __init__(self, diagnostics: list[Diagnostic]):
        self.diagnostics = diagnostics
        super().__init__(diagnostics[0].message)


class UnsupportedSpecVersion(DashboardInvalid):
    """The file is written for a format version this server does not read."""

    code = "unsupported_spec_version"


@dataclass(frozen=True)
class Loaded:
    spec: DashboardSpec
    warnings: list[Diagnostic]


def load(source: str | bytes) -> Loaded:
    """Parse and validate a dashboard YAML document."""
    raw = source.encode() if isinstance(source, str) else source
    if len(raw) > MAX_BYTES:
        raise DashboardInvalid(
            [Diagnostic("", None, None, f"A dashboard file is at most {MAX_BYTES // 1024} KB.")]
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DashboardInvalid(
            [Diagnostic("", None, None, "A dashboard file is UTF-8 text.")]
        ) from exc

    try:
        loader = _Loader(text)
        try:
            node = loader.get_single_node()
        finally:
            loader.dispose()
    except yaml.MarkedYAMLError as exc:
        raise DashboardInvalid([_yaml_diagnostic(exc)]) from exc
    except yaml.YAMLError as exc:
        raise DashboardInvalid([Diagnostic("", None, None, f"Not valid YAML: {exc}")]) from exc
    if node is None:
        raise DashboardInvalid([Diagnostic("", None, None, "The file is empty.", hint=_START_HINT)])

    positions = _Positions()
    data = positions.build(node, loader)
    return _validate(data, positions)


def parse(data: object) -> Loaded:
    """Validate a dashboard that arrived already parsed, as a JSON body."""
    return _validate(data, _Positions())


_START_HINT = f"Start the file with version: {SPEC_VERSION}, then name, title and rows."


def _validate(data: object, positions: _Positions) -> Loaded:
    if not isinstance(data, dict):
        raise DashboardInvalid(
            [positions.diagnostic((), "A dashboard is a mapping of keys.", hint=_START_HINT)]
        )
    _check_version(data, positions)
    try:
        spec = DashboardSpec.model_validate(data)
    except ValidationError as exc:
        raise DashboardInvalid(
            [_pydantic_diagnostic(e, data, positions) for e in exc.errors()]
        ) from exc

    errors, warnings = [], []
    for problem in check(spec):
        target = warnings if problem.warning else errors
        target.append(positions.diagnostic(_parse_path(problem.path), problem.message))
    if errors:
        raise DashboardInvalid(errors)
    return Loaded(spec=spec, warnings=warnings)


def _check_version(data: dict, positions: _Positions) -> None:
    if "version" not in data:
        raise DashboardInvalid([positions.diagnostic((), "version is required.", hint=_START_HINT)])
    version = data["version"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise DashboardInvalid(
            [
                positions.diagnostic(
                    ("version",), f"version is a whole number, such as {SPEC_VERSION}."
                )
            ]
        )
    if version in SUPPORTED_SPEC_VERSIONS:
        return
    if version > SPEC_VERSION:
        message = (
            f"This file is written for dashboard format version {version}; this server "
            f"reads {_versions()}. Upgrade DuckHaven to load it."
        )
    else:
        message = (
            f"Dashboard format version {version} is no longer read; this server "
            f"reads {_versions()}."
        )
    raise UnsupportedSpecVersion([positions.diagnostic(("version",), message)])


def _versions() -> str:
    return " and ".join(str(v) for v in SUPPORTED_SPEC_VERSIONS)


# ── YAML ──────────────────────────────────────────────────────────────────────


_BOOL = "tag:yaml.org,2002:bool"
_INT = "tag:yaml.org,2002:int"
_FLOAT = "tag:yaml.org,2002:float"
_MERGE = "tag:yaml.org,2002:merge"


class _Loader(yaml.SafeLoader):
    """SafeLoader with YAML 1.2's plain scalars and no references.

    Only ``true`` and ``false`` are booleans, and numbers are plain decimals: no
    sexagesimal ``1:30``, no ``0x``, no ``1_000``, no ``.inf``. Anything else
    stays text, which a strictly typed field then refuses by name.
    """

    yaml_implicit_resolvers = {
        first: [(tag, regex) for tag, regex in resolvers if tag not in (_BOOL, _INT, _FLOAT)]
        for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }

    def __init__(self, stream: str):
        super().__init__(stream)
        self._depth = 0

    def compose_node(self, parent, index):
        event = self.peek_event()
        if isinstance(event, yaml.AliasEvent):
            raise _problem("Aliases (*name) are not allowed; write the value out.", event)
        if getattr(event, "anchor", None) is not None:
            raise _problem("Anchors (&name) are not allowed; write each value out.", event)
        if isinstance(event, yaml.MappingStartEvent | yaml.SequenceStartEvent):
            self._depth += 1
            if self._depth > MAX_DEPTH:
                raise _problem(f"The file nests deeper than {MAX_DEPTH} levels.", event)
        node = super().compose_node(parent, index)
        if isinstance(node, MappingNode | SequenceNode):
            self._depth -= 1
        return node


_Loader.add_implicit_resolver(_BOOL, re.compile(r"^(?:true|false)$"), list("tf"))
_Loader.add_implicit_resolver(_INT, re.compile(r"^[-+]?(?:0|[1-9][0-9]*)$"), list("-+0123456789"))
_Loader.add_implicit_resolver(
    _FLOAT,
    re.compile(r"^[-+]?(?:[0-9]+\.[0-9]*|\.[0-9]+|[0-9]+(?=[eE]))(?:[eE][-+]?[0-9]+)?$"),
    list("-+0123456789."),
)


def _problem(message: str, event) -> yaml.MarkedYAMLError:
    return yaml.MarkedYAMLError(problem=message, problem_mark=event.start_mark)


def _yaml_diagnostic(exc: yaml.MarkedYAMLError) -> Diagnostic:
    mark = exc.problem_mark or exc.context_mark
    message = exc.problem or "not valid YAML"
    if exc.context and exc.problem:
        message = f"{exc.context}, {message}"
    message = message[0].upper() + message[1:].rstrip(".") + "."
    if mark is None:
        return Diagnostic("", None, None, message)
    return Diagnostic("", mark.line + 1, mark.column + 1, message)


class _Positions:
    """Where each value, and each key, sits in the source."""

    def __init__(self) -> None:
        self.values: dict[_Path, tuple[int, int]] = {}
        self.keys: dict[_Path, tuple[int, int]] = {}

    def build(self, node: Node, loader: _Loader, path: _Path = ()) -> object:
        self.values[path] = _mark(node)
        if isinstance(node, MappingNode):
            out: dict = {}
            for key_node, value_node in node.value:
                if key_node.tag == _MERGE:
                    raise _invalid("Merge keys (<<) are not allowed; write each key out.", key_node)
                if not isinstance(key_node, ScalarNode) or not isinstance(
                    key := loader.construct_object(key_node), str
                ):
                    raise _invalid("Keys are plain text.", key_node)
                if key in out:
                    first = self.keys[(*path, key)]
                    raise _invalid(
                        f"{key!r} appears twice in the same mapping; it was first set on "
                        f"line {first[0]}.",
                        key_node,
                    )
                self.keys[(*path, key)] = _mark(key_node)
                out[key] = self.build(value_node, loader, (*path, key))
            return out
        if isinstance(node, SequenceNode):
            return [self.build(item, loader, (*path, i)) for i, item in enumerate(node.value)]
        try:
            return loader.construct_object(node)
        except yaml.MarkedYAMLError as exc:
            raise DashboardInvalid([_yaml_diagnostic(exc)]) from exc

    def diagnostic(
        self, path: _Path, message: str, *, at_key: bool = False, hint: str | None = None
    ) -> Diagnostic:
        """A diagnostic at ``path``, or at the nearest enclosing value it can place."""
        line_col = None
        if at_key:
            line_col = self.keys.get(path)
        probe = path
        while line_col is None and probe:
            line_col = self.values.get(probe)
            probe = probe[:-1]
        if line_col is None:
            line_col = self.values.get(())
        line, col = line_col if line_col else (None, None)
        return Diagnostic(_format_path(path), line, col, message, hint)


def _mark(node: Node) -> tuple[int, int]:
    return node.start_mark.line + 1, node.start_mark.column + 1


def _invalid(message: str, node: Node) -> DashboardInvalid:
    line, col = _mark(node)
    return DashboardInvalid([Diagnostic("", line, col, message)])


# ── Paths and messages ────────────────────────────────────────────────────────


def _format_path(path: _Path) -> str:
    out = ""
    for part in path:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


def _parse_path(text: str) -> _Path:
    parts: list[str | int] = []
    for name, index in re.findall(r"([^.\[\]]+)|\[(\d+)\]", text):
        parts.append(int(index) if index else name)
    return tuple(parts)


def _data_path(loc: tuple, data: object) -> _Path:
    """Pydantic's location, minus the union tags it adds that are not in the file."""
    path: list[str | int] = []
    current = data
    for part in loc:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and isinstance(part, int) and part < len(current):
            current = current[part]
        else:
            continue
        path.append(part)
    return tuple(path)


def _all_keys() -> set[str]:
    keys: set[str] = set()
    for value in vars(spec_module).values():
        if isinstance(value, type) and issubclass(value, BaseModel):
            keys |= set(value.model_fields)
    return keys


_KNOWN_KEYS = _all_keys()

_PATTERN_HINTS = {
    spec_module.NAME_PATTERN: "Use lowercase letters, digits, - and _, starting with a letter.",
    spec_module.IDENTIFIER_PATTERN: "Use lowercase letters, digits and _, starting with a letter.",
    spec_module.PARAM_REF_PATTERN: "Refer to a parameter as $name.",
    spec_module.BINDING_PATTERN: "Bind a parameter as $name, $name.start or $name.end.",
}


def _pydantic_diagnostic(error: dict, data: object, positions: _Positions) -> Diagnostic:
    kind = error["type"]
    loc = tuple(error["loc"])
    path = _data_path(loc, data)

    if kind == "extra_forbidden":
        key = loc[-1]
        close = difflib.get_close_matches(str(key), _KNOWN_KEYS, n=1)
        return positions.diagnostic(
            path,
            f"Unknown key {key!r}.",
            at_key=True,
            hint=f"Did you mean {close[0]!r}?" if close else None,
        )
    if kind == "missing":
        return positions.diagnostic(path, f"{loc[-1]!r} is required.")
    if kind == "union_tag_invalid":
        ctx = error["ctx"]
        return positions.diagnostic(
            (*path, ctx["discriminator"].strip("'")),
            f"Unknown type {ctx['tag']!r}; use one of {ctx['expected_tags']}.",
        )
    if kind == "string_pattern_mismatch":
        pattern = error["ctx"]["pattern"]
        return positions.diagnostic(
            path, f"{error['input']!r} is not allowed here.", hint=_PATTERN_HINTS.get(pattern)
        )
    message = error["msg"].removeprefix("Value error, ")
    if kind in ("literal_error", "enum"):
        message = f"{error['input']!r} is not allowed here. {message}."
    elif kind.endswith(("_type", "_parsing")) and isinstance(error.get("input"), str | int | float):
        message = f"{message}, not {error['input']!r}."
    return positions.diagnostic(path, message)
