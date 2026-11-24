export interface Turn {
  turn: number;
  speaker: string;
  /** Stage direction in parentheses, e.g. "skeptical". */
  parenthetical?: string;
  text: string;
  runId?: string;
}

export interface Transcript {
  title: string;
  meta: Record<string, string>;
  /** Raw topic Markdown, if the transcript recorded one. */
  topic?: string;
  turns: Turn[];
  stoppedReason?: string;
}

const LEGACY_TURN_HEADER = /^### Turn (\d+) · (.+)$/;
const RUN_COMMENT = /^<!-- run: (.+) -->$/;
const META_LINE = /^- \*\*(.+?):\*\* (.+)$/;
const STOPPED_LINE = /^> \*\*Stopped:\*\* (.+)$/;
const DIRECTION_LINE = /^\((.+)\)$/;

/** Screenplay cues are indented; dialogue is indented less than a cue. */
const CUE_MIN_INDENT = 15;
const DIALOGUE_MIN_INDENT = 5;

const indentOf = (line: string) => line.length - line.trimStart().length;

/**
 * Parse the Markdown written by backend/converse.py.
 *
 * Current format is screenplay: an indented ALL-CAPS speaker name (matching `Agents` in the
 * metadata), an optional indented (direction), then indented dialogue. Older transcripts
 * with `### Turn N · Name` headers are still supported.
 */
function parseLegacy(markdown: string): Transcript {
  const lines = markdown.split("\n");
  const result: Transcript = { title: "", meta: {}, turns: [] };
  const roster = new Map<string, string>(); // UPPERCASE -> display name

  let topicLines: string[] | null = null;
  let current: Turn | null = null; // turn currently being read
  let lastTurn: Turn | null = null; // turn that a run comment belongs to
  let legacy = false;
  let state: "none" | "cue" | "dialogue" = "none";

  for (const line of lines) {
    // The topic can be multi-line Markdown, so capture it verbatim between markers.
    if (line === "<!-- topic:start -->") {
      topicLines = [];
      continue;
    }
    if (line === "<!-- topic:end -->") {
      result.topic = (topicLines ?? []).join("\n").trim() || undefined;
      topicLines = null;
      continue;
    }
    if (topicLines) {
      topicLines.push(line);
      continue;
    }

    const legacyHeader = line.match(LEGACY_TURN_HEADER);
    if (legacyHeader) {
      current = { turn: Number(legacyHeader[1]), speaker: legacyHeader[2].trim(), text: "" };
      result.turns.push(current);
      lastTurn = current;
      legacy = true;
      state = "none";
      continue;
    }

    const run = line.match(RUN_COMMENT);
    if (run) {
      if (current) current.runId = run[1];
      else if (lastTurn) lastTurn.runId = run[1];
      current = null;
      legacy = false;
      state = "none";
      continue;
    }

    const stopped = line.match(STOPPED_LINE);
    if (stopped) {
      result.stoppedReason = stopped[1];
      current = null;
      state = "none";
      continue;
    }

    if (legacy && current) {
      current.text += line + "\n";
      continue;
    }

    const trimmed = line.trim();
    if (!trimmed) {
      if (state === "dialogue") state = "none";
      continue;
    }

    // Header metadata and title, before any turns.
    if (result.turns.length === 0 && state === "none") {
      if (!result.title && line.startsWith("# ")) {
        result.title = line.slice(2).trim();
        continue;
      }
      const meta = line.match(META_LINE);
      if (meta) {
        result.meta[meta[1]] = meta[2];
        if (meta[1] === "Agents") {
          for (const name of meta[2].split(",").map((n) => n.trim()).filter(Boolean)) {
            roster.set(name.toUpperCase(), name);
          }
        }
        continue;
      }
    }

    // Screenplay speaker cue: indented, and a name from the roster.
    const cueName = indentOf(line) >= CUE_MIN_INDENT ? roster.get(trimmed.toUpperCase()) : undefined;
    if (cueName) {
      current = { turn: result.turns.length + 1, speaker: cueName, text: "" };
      result.turns.push(current);
      lastTurn = current;
      state = "cue";
      continue;
    }

    if (current && state === "cue") {
      const direction = trimmed.match(DIRECTION_LINE);
      if (direction) {
        current.parenthetical = direction[1].trim();
        state = "dialogue";
        continue;
      }
    }

    if (current && (state === "cue" || state === "dialogue") && indentOf(line) >= DIALOGUE_MIN_INDENT) {
      current.text = current.text ? `${current.text} ${trimmed}` : trimmed;
      state = "dialogue";
    }
  }

  for (const turn of result.turns) turn.text = turn.text.trim();
  return result;
}

