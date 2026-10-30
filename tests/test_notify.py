import json
import os

import pytest

from claude_companion.notify import address, compose, detail_for, owning_session, speak


@pytest.fixture(autouse=True)
def _a_name(monkeypatch):
    """Lines address the `name` setting; these tests use a neutral one."""
    monkeypatch.setenv("CLAUDE_COMPANION_NAME", "Sam")


# --- resolving which session fired the hook --------------------------------

def _sessions():
    return [
        {"pid": 100, "project": "home-lab", "proc_start": "5", "cwd": "/h/ha"},
        {"pid": 200, "project": "web-shop", "proc_start": "9", "cwd": "/h/rs"},
    ]


def test_walks_ppid_chain_to_the_owning_session():
    """The hook is a grandchild of claude, not a child."""
    chain = {999: 500, 500: 100, 100: 1}
    s = owning_session(999, _sessions(), ppid_of=chain.get)
    assert s["project"] == "home-lab"


def test_returns_none_when_no_ancestor_is_a_session():
    chain = {999: 500, 500: 1}
    assert owning_session(999, _sessions(), ppid_of=chain.get) is None


def test_chain_that_loops_does_not_hang():
    """A malformed /proc must not spin forever."""
    chain = {1: 2, 2: 1}
    assert owning_session(1, _sessions(), ppid_of=chain.get) is None


def test_falls_back_to_cwd_when_chain_is_broken():
    s = owning_session(999, _sessions(), ppid_of=lambda p: None, cwd="/h/rs")
    assert s["project"] == "web-shop"


def test_a_holding_hook_marks_its_session_again_without_a_word(tmp_path, monkeypatch):
    """2026-10-12: the first mark went down before Claude Code finished
    writing the question, so the session read as not waiting while it sat
    open. A hook that holds marks again; nothing is spoken or pushed."""
    from claude_companion import notify
    monkeypatch.setenv("CLAUDE_HOME", str(tmp_path))
    monkeypatch.setattr("claude_companion.registry.live_sessions",
                        lambda d: [dict(s, session_id=f"s{s['pid']}") for s in _sessions()])
    monkeypatch.setattr(notify, "owning_session", lambda pid, sessions, cwd="": None)
    said = []
    monkeypatch.setattr(notify, "speak", lambda *a, **k: said.append(a))
    monkeypatch.setattr(notify, "notify", lambda *a, **k: said.append(a))
    marker = tmp_path / notify.WAITING_DIR / "200"
    assert notify.still_waiting({"session_id": "s200", "cwd": "/h/rs"}) is True, "found by its session id"
    first = marker.stat().st_mtime
    os.utime(marker, (first - 60, first - 60))
    assert notify.still_waiting({}, session={"pid": 200}) is True, "or handed the one found at the start"
    assert marker.stat().st_mtime > first - 60, "marked again, newer than before"
    assert said == []


def test_a_holding_hook_never_marks_a_session_by_its_folder(tmp_path, monkeypatch):
    """A hook whose session died (its tree broken) must not mark whoever else
    works in that folder: announce()'s cwd fallback is not for a hook that
    marks every minute for a day."""
    from claude_companion import notify
    monkeypatch.setenv("CLAUDE_HOME", str(tmp_path))
    monkeypatch.setattr("claude_companion.registry.live_sessions", lambda d: _sessions())
    # The test's own process tree holds neither pid 100 nor 200, as a dead session's would not.
    assert notify.holding_session({"session_id": "dead", "cwd": "/h/rs"}) is None
    assert notify.still_waiting({"session_id": "dead", "cwd": "/h/rs"}) is False
    assert not (tmp_path / notify.WAITING_DIR / "200").exists()


