---
type: how-to
---

# Use the AI assistant

The AI data assistant lets you explore a workspace's data by chatting: ask a question in plain language and it will
browse the catalog, run SQL, and explain what it found. This guide covers using it day to day. For how it is governed
and what it can and cannot do, see the [AI data assistant](assistant.md) concept page.

## Before you begin

- **You need:** membership of a workspace where an administrator has added the assistant. If you don't see it, ask
  them; the steps are in [Set up the assistant](set-up-the-assistant.md).
- **What it can reach:** only what its own service account may, which can be less than you may.

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

Every answer, and every block of code or SQL in one, has a copy button. A link in an answer opens only if it points
into DuckHaven. For any other address the answer shows the address in full, so you can see where it leads before you
copy it.

Each conversation is private to you. Click the history icon in the panel header to search, switch, rename, or delete
conversations; the panel header also shows the conversation's total token usage. A title holds up to 255 characters.
Conversations persist so you can return to them later. If a conversation grows very long, the panel shows a small
notice that its oldest messages have dropped out of context — start a new conversation if you want a clean slate.

## Ask about DuckHaven itself

The assistant can answer questions about the product, not just about your data — it carries a summary of DuckHaven's
own behaviour and can read any page of this documentation:

- "What SQL statements can I run?"
- "How do I query a table as it was last Tuesday?"
- "Why does `information_schema.columns` show `__` for my table?"
- "Can I set a snapshot retention policy?"

When an answer draws on the documentation, a **Sources** row appears beneath it with a link to each page the
assistant opened. The links point at the documentation for the version you are running, so they show what it
actually read rather than whatever the site publishes today. Most answers cite nothing, and that is normal — the row
only appears when a page was opened.

The **Activity** list in the panel records the same `search_docs` and `read_doc_page` calls in more detail, but it is
collapsed by default; the Sources row is there so a citation does not require expanding a panel to find.

If the documentation does not cover something, the assistant will say so rather than guessing — DuckHaven differs
from other platforms in ways where a confident wrong answer is worse than none.

!!! note "It describes your version"
    The pages travel inside the DuckHaven image, so answers match the release you are running rather than the latest
    published documentation. See [Product knowledge](assistant.md#product-knowledge).

## Let the assistant write SQL in your worksheet

On the worksheet, ask the assistant to write or change the SQL you're editing — "write a query for last week's signups
by day", "add a WHERE clause for 2025", "fix this join". Instead of only chatting, it proposes an edit **directly in
your editor**:

- The proposed SQL replaces the editor content, shown as an inline diff: added lines are highlighted in place, and any
  line the assistant removed appears as struck-through ghost text just above its old position — so it's obvious
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
statement), the conversation pauses and shows you the exact SQL. Review it and choose **Approve & run** or **Deny**:

- **Approve & run** runs the statement — still subject to the same permission checks as any query.
- **Deny** tells the assistant not to run it; it will carry on and can explain or try a different approach.

The statement runs in the catalog your question was asked against, however you answer. The assistant never reads a
file or URL by path in a statement you have not approved, because an address in a query is a way for data to leave;
an approved write that loads a file may name it.

Each write is answered once. A second click, or the same write answered from another tab, is refused and the first
answer stands. Nothing that changes data runs without your approval.

## Tips for good results

- Let it discover structure: it will list schemas and describe tables before querying, so you rarely need to know exact
  table or column names.
- Be specific about the grain and filters you want ("by month", "for 2025", "excluding cancelled orders").
- If your question is missing something essential — like the time period or which table you mean — the assistant
  will ask a short clarifying question instead of guessing wrong.
- Results shown to the model are a capped sample — for large outputs, trust the reported total row count and open the
  full result in the grid.
- Describe your data where you keep it. When an Iceberg table's columns carry descriptions and the table a comment, the
  assistant reads them and uses what they say. DuckLake catalogs do not pass these on.
- If the assistant says something is not accessible, that reflects the grants of its service account, not a bug. Ask
  your administrator if you need broader access.

## If it goes wrong

### "Assistant is turned off"

The assistant is not enabled on this deployment. Ask an operator; see [Set up the assistant](set-up-the-assistant.md).

### "The assistant isn't a member of this workspace, so every question would come back denied."

An admin must add the **Assistant** account to this workspace.

### It says a table is not accessible

That is its service account's access, not a fault. Ask an admin to grant the account access to the table.

### "Nothing is waiting on that approval. It was answered already, or the conversation has moved on."

You answered the same write twice, or answered one from an earlier turn. The first answer stands. Reopen the
conversation to see what the assistant did with it.

### "That approval is already being answered."

A second click arrived while the first was still running. Wait for the turn to finish; do not click again.

### "This conversation was deleted while the assistant was answering, so this turn was not saved."

The conversation was deleted, in this tab or another, while a turn was running. Start a new conversation and ask
again.

### "The model provider …" or "This conversation has grown too long for the model."

The assistant reached the model and the model's provider turned it away or did not answer. Each message says which, and
none of them is a fault in your question.

| Message says | What to do |
|---|---|
| The model provider is rate limiting requests, or is overloaded or unavailable | Wait a moment and ask again. |
| The model provider took too long to answer | Ask again. |
| The conversation has grown too long for the model | Start a new conversation. |
| The model provider rejected the assistant's credentials, does not know the configured model, says its quota is used up, or could not be reached | An operator needs to check the assistant's API key, model setting, provider account or network. |
| The model provider's content filter stopped this answer | Rephrase the question. |
| The answer reached the model's output limit | Ask for less at once. |

### "The assistant could not finish: one of its tools kept failing."

The assistant asked for something that did not work, was told why, and tried again more times than it is allowed.
Ask a more specific question, such as naming the table or the column you mean.

### "Reading a file or URL by path is not allowed here"

The assistant tried to read a file or URL by path in a statement nobody had approved, so DuckHaven refused it. Ask it
to use a table in a catalog, or run the statement yourself in a worksheet.

### A character in your question or a result shows as �

A NUL byte or an invalid character cannot be stored, so DuckHaven replaces it with � in your question and in what the
assistant reads. Remove it from the source if it matters.

### A long conversation forgets what you said early on

Only the most recent turns are kept. Start a new conversation for a new topic.

## Related

- [AI data assistant](assistant.md) — how it is governed and what it cannot do.
- [Set up the assistant](set-up-the-assistant.md) — for operators.
- [Run queries](../querying/run-queries.md) — the worksheet it edits.
