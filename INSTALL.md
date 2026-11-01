# Install guide

This guide sets up claude-companion on a Windows 10 or 11 PC through WSL2 and
Ubuntu, then optionally makes the dashboard available to a paired phone. Keep
keys, tokens, Firebase files, and addresses outside this repository. The app
stores its configuration in `~/.config/claude-companion/` with restrictive
permissions.

## What you need

Required:

- Windows 10 or 11 with WSL2 and Ubuntu.
- Python 3.11 or newer.
- Claude Code, installed and signed in inside the Ubuntu WSL distribution.

Optional:

- Tailscale, which lets a phone reach the dashboard securely.
- Home Assistant, which provides fitness data.
- Piper, installed by the Comms setup, which enables spoken session replies.
- An Nvidia GPU, only for the optional voice lab.
- Node.js and npm, a JDK, and Android build tools, only to build the Android
  app.

`gh` is not part of the core install. It is needed only for the optional merge
queue and Claude-to-Claude collaboration notes.

## Core setup in WSL

### 1. Install WSL2 and Ubuntu

In an administrator PowerShell window, install Ubuntu for WSL2:

```powershell
wsl --install -d Ubuntu
```

Restart Windows if asked, then open Ubuntu from the Start menu and create its
Linux user. Confirm that Ubuntu uses WSL2 in PowerShell:

```powershell
wsl -l -v
```

Everything that follows runs inside Ubuntu. Enable systemd there so the user
service and timers can run:

```bash
printf '[boot]\nsystemd=true\n' | sudo tee -a /etc/wsl.conf
```

Run `wsl --shutdown` in PowerShell, reopen Ubuntu, and verify that the user
manager is available:

```bash
systemctl --user status
```

Keep the user manager available after logout:

```bash
sudo loginctl enable-linger "$USER"
loginctl show-user "$USER" -p Linger
```

WSL starts when Windows starts the distribution. If the dashboard must stay
available while no Ubuntu terminal is open, create a Windows sign-in task that
runs `wsl.exe -d Ubuntu --exec sleep infinity`.

### 2. Install Python, Git, and Claude Code

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git curl
python3 --version                    # 3.11 or newer
claude --version
```

Install and sign in to Claude Code inside Ubuntu before continuing. It must run
there because the dashboard reads its local `~/.claude` session files.

### 3. Clone and install

```bash
git clone https://github.com/trivgar/claude-companion.git
cd claude-companion
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[app,push]'
.venv/bin/python -c "import fastapi, uvicorn, claude_companion; print('ok')"
```

The repository is public. You do not need `gh auth login` to clone it.

### 4. Start the dashboard and pair a browser

Settings are optional. To start with a configuration file:

```bash
mkdir -p ~/.config/claude-companion
cp config.example.toml ~/.config/claude-companion/config.toml
chmod 700 ~/.config/claude-companion
chmod 600 ~/.config/claude-companion/config.toml
```

Start the server in one terminal:

```bash
.venv/bin/python -m claude_companion.server
```

In another terminal, create a one-time pairing link for this computer's
browser:

```bash
.venv/bin/python -m claude_companion.server pair --name browser
```

Open the link within ten minutes. The page stores the owner key in that
browser's local storage. The server stores only a hash of the key. Visit
`http://127.0.0.1:8770/` after pairing.

The hook token created at
`~/.config/claude-companion/claude-companion-token` is not an owner key. It
can create, poll, and withdraw an ask, but it cannot approve one.

### 5. Run it as a user service

This is optional, but makes the dashboard survive the terminal that started
it. The supplied unit assumes the clone is at `~/claude-companion`; edit its
two path lines if you cloned elsewhere.

```bash
mkdir -p ~/.config/systemd/user
cp systemd/claude-companion.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now claude-companion
systemctl --user is-active claude-companion
```

After updating the checkout, load the new dashboard code with:

```bash
systemctl --user restart claude-companion
```

## Phone access with Tailscale

The server listens on loopback by default. Tailscale can publish that local
address to your tailnet over HTTPS without opening the dashboard to the public
internet.

