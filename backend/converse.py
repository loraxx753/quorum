#!/usr/bin/env python3
r"""Run a turn-based group conversation between Cursor SDK agents and record it as a transcript.

Each agent is a persistent local agent, so it keeps its own memory of the conversation.
Speakers take turns round-robin. Without --turns it runs until Ctrl+C; the first Ctrl+C finishes
the current message, writes the stop note, and closes. A second Ctrl+C quits at once. Before each turn, the speaker is sent every message
posted since it last spoke, so it can respond to the whole group, not just the last line.

Every run has a moderator: a character with the role "Facilitator", or a built-in one when the cast has
none. It speaks only when the standing rules say there is tension or a problem, and otherwise replies
PASS. A PASS is not posted and doesn't use a turn.

The run also ends on its own when the conversation stalls (the last two rounds add almost no new
words, or replies shrink to a few words). With a facilitator (a character whose role is
"Facilitator"), it is asked to write a closing note first, which stays in the transcript. Use
--wrap-at N for a message budget and --no-stall-check to turn the stall check off. Errors are
printed to the terminal and never written into the transcript; a run that fails before the first
message leaves no transcript.

When the conversation ends (any way except an error or a second Ctrl+C), one character, the
facilitator if there is one, writes report.md and then summary.md. Use --no-report to skip them.

Repositories (optional) are copied into `sandbox/repos/` before the run. Agents read and
search those copies, so anything they change lands in the copy, never in your real repo.

Output goes to transcripts/<folder>/, where <folder> is the topic file's name (or the conversation
file's name, or the goal):

    transcript.md   the conversation: one "**Name:** text" paragraph per speech, plus italic notes
    experiment.md   the shared document the group edited (only with --draft)
    report.md       a research report: what the group accomplished and how feasible it looks
    summary.md      the report at several levels: log line, elevator pitch, synopsis, treatment

A copy of transcript.md is kept at transcripts/latest.md for the viewer. Only the Goal of the topic
goes into the transcript. Agents still get the full topic.

Personas file (--personas), Markdown:

    # Optional title

    ## Topic            <- optional; the topic if --topic is not given
    What the group discusses.

    ## Repos            <- optional; one repo per bullet, agents can read these
    - ~/projects/meanwhile/meanwhile

    ## Samantha Stone - Clinical Psychologist   <- "## Name - Role"; the role is optional
    How Samantha should act. Everything under the header is sent to her as her prompt.

Or the cast layout, where every character is a '###' under '## Cast':

    # Optional title
    Optional setting paragraph.                 <- shared: sent to every character

    ## How They Talk and Work Together          <- any other '##' section is shared guidance
    Rules every character follows.

    ## Cast
    ### Samantha Stone (Clinical Psychologist)  <- "### Name (Role)"; the role is optional
    How Samantha should act. Everything under the header is sent to her as her prompt.

    ## Topic / ## Repos                         <- still reserved, in either layout

Topic (--topic) is either plain text or the path to a .md file.
Repos can also be added on the command line with --repo (repeatable).

Usage:
    export CURSOR_API_KEY=...
    backend/.venv/bin/python backend/converse.py --personas conversations/ship-or-test.md
    backend/.venv/bin/python backend/converse.py --topic source-files/topics/cosmological-constant.md \
        --personas source-files/personas/cosmological-constant.md
    backend/.venv/bin/python backend/converse.py --topic "Is the API stable?" --agents Ada,Grace
    backend/.venv/bin/python backend/converse.py --topic source-files/topics/five-year-plan.md \
        --personas source-files/personas/five-year-plan.md \
        --rules source-files/rules/collaborative.md --draft outputs/macovin-5yr-plan-public.md \
        --repo ~/projects/myself/repo --wrap-at 80
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import signal
import sys
import threading
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

from cursor_sdk import Agent, CursorAgentError, LocalAgentOptions

ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPTS = ROOT / "transcripts"
SANDBOX = ROOT / "sandbox"  # all agents run here
REPO_COPIES = SANDBOX / "repos"

DEFAULT_AGENTS = ["Ada", "Grace", "Linus"]

# Used only when no conversation file is given (--agents).
PERSONAS = {
    "Ada": "You push on assumptions and ask for evidence.",
    "Grace": "You build on ideas, propose concrete next steps, and say where you disagree.",
    "Linus": "You are blunt about tradeoffs and call out vague claims.",
    "Mae": "You translate the discussion into plain language and summarize where things stand.",
}
ROLES = {"Ada": "Skeptic", "Grace": "Builder", "Linus": "Ops Engineer", "Mae": "Translator"}
GENERIC_PERSONA = "You are a thoughtful participant. Add something the others have not said yet."

RESERVED_HEADERS = {"topic", "repos"}
HONORIFIC = re.compile(r"^(?:Dr|Mr|Ms|Mrs|Prof)\.?\s+", re.IGNORECASE)

# Skipped when copying repos: dependencies, build output, VCS metadata, caches.
IGNORED_NAMES = {
    ".git", "node_modules", ".venv", "venv", "dist", "build", "coverage", "storybook-static",
    "__pycache__", ".next", ".mypy_cache", ".pytest_cache", ".DS_Store",
    # Personal exports and client material: never copied for agents to read.
    "raw-exports", "client.md",
}
MAX_FILE_BYTES = 2_000_000

CHAT_RULES = (
    "This is a group conversation, not a coding task. Do not use tools, read files, or edit anything. "
)
REPO_RULES = (
    "You may use read and search tools (Read, Grep, Glob, LS) on the repository copies listed below, "
    "to answer questions about them. Do not edit, create, or delete files, and do not run shell commands. "
    "Cite file paths when you refer to code. "
)
REPLY_RULES = (
    "Reply as spoken dialogue in plain sentences. Short paragraphs are fine. Do not start with your name or a label. "
    "No headings, bullet points, stage directions, or code. Respond to the most relevant point someone else made, "
    "then move the conversation forward with one claim or one question. Do not repeat what was already said."
)
FACILITATOR_RULES = (
    "You are the facilitator: you steer the discussion and do not argue a side. Each turn, keep it short and do not start "
    "with your name or a label. "
    "name the point that matters most, ask the question that moves things forward, or flag a gap in the reasoning. "
    "End your reply with a final line of the form 'Next: <name>', naming the one participant who should speak next. "
    "When the group has reached agreement or has stalled, end with 'Next: Close' instead. Before a 'Next: Close' line, "
    "write a summary in under 120 words: the decisions, the open questions, and the action items. "
    "You are responsible for ending the session. Never announce that steering is paused, silent, idle or dormant, "
    "and never wait for a later breach: if there is nothing left to decide, close with the summary right away. "
    "If nothing needs your attention this turn (the standing rules say when to step in), reply with only the word "
    "PASS. You may add a 'Next: <name>' line after it. Do not PASS when the discussion is closing."
)
# The moderator every run gets when the cast has no facilitator. Its full job is in the Moderator
# section of the standing rules; this is the fallback when no rules file is given.
MODERATOR_NAME = "Moderator"
MODERATOR_PERSONA = (
    "You moderate this group. You are not a participant, and you never take a side on the topic. "
    "Stay quiet unless there is tension or a problem: two people talking past each other, a point that "
    "matters for a decision that nobody has answered, the group going in circles, or someone being cut off. "
    "When you step in, name the tension in one or two plain sentences, say what would settle it, and give "
    "the floor to the right person with a 'Next:' line. When nothing needs you, reply with only the word PASS."
)
DOC_RULES = (
    "There is a shared document the group is editing. It is shown to you whenever it changes. To change it, add "
    "edit blocks after your dialogue, in exactly this form:\n"
    "<<<EDIT\n"
    "REPLACE\n"
    "<the exact text now in the document, copied word for word, appearing only once>\n"
    "WITH\n"
    "<the new text>\n"
    "END\n"
    ">>>\n"
    "Other forms: INSERT AFTER (exact anchor text, then WITH, then the new text, then END) adds text after the anchor; "
    "REMOVE (exact text, then END) deletes it. Only propose edits that make the document clearer, simpler and more "
    "useful. A proposal whose target text doesn't match exactly is not applied, so copy carefully."
)
WRAP_UP_PROMPT = (
    "The discussion {reason}. Close it now. Write the closing summary in under 120 words: the decisions made, "
    "the questions still open, and the action items. Then end with the final line 'Next: Close'."
)

# The two closing documents. One character writes both, speaking for the whole group. What goes in
# them (sections, scope, evidence) is set by the standing rules; these prompts only cover the mechanics,
# with a short fallback for runs that have no rules file.
REPORT_PROMPT = (
    "The conversation is over. Write report.md. Follow the 'Report' rules in the standing rules; if you were "
    "given none, write a short report on the topic with these '## ' sections: Question, Findings, Open Questions. "
    "Write as one neutral author speaking for the whole group. Do not speak as your character, do not use speaker "
    "labels, and do not end with a 'Next:' line. Start with a '# ' title line. Reply with the markdown only."
)
SUMMARY_PROMPT = (
    "Now write summary.md: the report you just wrote, at several levels of explanation. Follow the 'Summary' rules "
    "in the standing rules; if you were given none, write these '## ' sections: Log Line (one sentence), Elevator "
    "Pitch (three sentences at most), Synopsis (one paragraph). Use only what is in the report, and keep any [Open] "
    "marks. Same neutral author, no speaker labels, no 'Next:' line. Start with a '# ' title line. Reply with the "
    "markdown only."
)
DIRECTIVE_LINE = re.compile(r"^[\s*_]*Next:[\s*_]*(.+?)[\s*_.]*$", re.IGNORECASE | re.MULTILINE)

# Stagnation: stop when the group stops saying anything new.
NOVELTY_THRESHOLD = 0.15  # share of words in the recent window never used earlier in the conversation
MIN_AVG_WORDS = 6  # average words per message over the last round
# Prompt scaffolding that an agent sometimes echoes back instead of answering.
ECHO_MARKERS = ("New messages since you last spoke", "Open the conversation with your first point")
ECHO_RETRY_PROMPT = (
    "Your last reply repeated the prompt instead of answering it. "
    "Reply with only your own line of dialogue as {name}."
)
MAX_CONSECUTIVE_SKIPS = 3


class InputError(Exception):
    """Bad user input (missing file, malformed conversation file, ...)."""


# --------------------------------------------------------------------------- names


def short_name(name: str) -> str:
    """How a character is called in dialogue: the first name, without honorifics. 'Dr. Kenji Takahashi' -> 'Kenji'."""
    stripped = HONORIFIC.sub("", name.strip())
    return stripped.split()[0] if stripped.split() else name


def speaker_label(name: str, role: str) -> str:
    """'Samantha (Clinical Psychologist)', or just 'Ada' when there is no role."""
    short = short_name(name)
    return f"{short} ({role})" if role else short


def cast_line(name: str, role: str) -> str:
    return f"{name} - {role}" if role else name


def find_facilitator(roster: list[str], roles: dict[str, str]) -> str | None:
    """The character whose role is 'Facilitator', if any. Only one is allowed."""
    found = [name for name in roster if roles.get(name, "").strip().lower() == "facilitator"]
    if len(found) > 1:
        raise InputError(f"only one facilitator is allowed, found: {', '.join(found)}")
    return found[0] if found else None


def parse_directive(text: str, participants: list[str]) -> tuple[str | None, str]:
    """Pull the 'Next: ...' line out of a facilitator reply.

    Returns (target, cleaned text). target is a participant's name, 'close', or None when the
    line is missing or names nobody (the runner then falls back to round-robin).
    """
    matches = list(DIRECTIVE_LINE.finditer(text))
    if not matches:
        return None, text.strip()
    last = matches[-1]
    cleaned = (text[: last.start()] + text[last.end():]).strip()
    wanted = last.group(1).strip()
    if wanted.lower() == "close":
        return "close", cleaned
    for name in participants:
        if wanted.lower() in (name.lower(), short_name(name).lower()):
            return name, cleaned
    print(f"Facilitator named '{wanted}', which isn't a participant. Using round-robin.")
    return None, cleaned


def is_pass(text: str) -> bool:
    """True when a facilitator's reply is only the word PASS. Call after parse_directive has removed the Next: line."""
    return text.strip().strip("*_.` ").upper() == "PASS"


