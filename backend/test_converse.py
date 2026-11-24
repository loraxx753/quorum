"""Offline tests for converse.py. A fake Agent stands in for the SDK, so no API calls are made.

Run:  backend/.venv/bin/python -m unittest backend/test_converse.py -v
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import converse  # noqa: E402
from cursor_sdk import CursorAgentError  # noqa: E402

PERSONAS = Path(__file__).resolve().parent.parent / "source-files" / "personas" / "five-year-plan.md"


class FakeAgent:
    """Replies come from `script(name, call_index, prompt) -> str`, shared by all agents in a test."""

    script = None
    calls: dict[str, int] = {}
    fail_on_exit = False

    def __init__(self, name: str) -> None:
        self.name = name.split(" (group chat)")[0]

    @classmethod
    def create(cls, model, local, name):
        return cls(name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if FakeAgent.fail_on_exit:
            raise CursorAgentError("Bridge request failed: Connection refused")
        return False

    def send(self, prompt: str):
        n = FakeAgent.calls.get(self.name, 0)
        FakeAgent.calls[self.name] = n + 1
        text = FakeAgent.script(self.name, n, prompt)
        result = SimpleNamespace(status="finished", result=text, id="run-x", error_message=None)
        return SimpleNamespace(wait=lambda: result)


def varied(i: int) -> str:
    """A reply with plenty of words not used before (index-specific vocabulary)."""
    return " ".join(f"topic{i}word{j}" for j in range(14))


class ConverseCase(unittest.TestCase):
    """Shared setup: temp folders, fake agents, and a helper that runs main()."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        patches = [
            mock.patch.object(converse, "TRANSCRIPTS", self.tmp / "transcripts"),
            mock.patch.object(converse, "SANDBOX", self.tmp / "sandbox"),
            mock.patch.object(converse, "REPO_COPIES", self.tmp / "sandbox" / "repos"),
            mock.patch.object(converse, "Agent", FakeAgent),
            mock.patch.dict(os.environ, {"CURSOR_API_KEY": "test"}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        FakeAgent.calls = {}
        FakeAgent.fail_on_exit = False
        self.conv = self.tmp / "conv.md"
        self.conv.write_text(
            "## Ada - Skeptic\nAsk for evidence.\n\n## Grace - Builder\nPropose steps.\n\n"
            "## Linus - Ops\nCall out tradeoffs.\n\n## Mara - Facilitator\nRun the room.\n\n"
            "## Topic\n\n**Goal:** Decide something.\n",
            encoding="utf-8",
        )

    def run_main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["converse.py", *argv]), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = converse.main()
        return code, out.getvalue(), err.getvalue()

    def transcript(self) -> str:
        return (self.tmp / "transcripts" / "latest.md").read_text(encoding="utf-8")



