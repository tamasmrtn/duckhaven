# Use the AI assistant

The AI data assistant lets you explore a workspace's data by chatting: ask a question in plain language and it will
browse the catalog, run SQL, and explain what it found. This guide covers using it day to day. For how it is governed
and what it can and cannot do, see the [AI data assistant](../concepts/assistant.md) concept page.

!!! note "Availability"
    The assistant is only present when an administrator has enabled it and bound it to a service account. If you don't
    see it, ask your operator — the setup steps are in [Enable the assistant](#enable-the-assistant-operators) below.

## Have a conversation

1. Click the **✨ sparkle** button in the top bar (just left of the theme toggle) to open the assistant panel on the
   right — it stays available on every page, alongside your work, like a coding copilot. `Ctrl/Cmd+I` toggles it too.
2. On a fresh conversation, click one of the suggested starter prompts (generated from the catalogs in your workspace)
   or type your own question, for example:
   - "What tables are in the sales schema?"
   - "How many orders were placed last month, by region?"
   - "Describe the customers table."
3. The assistant streams its answer as it works, showing a rotating status word and an elapsed timer while it's busy. A
   question that takes several steps arrives as several messages — one each time the assistant pauses to run something —
   and they appear as it goes rather than all at the end. If it opened your worksheet's active catalog, unqualified
   table names resolve against that catalog automatically. When
   it runs a query, the SQL is shown right under the answer, and a preview of the result; open the full result in the
   query grid — or click the **View full result** link that appears after the answer — just as you would for a query
   you ran yourself. When the tables it queried can be resolved, an **Open _table_ in Catalog** chip appears alongside
   it for each one, taking you straight to that table's detail page.
4. While a turn is running, the Send button becomes a **Stop** button. Clicking it cancels the turn: the assistant
   stops generating, any query still running is cancelled, and the partial answer is discarded (nothing is saved). Your
   question stays in the composer's place with a **Retry** to run it again.
5. If a turn fails, a **Retry** button appears with the error so you can resend it without retyping; after a good
   answer, **Regenerate** resends the same question as a new turn if you want another attempt.

Each conversation is private to you. Click the history icon in the panel header to search, switch, rename, or delete
conversations; the panel header also shows the conversation's total token usage. Conversations persist so you can
return to them later. If a conversation grows very long, the panel shows a small notice that its oldest messages have
dropped out of context — start a new conversation if you want a clean slate.

## Ask about DuckHaven itself

The assistant can answer questions about the product, not just about your data — it carries a summary of DuckHaven's
own behaviour and can read any page of this documentation:

- "What SQL statements can I run?"
- "How do I query a table as it was last Tuesday?"
- "Why does `information_schema.columns` show `__` for my table?"
- "Can I set a snapshot retention policy?"

When it looks something up, the **Activity** list in the panel shows the `search_docs` and `read_doc_page` calls and
the path it read, and the answer names that path. If the documentation does not cover something, it will say so
rather than guessing — DuckHaven differs from other platforms in ways where a confident wrong answer is worse than
none.

!!! note "It describes your version"
    The pages travel inside the DuckHaven image, so answers match the release you are running rather than the latest
    published documentation. See [Product knowledge](../concepts/assistant.md#product-knowledge).

## Let the assistant write SQL in your worksheet

On the worksheet, ask the assistant to write or change the SQL you're editing — "write a query for last week's signups
by day", "add a WHERE clause for 2025", "fix this join". Instead of only chatting, it proposes an edit **directly in
your editor**:

- The proposed SQL replaces the editor content, shown as an inline diff: added lines are highlighted in place, and any
  line the assistant removed appears as struck-through ghost text just above where it used to be — so it's obvious
  what changed versus what was already yours.
- If you have text selected in the editor when you ask, the assistant proposes a replacement for just that selection
  instead of rewriting the whole worksheet — handy for a small tweak in a long query. If the worksheet changed since
  you asked, it falls back to a full replacement rather than risk applying the edit in the wrong place.
- A bar appears above the editor with **Accept** and **Reject**. Accept keeps the change (the worksheet is marked
  unsaved); Reject restores your original SQL.
- Nothing runs automatically — you review, accept, then run it with the usual **Run** button, which goes through the
  same permission checks as any query.

## Recovering from a failed query

When a query fails, the results pane's error banner has a **Fix with Assistant** button. Clicking it opens the
assistant panel with a message already drafted — the failed SQL and the exact engine error — so you don't have to
retype or re-explain what went wrong. The message is only pre-filled, not sent automatically: review it (the error
text can echo values from your data) and send it when you're ready, or edit it first. Any fix the assistant proposes
follows the same paths as everywhere else in the assistant — a SQL edit lands as a reviewable diff in your editor, and
anything that would write data still requires your approval.

## Approving a write

If your assistant is configured with write access and it proposes a change to data (an INSERT, UPDATE, DELETE, or a DDL
statement), the conversation pauses and shows you the exact SQL. Review it and choose **Approve** or **Deny**:

- **Approve** runs the statement — still subject to the same permission checks as any query.
- **Deny** tells the assistant not to run it; it will carry on and can explain or try a different approach.

Nothing that changes data runs without your approval.

## Tips for good results

- Let it discover structure: it will list schemas and describe tables before querying, so you rarely need to know exact
  table or column names.
- Be specific about the grain and filters you want ("by month", "for 2025", "excluding cancelled orders").
- If your question is missing something essential — like the time period or which table you mean — the assistant
  will ask a short clarifying question instead of guessing wrong.
- Results shown to the model are a capped sample — for large outputs, trust the reported total row count and open the
  full result in the grid.
- If the assistant says something is not accessible, that reflects the grants of its service account, not a bug. Ask
  your administrator if you need broader access.

## Enable the assistant (operators)

The assistant acts as a service account you control. To turn it on:

1. Create a service account and note its slug (see [Service accounts & tokens](service-accounts.md)). No token is
   needed — the assistant mints its own short-lived tokens.
2. Add the service account to each workspace where the assistant should be usable, and grant it the access you want it
   to have (`metadata`/`reader` for browse-and-read, `writer` to allow approved writes). See
   [How access works](access-levels.md).
3. Configure the model and point DuckHaven at the service account via the `ASSISTANT_*` settings in
   [Configuration](../reference/configuration.md#ai-assistant), then set `ASSISTANT_ENABLED=true`.

Because access is per workspace, you can safely enable one assistant across many workspaces and let each workspace's
grants decide what it can reach.
