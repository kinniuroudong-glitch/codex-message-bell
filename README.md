# Codex Done Bell

Get a voice alert when Codex finishes working.

Codex Done Bell is a tiny macOS helper for people who leave Codex running in the background. It waits for a real `task_complete` event, then plays a voice notification so you know it is time to reply.

By default it says:

> 我做好了，请回复

Perfect for long Codex runs, background coding tasks, and anyone tired of checking the window every few minutes.

## Why

- Speaks only after Codex finishes, not while it is streaming
- Runs quietly in the background with macOS `launchd`
- Uses built-in macOS tools: Python, `say`, and `afplay`
- No Python dependencies
- Easy install and uninstall

## Requirements

- macOS
- Python 3
- Codex Desktop or Codex CLI session logs under `~/.codex/sessions`

No Python packages are required.

## Quick Start

```bash
git clone https://github.com/kinniuroudong-glitch/codex-message-bell.git
cd codex-message-bell
sh install.sh
```

After installation, the helper runs in the background via `launchd`. When a Codex task completes, macOS plays the notification once.

## Test the Voice

```bash
python3 codex_message_bell.py --test-sound
```

The default voice file is created automatically at:

```text
~/Library/Sounds/codex-done-reply.aiff
```

## Change the Sound

Use any local audio file supported by `afplay`:

```bash
python3 codex_message_bell.py --sound ~/Library/Sounds/notification.wav --rings 1 --install
```

## Run in the Foreground

```bash
python3 codex_message_bell.py --verbose
```

## Uninstall

```bash
sh uninstall.sh
```

Or:

```bash
python3 codex_message_bell.py --uninstall
```

## How It Works

Codex writes JSONL session files under `~/.codex/sessions`. This helper periodically scans recent session logs and remembers the latest `task_complete` timestamp. When a newer completion appears, it plays the configured sound.

The LaunchAgent records the current latest completion at install time, so installing or restarting the helper does not replay old notifications.

The script does not modify Codex session files or databases. It only writes its own state file and macOS LaunchAgent:

```text
~/.codex-message-bell-state
~/Library/LaunchAgents/local.codex-message-bell.plist
```

Logs are written to:

```text
~/Library/Logs/codex-message-bell/
```

## Options

```text
--install              Install and start the LaunchAgent
--uninstall            Stop and remove the LaunchAgent
--test-sound           Play the configured sound once
--sound PATH           Audio file to play
--rings N              Number of times to play after completion
--interval SECONDS     Polling interval, default 2
--cooldown SECONDS     Minimum seconds between notifications, default 5
--finish-delay SECONDS Wait after completion before playing sound, default 2
--verbose              Print detected completion events
```


