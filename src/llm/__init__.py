"""Model routing: one table (``llm.route``: task kind -> tier -> ``config/models.yaml`` task,
default effort and role), the user's model choice (``llm.choice``), the one price table
(``llm.cost``), the reply log and cache (``llm.reply_cache``) and schema validation
(``llm.schema``). Every research call goes through ``workflow.deps.RunDeps.resolve_model`` to the
live port (``platform.llm.ChoiceLlm``); every response is schema-validated in code."""
