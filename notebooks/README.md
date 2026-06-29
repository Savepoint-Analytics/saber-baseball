# Notebooks — exploration ONLY

Per `CLAUDE.md` §3 and `AGENTS.md`, **production logic never lives in notebooks.**
Use this folder for ad-hoc exploration, EDA, and prototyping. When something proves
useful, port it into `src/` (typed, tested, lint-clean) and delete or archive the
notebook.

Guidelines:

- Clear outputs before committing (`.ipynb_checkpoints/` is git-ignored).
- Import from `src` rather than redefining pipeline logic.
- Any data shown that derives from a restricted source must still display the source
  attribution (`from src.compliance.attributions import attribution_block`).
