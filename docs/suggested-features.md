# Suggested features for your own PC

These are useful safeguards that deliberately are not part of
agent-companion. They depend on a particular computer, its games, its cooling
headroom, and its weekly work budget. Give one section below to your own Claude
Code and have it adapt the design to your installation. Keep the switch that
turns each safeguard off under the computer owner's control, never an agent's.

## Load guard

**Goal:** While the computer is being used for a game, queue expensive agent
commands so they use one low-priority slot on a small set of CPUs. Normal shell
work continues immediately.

Why: a full test suite started during a game once made a game stream lag,
overheated the CPU, and crashed the PC through a GPU driver bugcheck. Lowering
priority alone was not enough. Limiting heavy work to pinned CPUs also protects
the cores a game needs.

Ask your Claude to build these pieces:

1. A small state writer, running every 15 seconds, that writes `ok`, `gaming`,
   or `hot` to a state file. Make the state include the number of available
   heavy slots and, while gaming, the CPUs heavy work may use.
2. Game detection suited to this PC. A reasonable starting signal is a process
   launched from a game-library folder. Require recent keyboard or mouse use,
   or active streaming, and retain the state briefly after the signal ends.
   That grace period avoids flipping rapidly, while the activity check avoids
   throttling agents for an idle game left open.
3. A `PreToolUse` hook for `Bash` that recognizes heavy-looking commands such
   as test suites, headless-browser runs, and builds. Prefix a matching command
   with a queue wrapper which takes one `flock` slot, uses `nice 19`, and uses
   idle I/O priority. Do not rewrite it as `bash -c ...`: Claude Code would
   then need to approve a command whose original contents it cannot inspect.
   A prefix leaves the original command visible at top level.
4. A queue wrapper which pins the launched command to the configured CPUs only
   while gaming. Give waiting work a finite escape hatch so a hook timeout
   cannot leave an agent stalled forever. On missing or invalid state, fail
   open at the lowest priority.
5. An owner-only off switch stored separately from the state. Agents must read
   it but never create, extend, or change it. Record why it was switched off so
   the owner can audit the choice.

Test the command matcher against harmless commands, test and browser commands,
multi-command shell lines, quoted text that merely mentions a test, and a
command already prefixed by the wrapper. Test the state transitions without
requiring a real game to run.

## Budget guard

**Goal:** Protect a weekly Claude usage budget without blocking a main
session's ordinary work.

Use the seven-day utilisation and reset timestamp already supplied by
`agent_companion/limits.py`. Compute a weekly pace line from the fraction of
the current usage window that has elapsed. Once utilisation is above that line,
a `PreToolUse` hook may refuse only expensive multiplier tools, such as
subagents and workflows. Its refusal should say that usage is ahead of the
weekly pace and name the next reset when known. It should never refuse regular
work in the main session.

Ask your Claude to keep it safe and understandable:

1. Read the cached usage response and calculate the allowed percentage from
   window progress, rather than using one fixed weekly ceiling. A quiet day
   should restore headroom.
2. Fail open when utilisation, a reset timestamp, or the cache cannot be read.
   A broken local cache must not prevent ordinary work.
3. Match only the expensive tools the owner selects. Keep the matcher explicit
   and test that normal shell, file, and main-session work remains allowed.
4. Make the override an owner-only file, not an environment variable that an
   agent can set for itself. Set its expiry to the current weekly reset so it
   lapses automatically with the next usage window. The hook only reads that
   file; no agent should write it.
5. Log only local, non-sensitive guard decisions needed to explain a refusal.
   Do not put prompts, tokens, or credentials in that log.

Test the pace calculation around a reset, an expired override, missing and
malformed data, over-pace usage, and each protected versus ordinary tool. The
hook should always leave unrelated tool calls alone.

## Spend routing

**Goal:** Save a Claude plan for design, review, and the jobs that need it by
giving routine implementation work to another plan's unattended CLI first.

Why: people who pay for Claude, ChatGPT with Codex, or Gemini with its CLI may
have useful capacity outside Claude. Build-to-a-plan and test-fixing jobs are
often well suited to a single headless worker. Keep review with a strong model,
then use Claude subagents last, so the Claude week lasts longer.

Ask your Claude to build a small `delegate` wrapper like this:

1. Accept `delegate [--to auto|codex|gemini-model] [--dir <worktree>] "job"`.
   Run one job, headless and unattended, in the selected Git worktree. `auto`
   should choose an ordered list of plans and try the next one only after a
   recognizable quota error.
2. Refuse a directory that is not a Git checkout or worktree, the repository's
   main checkout, and repositories marked private in local configuration. Never
   send prompts, source, credentials, or other private data to another
   company's cloud. Let the owner decide which repositories may be delegated.
3. Make workers unable to push, and tell them they are builders, not mergers.
   Log the requested target, start time, result, and non-sensitive output for
   each job. Add `delegate --usage` to count recent jobs per plan for the week.
4. Add a `PreToolUse` hook that reminds the session to delegate before starting
   a Claude subagent when another plan may fit. The hook must only remind, never
   block, and must leave the final decision with the session owner.
5. Give every wait a finite escape hatch. A worker instructed to wait for a
   service, test, or other event can time out; it needs a way to continue, stop
   safely, or return useful partial results.
6. Require the main session to review the worker's diff, run any required
   checks, and decide whether to commit. A worker's sandbox may not be able to
   run the application's route or browser tests, so the session runs those.

Test argument parsing, target selection after fake quota failures, refusal of
the main checkout and private targets, push prevention, logging, weekly usage
counts, and the reminder hook. Use fixture repositories and fake CLIs. Do not
send a real prompt, invoke a real plan, or require a browser to test the
wrapper.