def test_a_sessions_state_is_claude_codes_own_word(tmp_path, monkeypatch):
    """"waiting" while a prompt is open in its window, from its own registry
    entry; "gone" once the process has ended."""
    import json as _json
    from claude_companion import notify
    monkeypatch.setenv("CLAUDE_HOME", str(tmp_path))
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "200.json").write_text(_json.dumps({"pid": 200, "status": "waiting",
                                                                "statusUpdatedAt": 1791810000123}))
    monkeypatch.setattr("claude_companion.liveness.is_alive", lambda pid, start: pid == 200 and start == "9")
    assert notify.session_state({"pid": 200, "proc_start": "9"}) == "waiting"
    assert notify.session_status({"pid": 200, "proc_start": "9"}) == ("waiting", 1791810000.123), "and when it changed"
    assert notify.session_state({"pid": 200, "proc_start": "8"}) == "gone", "a recycled pid is not it"
    (tmp_path / "sessions" / "200.json").write_text("{")
    assert notify.session_state({"pid": 200, "proc_start": "9"}) == ""


# --- the sentence ----------------------------------------------------------

def test_does_not_repeat_what_the_opener_already_said():
    """speak.sh prepends "<Name> here, on <project>." to every clip."""
    text = compose({"tool_name": "Bash"}, speaker="Delia", project="home-lab")
    assert "Delia" not in text and "home-lab" not in text
    assert "Bash" in text


def test_names_the_speaker_when_no_opener_will():
    """Without a speaker there is nobody for "I" to refer to."""
    text = compose({"tool_name": "Bash"}, speaker="", project="docs-site")
    assert "docs-site" in text


def test_addresses_you_rather_than_describing_you():
    """These are spoken aloud; third person describes the situation to nobody."""
    text = compose({"tool_name": "Bash"}, speaker="Delia", project="home-lab")
    assert text.startswith("Sam,")


def test_with_no_name_the_address_drops_cleanly(monkeypatch):
    """A blank `name` leaves no stray comma or capital-less start."""
    monkeypatch.setenv("CLAUDE_COMPANION_NAME", "")
    assert compose({"tool_name": "AskUserQuestion"}, "", "web") == "Web has a question for you."
    assert compose({"tool_name": "Bash"}, "", "web") == "Web needs your OK: Bash"
    assert address("{you}, one quick thing?", "") == "One quick thing?"
    assert address("A question for you, {you}. I'll wait.", "") == "A question for you. I'll wait."
    assert address("{you}. Approve?", "Sam") == "Sam. Approve?"


def test_a_question_is_not_a_permission():
    """AskUserQuestion blocks for an ANSWER, and in bypassPermissions sessions
    it is the only thing that fires this hook at all."""
    # Not "the word question appears" -- Vance says "Sam. Need an answer."
    # The point is that it never calls a question an approval.
    from claude_companion.notify import QUESTION_LINES
    for _ in range(30):
        text = compose({"tool_name": "AskUserQuestion"}, speaker="Vance", project="budget")
        assert text in {address(x) for x in QUESTION_LINES["Vance"]}
        assert "permission" not in text.lower()
        assert "OK" not in text
        assert "approv" not in text.lower()


def test_a_real_permission_still_asks_for_an_OK():
    text = compose({"tool_name": "Bash", "tool_input": {"command": "rm -rf /tmp/x"}},
                   speaker="Zed", project="api")
    assert "need your OK" in text and "Bash" in text


def test_a_question_stays_short():
    """No tool detail on the question branch: the answer is in the terminal."""
    text = compose({"tool_name": "AskUserQuestion",
                    "tool_input": {"command": "x" * 300}}, speaker="Juno", project="p")
    assert len(text) < 80


def test_falls_back_to_project_without_a_speaker():
    text = compose({"tool_name": "Bash"}, speaker="", project="docs-site")
    assert "docs-site" in text
    assert text.strip()


def test_missing_tool_name_still_produces_a_sentence():
    """No tool name is not a reason to say nothing -- the opener still names
    the speaker, so the body only has to be a sentence addressed to you."""
    text = compose({}, speaker="Vance", project="p")
    # Speakers punctuate differently -- Vance says "Sam." where Juno says
    # "Sam!" -- so assert it addresses you and is a sentence, not one spelling.
    assert text.startswith("Sam") and text[-1] in ".?!" and len(text) > 10


