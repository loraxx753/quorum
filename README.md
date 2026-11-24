# Quorum

Public engine for multi-agent “quorum” conversations: a Cursor SDK runner plus a live transcript viewer.

Private runs live in [`quorum-instance`](https://github.com/loraxx753/quorum-instance). When a conversation is worth sharing, **import a curated copy into this repo** (for example under `shared/`). Do not mirror the whole instance; publish only what you choose.

| Repo | Role |
| --- | --- |
| [`loraxx753/quorum`](https://github.com/loraxx753/quorum) | Public engine + optionally shared conversations |
| [`loraxx753/quorum-instance`](https://github.com/loraxx753/quorum-instance) | Private instance: all runs, personas, topics, drafts, answers |

## Layout

- `backend/` — `converse.py` runner and offline tests
- `frontend/` — React viewer that polls `transcripts/latest.md`
- `source-files/rules/` — default room rules (generalized case)
- `examples/` — public-safe sample cast (not a live transcript)
- `shared/` — curated exports from `quorum-instance` (add only when sharing on purpose)

## Quick start

```bash
# Runner
cd backend
python3.12 -m venv .venv
.venv/bin/pip install cursor-sdk
export CURSOR_API_KEY=...
.venv/bin/python converse.py \
  --personas ../examples/ship-or-test.md \
  --rules ../source-files/rules/collaborative.md \
  --wrap-at 40

# Viewer (separate terminal)
cd frontend
npm install
npm run dev
```

For day-to-day work, use `quorum-instance`. Keep drafting, answers, and raw transcripts there.

## Sharing a conversation

1. In `quorum-instance`, pick a run worth publishing.
2. Copy only the files you want public into `quorum/shared/<name>/` (for example `transcript.md`, `report.md`, `summary.md`).
3. Strip or rewrite anything that should stay private before committing.
4. Commit and push **this** repo. Instance paths stay gitignored here so a casual `git add` cannot publish them by accident.

## Safety

These paths are ignored in this repo on purpose (they belong on the instance until you deliberately export):

- `conversations/`, `transcripts/`, `outputs/`, `sandbox/`
- `source-files/personas/`, `topics/`, `drafts/`, `answers/`
- `*private.md` / Founder OS private notes

`shared/` is tracked. Only put content there when you mean to publish it.
