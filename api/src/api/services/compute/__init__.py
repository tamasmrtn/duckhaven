"""Elastic compute: provision agents on demand and terminate them when idle.

The control plane already knows how to *enroll* an agent (mint a bootstrap token,
the agent dials home and registers — `routers/admin/agents.py`, `routers/agents_ws.py`).
This package adds the missing piece: *lifecycle orchestration* — creating the
container that runs the agent, and tearing it down when it goes idle.

Structure mirrors the repo's existing seams rather than introducing new ones:

* Backends are selected by the ``provider`` string (like ``Schedule.job_type``),
  not a class hierarchy. ``backends.get_backend`` returns the one concrete backend;
  a Protocol is extracted only once a second cloud is real.
* The idle/leak reaper is a leader-elected loop (Postgres advisory lock) exactly
  like ``sql_sessions.reaper`` and the scheduler/scanner.
* Elastic lifecycle lives on ``Agent`` rows in Postgres (I9); the cloud is
  reconciled *to* Postgres, never trusted as a second source of truth.
"""