def test_detail_prefers_the_command_for_bash():
    # The PROGRAM, not the command line. Reading shell aloud came out as "rm
    # dash r f slash tmp slash x"; nobody should parse a pipeline by ear.
    assert detail_for("Bash", {"command": "rm -rf /tmp/x"}) == "rm"
    assert detail_for("Bash", {"command": "cd ~/x && rm -f y"}) == "rm"
    assert detail_for("Bash", {"command": "timeout 30 ssh host true"}) == "ssh"


def test_detail_prefers_the_path_for_file_tools():
    # The basename: a full path read aloud is character soup.
    assert detail_for("Write", {"file_path": "/a/b.py"}) == "b.py"


def test_detail_is_truncated():
    d = detail_for("Bash", {"command": "x" * 500})
    assert len(d) <= 80


def test_detail_survives_a_non_dict_tool_input():
    assert detail_for("Bash", "not a dict") == ""


def test_sentence_stays_short_enough_to_speak():
    """Clips queue across five sessions; a long one eats the budget."""
    text = compose({"tool_name": "Bash", "tool_input": {"command": "y" * 400}},
                   speaker="Wren", project="my-project")
    assert len(text) <= 200


# --- delivery is optional --------------------------------------------------

def test_speak_is_a_no_op_without_agentvibes(tmp_path):
    """No ~/.claude/tts means no TTS, and definitely no crash."""
    assert speak("hello", tts_dir=tmp_path / "nope") is False


def test_speak_defaults_to_the_comms_installer_coordinator(monkeypatch):
    """comms/install.sh leaves speak.sh in the repository's comms directory."""
    from claude_companion import notify
    calls = []
    monkeypatch.setattr("claude_companion.notify.subprocess.Popen",
                        lambda *a, **k: calls.append(a) or type("P", (), {"pid": 1})())
    assert speak("hello") is True
    assert calls[0][0][0] == str(notify.BUNDLED_TTS_DIR / "speak.sh")


def test_speak_configured_coordinator_overrides_the_bundled_one(tmp_path, monkeypatch):
    tts = tmp_path / "agentvibes"
    tts.mkdir()
    script = tts / "speak.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    calls = []
    monkeypatch.setenv("CLAUDE_COMPANION_TTS_DIR", str(tts))
    monkeypatch.setattr("claude_companion.notify.subprocess.Popen",
                        lambda *a, **k: calls.append(a) or type("P", (), {"pid": 1})())
    assert speak("hello") is True
    assert calls[0][0][0] == str(script)


def test_speak_names_the_speaker_so_the_clip_is_not_a_phantom(tmp_path, monkeypatch):
    """Detaching severs the ancestry the coordinator walks to identify us.

    Without TTS_SPEAKER the detached clip is attributed to a new auto-assigned
    name and registers a second roster row -- a speaker that talks with settings
    nobody is editing.
    """
    tts = tmp_path / "tts"
    tts.mkdir()
    (tts / "speak.sh").write_text("#!/bin/sh\nexit 0\n")
    (tts / "speak.sh").chmod(0o755)
    captured = {}
    monkeypatch.setattr("claude_companion.notify.subprocess.Popen",
                        lambda *a, **k: captured.update(k) or type("P", (), {"pid": 1})())
    speak("hello", tts_dir=tts, speaker="Wren")
    assert captured["env"]["TTS_SPEAKER"] == "Wren"
    assert captured["start_new_session"] is True      # still never blocks the hook


def test_speak_omits_the_speaker_when_unknown(tmp_path, monkeypatch):
    tts = tmp_path / "tts"
    tts.mkdir()
    (tts / "speak.sh").write_text("#!/bin/sh\nexit 0\n")
    (tts / "speak.sh").chmod(0o755)
    captured = {}
    monkeypatch.setattr("claude_companion.notify.subprocess.Popen",
                        lambda *a, **k: captured.update(k) or type("P", (), {"pid": 1})())
    speak("hello", tts_dir=tts)
    assert "TTS_SPEAKER" not in captured["env"]


