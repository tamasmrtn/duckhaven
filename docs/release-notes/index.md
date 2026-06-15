# Release notes

DuckHaven releases are cut by pushing a `vX.Y.Z` Git tag — the tag is the source of truth (there is no version file).

## Where to find them

- **GitHub Releases** — each release's notes are generated from
  [Conventional Commits](https://www.conventionalcommits.org/) by [git-cliff](https://git-cliff.org/) and published at
  the project's [Releases page](https://github.com/tamasmrtn/duckhaven/releases).
- **Container images** — multi-arch images are published per release to `ghcr.io/tamasmrtn/duckhaven-api` and
  `ghcr.io/tamasmrtn/duckhaven-agent`, tagged `:vX.Y.Z`, `:vX.Y`, and `:vX`.

## Versioning

Tags follow semantic versioning, with the bump chosen from the commits since the last tag:

- `fix:` → patch
- `feat:` → minor
- a breaking change → major

A tag containing `-` (for example `v1.3.0-rc.1`) is published as a prerelease.

## Consuming a release

Pin a release with `DUCKHAVEN_IMAGE_TAG` in your `.env` — see [Updating](../deployment/updating.md). For the full
release process, see [Releasing](../developer/releasing.md).
