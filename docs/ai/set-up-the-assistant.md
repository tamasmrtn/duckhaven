---
type: how-to
---

# Set up the assistant

Turn on the AI assistant with a model of your choice, then give its service account access to the workspaces it
should serve.

The assistant acts as a service account named **Assistant**, which DuckHaven creates when the assistant is enabled,
with no access at all. What it can see and do is exactly what you grant that account; see
[AI data assistant](assistant.md#identity).

## Before you begin

- **You need:** shell access to the control-plane host to edit `.env`, and the `users:manage` permission (the `admin`
  role) to give the account workspace access.
- **A model:** an API key for Anthropic, OpenAI or Mistral, or an OpenAI-compatible endpoint such as Ollama or vLLM,
  which can run without a key.
- **Check for an existing account named Assistant.** If **Admin → Service accounts** already lists one, created for
  something else, enabling the assistant adopts it with all its access. Rename it first if the assistant should not
  inherit that.

## 1. Choose the model and turn it on

Add to `deploy/.env`, for example for Anthropic:

```bash
ASSISTANT_ENABLED=true
ASSISTANT_MODEL=anthropic:claude-sonnet-5
ASSISTANT_API_KEY=sk-ant-…
```

or for a self-hosted, OpenAI-compatible endpoint:

```bash
ASSISTANT_ENABLED=true
ASSISTANT_MODEL=openai:llama3.1
ASSISTANT_OPENAI_BASE_URL=http://ollama.internal:11434/v1
```

`ASSISTANT_MODEL` is a Pydantic AI model string: `anthropic:…`, `openai:…` or `mistral:…`. Instead of
`ASSISTANT_API_KEY` you can set the provider's own variable (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or
`MISTRAL_API_KEY`). The other settings, such as limits per turn, are in
[Configuration](../reference/configuration.md#ai-assistant).

Apply it:

```bash
docker compose up -d
```

The API creates the **Assistant** service account on startup.

## 2. Give it access to a workspace

1. Open **Admin → Service accounts**, then the **Assistant** account's **⋯ → Manage workspaces**.
2. Choose its role in each workspace where people should use it:
    - `reader` lets it browse and read;
    - `writer` lets it propose writes, which still need a person's approval each time.
3. In a [scoped catalog](../access/scoped-access.md), also grant it the tables it should see, as for any member.

Leave it out of a workspace and the assistant cannot be used there. The same assistant can be broad in a sandbox and
narrow in production.

## Check that it worked

Open a worksheet in a workspace you gave it, open the assistant panel, and ask "Which tables are in this workspace?".
It answers with the tables its grants show it. Its queries appear in History under the **Assistant** account, with the
client `duckhaven-assistant`.

## If it goes wrong

### "Assistant is turned off"

`ASSISTANT_ENABLED` is not `true` on the API. Set it in `.env` and run `docker compose up -d`; a restart without
`up -d` keeps the old environment.

### "The assistant isn't a member of this workspace, so every question would come back denied."

Give the **Assistant** account a role in this workspace, as in step 2.

### "The assistant's service account is missing or disabled, so it can't answer anything."

The account was deactivated, which stops the assistant everywhere. Reactivate it under **Admin → Service accounts**.
A deleted account is recreated, with no access, on the next restart.

### The model call fails

The API's log has the provider's error. Check `ASSISTANT_MODEL` names a model your key can use, and for a self-hosted
endpoint that `ASSISTANT_OPENAI_BASE_URL` ends in `/v1` and is reachable from the API container.

### It answers questions about DuckHaven without its documentation

Documentation lookup is controlled by `ASSISTANT_DOCS_ENABLED`, on by default; the documentation ships inside the API
image.

## Related

- [AI data assistant](assistant.md) — what it can do, and how it is governed.
- [Use the AI assistant](use-the-assistant.md) — for the people who chat with it.
- [Service accounts & tokens](../access/service-accounts.md)