def test_speak_invokes_the_script_when_present(tmp_path, monkeypatch):
    tts = tmp_path / "tts"
    tts.mkdir()
    script = tts / "speak.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    calls = []
    monkeypatch.setattr("claude_companion.notify.subprocess.Popen",
                        lambda *a, **k: calls.append(a) or type("P", (), {"pid": 1})())
    assert speak("hello", tts_dir=tts) is True
    assert calls and calls[0][0][0] == str(script)
    # Passed as argv, never through a shell.
    assert calls[0][0][1] == "hello"


# --- each speaker sounds like themselves -----------------------------------

def test_a_speaker_has_more_than_one_line():
    """A fixed sentence means Juno says the identical thing every time."""
    from claude_companion.notify import compose
    seen = {compose({"tool_name": "AskUserQuestion"}, "Juno", "api")
            for _ in range(60)}
    assert len(seen) > 1


def test_lines_come_from_that_speaker_s_own_set():
    from claude_companion.notify import QUESTION_LINES, compose
    for _ in range(40):
        line = compose({"tool_name": "AskUserQuestion"}, "Delia", "home-lab")
        assert line in {address(x) for x in QUESTION_LINES["Delia"]}


def test_an_unknown_speaker_still_gets_a_sentence():
    from claude_companion.notify import compose
    line = compose({"tool_name": "AskUserQuestion"}, "Vesper", "somewhere")
    assert line.startswith("Sam") and line.endswith(".")


def test_every_line_addresses_you_and_stays_short():
    """Clips queue across five sessions; the whole message is 'come here'."""
    from claude_companion.notify import APPROVAL_LINES, NEUTRAL_APPROVAL, NEUTRAL_QUESTION, QUESTION_LINES
    groups = list(QUESTION_LINES.values()) + list(APPROVAL_LINES.values())
    groups += [NEUTRAL_QUESTION, NEUTRAL_APPROVAL]
    for lines in groups:
        for line in lines:
            assert "{you}" in line, line
            assert len(address(line, "Alexandria")) <= 60, line


def test_no_line_repeats_the_opener():
    """speak.sh already said the name and the project."""
    from claude_companion.notify import APPROVAL_LINES, QUESTION_LINES
    for speaker, lines in {**QUESTION_LINES, **APPROVAL_LINES}.items():
        for line in lines:
            assert speaker not in line, f"{speaker} names itself: {line}"


def test_question_lines_are_whole_sentences():
    """Delia's version of this caught "code-reviewer subagent finished." --
    lowercase because a variable led the line. An exact-string test passes that
    happily."""
    from claude_companion.notify import NEUTRAL_QUESTION, QUESTION_LINES
    for lines in list(QUESTION_LINES.values()) + [NEUTRAL_QUESTION]:
        for raw in lines:
            for line in (address(raw, "Sam"), address(raw, "")):
                assert line[0].isupper(), f"starts lowercase: {line}"
                assert line[-1] in ".?!", f"no sentence punctuation: {line}"


def test_approval_lines_are_fragments_waiting_for_the_tool():
    """These are completed at runtime -- "Sam, I need your OK" + ": Bash, ..."
    -- so trailing punctuation here would produce "your OK.: Bash". The only
    exception is a line that is already a question."""
    from claude_companion.notify import APPROVAL_LINES, NEUTRAL_APPROVAL
    for lines in list(APPROVAL_LINES.values()) + [NEUTRAL_APPROVAL]:
        for raw in lines:
            for line in (address(raw, "Sam"), address(raw, "")):
                assert line[0].isupper(), f"starts lowercase: {line}"
                assert line[-1] not in ".!", f"fragment ends like a sentence: {line}"


def test_every_speaker_has_a_few_to_choose_from():
    from claude_companion.notify import QUESTION_LINES
    for speaker, lines in QUESTION_LINES.items():
        assert len(set(lines)) >= 3, f"{speaker} has {len(set(lines))} distinct lines"


def test_approval_names_the_tool_and_the_program_not_the_command():
    """It carried the whole command line, so an approval arrived as "Bash, cd
    tilde slash dot claude slash tts and and rm dash f held slash star". The
    full text is in the notification and on the dashboard; the clip only has to
    say enough to know whether you care."""
    from claude_companion.notify import compose
    line = compose({"tool_name": "Bash", "tool_input": {"command": "rm -rf /tmp/x"}},
                   "Vance", "money")
    assert "Bash" in line and "rm" in line
    assert "-rf" not in line and "/tmp/x" not in line


