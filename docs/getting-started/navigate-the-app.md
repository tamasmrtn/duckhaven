---
type: how-to
---

# Find your way around the app

The navigation on the left lists DuckHaven's pages in four groups, and ⌘K finds pages and objects by name. You can keep
the navigation expanded, collapse it to icons, or have it open when you point at it; your account remembers which.

## Before you begin

- **You need:** a sign-in. **Admin** appears only if you hold a
  [global permission](../reference/permissions.md#global-permissions).
- **Applies to:** every deployment.

## Find a page

| Group | Pages |
|---|---|
| **SQL** | **Worksheets**, **Saved queries**, **Schedules**, **History** |
| **Data** | **Catalog**, **Lakehouse health** |
| **Analytics** | **Dashboards**, **Semantic models** |
| **Manage** | **Compute**, **Admin** |

A bar beside its name marks the page you are on: ink in the light theme, yellow in the dark.

- **Connections**, the SQL sessions that dbt and dlt open, is a view of **History**: open **History** and choose
  **Connections** next to the title. See [Read the session audit trail](../querying/session-audit.md).
- **Settings** is in the account menu, under your name at the top right.

## Search with ⌘K

Press **⌘K** (**Ctrl+K** on Windows and Linux), or click **Search…** in the top bar. Type two or more characters to
find, by name:

- catalogs, schemas and tables;
- saved queries, which open in a worksheet;
- dashboards, by name or title, and semantic models;
- any page in the table above, and **Connections** and **Settings**.

You find only what you could open: a table you hold no grant on, or a dashboard nobody has published while you are a
reader, does not appear. With nothing typed, ⌘K lists under **Recent** the last eight tables and schemas you opened in
the workspace.

## Collapse or expand the navigation

Click the panel button at the far left of the top bar, or press **⌘B** (**Ctrl+B**). The navigation switches between
expanded and collapsed. Collapsed, it is a column of icons that leaves the page about 150 px more room; point at an
icon, or move keyboard focus to it, to see its name.

To choose how it opens every time, open **Settings** → **Account** → **Navigation**:

| Choice | What you get |
|---|---|
| **Expanded** | The default. Icons and names, in their groups, beside the page |
| **Collapsed** | The column of icons |
| **Expand on hover** | The column of icons, which opens over the page when you rest the pointer on it or tab into it. It closes when you move away, choose a page, or press **Esc**, and the page never moves |

Your account keeps the choice, so it applies in every browser you sign in from. With **Expand on hover**, ⌘B pins the
navigation open; press it again to collapse it.

## Use it on a phone

In a window narrower than 768 px the navigation is hidden. Tap the menu button at the far left of the top bar, then
choose a page; the menu closes. Your saved choice applies again in a wider window.

## If it goes wrong

### ⌘B does nothing

Focus is outside the page, for example in the browser's address bar: click the page and press it again. In a window
narrower than 768 px, ⌘B does nothing; use the menu button. On a Mac, use ⌘B in the SQL editor: there, Ctrl+B moves the
cursor.

### Admin is missing

**Admin** appears only for users holding a global permission. Ask an administrator for one; see
[Permissions](../reference/permissions.md#global-permissions).

### "Couldn't save your preference"

DuckHaven could not reach the server, so the navigation went back to the choice your account holds. Choose again once
the connection is back.

## Related

- [Read the session audit trail](../querying/session-audit.md)
- [Saved queries](../querying/saved-queries.md)
- [Your preferences in the REST API](../reference/rest-api.md#your-preferences), to set the choice from a script