def is_echo(text: str) -> bool:
    """True when a reply is the runner's own prompt scaffolding repeated back, not a real reply."""
    return any(marker in text for marker in ECHO_MARKERS) or bool(re.search(r"Reply as [^\n]+\.\s*$", text))


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


class StagnationMonitor:
    """Detects a conversation that has stopped producing anything new.

    Two signals, checked after every message once three full rounds exist:
    - novelty: the last two rounds introduced almost no word not already used earlier. It must stay
      low for a full round of consecutive messages, so one quiet stretch doesn't end the run.
    - brevity: replies in the last round averaged only a few words ("Dark.", "Closed.").
    """

    def __init__(self, round_size: int) -> None:
        self.round_size = round_size
        self.low_streak = 0

    def novelty(self, history: list[tuple[str, str]]) -> float:
        window = 2 * self.round_size
        seen = {w for _, text in history[:-window] for w in _words(text)}
        recent = [w for _, text in history[-window:] for w in _words(text)]
        return sum(w not in seen for w in recent) / max(1, len(recent))

    def update(self, history: list[tuple[str, str]]) -> str | None:
        """Call after each message. Returns a reason (a clause for the wrap-up prompt) or None."""
        if len(history) < 3 * self.round_size:
            return None
        avg_words = sum(len(_words(text)) for _, text in history[-self.round_size:]) / self.round_size
        if avg_words < MIN_AVG_WORDS:
            return f"has shrunk to one-word replies (about {avg_words:.0f} words each)"
        self.low_streak = self.low_streak + 1 if self.novelty(history) < NOVELTY_THRESHOLD else 0
        if self.low_streak >= self.round_size:
            return "has stopped producing new points; the last two rounds repeated earlier ones"
        return None


