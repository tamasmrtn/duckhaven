---
type: how-to
---

# Manage dashboards in git

Keep each dashboard's file in a git repository, review changes in pull requests, and let CI apply them to DuckHaven.

A dashboard applied from git is managed there. DuckHaven marks it with a **git** badge and refuses every change to it
except the next apply, so the repository stays the one place it is edited. The file is kept as it is in the
repository, comments included. Nothing flows the other way: DuckHaven never writes to the repository.

## Before you begin

- A workspace whose [semantic models](../metadata/semantic-layer.md) the dashboards read are published.
- Two [service accounts](../access/service-accounts.md) with a token each: a workspace `writer` for pull requests,
  and a workspace `owner` to publish from `main`.
- A repository holding the [dashboard files](../reference/dashboard-spec.md), one per dashboard, each named after
  the `name` inside it: `sales-overview.dashboard.yml` holds `name: sales-overview`.

## Lay out the repository

```text
dashboards/
  sales-overview.dashboard.yml
  refunds.dashboard.yml
scripts/
  apply-dashboards.sh
.github/
  CODEOWNERS
  workflows/dashboards.yml
```

To have your editor complete and check the files, start each one with
`# yaml-language-server: $schema=https://<your DuckHaven>/api/dashboard-schema`.

## Apply the files from CI

The script [applies each file](../reference/rest-api.md#applying-a-file-from-git) with
`PUT …/dashboards/{dashboard}/source`, naming the dashboard after its file. It records the repository as `source_id`
and the commit as `source_ref`, and reports every file that fails, not only the first:

```bash
#!/usr/bin/env bash
# scripts/apply-dashboards.sh: apply every dashboard file. OPTIONS holds the apply's options.
set -uo pipefail
status=0
for file in dashboards/*.dashboard.yml; do
  name=$(basename "$file" .dashboard.yml)
  url="$DUCKHAVEN_URL/api/workspaces/$WORKSPACE/dashboards/$name/source"
  echo "::group::$name"
  curl --fail-with-body --silent --show-error -X PUT \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/yaml" \
    --data-binary "@$file" \
    "$url?source_id=$GITHUB_REPOSITORY&source_ref=$GITHUB_SHA&$OPTIONS" || status=1
  echo
  echo "::endgroup::"
done
exit "$status"
```

The workflow runs it twice. On a pull request it makes a dry run with `publish=true`, which checks each file as
publishing would and changes nothing, so a writer's token is enough. On `main` it applies and publishes each file:

```yaml
# .github/workflows/dashboards.yml
name: Dashboards

on:
  pull_request:
    paths: ["dashboards/**"]
  push:
    branches: [main]
    paths: ["dashboards/**"]

env:
  DUCKHAVEN_URL: ${{ vars.DUCKHAVEN_URL }}
  WORKSPACE: analytics

jobs:
  check:
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Check each file as publishing would
        run: bash scripts/apply-dashboards.sh
        env:
          TOKEN: ${{ secrets.DUCKHAVEN_WRITER_TOKEN }}
          OPTIONS: dry_run=true&publish=true

  publish:
    if: github.event_name == 'push'
    runs-on: ubuntu-latest
    environment: duckhaven          # holds the owner token; let only main deploy to it
    concurrency: dashboards         # two merges apply one after the other, in order
    steps:
      - uses: actions/checkout@v4
      - name: Apply and publish each file
        run: bash scripts/apply-dashboards.sh
        env:
          TOKEN: ${{ secrets.DUCKHAVEN_OWNER_TOKEN }}
          OPTIONS: publish=true
```

Each answer says what the apply did: `action` is `create`, `update` or `unchanged`, and `changes` lists the widgets,
parameters and fields that changed. A file that is the same as the dashboard's changes nothing.

Keep the owner token in a GitHub environment whose deployment branches allow `main` only. A workflow edited in a pull
request then cannot read it.

## Review each change with CODEOWNERS

Publishing from `main` means that whoever merges a change publishes it. Make a review by the dashboard's owners the
gate: name them in `CODEOWNERS`, and turn on **Require review from Code Owners** in the branch protection for `main`.

```text
# .github/CODEOWNERS: the last matching line wins.
/dashboards/                          @acme/analytics
/dashboards/finance-*.dashboard.yml   @acme/finance
```

A change to `finance-margin.dashboard.yml` then needs a review from `@acme/finance` before it can merge and publish.

## Take over a dashboard made in DuckHaven

A file whose dashboard was made in DuckHaven is refused with **409** `dashboard_name_taken` until you adopt it:

1. Save its file into the repository:

    ```bash
    curl -H "Authorization: Bearer $TOKEN" \
      "$DUCKHAVEN_URL/api/workspaces/analytics/dashboards/refunds/source" > dashboards/refunds.dashboard.yml
    ```

2. Apply it once with `adopt=true&publish=true`, with an owner's token.
3. Commit the file. From then on, CI applies it.

## Rename a dashboard

The script names each dashboard after its file, so applying a renamed file creates a second dashboard. To rename one
and keep its revisions and links, change the `name` in the file and the file's name together. Before merging, apply
the renamed file once at the old name, with an owner's token. To rename `refunds` to `refund-requests`, put
`refund-requests.dashboard.yml` to `…/dashboards/refunds/source?publish=true`. The old name keeps working in links.

## Remove a dashboard

Applying never deletes a dashboard. After you delete its file, detach the dashboard from git, then delete it, both
with an owner's token:

```bash
board="$DUCKHAVEN_URL/api/workspaces/analytics/dashboards/refunds"
curl -X DELETE -H "Authorization: Bearer $TOKEN" "$board/source"
curl -X DELETE -H "Authorization: Bearer $TOKEN" "$board"
```

To hand a dashboard back to DuckHaven instead, detach it and stop there. It keeps its file, draft and revisions, and
writers edit it in DuckHaven again. Delete its file from the repository too, or the next apply is refused.

## If it goes wrong

- **422 `invalid_dashboard`.** The file has errors, or with `publish` cannot be published: a metric that does not
  exist, or a model that is not published. `details.diagnostics` lists each problem with its line.
- **422 `invalid_dashboard` on `name`.** A new dashboard's file must be named after the `name` inside it. Rename the
  file, or change the `name`.
- **409 `dashboard_name_taken`.** A dashboard of that name was made in DuckHaven. Adopt it, or rename the file.
- **409 `source_changed`.** Another repository manages the dashboard: `GET …/dashboards/{dashboard}` shows its
  `source_id`. Apply with `force=true` to manage it from this repository instead.
- **409 `managed_dashboard`** when editing in DuckHaven. The dashboard is managed in git: change its file there, or
  detach it.
- **403.** Publishing, adopting and detaching need a workspace `owner`. A dry run needs only `writer`.
