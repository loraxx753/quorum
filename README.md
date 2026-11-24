# Quorum

Public engine for multi-agent “quorum” conversations: a Cursor SDK runner plus a live transcript viewer.

Private runs (personas, topics, drafts, answers, and transcripts) live in a separate **instance** repo and are gitignored here so they are not published by accident.

| Repo | Role |
| --- | --- |
| [`loraxx753/quorum`](https://github.com/loraxx753/quorum) | Public engine (this repo) |
| [`loraxx753/quorum-instance`](https://github.com/loraxx753/quorum-instance) | Private instance data and full local runs |

## Layout

- `backend/` — `converse.py` runner and offline tests
- `frontend/` — React viewer that polls `transcripts/latest.md`
- `source-files/rules/` — default room rules (generalized case)
- `examples/` — public-safe sample cast (not a live transcript)

## Quick start

```bash
# Runner
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r <(echo cursor-sdk)   # or your pinned deps
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

For real work, clone `quorum-instance` (or copy `source-files/` and `transcripts/` into a local checkout) and keep those paths untracked in any public fork.

## Safety

These paths are ignored in this repo on purpose:

- `conversations/`, `transcripts/`, `outputs/`, `sandbox/`
- `source-files/personas/`, `topics/`, `drafts/`, `answers/`
- `*private.md` / Founder OS private notes