def test_a_tap_opens_that_agents_own_chat_window():
    """Never the Claude app: the dashboard's own chat window, by the
    speaker's name."""
    from claude_companion.notify import _app_url, APP_ORIGIN
    assert _app_url("Vance") == APP_ORIGIN + "/?agent=Vance"
    assert "claude.ai" not in _app_url("Vance") and "/open" not in _app_url("Vance")


def test_a_tap_never_carries_a_room():
    """The room deep link existed for one commit. This is what stops it coming
    back through a helpful refactor."""
    from claude_companion.notify import _app_url
    for who in ("Vance", "", None):
        assert "room=" not in _app_url(who)


def test_no_name_or_a_bad_one_falls_back_to_the_dashboard():
    from claude_companion.notify import _app_url, APP_ORIGIN
    for who in ("", None, "a&b=c", "x" * 40, "../etc"):
        assert _app_url(who) == APP_ORIGIN + "/"


@pytest.mark.skip(reason="needs the server and page: step 2 of the split")
def test_a_tap_on_our_own_page_takes_over_the_open_dashboard():
    """An agent's window is ours, so the worker focuses the open dashboard and
    navigates it rather than opening a second window."""
    import pathlib
    sw = (pathlib.Path(__file__).resolve().parents[1] / "static" / "sw.js").read_text()
    assert "if (isOurs(url)) {" in sw and "isHandoff" not in sw
    assert sw.index("isOurs(url)") < sw.index("c.navigate("), "the origin check must gate the navigate"


@pytest.mark.skip(reason="needs the server and page: step 2 of the split")
def test_the_status_bar_badge_is_a_silhouette_not_a_square():
    """Android builds the status-bar icon from the alpha channel alone, so an
    opaque image is a solid white box -- which is exactly what pointing badge at
    the app icon produced. The badge must be mostly transparent, and the shape
    it keeps must be opaque enough to read at 24dp."""
    import pathlib
    png = pytest.importorskip("PIL.Image")
    static = pathlib.Path(__file__).resolve().parents[1] / "static"

    sw = (static / "sw.js").read_text()
    # Inlined since 2026-09-23 (an idle phone never fetched the URL);
    # test_pushicon checks the inline copy IS badge-96.png, so checking that
    # file here still checks what Android gets.
    assert "badge: BADGE" in sw and "app-icon-192" not in sw.split("const BADGE", 1)[1][:200], \
        "the badge must not be the app icon"

    img = png.open(static / "badge-96.png").convert("RGBA")
    alpha = list(img.getchannel("A").getdata())
    opaque = sum(1 for a in alpha if a > 200)
    assert 0.10 < opaque / len(alpha) < 0.55, \
        f"{opaque / len(alpha):.0%} opaque: a badge is a cut-out, not a filled square"
    assert alpha[0] == 0 and alpha[-1] == 0, "the corners must be transparent"




def test_your_own_cast_comes_from_the_settings_file(tmp_path, monkeypatch):
    """Character lines live in your config, not the repo; a bad entry falls
    back to the built-in table rather than to silence."""
    f = tmp_path / "c.toml"
    f.write_text('[question_lines]\nAlex = ["{you}. Question, mate."]\n'
                 'Juno = []\nBroken = 7\n'
                 '[approval_lines]\nAlex = "{you}, sign here"\n')
    monkeypatch.setenv("CLAUDE_COMPANION_CONFIG", str(f))
    from claude_companion.notify import QUESTION_LINES
    assert compose({"tool_name": "AskUserQuestion"}, "Alex", "p") == "Sam. Question, mate."
    assert compose({"tool_name": "Bash"}, "Alex", "p") == "Sam, sign here: Bash"
    assert compose({"tool_name": "AskUserQuestion"}, "Juno", "p") in {address(x) for x in QUESTION_LINES["Juno"]}
    assert compose({"tool_name": "AskUserQuestion"}, "Broken", "p") == "Sam, I have a question for you."
