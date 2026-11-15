---
type: how-to
---

# Manage dashboards in git

Keep each dashboard's file in a git repository, review changes in pull requests, and let CI apply them to DuckHaven
with [`dh`](../getting-started/cli-quickstart.md).

A dashboard applied from git is managed there. DuckHaven marks it with a **git** badge and refuses every change to it
except the next apply, so the repository stays the one place it is edited. The file is kept as it is in the
repository, comments included. Nothing flows the other way: DuckHaven never writes to the repository.

## Before you begin

- A workspace whose [semantic models](../metadata/semantic-layer.md) the dashboards read are published.
- Two [service accounts](../access/service-accounts.md) with a token each: a workspace `writer` for pull requests,
  and a workspace `owner` to publish from `main`.
- A repository holding the [dashboard files](../reference/dashboard-spec.md), one per dashboard, each named after
  the `name` inside it: `sales-overview.dashboard.yml` holds `name: sales-overview`.
- `dh` 0.5.0 or later, the first release with the `dh dashboard` commands: `pip install "duckhaven-cli>=0.5.0"`.

## Lay out the repository

```text
dashboards/
  sales-overview.dashboard.yml
  refunds.dashboard.yml
.github/
  CODEOWNERS
  workflows/dashboards.yml
```

To have your editor complete and check the files, start each one with
`# yaml-language-server: $schema=https://<your DuckHaven>/api/dashboard-schema`. `dh dashboard schema` prints the same
schema, for an editor that reads it from a file.

## Check a file as you write it

`dh dashboard validate` checks files the way publishing does, against the workspace as you, and reports each problem
at its line. It exits 5 when a file has an error:

```bash
dh dashboard validate dashboards/
```

```text
dashboards/refunds.dashboard.yml:15:16: error: 'shop' has no metric called 'refund_total'. Available: …
```