Install Tailscale by following the [official Linux installation
instructions](https://tailscale.com/kb/1031/install-linux) for your
distribution, then install the `tailscale` distro package. Do not use an
unreviewed shell installer. Sign in and publish the local server:

```bash
sudo tailscale up
tailscale serve --bg 8770
tailscale serve status
```

Enable HTTPS certificates in the Tailscale admin console if `tailscale serve`
asks for it. Put the displayed HTTPS URL in the configuration file, then
restart the user service if you use one:

```toml
url = "https://<machine>.<tailnet>.ts.net"
contact = "mailto:you@example.com"
```

Create a phone pairing link with `.venv/bin/python -m
claude_companion.server pair --name phone`, and open that link on the phone.
Use the web page in the phone browser. You can add it to the home screen and
enable browser push after opening it over the HTTPS Tailscale address.

## Optional components

### Asks, chat, and deferred handoff hooks

The installer writes its PermissionRequest and AskUserQuestion hooks plus
SessionStart and UserPromptSubmit hooks for deferred handoffs to
`~/.claude/settings.json` and makes a timestamped backup first:

```bash
.venv/bin/python -m claude_companion.server.install_hooks
```

To remove only those hooks later:

```bash
.venv/bin/python -m claude_companion.server.install_hooks --uninstall
```

Claude Code now starts in auto mode by default, which settles most permission
prompts itself, so only what auto mode will not decide reaches the Asks tab.
For every prompt to come to the dashboard, start sessions with
`--permission-mode manual` or set `"defaultMode": "manual"` under
`permissions` in `~/.claude/settings.json`.

Start a new Claude Code session after changing hooks. Phone chat needs sessions
to run in tmux, so install it if you want that feature:

```bash
sudo apt install -y tmux
```

### Session restore snapshots

To keep a small local snapshot of tmux-backed sessions for crash recovery,
install the optional user timer. The Sessions tab offers to restore missing
saved sessions after a reboot or crash.

```bash
.venv/bin/python -m claude_companion.restore install-timer
```

Remove only that timer later with:

```bash
.venv/bin/python -m claude_companion.restore uninstall-timer
```

### Comms and Piper

Comms is optional. It makes Claude Code sessions produce short audio clips
using Piper. Review the planned changes first:

```bash
comms/install.sh --dry-run
comms/install.sh
```

The installer downloads Piper and voices, writes Stop, SessionStart, and
UserPromptSubmit hooks to `~/.claude/settings.json` with a backup, and registers
the `tts` MCP server at Claude Code user scope. Open a new Claude Code session
after installation. Test a clip with:

```bash
TTS_SPEAKER=Juno COMMS_LOCAL_PLAY=0 comms/speak.sh "Testing one two."
ls ~/.local/share/claude-companion/clips
```

### Home Assistant fitness data

Home Assistant is optional. Configure its URL, a token file outside the
repository, and entity IDs in `~/.config/claude-companion/config.toml`. See
[docs/fitness-sources.md](docs/fitness-sources.md) for the available settings.
You do not need Home Assistant to use the rest of the dashboard.

### Mail drafting

Mail is optional. Gmail uses the Gmail connector configured in your Claude
account; Outlook is a plug-in described in
[docs/mail-outlook.md](docs/mail-outlook.md). The code creates drafts only. It
does not send mail.

### Voice lab

The voice lab is optional and needs an Nvidia GPU. It trains a voice only from
audio provided with the speaker's agreement. On WSL2, use the normal Windows
Nvidia driver rather than installing a Linux driver in the distribution. See
[voicelab/README.md](voicelab/README.md).

### Android app

The Android app is optional. Before building it, install Node.js and npm, a
JDK supported by the Android Gradle build, and the Android SDK with its build
tools. Then configure Firebase outside the repository:

```toml
app_name = "Companion"
url = "https://<machine>.<tailnet>.ts.net"
firebase_app_file = "/path/outside/the/repo/google-services.json"
fcm_service_account_file = "/path/outside/the/repo/firebase-service-account.json"
```

`google-services.json` is the Android app's Firebase configuration. It is
copied into the local Android build and does not authorize server-side sends.
`fcm_service_account_file` is a separate Firebase service-account credential
used by the dashboard server to send FCM notifications. Keep both files out of
the repository and readable only by the account that needs them.

Build a debug APK:

```bash
cd mobile
npm ci
cd ..
.venv/bin/python mobile/configure.py
cd mobile
npx cap sync android
cd android
./gradlew assembleDebug
```

The APK is at `mobile/android/app/build/outputs/apk/debug/app-debug.apk`.

**Known issue:** the Android app's native background features, including
push-delivered comms, clip playback, and update checks, do not yet send the
paired owner key and will be refused. For now, use the web app in the phone
browser or the app's foreground WebView.

### Merge queue and collaboration notes

These are optional features for people sharing a repository. They need GitHub
CLI authentication because they use GitHub pull requests and issues:

```bash
gh auth login
```

For the merge queue, copy and enable the supplied user service and timer:

```bash
cp systemd/claude-companion-mergeq.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now claude-companion-mergeq.timer
```

For collaboration notes, read [COLLAB.md](COLLAB.md), set `collab_label`,
`collab_from`, and `collab_repo` in the configuration, then run:

```bash
.venv/bin/python -m claude_companion.collab install
.venv/bin/python -m claude_companion.collab check
```

## Troubleshooting

- If the local page does not load, check the foreground server or run
  `systemctl --user status claude-companion`.
- If a phone cannot load the page, check `tailscale serve status`, the
  Tailscale URL in `url`, and that the phone is on the same tailnet.
- If a paired browser shows the pairing page again, make a new pairing link;
  its local owner key may have been removed.
- If a hook did nothing, start a new Claude Code session after installing it.
