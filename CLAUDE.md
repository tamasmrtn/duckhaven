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
- Commit message rules:
   - Start with a capital letter.
   - Imperative mood: "Add validation" not "Added validation".
   - Max 72 characters, no trailing period.
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

- PR title follows the same format as the commit subject (capital letter, imperative, ≤72 chars).
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

**Every new feature, new concept, or change to an existing capability ships with a docs update — in the same PR as the
code.**

The documentation website lives in `docs/` (MkDocs Material). When behavior that a user, operator, or contributor can
observe changes, update the page that describes it. Map the change to the right place:

- New or changed **concept** (workspaces, catalogs, agents, storage, query execution, permissions, …) → `docs/concepts/`
- New or changed **user task** → `docs/guides/`
- New or changed **deploy or day-2 behavior** → `docs/deployment/` or `docs/operations/`
- New or changed **config, SQL, API, or operator script** → `docs/reference/`
- New first-run flow → `docs/getting-started/`

How to write it:

- **Surgical (§3).** Touch only the page(s) the change affects; match the existing structure, tone, and cross-links.
  Don't restructure or rewrite unrelated docs.
- **For humans, not the compiler.** These pages are read by people who don't know the code. Explain in plain prose
  *what* the feature does and *why* it matters — enough to understand it without reading the source — not a terse
  changelog line.
- **Honest about scope (§2).** If something is partial, experimental, or not yet shipped, say so (an admonition works
  well). Never document behavior that does not exist.

**Every plan that changes observable behavior must include a documentation step:**

```
N. Update docs/<section>/<page>.md for the new behavior → verify: mkdocs build --strict
```

`mkdocs build --strict` must pass — no broken internal links or anchors.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer
rewrites due to overcomplication, and clarifying questions come before
implementation rather than after mistakes.