To see the numbers as you edit, `dh dashboard preview` holds the file as a
[preview](dashboards.md#trying-out-a-file-before-saving-it) and prints the address of its page. Each time you save the
file, the page runs it again, as you. Nothing is saved to the dashboard, and only you can see the page.

```bash
dh dashboard preview dashboards/refunds.dashboard.yml --open
```

`--open` opens the page in your browser. If the browser is signed out, the page opens after you sign in.

`dh dashboard test dashboards/` runs every widget once, as you, and reports each one as `ok`, `empty`, `error` or
`no_access`, with how long it took. It exits 6 when a widget fails or reads something you may not read.

## Apply the files from CI

`dh dashboard apply` [applies each file](../reference/rest-api.md#applying-a-file-from-git) as the dashboard it is
named after. `--source-id` records the repository and `--source-ref` the commit. It applies every file and reports
every one that fails, not only the first, then exits with the first failure's status.

The workflow runs it twice. On a pull request it makes a dry run with `--publish`, which checks each file as
publishing would and changes nothing, so a writer's token is enough. On `main` it applies and publishes each file.
`dh` reads its address, token and workspace from `DH_HOST`, `DH_TOKEN` and `DH_WORKSPACE`:

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
  DH_HOST: ${{ vars.DUCKHAVEN_URL }}
  DH_WORKSPACE: analytics

jobs:
  check:
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    env:
      DH_TOKEN: ${{ secrets.DUCKHAVEN_WRITER_TOKEN }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install "duckhaven-cli>=0.5.0"
      - name: Check each file as publishing would
        run: dh dashboard apply dashboards/ --dry-run --publish --source-id "$GITHUB_REPOSITORY"
      - name: Run each widget
        run: dh dashboard test dashboards/

  publish:
    if: github.event_name == 'push'
    runs-on: ubuntu-latest
    environment: duckhaven          # holds the owner token; let only main deploy to it
    concurrency: dashboards         # two merges apply one after the other, in order
    env:
      DH_TOKEN: ${{ secrets.DUCKHAVEN_OWNER_TOKEN }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install "duckhaven-cli>=0.5.0"
      - name: Apply and publish each file
        run: dh dashboard apply dashboards/ --publish --source-id "$GITHUB_REPOSITORY" --source-ref "$GITHUB_SHA"
```

For each file, `dh` prints what the apply did, `create`, `update` or `unchanged`, and the widgets, parameters and
fields that changed:

```text
refunds: update, published: widgets.refund_rate changed
```

A file that is the same as the dashboard's changes nothing. `--format json` prints each apply's answer in full.

The **Run each widget** step runs the files as the writer account, so it fails the check on a widget whose query
fails, or that reads a table the account may not read. It starts compute when compute is stopped. Leave it out if the
writer account should not read the data the dashboards show.

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

A file whose dashboard was made in DuckHaven is refused with **409** `dashboard_name_taken` until you adopt it. With
an owner's token, from the repository's root, give the `--source-id` the workflow gives, the repository's
`owner/name`:

```bash
dh dashboard adopt refunds dashboards/ --source-id acme/bi
```

`adopt` saves the dashboard's newest file, its draft when it has one, as `dashboards/refunds.dashboard.yml`, and
applies that file to the dashboard, which is then managed in git. Nothing is published, so readers see what they saw.
Commit the file. From then on, CI applies it.

## Rename a dashboard

`dh dashboard apply` names each dashboard after its file, so applying a renamed file creates a second dashboard. To
rename one and keep its revisions and links, change the `name` in the file and the file's name together. Before
merging, rename it once, with an owner's token:

```bash
dh dashboard rename refunds dashboards/refund-requests.dashboard.yml --source-id acme/bi
```

`rename` applies the renamed file at the old name and publishes it, since a rename takes effect when it is published.
The old name keeps working in links.

## Remove a dashboard

Applying never deletes a dashboard. To have CI delete the dashboards whose files you delete, add `--prune` to both
applies, and `--yes` to the one on `main`. `--prune` looks for the dashboards applied from the `--source-id` that no
file names:

- On a pull request, the dry run lists each one as `deleted, dry run` and changes nothing.
- On `main`, each one is detached and deleted once every file has applied. If a file fails, nothing is pruned.

To remove one by hand after you delete its file, detach the dashboard from git, then delete it, both with an owner's
token:

```bash
dh dashboard detach refunds
dh dashboard delete refunds
```

To hand a dashboard back to DuckHaven instead, detach it and stop there. It keeps its file, draft and revisions, and
writers edit it in DuckHaven again. Delete its file from the repository too, or the next apply is refused.

## Without dh

Each command is a call to the [REST API](../reference/rest-api.md#applying-a-file-from-git), which any HTTP client can
make: `dh dashboard apply` sends each file with `PUT …/dashboards/{dashboard}/source`, and its flags are that route's
query parameters, such as `dry_run=true&publish=true`.

## If it goes wrong

`dh` exits 5 when DuckHaven refuses a file, and 3 when the token may not do what was asked. Each refusal below is
DuckHaven's error code, which `--format json` prints as `error`.

- **422 `invalid_dashboard`.** The file has errors, or with `--publish` cannot be published: a metric that does not
  exist, or a model that is not published. `dh` prints each problem at its line.
- **422 `invalid_dashboard` on `name`.** A new dashboard's file must be named after the `name` inside it. Rename the
  file, or change the `name`.
- **409 `dashboard_name_taken`.** A dashboard of that name was made in DuckHaven. Adopt it, or rename the file.
- **409 `source_changed`.** Another repository manages the dashboard: `dh dashboard get` shows its `source_id`. Apply
  with `--force` to manage it from this repository instead.
- **409 `managed_dashboard`** when editing in DuckHaven. The dashboard is managed in git: change its file there, or
  detach it.
- **403.** Publishing, adopting, renaming, detaching and pruning need a workspace `owner`. A dry run needs only
  `writer`.
