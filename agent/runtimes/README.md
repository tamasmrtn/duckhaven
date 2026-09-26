# Runtime DuckDB pins

Each non-default agent runtime (see `shared/src/duckhaven_shared/runtimes.py`) pins
its own DuckDB here:

- `<id>.in` holds the requirement you edit, for example `duckdb==2.0.1`.
- `<id>.txt` is the hash-locked output of `make runtimes-lock`. Don't edit it by hand.

`agent/Dockerfile` installs `<id>.txt` over the locked environment when it builds
that runtime. The default runtime has no files here, because its DuckDB comes from
`uv.lock`.
