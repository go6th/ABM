# Job classification rubric

The PRD names four classes: Build role, Admin and owner role, Working tool, and User
role. It does not define their meanings or provide evidence cues. `config/posting_rubric.yaml`
therefore contains proposal-only cues derived from the class labels. A cue hit is not a
classification finding unless the posting description has been read and its evidence is
verified. Conflicting cues remain `unclear`. Title-only platform matches remain leads.

**D-16 remains open.** Product must define class meanings/cues and choose whether full-text
is required for `[build, admin_owner]` or `[build]`. The active full-text scope is null until
that decision is supplied. No model or rule can classify from the title alone.