def check_short_names(roster: list[str]) -> None:
    seen: dict[str, str] = {}
    for name in roster:
        short = short_name(name).lower()
        if short in seen:
            raise InputError(
                f"'{seen[short]}' and '{name}' both appear as '{short_name(name)}' in dialogue. "
                "Rename one of them so each character has a distinct first name."
            )
        seen[short] = name


# --------------------------------------------------------------------------- parsing


@dataclass
class ConversationFile:
    title: str | None = None
    topic: str | None = None
    repos: list[str] = field(default_factory=list)
    personas: dict[str, str] = field(default_factory=dict)  # name -> prompt; insertion order = speaking order
    roles: dict[str, str] = field(default_factory=dict)  # name -> role ("" when none)


def parse_bullets(text: str) -> list[str]:
    return [m.group(1).strip() for line in text.splitlines() if (m := re.match(r"^\s*[-*]\s+(.+?)\s*$", line))]


def parse_conversation_md(text: str) -> ConversationFile:
    """Split on level-2 headers. `## Topic` and `## Repos` are reserved; every other `##` header is a character.

    A character header is `## Name` or `## Name - Role`. Headers inside fenced code blocks are ignored.
    """
    conv = ConversationFile()
    sections: list[tuple[str, list[str]]] = []
    preamble: list[str] = []  # text before the first '##' header
    fence: str | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            fence = None if fence == marker else (fence or marker)
            (sections[-1][1] if sections else preamble).append(line)
            continue

        if fence is None:
            h2 = re.match(r"^##\s+(.+?)\s*#*\s*$", line)
            if h2:
                sections.append((h2.group(1).strip(), []))
                continue
            h1 = re.match(r"^#\s+(.+?)\s*#*\s*$", line)
            if h1 and conv.title is None and not sections:
                conv.title = h1.group(1).strip()
                continue

        (sections[-1][1] if sections else preamble).append(line)

    if any(header.lower() == "cast" for header, _ in sections):
        return _parse_cast_format(sections, preamble, conv)

    for header, body in sections:
        content = "\n".join(body).strip()
        key = header.lower()
        if key == "topic":
            conv.topic = content or None
        elif key == "repos":
            conv.repos = parse_bullets(content)
        else:
            split = re.match(r"^(.+?)\s+-\s+(.+)$", header)
            name, role = (split.group(1).strip(), split.group(2).strip()) if split else (header, "")
            if name in conv.personas:
                raise InputError(f"duplicate character header: '## {name}'")
            conv.personas[name] = content
            conv.roles[name] = role

    return conv


