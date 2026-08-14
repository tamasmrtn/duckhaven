"""The lineage graph: deriving it, importing it, storing it, traversing it.

- ``keys`` — the canonical asset key both endpoints of an edge are addressed by.
- ``extract`` — recovering ``source -> target`` pairs from executed SQL.
- ``ingest`` — persisting edges, whatever produced them, plus reconciliation.
- ``traverse`` — bounded graph walks for the read API.
- ``redact`` — clamping a walk to the caller's workspace and grants.
- ``providers`` — adapters translating an external producer's format into edges.
"""
