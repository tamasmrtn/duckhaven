"""Which datasets are reachable from which, and by exactly what path.

Joins here are *declared*, not inferred. Deriving them from key types means
policing a matrix of which combinations are legal; declaring them means the
illegal combination is never written down in the first place. What is left for
this module is the part declaration cannot settle on its own — which of several
declared joins to use, and how far to walk.

Three rules, all of them about refusing to guess:

* **Direction.** Traversal only ever follows a relationship from its ``left``
  (many) side to its ``right`` (unique) side. Walking the other way multiplies
  fact rows, so the graph is directed and the reverse edge simply does not exist.
* **Depth.** Two hops. Three tables in one query is where the paths stop being
  obvious to the person reading the answer, and an answer nobody can check is
  worth less than a refusal.
* **Ambiguity is an error.** If two distinct paths reach the same dataset, both
  are returned in the failure rather than one being chosen. Picking one would be
  right about half the time and silent about the other half.
"""

from __future__ import annotations

from api.services.semantic.errors import SemanticError
from api.services.semantic.model import LoadedModel, Relationship

MAX_HOPS = 2

Path = tuple[Relationship, ...]


def _paths_from(model: LoadedModel, start: str) -> dict[str, list[Path]]:
    """Every path of at most ``MAX_HOPS`` from ``start``, keyed by destination.

    Breadth-first and exhaustive rather than shortest-first, because the question
    is not "is there a way there" but "is there exactly one way there".
    """
    found: dict[str, list[Path]] = {}
    frontier: list[tuple[str, Path]] = [(start, ())]

    for _ in range(MAX_HOPS):
        nxt: list[tuple[str, Path]] = []
        for node, path in frontier:
            for rel in model.relationships:
                if rel.left != node:
                    continue
                # A path that revisits a dataset is a cycle, not a join path.
                visited = {start} | {r.right for r in path}
                if rel.right in visited:
                    continue
                extended = (*path, rel)
                found.setdefault(rel.right, []).append(extended)
                nxt.append((rel.right, extended))
        frontier = nxt
        if not frontier:
            break

    return found


def reachable(model: LoadedModel, start: str) -> dict[str, Path]:
    """Datasets reachable from ``start`` by exactly one path, with that path.

    A dataset reachable by two paths is deliberately absent: it is not usable
    without a decision nobody has made, so it must not appear in a list of things
    that will work.
    """
    return {dest: paths[0] for dest, paths in _paths_from(model, start).items() if len(paths) == 1}


def resolve_path(model: LoadedModel, start: str, dest: str) -> Path:
    """The single join path from ``start`` to ``dest``.

    Raises :class:`SemanticError` when there is no path or more than one, in both
    cases naming what *would* have worked.
    """
    if start == dest:
        return ()

    candidates = _paths_from(model, start).get(dest, [])

    if not candidates:
        options = sorted(reachable(model, start))
        raise SemanticError(
            f"{dest!r} cannot be reached from {start!r} by any declared relationship, "
            f"so a query combining them would need a join nobody has defined.",
            alternatives=options,
        )

    if len(candidates) > 1:
        rendered = sorted(" -> ".join(r.name for r in path) for path in candidates)
        raise SemanticError(
            f"There is more than one way to join {start!r} to {dest!r}, and they can give "
            f"different answers, so this query is ambiguous. Ask which path is meant, or "
            f"query the metrics separately. Candidate paths: {'; '.join(rendered)}"
        )

    return candidates[0]


def merge_paths(paths: list[Path]) -> list[Relationship]:
    """Flatten several join paths into one deduplicated join list, in order.

    Order matters: a two-hop path's first relationship has to be joined before its
    second, and two paths sharing a first hop must join it once.
    """
    seen: set = set()
    merged: list[Relationship] = []
    for path in paths:
        for rel in path:
            if rel.id in seen:
                continue
            seen.add(rel.id)
            merged.append(rel)
    return merged