/** Same rule as backend/converse.py short_name: first word after honorifics. */
function shortName(name: string): string {
  const stripped = name.trim().replace(/^(?:Dr|Mr|Ms|Mrs|Prof)\.?\s+/i, "");
  return stripped.split(/\s+/)[0] || name;
}

interface CastMember {
  name: string;
  role: string;
  /** Text that starts a speech, e.g. "Samantha (Clinical Psychologist)". */
  label: string;
}

/**
 * Parse the play-script format written by backend/converse.py:
 *
 *   Samantha Stone - Clinical Psychologist
 *   Sam Steele - Anarchist Revolutionary
 *
 *   PROMPT: Who had what for breakfast?
 *
 *   Samantha (Clinical Psychologist): Breakfast? I wouldn't have had breakfast.
 */
function parsePlay(markdown: string): Transcript {
  const result: Transcript = { title: "", meta: {}, turns: [] };
  const cast: CastMember[] = [];
  let sawPrompt = false;

  for (const raw of markdown.split("\n")) {
    const line = raw.trim();
    if (!line) continue;

    if (!sawPrompt) {
      const prompt = line.match(/^PROMPT:\s*(.*)$/);
      if (prompt) {
        result.title = prompt[1];
        sawPrompt = true;
        continue;
      }
      const entry = line.match(/^(.+?)(?:\s+-\s+(.+))?$/);
      if (entry) {
        const name = entry[1].trim();
        const role = (entry[2] ?? "").trim();
        cast.push({ name, role, label: role ? `${shortName(name)} (${role})` : shortName(name) });
      }
      continue;
    }

    const stopped = line.match(/^\[Stopped: (.+)\]$/);
    if (stopped) {
      result.stoppedReason = stopped[1];
      continue;
    }

    const member = cast.find((c) => line.startsWith(`${c.label}: `));
    if (member) {
      result.turns.push({
        turn: result.turns.length + 1,
        speaker: shortName(member.name),
        parenthetical: member.role || undefined,
        text: line.slice(member.label.length + 2).trim(),
      });
    }
  }

  result.meta.Agents = cast.map((c) => c.name).join(", ");
  return result;
}

/**
 * Parse transcript.md written by backend/converse.py: a `# Title`, an optional `*Goal: ...*` line,
 * then one `**Name:** text` speech per paragraph group. A speech can run over several paragraphs
 * (blank lines inside it). Italic `*[...]*` lines are notes: a stop reason, or an edit note.
 */
function parseDocument(markdown: string): Transcript {
  const result: Transcript = { title: "", meta: {}, turns: [] };
  const speech = /^\*\*(.+?):\*\*\s*(.*)$/;
  const note = /^\*\[(.+)\]\*$/;
  let current: Turn | null = null;
  let pendingBreak = false;

  for (const raw of markdown.split("\n")) {
    const line = raw.trimEnd();

    if (!result.title && result.turns.length === 0 && line.startsWith("# ")) {
      result.title = line.slice(2).trim();
      continue;
    }
    const goal = line.match(/^\*Goal: (.+)\*$/);
    if (goal && result.turns.length === 0) {
      result.meta.Goal = goal[1];
      continue;
    }

    const said = line.match(speech);
    if (said) {
      current = { turn: result.turns.length + 1, speaker: said[1].trim(), text: said[2].trim() };
      result.turns.push(current);
      pendingBreak = false;
      continue;
    }

    const noted = line.match(note);
    if (noted) {
      const stopped = noted[1].match(/^Stopped: (.+)$/);
      if (stopped) result.stoppedReason = stopped[1];
      current = null;
      continue;
    }

    if (!line.trim()) {
      if (current) pendingBreak = true;
      continue;
    }
    if (current) {
      const piece = line.trim();
      current.text = current.text ? `${current.text}${pendingBreak ? "\n\n" : "\n"}${piece}` : piece;
      pendingBreak = false;
    }
  }

  result.meta.Agents = [...new Set(result.turns.map((t) => t.speaker))].join(", ");
  return result;
}

/**
 * Read any transcript the runner has written: transcript.md (`**Name:**` speeches), the play script
 * (PROMPT line), or the older turn format.
 */
export function parseTranscript(markdown: string): Transcript {
  if (/^PROMPT: /m.test(markdown)) return parsePlay(markdown);
  if (/^\*\*[^*\n]+:\*\* /m.test(markdown)) return parseDocument(markdown);
  return parseLegacy(markdown);
}
