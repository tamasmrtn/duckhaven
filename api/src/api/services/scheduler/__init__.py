"""Time-based job scheduler.

A leader-elected background loop (mirroring the maintenance scanner) that runs
saved queries on a cron cadence without an external cron job. Generic by design:
``Schedule.job_type`` discriminates the work and the loop dispatches each type
through a small seam; v1 implements only ``"saved_query"``. Every run is recorded
as a ``queries`` row tagged ``origin="scheduled"`` so it appears in History and in
the per-schedule run list.
"""