class ConverseTests(ConverseCase):
    # ---- unit tests

    def test_monitor_ignores_a_healthy_conversation(self):
        monitor = converse.StagnationMonitor(4)
        history = []
        for i in range(60):
            history.append(("x", varied(i)))
            self.assertIsNone(monitor.update(history), f"false positive at message {i + 1}")

    def test_monitor_flags_repetition(self):
        monitor = converse.StagnationMonitor(4)
        history = [("x", varied(i)) for i in range(16)]
        reason = None
        for i in range(30):
            history.append(("x", "we keep the same plan and the same gate until a breach happens"))
            reason = monitor.update(history)
            if reason:
                break
        self.assertIsNotNone(reason)
        self.assertIn("new points", reason)

    def test_monitor_flags_one_word_replies(self):
        monitor = converse.StagnationMonitor(4)
        history = [("x", varied(i)) for i in range(16)] + [("x", "Dark.")] * 4
        self.assertIn("one-word", monitor.update(history))

    def test_monitor_on_real_run_stops_near_the_seal(self):
        latest = Path(__file__).resolve().parent.parent / "transcripts" / "20261119-003316-this-document-governs-a-multi-persona-group-chat-d.md"
        if not latest.exists():
            self.skipTest("recorded run not present")
        import re

        history = [
            (m.group(1), m.group(2))
            for line in latest.read_text(encoding="utf-8").splitlines()
            if (m := re.match(r"^([A-Z][\w\-\. ]*?)(?: \([^)]*\))?: (.*)", line)) and m.group(1) != "PROMPT"
        ]
        monitor = converse.StagnationMonitor(5)
        first = next((i for i in range(1, len(history) + 1) if monitor.update(history[:i])), None)
        # The council "sealed" around message 58-62 and idled after; the old run went to 347.
        self.assertIsNotNone(first)
        self.assertTrue(55 <= first <= 80, first)

    def test_rules_reach_every_character_once_and_not_the_transcript(self):
        rules = self.tmp / "rules.md"
        rules.write_text("RULE_MARKER: write in plain English, no em dashes.\n", encoding="utf-8")
        seen: dict[str, list[str]] = {}

        def script(name, n, prompt):
            seen.setdefault(name, []).append(prompt)
            nxt = ["Ada", "Grace", "Linus"][n % 3]
            return varied(n * 17 + len(name)) + (f"\nNext: {nxt}" if name == "Mara" else "")

        FakeAgent.script = staticmethod(script)
        code, out, err = self.run_main("--personas", str(self.conv), "--rules", str(rules), "--turns", "8", "--no-stall-check")
        self.assertEqual(code, 0, err)
        self.assertEqual(set(seen), {"Ada", "Grace", "Linus", "Mara"})
        for name, prompts in seen.items():
            self.assertEqual(sum("RULE_MARKER" in p for p in prompts), 1, f"{name} should get the rules once")
            self.assertIn("RULE_MARKER", prompts[0])
        self.assertNotIn("RULE_MARKER", self.transcript())

    def test_missing_rules_file_is_a_clean_error(self):
        code, out, err = self.run_main("--personas", str(self.conv), "--rules", str(self.tmp / "nope.md"))
        self.assertEqual(code, 1)
        self.assertIn("rules file not found", err)

    def test_split_and_apply_edits(self):
        text = "Here is my point.\n<<<EDIT\nREPLACE\nold line\nWITH\nnew line\nEND\n>>>\n"
        edits, dialogue = converse.split_edits(text)
        self.assertEqual(dialogue, "Here is my point.")
        self.assertEqual(len(edits), 1)
        doc, reason = converse.apply_edit("a\nold line\nb\n", edits[0])
        self.assertEqual(doc, "a\nnew line\nb\n")

        edits, _ = converse.split_edits("<<<EDIT\nINSERT AFTER\nanchor\nWITH\nadded\nEND\n>>>")
        doc, _ = converse.apply_edit("anchor\nrest", edits[0])
        self.assertEqual(doc, "anchor\n\nadded\nrest")

        edits, _ = converse.split_edits("<<<EDIT\nREMOVE\ndrop me\nEND\n>>>")
        doc, _ = converse.apply_edit("keep\ndrop me\n", edits[0])
        self.assertEqual(doc, "keep\n\n")

    def test_edit_target_must_appear_exactly_once(self):
        edits, _ = converse.split_edits("<<<EDIT\nREPLACE\nx\nWITH\ny\nEND\n>>>")
        doc, reason = converse.apply_edit("x and x", edits[0])
        self.assertIsNone(doc)
        self.assertIn("found 2", reason)
        doc, reason = converse.apply_edit("nothing here", edits[0])
        self.assertIsNone(doc)

    def test_malformed_edit_block_is_reported_not_applied(self):
        edits, dialogue = converse.split_edits("Hi.\n<<<EDIT\nFIDDLE\nthing\nEND\n>>>")
        self.assertEqual(edits[0].op, "invalid")
        self.assertIn("unknown edit keyword", edits[0].text)
        self.assertEqual(dialogue, "Hi.")

    def test_clean_speech_drops_self_labels(self):
        self.assertEqual(converse.clean_speech("**Built-In (Facilitator)** — Phase 1 done.", "Built-In", "Facilitator"), "Phase 1 done.")
        self.assertEqual(converse.clean_speech("Priya (Open-Core DevRel): hi there", "Priya", "Open-Core DevRel"), "hi there")
        self.assertEqual(converse.clean_speech("Marcus is right.", "Marcus", ""), "Marcus is right.")

    def test_draft_edits_are_applied_shown_to_others_and_written_to_experiment(self):
        draft = self.tmp / "draft.md"
        draft.write_text("Alpha paragraph.\n\nBeta paragraph.\n", encoding="utf-8")
        seen: dict[str, list[str]] = {}

        def script(name, n, prompt):
            seen.setdefault(name, []).append(prompt)
            if name == "Mara":
                nxt = "Ada" if n == 0 else "Grace"
                return f"Let's tighten it.\nNext: {nxt}"
            if name == "Ada" and n == 0:
                return "Beta is vague.\n<<<EDIT\nREPLACE\nBeta paragraph.\nWITH\nBeta paragraph, now clearer.\nEND\n>>>"
            if name == "Grace":
                return "Looks good."
            return varied(n)

        FakeAgent.script = staticmethod(script)
        code, out, err = self.run_main("--personas", str(self.conv), "--draft", str(draft), "--turns", "4", "--no-stall-check")
        self.assertEqual(code, 0, err)

        output = (self.tmp / "transcripts" / "conv" / "experiment.md").read_text(encoding="utf-8")
        self.assertIn("Beta paragraph, now clearer.", output)
        self.assertNotIn("<<<EDIT", output)
        self.assertFalse((self.tmp / "transcripts" / "conv" / "output.md").exists())

        transcript = (self.tmp / "transcripts" / "conv" / "transcript.md").read_text(encoding="utf-8")
        self.assertIn("**Ada:** Beta is vague.", transcript)
        self.assertNotIn("<<<EDIT", transcript)
        self.assertIn('*[Ada proposed an edit: replaced "Beta paragraph."]*', transcript)
        self.assertEqual(self.transcript(), transcript)  # latest.md is the viewer's copy

        grace_first = seen["Grace"][0]
        self.assertIn("Current document, version 1", grace_first)
        self.assertIn("Beta paragraph, now clearer.", grace_first)
        self.assertIn("shared document", grace_first)
        # Ada saw her own edit already, so she isn't sent the document again on her second turn.
        self.assertTrue(all("Current document, version" not in p for p in seen["Ada"][1:]))

    def test_rejected_edit_is_noted_and_document_unchanged(self):
        draft = self.tmp / "draft.md"
        draft.write_text("Only line.\n", encoding="utf-8")

        def script(name, n, prompt):
            if name == "Mara":
                return "Go.\nNext: Ada" if n == 0 else "Next: Ada"
            return "Try.\n<<<EDIT\nREPLACE\nNot in the doc\nWITH\nX\nEND\n>>>" if n == 0 else varied(n)

        FakeAgent.script = staticmethod(script)
        code, out, err = self.run_main("--personas", str(self.conv), "--draft", str(draft), "--turns", "2", "--no-stall-check")
        self.assertEqual(code, 0, err)
        transcript = (self.tmp / "transcripts" / "conv" / "transcript.md").read_text(encoding="utf-8")
        self.assertIn("not applied, its target text must appear exactly once", transcript)
        output = (self.tmp / "transcripts" / "conv" / "experiment.md").read_text(encoding="utf-8")
        self.assertEqual(output, "Only line.\n")

    def test_no_draft_means_no_experiment_file(self):
        FakeAgent.script = staticmethod(lambda name, n, prompt: varied(n * 3 + len(name)) + ("\nNext: Ada" if name == "Mara" else ""))
        code, out, err = self.run_main("--personas", str(self.conv), "--turns", "3", "--no-stall-check")
        self.assertEqual(code, 0, err)
        self.assertFalse((self.tmp / "transcripts" / "conv" / "experiment.md").exists())

    def test_echo_detection(self):
        self.assertTrue(converse.is_echo("New messages since you last spoke: Marcus: hi. Reply as Elena."))
        self.assertTrue(converse.is_echo("Something. Reply as Elena Rostova."))
        self.assertFalse(converse.is_echo("I can reply as quickly as the form allows."))

    def test_persona_file_has_a_facilitator(self):
        conv = converse.read_conversation_file(str(PERSONAS))
        self.assertEqual(converse.find_facilitator(list(conv.personas), conv.roles), "Built-In")
        self.assertEqual(conv.roles["Priya Nair"], "Open-Core DevRel")

    # ---- end-to-end with fake agents

    def test_facilitator_wraps_up_when_the_conversation_stalls(self):
        def script(name, n, prompt):
            if "Close it now" in prompt:
                return "Decided: ship the storefront. Open: kit prices. Action: merge the tag.\nNext: Close"
            if n >= 4:
                stale = "Same plan, same gate, nothing new to add here today."
                return stale + ("\nNext: " + ["Ada", "Grace", "Linus"][n % 3] if name == "Mara" else "")
            if name == "Mara":
                return varied(100 + n) + "\nNext: " + ["Ada", "Grace", "Linus"][n % 3]
            return varied(n * 7 + len(name))

        FakeAgent.script = staticmethod(script)
        code, out, err = self.run_main("--personas", str(self.conv))
        self.assertEqual(code, 0, err)
        text = self.transcript()
        self.assertIn("Decided: ship the storefront", text)
        self.assertIn("[Stopped: facilitator wrapped up", text)
        self.assertNotIn("Next: Close", text)
        self.assertLess(text.count("\n"), 80, "should stop soon after stalling")
        self.assertFalse((self.tmp / "outputs").exists())  # the closing note stays in the transcript

    def test_facilitator_natural_close(self):
        def script(name, n, prompt):
            if name == "Mara":
                return "All agreed. Action: ship it.\nNext: Close" if n >= 2 else varied(50 + n) + "\nNext: Ada"
            return varied(n * 11 + len(name))

        FakeAgent.script = staticmethod(script)
        code, out, err = self.run_main("--personas", str(self.conv))
        self.assertEqual(code, 0, err)
        self.assertIn("[Stopped: facilitator closed the discussion]", self.transcript())

    def test_wrap_at_budget(self):
        def script(name, n, prompt):
            if "Close it now" in prompt:
                return "Wrap summary.\nNext: Close"
            return varied(n * 13 + len(name) + 1000 * (name == "Mara")) + ("\nNext: Ada" if name == "Mara" else "")

        FakeAgent.script = staticmethod(script)
        code, out, err = self.run_main("--personas", str(self.conv), "--wrap-at", "6", "--no-stall-check")
        self.assertEqual(code, 0, err)
        text = self.transcript()
        self.assertIn("reached its 6-message budget", text)
        self.assertIn("Wrap summary.", text)

    def test_default_moderator_wraps_up_when_the_conversation_stalls(self):
        conv = self.tmp / "plain.md"
        conv.write_text("## Ada - Skeptic\nx\n\n## Grace - Builder\ny\n\n## Topic\nWhat now?\n", encoding="utf-8")

        def script(name, n, prompt):
            if "Close it now" in prompt:
                return "Decided: nothing yet.\nNext: Close"
            if "Write report.md" in prompt:
                return "# Report\n\n## Question\nWhat now?"
            if "Now write summary.md" in prompt:
                return "# Summary\n\n## Log Line\nNothing yet."
            if name == "Moderator":
                return "PASS"
            return varied(n * 5 + len(name)) if n < 3 else "Dark."

        FakeAgent.script = staticmethod(script)
        code, out, err = self.run_main("--personas", str(conv))
        self.assertEqual(code, 0, err)
        self.assertIn("[Stopped: facilitator wrapped up", self.transcript())
        self.assertIn("Decided: nothing yet.", self.transcript())
        self.assertTrue((self.tmp / "transcripts" / "plain" / "report.md").exists())

    def test_moderator_passes_are_not_posted_and_participants_take_turns(self):
        def script(name, n, prompt):
            if name == "Moderator":
                return "PASS"
            return varied(n * 7 + len(name))

        FakeAgent.script = staticmethod(script)
        code, out, err = self.run_main("--agents", "Ada,Grace,Linus", "--topic", "What now?", "--turns", "4", "--no-stall-check")
        self.assertEqual(code, 0, err)
        text = self.transcript()
        self.assertNotIn("Moderator:", text)
        self.assertNotIn("PASS", text)
        speakers = [line.split(":")[0].strip("*") for line in text.splitlines() if line.startswith("**")]
        self.assertEqual(speakers, ["Ada", "Grace", "Linus", "Ada"])
        self.assertIn("Moderator (Facilitator): pass", out)

    def test_pass_with_a_next_line_names_who_speaks(self):
        def script(name, n, prompt):
            if name == "Moderator":
                return "PASS\nNext: Linus"
            return varied(n * 7 + len(name))

        FakeAgent.script = staticmethod(script)
        code, out, err = self.run_main("--agents", "Ada,Grace,Linus", "--topic", "What now?", "--turns", "1", "--no-stall-check")
        self.assertEqual(code, 0, err)
        self.assertIn("**Linus:**", self.transcript())
        self.assertNotIn("**Ada:**", self.transcript())

    def test_pass_is_not_allowed_while_closing(self):
        calls = {"moderator": 0}

        def script(name, n, prompt):
            if "Close it now" in prompt:
                calls["moderator"] += 1
                return "PASS" if calls["moderator"] == 1 else "Decided: ship it.\nNext: Close"
            if name == "Moderator":
                return "PASS"
            return varied(n * 7 + len(name))

        FakeAgent.script = staticmethod(script)
        code, out, err = self.run_main("--agents", "Ada,Grace,Linus", "--topic", "What now?", "--wrap-at", "4", "--no-stall-check")
        self.assertEqual(code, 0, err)
        self.assertEqual(calls["moderator"], 2)  # the first PASS while closing was refused
        self.assertIn("passed while closing", err)
        self.assertIn("Decided: ship it.", self.transcript())

    def test_every_run_gets_a_moderator_unless_there_is_a_facilitator(self):
        FakeAgent.script = staticmethod(lambda name, n, prompt: varied(n * 3 + len(name)))
        code, out, err = self.run_main("--agents", "Ada,Grace", "--topic", "What now?", "--turns", "2", "--no-stall-check")
        self.assertEqual(code, 0, err)
        self.assertIn("Moderator (Facilitator)", out)

        FakeAgent.script = staticmethod(lambda name, n, prompt: varied(n * 3 + len(name)) + ("\nNext: Ada" if name == "Mara" else ""))
        code, out, err = self.run_main("--personas", str(self.conv), "--turns", "2", "--no-stall-check")
        self.assertEqual(code, 0, err)
        self.assertNotIn("Moderator", out)  # the conversation file's facilitator is used instead

    def test_moderator_name_is_reserved(self):
        conv = self.tmp / "clash.md"
        conv.write_text("## Moderator\nx\n\n## Ada\ny\n\n## Topic\nWhat now?\n", encoding="utf-8")
        code, out, err = self.run_main("--personas", str(conv))
        self.assertEqual(code, 1)
        self.assertIn("reserved for the built-in moderator", err)

    def test_is_pass(self):
        self.assertTrue(converse.is_pass("PASS"))
        self.assertTrue(converse.is_pass("**PASS**"))
        self.assertTrue(converse.is_pass(" pass. "))
        self.assertFalse(converse.is_pass("Pass the salt, please."))
        self.assertFalse(converse.is_pass("PASSION"))

    def test_echo_is_retried_then_recorded_cleanly(self):
        def script(name, n, prompt):
            if name == "Ada" and n == 0 and "repeated the prompt" not in prompt:
                return "New messages since you last spoke: ... Reply as Ada."
            return varied(n * 3 + len(name))

        FakeAgent.script = staticmethod(script)
        code, out, err = self.run_main("--personas", str(self.conv), "--turns", "3", "--no-stall-check")
        self.assertEqual(code, 0, err)
        self.assertNotIn("New messages since", self.transcript())

    def test_persistent_echo_aborts_without_polluting_transcript(self):
        def script(name, n, prompt):
            if name == "Mara":
                return varied(n) + "\nNext: Ada"
            return "New messages since you last spoke: ... Reply as Ada."

        FakeAgent.script = staticmethod(script)
        code, out, err = self.run_main("--personas", str(self.conv), "--no-stall-check")
        self.assertEqual(code, 2)
        self.assertIn("Too many unusable replies", err)
        self.assertNotIn("New messages since", self.transcript())
        self.assertNotIn("could not start", self.transcript())

    def test_startup_failure_leaves_no_transcript_and_keeps_latest(self):
        (self.tmp / "transcripts").mkdir()
        (self.tmp / "transcripts" / "latest.md").write_text("previous run\n")

        def boom(model, local, name):
            raise CursorAgentError("Bridge request failed: Connection refused")

        with mock.patch.object(FakeAgent, "create", staticmethod(boom)):
            code, out, err = self.run_main("--personas", str(self.conv))
        self.assertEqual(code, 1)
        self.assertIn("Connection refused", err)
        self.assertEqual(self.transcript(), "previous run\n")
        self.assertEqual([p.name for p in (self.tmp / "transcripts").iterdir()], ["latest.md"])

    def test_error_while_closing_agents_stays_out_of_transcript(self):
        FakeAgent.script = staticmethod(lambda name, n, prompt: varied(n * 3 + len(name)) + ("\nNext: Ada" if name == "Mara" else ""))
        FakeAgent.fail_on_exit = True
        code, out, err = self.run_main("--personas", str(self.conv), "--turns", "3", "--no-stall-check")
        self.assertEqual(code, 0)
        self.assertIn("only closing the agents failed", err)
        self.assertNotIn("Connection refused", self.transcript())
        self.assertNotIn("could not start", self.transcript())