def _split_h3(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Split a block into '###' sections. Headers inside fenced code blocks are ignored; lines before the first header are dropped."""
    out: list[tuple[str, list[str]]] = []
    fence: str | None = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            fence = None if fence == marker else (fence or marker)
        elif fence is None:
            h3 = re.match(r"^###\s+(.+?)\s*#*\s*$", line)
            if h3:
                out.append((h3.group(1).strip(), []))
                continue
        if out:
            out[-1][1].append(line)
    return out


def _split_name_role(header: str) -> tuple[str, str]:
    """'Name (Role)', 'Name - Role', or just 'Name'."""
    paren = re.match(r"^(.+?)\s*\((.+)\)\s*$", header)
    if paren:
        return paren.group(1).strip(), paren.group(2).strip()
    dash = re.match(r"^(.+?)\s+-\s+(.+)$", header)
    if dash:
        return dash.group(1).strip(), dash.group(2).strip()
    return header.strip(), ""


def _parse_cast_format(
    sections: list[tuple[str, list[str]]], preamble: list[str], conv: ConversationFile
) -> ConversationFile:
    """The '## Cast' layout:

        # Title
        <setting paragraph>                     <- shared, sent to every character
        ## How They Talk and Work Together       <- shared, sent to every character
        ## Cast
        ### Name (Role)                         <- one character each, prompt is the body
        ## Topic / ## Repos                     <- reserved, as in the old layout

    Every other '##' section is shared guidance. Shared text comes first in each character's prompt.
    """
    shared: list[str] = []
    cast_body: list[str] | None = None

    setting = "\n".join(preamble).strip()
    if setting:
        shared.append(setting)

    for header, body in sections:
        key = header.lower()
        content = "\n".join(body).strip()
        if key == "topic":
            conv.topic = content or None
        elif key == "repos":
            conv.repos = parse_bullets(content)
        elif key == "cast":
            if cast_body is not None:
                raise InputError("duplicate '## Cast' section")
            cast_body = body
        elif content:
            shared.append(f"## {header}\n{content}")

    shared_text = "\n\n".join(shared)
    for header, body in _split_h3(cast_body or []):
        name, role = _split_name_role(header)
        if name in conv.personas:
            raise InputError(f"duplicate character header: '### {name}'")
        prompt = "\n".join(body).strip()
        conv.personas[name] = f"{shared_text}\n\n{prompt}" if shared_text else prompt
        conv.roles[name] = role
    return conv


def read_conversation_file(raw: str) -> ConversationFile:
    path = Path(raw).expanduser()
    if not path.is_file():
        raise InputError(f"conversation file not found: {path}")
    conv = parse_conversation_md(path.read_text(encoding="utf-8"))
    if len(conv.personas) < 2:
        raise InputError(
            f"{path} needs at least two character headers (found {len(conv.personas)}). "
            "Add sections like '## Ada - Skeptic' and '## Grace - Builder'."
        )
    return conv


def read_rules_file(raw: str) -> str:
    """Standing rules for every character (the --rules file). Sent once, on each agent's first turn."""
    path = Path(raw).expanduser()
    if not path.is_file():
        raise InputError(f"rules file not found: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise InputError(f"rules file is empty: {path}")
    return text


def resolve_topic(raw: str) -> tuple[str, str | None]:
    """Return (topic text, title). `raw` is plain text, or a path to a .md file."""
    if raw.lower().endswith(".md"):
        path = Path(raw).expanduser()
        if not path.is_file():
            raise InputError(f"topic file not found: {path}")
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise InputError(f"topic file is empty: {path}")
        h1 = re.search(r"^#\s+(.+?)\s*$", text, flags=re.MULTILINE)
        return text, (h1.group(1) if h1 else path.stem.replace("-", " ").replace("_", " "))
    return raw.strip(), None


def resolve_repos(raw_paths: list[str]) -> list[Path]:
    repos: list[Path] = []
    for raw in raw_paths:
        path = Path(raw).expanduser().resolve()
        if not path.is_dir():
            raise InputError(f"repo not found (or not a directory): {path}")
        if path not in repos:
            repos.append(path)
    return repos


def derive_title(topic: str) -> str:
    first_line = next((ln.strip() for ln in topic.splitlines() if ln.strip()), "Conversation")
    first_line = re.sub(r"^#+\s*", "", first_line)
    return first_line if len(first_line) <= 80 else first_line[:77] + "..."


def _paragraph(lines: list[str]) -> str:
    """The first run of non-blank lines, stopping at a blank line, heading, or **Label:**."""
    out: list[str] = []
    for line in lines:
        if not line.strip():
            if out:
                break
            continue
        if out and (line.lstrip().startswith("#") or re.match(r"^\s*\*\*[^*]+:\*\*", line)):
            break
        out.append(line.strip())
    return " ".join(out)


def extract_goal(topic: str) -> str:
    """The Goal of a topic: the paragraph under a `## Goal` (or `## Objective`) heading,
    or after a `**Goal:**` label. Falls back to the first paragraph of the topic."""
    lines = topic.splitlines()
    for i, line in enumerate(lines):
        label = re.match(r"^\s*\*\*(?:Goal|Objective):\*\*\s*(.*)$", line, re.IGNORECASE)
        if label:
            return _paragraph([label.group(1), *lines[i + 1:]])
        if re.match(r"^#{1,6}\s*(?:Goal|Objective)\s*$", line.strip(), re.IGNORECASE):
            return _paragraph(lines[i + 1:])
    body = [ln for ln in lines if not ln.lstrip().startswith("#")]
    return _paragraph(body) or derive_title(topic)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "conversation"


# --------------------------------------------------------------------------- repo snapshots


def _copy_tree(src: Path, dest: Path) -> None:
    """Copy file contents only (no macOS metadata), skipping ignored folders and oversized files."""
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_NAMES)
        rel = Path(dirpath).relative_to(src)
        (dest / rel).mkdir(parents=True, exist_ok=True)
        for filename in sorted(filenames):
            if filename in IGNORED_NAMES:
                continue
            source = Path(dirpath) / filename
            if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_FILE_BYTES:
                continue
            shutil.copyfile(source, dest / rel / filename)


def snapshot_repos(repos: list[Path]) -> dict[str, Path]:
    """Copy each repo into sandbox/repos/<name>. Returns {name: copy path}. Replaces older copies."""
    if REPO_COPIES.exists():
        shutil.rmtree(REPO_COPIES)
    REPO_COPIES.mkdir(parents=True)

    copies: dict[str, Path] = {}
    for repo in repos:
        name = repo.name
        n = 2
        while name in copies:  # two repos with the same folder name
            name = f"{repo.name}-{n}"
            n += 1
        dest = REPO_COPIES / name
        dest.mkdir()
        _copy_tree(repo, dest)
        copies[name] = dest
    return copies


# --------------------------------------------------------------------------- prompts


def build_prompt(
    name: str,
    role: str,
    roster: list[str],
    persona: str,
    topic: str,
    repo_copies: dict[str, Path],
    unseen: list[tuple[str, str]],
    introduced: bool,
    facilitator: bool = False,
    wrap_up: str | None = None,
    standing_rules: str | None = None,
    document: str | None = None,
    show_doc: bool = False,
    doc_version: int = 0,
) -> str:
    """Intro (roster, persona, topic, repos, rules) on an agent's first send only, then every unseen message."""
    parts: list[str] = []
    if not introduced:
        others = ", ".join(a for a in roster if a != name)
        reply = FACILITATOR_RULES if facilitator else REPLY_RULES
        rules = (REPO_RULES if repo_copies else CHAT_RULES) + reply
        who = f"{name} ({role})" if role else name
        intro = (
            f"You are {who}, one of several AI agents in a group conversation with {others}.\n\n"
            f"How to act in this conversation:\n{persona}\n\n"
            f"Topic:\n{topic}\n"
        )
        if standing_rules:
            intro += f"\nStanding rules for the whole conversation (follow them in every reply):\n{standing_rules}\n"
        if document is not None:
            intro += f"\n{DOC_RULES}\n"
        if repo_copies:
            listing = "\n".join(f"- {repo_name}: {path}" for repo_name, path in repo_copies.items())
            intro += f"\nRepository copies you can read (edits here are discarded):\n{listing}\n"
        parts.append(f"{intro}\n{rules}")
    if document is not None and show_doc:
        parts.append(f"Current document, version {doc_version}:\n\n{document}")
    if unseen:
        body = "\n\n".join(f"{speaker}:\n{text}" for speaker, text in unseen)
        parts.append(f"New messages since you last spoke:\n\n{body}\n\nReply as {name}.")
    elif not wrap_up:
        parts.append("Open the conversation with your first point.")
    if wrap_up:
        parts.append(WRAP_UP_PROMPT.format(reason=wrap_up))
    return "\n\n".join(parts)


def clean_document(text: str) -> str:
    """The markdown an agent wrote, minus a wrapping code fence and any trailing 'Next:' line."""
    body = text.strip()
    lines = body.splitlines()
    if len(lines) >= 2 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        body = "\n".join(lines[1:-1]).strip()
    matches = list(DIRECTIVE_LINE.finditer(body))
    if matches and not body[matches[-1].end():].strip():
        body = body[: matches[-1].start()].strip()
    return body


def closing_prompt(instruction: str, unseen: list[tuple[str, str]], document: str | None) -> str:
    """The report request, with what the writer hasn't seen yet: the document as it stands and any new messages."""
    parts: list[str] = []
    if document is not None:
        parts.append(f"The shared document as it stands:\n\n{document}")
    if unseen:
        body = "\n\n".join(f"{speaker}:\n{text}" for speaker, text in unseen)
        parts.append(f"Messages posted since you last spoke:\n\n{body}")
    parts.append(instruction)
    return "\n\n".join(parts)


def request_document(agent, prompt: str, filename: str) -> str | None:
    """Ask `agent` to write one document. Returns the markdown, or None (noted on stderr) when it can't be used."""
    try:
        result = agent.send(prompt).wait()
    except CursorAgentError as err:
        print(f"Could not write {filename}: {err.message}", file=sys.stderr)
        return None
    if result.status != "finished":
        print(
            f"Could not write {filename}: run ended with status {result.status}: {result.error_message or 'no details'}",
            file=sys.stderr,
        )
        return None
    text = clean_document(result.result or "")
    if not text or is_echo(text):
        print(f"Could not write {filename}: the reply was empty or repeated the prompt.", file=sys.stderr)
        return None
    return text


# --------------------------------------------------------------------------- transcript


def read_draft(raw: str) -> str:
    """The shared document the group edits (--draft). Agents propose changes; the runner applies them."""
    path = Path(raw).expanduser()
    if not path.is_file():
        raise InputError(f"draft file not found: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise InputError(f"draft file is empty: {path}")
    return text + "\n"


def topic_folder(topic_arg: str | None, conversation_arg: str | None, goal: str) -> str:
    """The folder under transcripts/ for this topic: the topic file's name, else the conversation file's, else the goal."""
    if topic_arg and topic_arg.lower().endswith(".md"):
        return slugify(Path(topic_arg).stem)
    if conversation_arg:
        return slugify(Path(conversation_arg).stem)
    return slugify(goal)


@dataclass
class Edit:
    op: str  # "replace", "insert_after", "remove", or "invalid" (text holds the error)
    target: str = ""
    text: str = ""

    def describe(self) -> str:
        if self.op == "invalid":
            return "malformed edit block"
        first = " ".join(self.target.split())[:60]
        return {
            "replace": f'replaced "{first}"',
            "insert_after": f'added text after "{first}"',
            "remove": f'removed "{first}"',
        }[self.op]


EDIT_BLOCK = re.compile(r"<<<EDIT[ \t]*\n(.*?)\n[ \t]*>>>", re.DOTALL)
EDIT_OPS = {"REPLACE": "replace", "INSERT AFTER": "insert_after", "REMOVE": "remove"}


def _collect(lines: list[str], i: int, stop: set[str]) -> tuple[str, int]:
    """Gather lines from i until a line that is one of the stop keywords. Returns (text, index of the keyword)."""
    out: list[str] = []
    while i < len(lines) and lines[i].strip().upper() not in stop:
        out.append(lines[i])
        i += 1
    return "\n".join(out).strip("\n"), i


def _parse_edit_body(body: str) -> list[Edit]:
    edits: list[Edit] = []
    lines = body.split("\n")
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        head = lines[i].strip().upper()
        if head not in EDIT_OPS:
            raise ValueError(f"unknown edit keyword '{lines[i].strip()}' (use REPLACE, INSERT AFTER or REMOVE)")
        i += 1
        target, i = _collect(lines, i, {"WITH", "END"})
        new = ""
        if i < len(lines) and lines[i].strip().upper() == "WITH":
            i += 1
            new, i = _collect(lines, i, {"END"})
        if i >= len(lines) or lines[i].strip().upper() != "END":
            raise ValueError("an edit block is missing its END line")
        i += 1
        op = EDIT_OPS[head]
        if not target.strip():
            raise ValueError(f"{head} needs the exact target text")
        if op != "remove" and not new.strip():
            raise ValueError(f"{head} needs new text after WITH")
        edits.append(Edit(op, target, new))
    return edits


def split_edits(text: str) -> tuple[list[Edit], str]:
    """Pull the <<<EDIT ... >>> blocks out of a reply. Returns (edits, the dialogue without them)."""
    edits: list[Edit] = []
    for match in EDIT_BLOCK.finditer(text):
        try:
            edits.extend(_parse_edit_body(match.group(1)))
        except ValueError as err:
            edits.append(Edit("invalid", text=str(err)))
    return edits, EDIT_BLOCK.sub("", text).strip()


def apply_edit(doc: str, edit: Edit) -> tuple[str | None, str]:
    """Apply one edit. Returns (new document, '') or (None, reason). The target must appear exactly once."""
    if edit.op == "invalid":
        return None, edit.text
    count = doc.count(edit.target)
    if count != 1:
        return None, f"its target text must appear exactly once in the document (found {count})"
    if edit.op == "replace":
        return doc.replace(edit.target, edit.text, 1), ""
    if edit.op == "remove":
        return doc.replace(edit.target, "", 1), ""
    return doc.replace(edit.target, edit.target + "\n\n" + edit.text, 1), ""


def clean_speech(text: str, short: str, role: str) -> str:
    """Drop a label the agent wrote at the start of its own reply, e.g. '**Built-In (Facilitator)** —' or 'Priya:'."""
    bold = re.match(r"^\s*\*\*([^*\n]{1,80})\*\*\s*[—–:-]?\s*", text)
    if bold:
        label = bold.group(1).lower()
        if short.lower() in label or "facilitator" in label or (role and role.lower() in label):
            text = text[bold.end():]
    plain = re.match(r"^\s*" + re.escape(short) + r"(?:\s*\([^)\n]{1,80}\))?\s*:\s+", text, re.IGNORECASE)
    if plain:
        text = text[plain.end():]
    return text.strip()


class Transcript:
    """The group's record, in transcripts/<folder>/:

    - transcript.md: the conversation, one `**Name:**` paragraph per speech. Errors and edit notes
      appear as italic notes. A copy goes to transcripts/latest.md for the viewer.
    - experiment.md: the shared document the group edited (only with --draft).
    - report.md and summary.md: written by one character when the conversation ends.

    Nothing is written until the first message, so a run that fails to start leaves no files. An
    existing copy of any of these files in the folder is renamed with its old timestamp, not overwritten.
    """

    def __init__(self, goal: str, cast: list[tuple[str, str]], folder: str) -> None:
        self.goal = goal
        self.title = derive_title(goal)
        self.stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.folder = TRANSCRIPTS / folder
        self.path = self.folder / "transcript.md"
        self.experiment_path = self.folder / "experiment.md"
        self.report_path = self.folder / "report.md"
        self.summary_path = self.folder / "summary.md"
        self.latest = TRANSCRIPTS / "latest.md"
        self.experiment_text: str | None = None
        self.started = False
        # Everything written to transcript.md, so the file can be rebuilt if the folder is moved or deleted mid-run.
        self._written = ""

    @staticmethod
    def _archive(path: Path) -> None:
        if path.exists():
            stamp = dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y%m%d-%H%M%S")
            path.rename(path.with_name(f"{path.stem}-{stamp}.md"))

    def _start(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        for old in (self.path, self.experiment_path, self.report_path, self.summary_path):
            self._archive(old)
        self._written = f"# {self.title}\n\n*Goal: {self.goal}*\n\n"
        self.path.write_text(self._written, encoding="utf-8")
        self.started = True
        self._write_experiment()
        self._sync()

    def _ensure_folder(self) -> None:
        """Recreate the topic folder and transcript.md if either was moved or deleted during the run."""
        self.folder.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(self._written, encoding="utf-8")
            self._write_experiment()

    def _append(self, text: str) -> None:
        if not self.started:
            self._start()
        self._ensure_folder()
        self._written += text
        with self.path.open("a", encoding="utf-8") as f:
            f.write(text)
        self._sync()

    def add(self, name: str, role: str, text: str) -> None:
        body = text.strip()
        if body:
            self._append(f"**{short_name(name)}:** {body}\n\n")
        elif not self.started:
            self._start()

    def edit_note(self, name: str, message: str) -> None:
        self._append(f"*[{short_name(name)} proposed an edit: {message}]*\n\n")

    def note(self, text: str) -> None:
        if not self.started:
            return
        self._append(f"*[Stopped: {text}]*\n\n")

    def set_experiment(self, text: str) -> None:
        self.experiment_text = text
        if self.started:
            self._write_experiment()

    def _write_experiment(self) -> None:
        if self.experiment_text is not None:
            self.folder.mkdir(parents=True, exist_ok=True)
            self.experiment_path.write_text(self.experiment_text.rstrip() + "\n", encoding="utf-8")

    def save_report(self, text: str, ended: str) -> Path:
        """Write report.md, with a one-line note of where it came from."""
        self._ensure_folder()
        self.report_path.write_text(
            f"{text.rstrip()}\n\n---\n\n*Written from {self.path.name}. The conversation ended: {ended}.*\n",
            encoding="utf-8",
        )
        return self.report_path

    def save_summary(self, text: str) -> Path:
        self._ensure_folder()
        self.summary_path.write_text(text.rstrip() + "\n", encoding="utf-8")
        return self.summary_path

    def _sync(self) -> None:
        shutil.copyfile(self.path, self.latest)


# --------------------------------------------------------------------------- main


def parse_roster(raw: str) -> list[str]:
    roster = [name.strip() for name in raw.split(",") if name.strip()]
    if len(roster) < 2:
        raise argparse.ArgumentTypeError("need at least two agents, e.g. --agents Ada,Grace")
    if len(set(roster)) != len(roster):
        raise argparse.ArgumentTypeError("agent names must be unique")
    return roster


def install_ctrl_c(stop: threading.Event) -> None:
    """First Ctrl+C: let the current message finish, record it, then close. Second Ctrl+C: quit now."""
    presses = 0

    def handler(signum, frame):
        nonlocal presses
        presses += 1
        if presses == 1:
            stop.set()
            print(
                "\nCtrl+C: finishing the current message, then closing. Press Ctrl+C again to quit now.",
                file=sys.stderr,
            )
        else:
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--personas", metavar="FILE.md", help="Markdown file defining the characters and their prompts.")
    parser.add_argument("--topic", help="Topic as plain text, or the path to a .md file. Overrides '## Topic'.")
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        metavar="PATH",
        help="Repo the agents may read (a copy is made). Repeatable. Adds to '## Repos'.",
    )
    parser.add_argument(
        "--agents",
        type=parse_roster,
        help="Comma-separated names with built-in personas (default Ada,Grace,Linus). Not used with --personas.",
    )
    parser.add_argument(
        "--turns",
        type=int,
        default=None,
        help="Stop after this many messages. Omit to run until Ctrl+C.",
    )
    parser.add_argument(
        "--wrap-at",
        type=int,
        default=None,
        metavar="N",
        help="After N messages, have the facilitator write a closing note and end the run. Omit for no budget.",
    )
    parser.add_argument(
        "--no-stall-check",
        action="store_true",
        help="Don't end the run when the conversation stops producing new points.",
    )
    parser.add_argument(
        "--rules",
        metavar="FILE.md",
        help="Standing rules every character follows (tone, style, editing habits). Sent once per character; not written to the transcript.",
    )
    parser.add_argument(
        "--draft",
        metavar="FILE.md",
        help="Shared document the group edits. Characters propose changes; the runner applies them and keeps "
        "transcripts/<folder>/experiment.md up to date.",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="Don't write report.md and summary.md when the conversation ends.",
    )
    parser.add_argument("--model", default="composer-2.5", help="Model id (default composer-2.5).")
    args = parser.parse_args()

    if args.personas and args.agents:
        parser.error("--agents can't be combined with --personas; the file defines the characters")
    if args.turns is not None and args.turns < 1:
        parser.error("--turns must be at least 1, or omit it to run until Ctrl+C")
    if args.wrap_at is not None and args.wrap_at < 2:
        parser.error("--wrap-at must be at least 2")

    try:
        conv = read_conversation_file(args.personas) if args.personas else None

        topic: str | None = None
        if args.topic:
            topic, _ = resolve_topic(args.topic)
        elif conv and conv.topic:
            topic = conv.topic
        if not topic:
            parser.error("no topic: pass --topic, or add a '## Topic' section to the conversation file")

        if conv:
            roster = list(conv.personas)
            personas = conv.personas
            roles = conv.roles
        else:
            roster = args.agents or DEFAULT_AGENTS
            personas = PERSONAS
            roles = {name: ROLES.get(name, "") for name in roster}
        check_short_names(roster)
        if find_facilitator(roster, roles) is None:
            # Every run has a moderator. It is added unless the cast already has a facilitator.
            if MODERATOR_NAME in roster:
                raise InputError(
                    f"a character named '{MODERATOR_NAME}' is reserved for the built-in moderator; "
                    "rename it, or give it the Facilitator role"
                )
            roster = roster + [MODERATOR_NAME]
            personas = {**personas, MODERATOR_NAME: MODERATOR_PERSONA}
            roles = {**roles, MODERATOR_NAME: "Facilitator"}
        facilitator = find_facilitator(roster, roles)
        participants = [name for name in roster if name != facilitator]
        if facilitator and len(participants) < 2:
            raise InputError("a facilitator needs at least two other characters to steer")

        standing_rules = read_rules_file(args.rules) if args.rules else None
        document = read_draft(args.draft) if args.draft else None
        repo_paths = (conv.repos if conv else []) + args.repo
        repos = resolve_repos(repo_paths)
    except InputError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    goal = extract_goal(topic)

    if not os.environ.get("CURSOR_API_KEY"):
        print("CURSOR_API_KEY is not set. Create a key at https://cursor.com/dashboard/integrations.", file=sys.stderr)
        return 1

    SANDBOX.mkdir(parents=True, exist_ok=True)
    repo_copies: dict[str, Path] = {}
    if repos:
        print("Copying repos (skipping node_modules, .git, build output)...")
        repo_copies = snapshot_repos(repos)

    transcript = Transcript(
        goal,
        [(name, roles.get(name, "")) for name in roster],
        folder=topic_folder(args.topic, args.personas, goal),
    )
    print(f"Cast: {', '.join(speaker_label(n, roles.get(n, '')) for n in roster)}")
    if repo_copies:
        print(f"Repos: {', '.join(repo_copies)}")
    print(f"Recording to {transcript.path}")

    local = LocalAgentOptions(cwd=str(SANDBOX))
    history: list[tuple[str, str]] = []  # every message so far, in order
    seen_upto = {name: 0 for name in roster}  # index into history of each agent's first unseen message
    introduced = {name: False for name in roster}
    document_version = 0
    doc_seen = {name: -1 for name in roster}  # the document version each character has been shown
    if document is not None:
        transcript.set_experiment(document)

    stop_requested = threading.Event()
    install_ctrl_c(stop_requested)
    if args.turns is None:
        print("Running until Ctrl+C.")

    turn = 0
    failed = False
    try:
        with ExitStack() as stack:
            agents = {
                name: stack.enter_context(Agent.create(model=args.model, local=local, name=f"{name} (group chat)"))
                for name in roster
            }

            # Without a facilitator, characters speak round-robin. With one, the facilitator
            # speaks, then the participant its 'Next:' line names, and so on. When the run needs
            # to end (stalled, or --wrap-at reached), the facilitator speaks next, writes the
            # closing summary, and the run closes whatever it says.
            next_target: str | None = None  # set by the facilitator, used for the next participant turn
            just_passed = False  # the facilitator passed this turn, so a participant speaks next
            round_robin = 0  # index into participants for round-robin fallback
            closed = False
            stop_reason: str | None = None  # why the run ended, for the transcript note
            wrap_reason: str | None = None  # set when the facilitator should wrap up now
            skips = 0  # consecutive turns that produced no usable reply
            monitor = None if args.no_stall_check else StagnationMonitor(len(roster))

            while args.turns is None or turn < args.turns:
                if stop_requested.is_set():
                    break
                turn += 1
                if wrap_reason and facilitator:
                    speaker = facilitator
                elif facilitator is None:
                    speaker = roster[(turn - 1) % len(roster)]
                elif not just_passed and (not history or history[-1][0] != facilitator):
                    speaker = facilitator
                else:
                    speaker = next_target or participants[round_robin % len(participants)]
                    next_target = None
                just_passed = False
                role = roles.get(speaker, "")
                prompt = build_prompt(
                    name=speaker,
                    role=role,
                    roster=roster,
                    persona=personas.get(speaker) or GENERIC_PERSONA,
                    topic=topic,
                    repo_copies=repo_copies,
                    unseen=history[seen_upto[speaker]:],
                    introduced=introduced[speaker],
                    facilitator=speaker == facilitator,
                    wrap_up=wrap_reason if speaker == facilitator else None,
                    standing_rules=standing_rules,
                    document=document,
                    show_doc=document is not None and doc_seen[speaker] != document_version,
                    doc_version=document_version,
                )
                introduced[speaker] = True
                doc_seen[speaker] = document_version

                run = agents[speaker].send(prompt)
                result = run.wait()
                if result.status != "finished":
                    # Errors go to the terminal, not the play.
                    print(
                        f"Turn {turn} failed: {speaker} run ended with status {result.status}: "
                        f"{result.error_message or 'no details'}",
                        file=sys.stderr,
                    )
                    failed = True
                    break

                text = result.result or ""
                if is_echo(text):
                    # The agent repeated the prompt instead of answering. Ask once more, then skip.
                    retry = agents[speaker].send(ECHO_RETRY_PROMPT.format(name=short_name(speaker)))
                    retry_result = retry.wait()
                    text = (retry_result.result or "") if retry_result.status == "finished" else ""
                edits, text = split_edits(text)
                if is_echo(text) or (not text.strip() and not edits):
                    skips += 1
                    print(f"[{turn}] {speaker_label(speaker, role)} gave no usable reply; skipped ({skips}).", file=sys.stderr)
                    if skips >= MAX_CONSECUTIVE_SKIPS:
                        print("Too many unusable replies in a row; stopping.", file=sys.stderr)
                        failed = True
                        break
                    continue
                skips = 0
                text = clean_speech(text, short_name(speaker), role)

                if speaker == facilitator:
                    target, text = parse_directive(text, participants)
                    if is_pass(text):
                        if wrap_reason:
                            # Closing: the facilitator must write the summary, not pass.
                            skips += 1
                            print(f"[{turn}] {speaker_label(speaker, role)} passed while closing; asking again ({skips}).", file=sys.stderr)
                            if skips >= MAX_CONSECUTIVE_SKIPS:
                                print("Too many unusable replies in a row; stopping.", file=sys.stderr)
                                failed = True
                                break
                            continue
                        # Nothing to step in on. A pass is not a message and doesn't use a turn.
                        print(f"[{turn}] {speaker_label(speaker, role)}: pass")
                        turn -= 1
                        just_passed = True
                        next_target = target
                        continue
                    if target == "close" or wrap_reason:
                        closed = True
                    else:
                        next_target = target
                else:
                    round_robin = participants.index(speaker) + 1
                history.append((speaker, text))
                seen_upto[speaker] = len(history)  # the speaker has seen its own message
                transcript.add(speaker, role, text)
                if edits:
                    if document is None:
                        print(f"{speaker_label(speaker, role)} proposed edits, but there is no --draft; ignored.", file=sys.stderr)
                    else:
                        for edit in edits:
                            new_doc, reason = apply_edit(document, edit)
                            if new_doc is None:
                                transcript.edit_note(speaker, f"not applied, {reason}")
                            else:
                                document = new_doc
                                document_version += 1
                                transcript.edit_note(speaker, edit.describe())
                        doc_seen[speaker] = document_version
                        transcript.set_experiment(document)
                total = f"/{args.turns}" if args.turns else ""
                print(f"[{turn}{total}] {speaker_label(speaker, role)}: {text[:80]}...")

                if closed:
                    stop_reason = (
                        f"facilitator wrapped up: the discussion {wrap_reason}"
                        if wrap_reason
                        else "facilitator closed the discussion"
                    )
                    break

                if wrap_reason is None:
                    if monitor and (reason := monitor.update(history)):
                        wrap_reason = reason
                    elif args.wrap_at and len(history) >= args.wrap_at:
                        wrap_reason = f"reached its {args.wrap_at}-message budget"
                    if wrap_reason:
                        if facilitator:
                            print(f"Wrapping up: the discussion {wrap_reason}.")
                        else:
                            stop_reason = f"discussion {wrap_reason}"
                            break

            if stop_reason:
                transcript.note(stop_reason)
                print(f"Stopped: {stop_reason}.")
            elif stop_requested.is_set():
                transcript.note(f"Ctrl+C after {len(history)} messages")
                print("Wrapped up and closed the agents.")
            # A run that reaches --turns adds nothing to the play.

            # One character writes the report, then the summary. The facilitator if there is one.
            # It hasn't seen the messages since it last spoke, so the request carries them.
            if history and not failed and not args.no_report:
                writer = facilitator or roster[0]
                ended = stop_reason or (
                    f"Ctrl+C after {len(history)} messages"
                    if stop_requested.is_set()
                    else f"it reached the {args.turns}-message limit"
                )
                print(f"Writing report.md ({short_name(writer)}). Press Ctrl+C twice to quit.")
                unseen = history[seen_upto[writer]:]
                report = request_document(
                    agents[writer], closing_prompt(REPORT_PROMPT, unseen, document), "report.md"
                )
                if report:
                    print(f"Report saved to {transcript.save_report(report, ended)}")
                    print("Writing summary.md.")
                    summary = request_document(agents[writer], SUMMARY_PROMPT, "summary.md")
                    if summary:
                        print(f"Summary saved to {transcript.save_summary(summary)}")
    except KeyboardInterrupt:
        transcript.note(f"second Ctrl+C after {len(history)} messages, before the next reply finished")
        print("\nQuit now.", file=sys.stderr)
        return 130
    except CursorAgentError as err:
        # Raised on startup, or while closing agents after a finished conversation. Terminal only.
        print(f"Agent error: {err.message} (retryable={err.is_retryable})", file=sys.stderr)
        if not history:
            return 1
        print("The conversation had already been recorded; only closing the agents failed.", file=sys.stderr)

    if transcript.started:
        print(f"Done. Transcript: {transcript.path}")
    else:
        print("Done. No messages were recorded.")
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
