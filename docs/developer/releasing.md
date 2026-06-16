# Releasing DuckHaven

A release is cut by pushing a `vX.Y.Z` git tag. There is no version file to bump
— the tag is the source of truth. Pushing the tag triggers two workflows that
publish the artifacts:

- [`.github/workflows/release.yml`](https://github.com/tamasmrtn/duckhaven/blob/main/.github/workflows/release.yml)
  creates a GitHub Release with an auto-generated changelog.
- [`.github/workflows/build.yml`](https://github.com/tamasmrtn/duckhaven/blob/main/.github/workflows/build.yml)
  builds and publishes the `duckhaven-api` and `duckhaven-agent` container images to GHCR.

## Versioning

Tags follow semantic versioning: `vMAJOR.MINOR.PATCH`. Pick the bump from the
conventional commits merged since the last tag:

- `fix:` → patch (`v0.2.0` → `v0.2.1`)
- `feat:` → minor (`v0.2.0` → `v0.3.0`)
- a breaking change → major (`v0.2.0` → `v1.0.0`)

A tag containing a `-` (e.g. `v1.3.0-rc.1`) is published as a **prerelease**.

## Before you tag

Tag from an up-to-date `main` with green CI:

```bash
git checkout main && git pull
git log v0.2.0..HEAD --oneline   # review what will ship since the last tag
```

## Cut the release

Create and push the tag:

```bash
git tag v0.3.0
git push origin v0.3.0
```

## What happens automatically

| Workflow | Output |
|---|---|
| `release.yml` | A GitHub Release whose notes are generated from conventional commits by [git-cliff](https://git-cliff.org/) (`cliff.toml`). Marked **prerelease** when the tag contains `-`. |
| `build.yml` | Multi-arch (`linux/amd64`, `linux/arm64`) images pushed to `ghcr.io/<owner>/duckhaven-api` and `ghcr.io/<owner>/duckhaven-agent`, tagged `:X.Y.Z`, `:X.Y`, and `:X`. |

The changelog includes Features, Bug Fixes, Performance, Refactoring,
Documentation, and Maintenance entries; `ci`, `test`, and `style` commits are
omitted, and non-conventional commits are filtered out.

Note: the `:latest` image tag is only published on pushes to `main`, **not** on
release tags. Tagging a release does not move `:latest`.

## Verify the release

```bash
gh release view v0.3.0
```

Confirm the images appear under the repository's GHCR packages.

## Consuming a release

Self-hosters pin a release via `DUCKHAVEN_IMAGE_TAG` in `.env`. See
[Update DuckHaven](../deployment/updating.md).