class ClosingDocumentTests(ConverseCase):
    """report.md and summary.md, written by one character when the conversation ends."""

    REPORT = "# Hobby Report\n\n## Question\nCan it work?\n\n## Feasibility\nPossibly. [Open]"
    SUMMARY = "# Summary\n\n## Log Line\nA group tried an idea.\n\n## Treatment\nThey talked."

    def folder(self) -> Path:
        return self.tmp / "transcripts" / "conv"

    def closing_script(self, prompts: list[tuple[str, str]], report: str | None = None, summary: str | None = None):
        def script(name, n, prompt):
            if "Write report.md" in prompt:
                prompts.append((name, prompt))
                return self.REPORT + "\n\nNext: Close" if report is None else report
            if "Now write summary.md" in prompt:
                prompts.append((name, prompt))
                return f"```markdown\n{self.SUMMARY}\n```" if summary is None else summary
            if name == "Mara":
                return "All agreed. Action: ship it.\nNext: Close" if n >= 2 else varied(50 + n) + "\nNext: Ada"
            return varied(n * 11 + len(name))

        return staticmethod(script)

    def test_facilitator_writes_report_then_summary(self):
        prompts: list[tuple[str, str]] = []
        FakeAgent.script = self.closing_script(prompts)
        code, out, err = self.run_main("--personas", str(self.conv))
        self.assertEqual(code, 0, err)

        self.assertEqual([name for name, _ in prompts], ["Mara", "Mara"])
        self.assertIn("Write report.md", prompts[0][1])
        self.assertIn("Now write summary.md", prompts[1][1])

        report = (self.folder() / "report.md").read_text(encoding="utf-8")
        self.assertIn("# Hobby Report", report)
        self.assertIn("[Open]", report)
        self.assertNotIn("Next:", report)
        self.assertIn("Written from transcript.md. The conversation ended: facilitator closed the discussion.", report)

        summary = (self.folder() / "summary.md").read_text(encoding="utf-8")
        self.assertEqual(summary, self.SUMMARY + "\n")  # the wrapping code fence is removed

        self.assertNotIn("Hobby Report", self.transcript())
        self.assertIn("Report saved to", out)
        self.assertIn("Summary saved to", out)

    def test_run_survives_the_topic_folder_being_renamed_mid_run(self):
        """Renaming transcripts/<topic>/ while a run is going must not crash it or lose messages."""
        moved = self.tmp / "transcripts" / "conv-renamed"
        seen = {"moved": False}
        prompts: list[tuple[str, str]] = []
        base = self.closing_script(prompts).__func__

        def script(name, n, prompt):
            if not seen["moved"] and n >= 2 and self.folder().exists():
                self.folder().rename(moved)
                seen["moved"] = True
            return base(name, n, prompt)

        FakeAgent.script = staticmethod(script)
        code, out, err = self.run_main("--personas", str(self.conv))
        self.assertEqual(code, 0, err)
        self.assertTrue(seen["moved"])
        text = (self.folder() / "transcript.md").read_text(encoding="utf-8")
        self.assertIn("Decide something.", text)  # the goal header is rebuilt
        self.assertEqual(text, self.transcript())  # nothing written before the move was lost
        self.assertTrue((self.folder() / "report.md").exists())
        self.assertTrue((self.folder() / "summary.md").exists())

    def test_writer_without_a_facilitator_gets_what_it_has_not_seen(self):
        plain = self.tmp / "plain.md"
        plain.write_text("## Ada - Skeptic\nx\n\n## Grace - Builder\ny\n\n## Topic\nWhat now?\n", encoding="utf-8")
        draft = self.tmp / "draft.md"
        draft.write_text("Alpha paragraph.\n", encoding="utf-8")
        prompts: list[tuple[str, str]] = []

        def script(name, n, prompt):
            if "Write report.md" in prompt:
                prompts.append((name, prompt))
                return self.REPORT
            if "Now write summary.md" in prompt:
                return self.SUMMARY
            return f"{name.upper()}_MARKER " + varied(n * 3 + len(name))

        FakeAgent.script = staticmethod(script)
        code, out, err = self.run_main("--personas", str(plain), "--draft", str(draft), "--turns", "2", "--no-stall-check")
        self.assertEqual(code, 0, err)

        (writer, prompt), = prompts
        self.assertEqual(writer, "Moderator")  # every run has one, and it writes the report
        self.assertIn("ADA_MARKER", prompt)  # Ada spoke after the moderator did
        self.assertNotIn("MODERATOR_MARKER", prompt)  # the moderator already knows its own message
        self.assertIn("Alpha paragraph.", prompt)
        self.assertIn("ended: it reached the 2-message limit", (self.tmp / "transcripts" / "plain" / "report.md").read_text())

    def test_no_report_flag_skips_both_documents(self):
        prompts: list[tuple[str, str]] = []
        FakeAgent.script = self.closing_script(prompts)
        code, out, err = self.run_main("--personas", str(self.conv), "--no-report")
        self.assertEqual(code, 0, err)
        self.assertEqual(prompts, [])
        self.assertFalse((self.folder() / "report.md").exists())
        self.assertFalse((self.folder() / "summary.md").exists())

    def test_a_failed_run_writes_no_documents(self):
        def script(name, n, prompt):
            if name == "Mara":
                return varied(n) + "\nNext: Ada"
            return "New messages since you last spoke: ... Reply as Ada."

        FakeAgent.script = staticmethod(script)
        code, out, err = self.run_main("--personas", str(self.conv), "--no-stall-check")
        self.assertEqual(code, 2)
        self.assertFalse((self.folder() / "report.md").exists())
        self.assertFalse((self.folder() / "summary.md").exists())

    def test_an_unusable_report_is_skipped_and_no_summary_is_requested(self):
        prompts: list[tuple[str, str]] = []
        FakeAgent.script = self.closing_script(prompts, report="New messages since you last spoke: ... Reply as Mara.")
        code, out, err = self.run_main("--personas", str(self.conv))
        self.assertEqual(code, 0, err)
        self.assertIn("Could not write report.md", err)
        self.assertEqual(len(prompts), 1)  # only the report request; no summary request
        self.assertFalse((self.folder() / "report.md").exists())
        self.assertFalse((self.folder() / "summary.md").exists())
        self.assertIn("All agreed", self.transcript())  # the conversation itself is untouched

    def test_old_documents_are_archived_not_overwritten(self):
        FakeAgent.script = self.closing_script([])
        self.assertEqual(self.run_main("--personas", str(self.conv))[0], 0)
        first = (self.folder() / "report.md").read_text(encoding="utf-8")
        FakeAgent.calls = {}
        self.assertEqual(self.run_main("--personas", str(self.conv))[0], 0)

        for stem in ("report", "summary", "transcript"):
            archived = list(self.folder().glob(f"{stem}-*.md"))
            self.assertEqual(len(archived), 1, f"{stem} should have one archived copy")
        self.assertEqual(list(self.folder().glob("report-*.md"))[0].read_text(encoding="utf-8"), first)

    def test_clean_document(self):
        self.assertEqual(converse.clean_document("```markdown\n# T\n\nBody.\n```"), "# T\n\nBody.")
        self.assertEqual(converse.clean_document("# T\n\nBody.\n\nNext: Close"), "# T\n\nBody.")
        self.assertEqual(converse.clean_document("# T\n\n- Next: do the thing\n\nMore."), "# T\n\n- Next: do the thing\n\nMore.")
        self.assertEqual(converse.clean_document("  \n"), "")


