# Workspace chat

The `/chat` screen uses the authenticated tenant API. The browser receives only the allowlisted
model catalogue and streamed answer text; provider credentials stay in the API process. Locally,
the existing `ANTHROPIC_API_KEY` setting is read from the ignored `.env`. Cloud deployments reference
the `anthropic-api-key` Secret Manager secret through Cloud Run, and do not put secret values in an
image or Terraform state. When the cloud KMS key is configured, workspace admins can optionally set
a tenant-owned key in the chat sidebar. It is encrypted with Cloud KMS using tenant-bound associated
data, stored as ciphertext under PostgreSQL RLS, and never returned to the browser. The password
field is cleared after submission; the key is not persisted in local storage. Without a tenant key,
chat uses the server-side default credential.

## Models and limits

`config/chat_models.yaml` is the model and price allowlist. It defines the default model, public
input/output and cache prices, provider capabilities, context sizes, and server limits. The API
returns the runtime output cap in `GET /v1/models`; per-request model ids must be present in the
allowlist. The chat endpoint currently supports streaming text only, so its vision and tool-use
capabilities are false even when the underlying provider model supports them.

Each thread is capped at 20 turns and $1.00 of estimated or reported spend. Input is limited to
60,000 characters per conversation, output to 2,048 tokens per response, and a worst-case cost
reservation is made before sending a request. The reservation prices all input at the higher of
ordinary-input and cache-write rates. Reported usage and spend are committed atomically; if usage
cannot be reported, the reserved maximum is charged. No request can raise these limits.

## Stored data and access

Threads and turns are stored in PostgreSQL with forced row-level security, scoped by tenant and
owner. A member cannot list, read, stream into, or delete another member's conversation. Optional
account attribution is checked against the caller's tenant before it is stored. Delete removes the
whole conversation and is blocked while a response is active. Idempotency keys prevent a completed
turn from being charged twice.

Tenant key management is admin-only and step-up protected. The API exposes only availability,
configured state, and an update timestamp. Replacing or removing a key creates a content-free audit
event; the key and ciphertext are not part of the event.

Successful token usage and spend are emitted as structured logs tagged with tenant, account (when
provided), client, model, phase, and outcome. Failed turns are recorded with `usage_known=false`
instead of fabricating provider token counts. Prompts and response text are never added to logs.

## Price source

The prices and model identifiers in the versioned YAML were checked against the
[Anthropic pricing documentation](https://platform.claude.com/docs/en/about-claude/pricing) on
2026-10-09. Verify both before changing the catalogue; provider pricing and model lifecycle can
change independently of this repository.
