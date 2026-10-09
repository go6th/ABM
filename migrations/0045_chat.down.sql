-- Remove tenant-scoped chat history and its turn-level spend ledger.

DROP TABLE IF EXISTS chat_turns;
DROP TABLE IF EXISTS chat_threads;