class CastFormatTests(unittest.TestCase):
    CAST_FILE = """# Personas

Setting paragraph for the room.

## How They Talk and Work Together
- Plain English.
- No em dashes.

---

## Cast

### Maya (Behavior Analysis)
Maya's prompt.

### Theo (Traditional Astrology)
Theo's prompt.

```
### not a character
```

### Julian
Julian's prompt.

## Topic
The topic text.

## Repos
- ~/projects/example
"""

    def parse(self, text: str) -> converse.ConversationFile:
        return converse.parse_conversation_md(text)

    def test_cast_headers_are_the_characters(self) -> None:
        conv = self.parse(self.CAST_FILE)
        self.assertEqual(list(conv.personas), ["Maya", "Theo", "Julian"])
        self.assertEqual(conv.roles["Maya"], "Behavior Analysis")
        self.assertEqual(conv.roles["Theo"], "Traditional Astrology")
        self.assertEqual(conv.roles["Julian"], "")

    def test_shared_guidance_goes_first_in_every_prompt(self) -> None:
        conv = self.parse(self.CAST_FILE)
        for name in conv.personas:
            prompt = conv.personas[name]
            self.assertIn("Setting paragraph for the room.", prompt)
            self.assertIn("## How They Talk and Work Together", prompt)
            self.assertLess(prompt.index("Setting paragraph"), prompt.index(f"{name}'s prompt"))
        self.assertTrue(conv.personas["Maya"].endswith("Maya's prompt."))
        self.assertNotIn("Theo's prompt", conv.personas["Maya"])

    def test_fenced_hash_lines_are_not_characters(self) -> None:
        conv = self.parse(self.CAST_FILE)
        self.assertNotIn("not a character", conv.personas)
        self.assertIn("### not a character", conv.personas["Theo"])

    def test_topic_and_repos_still_reserved(self) -> None:
        conv = self.parse(self.CAST_FILE)
        self.assertEqual(conv.topic, "The topic text.")
        self.assertEqual(conv.repos, ["~/projects/example"])
        self.assertNotIn("Topic", conv.personas)
        self.assertNotIn("Repos", conv.personas)

    def test_duplicate_cast_character_is_an_error(self) -> None:
        text = "## Cast\n### Maya\nA\n### Maya\nB\n"
        with self.assertRaises(converse.InputError):
            self.parse(text)

    def test_old_layout_is_unchanged(self) -> None:
        text = "# T\n\n## Ada - Skeptic\nAda's prompt.\n\n## Grace\nGrace's prompt.\n"
        conv = self.parse(text)
        self.assertEqual(list(conv.personas), ["Ada", "Grace"])
        self.assertEqual(conv.personas["Ada"], "Ada's prompt.")
        self.assertEqual(conv.roles["Ada"], "Skeptic")
        self.assertEqual(conv.roles["Grace"], "")


if __name__ == "__main__":
    unittest.main()
