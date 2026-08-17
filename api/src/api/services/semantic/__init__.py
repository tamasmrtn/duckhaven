"""The semantic layer: business definitions, and the SQL they compile to.

Six seams, each one module:

``model``     the in-memory shape of a loaded semantic model, decoupled from ORM
``joins``     which datasets are reachable from which, and by what path
``compile``   a metric request in, deterministic SQL out
``validate``  whether a model's bindings still hold against the live catalog
``retrieve``  which definitions a question is about
``impact``    which definitions depend on a given table or column

Nothing in here executes SQL or reads Polaris except :mod:`validate`, which does
so explicitly and on demand. The compiler is a pure function over a
:class:`~api.services.semantic.model.LoadedModel`, which is what makes its output
testable without a database and auditable without running it.
"""
