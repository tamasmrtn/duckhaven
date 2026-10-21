# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:

- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
N. Run tests and pre-commit → verify: make test && pre-commit run --all-files
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 5. Git Workflow

**Branch first. Commit logically. PR when done.**

### Branching

- Never commit directly to `main`.
- Create a branch before any work begins, named with a type prefix:
   - `feat/` — new feature
   - `fix/` — bug fix
   - `chore/` — maintenance, deps, config, tooling
   - `docs/` — documentation only
   - `refactor/` — behaviour-preserving restructure
   - `test/` — test additions or corrections
- Branch slug: lowercase, hyphen-separated, 3–5 words. Example: `feat/agent-capability-advertisement`.

### Committing

- Group changed files into **logical units** and commit each unit separately. Do not dump all changes into one commit.
- Stage specific files by name (`git add src/foo.py`), never `git add .` or `git add -A`.
- Sign off every commit with `git commit -s` (DCO); the `DCO sign-off` CI job fails a PR without it.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):
  `<type>[(scope)][!]: <description>`.
   - Type is one of the branch prefixes: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`.
   - Optional scope names the area, e.g. `fix(agent): Bound the EXPLAIN timeout`.
   - Mark breaking changes with `!` after the type/scope and a `BREAKING CHANGE:` footer.
   - The description starts with a capital letter.
   - Imperative mood: "Add validation" not "Added validation".
   - Max 72 characters for the whole subject line, no trailing period.
   - No `Co-authored-by` trailers. No AI attribution of any kind.
   - Body (if needed): blank line after subject, wrapped at 72 chars.
- Do not commit debug statements, commented-out code, or secrets.

### Safety

- Never `--force` push to `main`. No exceptions.
- Never skip hooks (`--no-verify`) unless the user explicitly requests it.
- Never amend a commit that has already been pushed to the remote.
- Prefer `--force-with-lease` over `--force` when a force push is genuinely required on a feature branch.

### Pull requests

After all commits are on the branch, open a PR against `main` using the GitHub CLI:

```sh
gh pr create --base main --title "<type>: <short description>" --body "$(cat <<'EOF'
## Summary
- <what changed and why>

## Test plan
- [ ] <how to verify>

Closes #<issue if applicable>
EOF
)"
```

- PR title follows the same format as the commit subject (`<type>: <Description>`, imperative, ≤72 chars).
- Use `--draft` if the work is not yet ready for review.
- Do not push or create a PR unless the user explicitly asks.

## 6. Testing

**Every feature or fix requires tests. Write them as part of the implementation, not after.**

For every plan involving a code change:

- Add or update tests covering the new or changed behavior.
- Frontend (`web/`): add tests under `web/tests/` using Vitest + React Testing Library + MSW.
- Python API: add tests under `api/tests/` using pytest.
- Python agent: add tests under `agent/tests/` using pytest.

Test scope by change type:

- New utility/pure function → unit test inputs, outputs, and edge cases.
- New component → render output and user interactions.
- New query/hook → use `renderHook` + MSW handler override.
- Bug fix → regression test that fails before the fix.

**Every plan must end with this step:**

```
N. Run tests and pre-commit → verify: make test && pre-commit run --all-files
```

If only one layer changed, scope the test command:

- `make test-web` — frontend only
- `make test-api` — Python API only
- `make test-agent` — Python agent only

## 7. Documentation

**A change a user, operator, or contributor can observe ships with a docs update in the same PR.** The docs site
(`docs/`, MkDocs Material) has two kinds of reader: people, who mostly arrive with one question and scan, and the
in-product assistant and MCP server, which search these pages and quote them at runtime.

Put the change on the page for its subject:

- Querying, worksheets, SQL sessions, results, history → `docs/querying/`
- Catalogs, tables, DuckLake, storage, maintenance → `docs/data/`
- Metadata, lineage, semantic layer → `docs/metadata/`
- AI assistant, MCP → `docs/ai/`
- Identity, roles, grants, tokens → `docs/access/`
- Agents, elastic compute, runtimes → `docs/compute/`
- Install, upgrade, host setup → `docs/deployment/`; observing and troubleshooting the deployment → `docs/operations/`
- Tables that span subjects (configuration, SQL, API, errors, permissions) → `docs/reference/`
- First-run flow → `docs/getting-started/`

The writing rules are in `.claude/rules/docs.md`, which loads when you edit `docs/`, and the templates are in
`docs/developer/writing-docs.md`. The essentials:

- **Edit the page that owns the fact, in place.** Describe current behavior as if it had always worked this way;
  history belongs in the release notes.
- **One page type per page** (tutorial, how-to, concept, reference, troubleshooting), following its template.
- **When a change alters a fact** — a default, a limit, an allowed statement, a role — search `docs/` for the old
  fact and fix every page that states it.
- **Moving or renaming a page** needs a `redirect_maps` entry in `mkdocs.yml` and `make docs-index`. Never hand-edit
  `docs/reference/cli.md`; it is generated in duckhaven-clients.
- **Be honest about scope.** Say plainly what is partial or unsupported; never document behavior that does not exist.

**Every plan that changes observable behavior must include a documentation step:**

```
N. Update docs/<subject>/<page>.md → verify: make docs-check && make docs-index
```

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer
rewrites due to overcomplication, and clarifying questions come before
implementation rather than after mistakes.
