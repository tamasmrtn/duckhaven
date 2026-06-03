#!/bin/sh
# Idempotent wrapper around the Polaris admin-tool bootstrap.
#
# Re-running `docker compose up` re-runs this one-shot. The admin tool exits 3
# ("realm already exists") on a second run, which would otherwise block the
# dependent services that wait for it to complete successfully. Treat an
# already-bootstrapped realm as success so selective up/start stays smooth.
"$@"
status=$?
if [ "$status" -eq 0 ] || [ "$status" -eq 3 ]; then
  exit 0
fi
exit "$status"
