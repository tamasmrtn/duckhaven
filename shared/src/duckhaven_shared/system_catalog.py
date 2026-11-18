"""Names the control plane and the agent agree on for the ``duckhaven`` system catalog.

The control plane sends it as one more catalog to attach, of kind ``system``,
only for a statement that reads it and only to an agent advertising the
``system_catalog`` protocol feature. The agent attaches it read-only through the
``postgres`` extension and never makes it the current catalog.
"""

KIND_SYSTEM = "system"
SYSTEM_CATALOG_ALIAS = "duckhaven"
SYSTEM_CATALOG_FEATURE = "system_catalog"
