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
3. The assistant streams its answer as it works, showing a rotating status word and an elapsed timer while it's busy. If
   it opened your worksheet's active catalog, unqualified table names resolve against that catalog automatically. When
   it runs a query, the SQL is shown right under the answer, and a preview of the result; open the full result in the
   query grid — or click the **View full result** link that appears after the answer — just as you would for a query
   you ran yourself.
4. While a turn is running, the Send button becomes a **Stop** button. Clicking it cancels the turn: the assistant
   stops generating, any query still running is cancelled, and the partial answer is discarded (nothing is saved). Your
   question stays in the composer's place with a **Retry** to run it again.
5. If a turn fails, a **Retry** button appears with the error so you can resend it without retyping; after a good
   answer, **Regenerate** resends the same question as a new turn if you want another attempt.

Each conversation is private to you. Click the history icon in the panel header to search, switch, rename, or delete
conversations; the panel header also shows the conversation's total token usage. Conversations persist so you can
return to them later.

## Let the assistant write SQL in your worksheet

On the worksheet, ask the assistant to write or change the SQL you're editing — "write a query for last week's signups
by day", "add a WHERE clause for 2025", "fix this join". Instead of only chatting, it proposes an edit **directly in
your editor**:

- The proposed SQL replaces the editor content, with the **changed lines highlighted** so it's obvious what the
  assistant wrote versus what was already yours.
- If you have text selected in the editor when you ask, the assistant proposes a replacement for just that selection
  instead of rewriting the whole worksheet — handy for a small tweak in a long query. If the worksheet changed since
  you asked, it falls back to a full replacement rather than risk applying the edit in the wrong place.
- A bar appears above the editor with **Accept** and **Reject**. Accept keeps the change (the worksheet is marked
  unsaved); Reject restores your original SQL.
- Nothing runs automatically — you review, accept, then run it with the usual **Run** button, which goes through the
  same permission checks as any query.

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
