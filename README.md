# X-Ray Workspace

**Find the access that is already broken.**

Access does not fail loudly. It fails on the path nobody is currently looking at — the credential
that works in your shell but is missing from cron's, the connector that quietly dropped out of the
list, the scheduled job whose binary was uninstalled two months ago. You find out when a client
opens a link, or when you notice a log that has been empty since August.

This is the page that would have told you. Run it against a workspace and it answers, for every
tool an agent reaches through: **what is it, who owns it, how is it reached, does it work right
now, when did it last work, what failed last and why, what do I switch to, and how do I fix it.**

Run against one real workspace, it found four live failures in an hour — each days or weeks old,
none of which had produced a single alert:

| What was wrong | Why nobody noticed |
|---|---|
| API credentials gone from the shell environment | The OAuth connector for the same service still worked, so every interactive check passed while every scheduled run failed |
| A CLI no longer installed | Two skills depended on it and simply stopped producing output |
| A scheduled job's agent, state directory and venv all missing after a machine wipe | A job that was never loaded fails exactly like a job with nothing to do |
| An MCP connector dropped out of the list | It had been used heavily three days earlier |

## How it works

Three sources, kept deliberately separate:

- **Measured, never typed.** Call counts, last use, last failure and the failure text come from
  parsing your Claude Code session transcripts. Nobody maintains them, so they cannot go stale.
- **Curated, because no machine knows it.** Owner, route, fallbacks, dependencies, fix — one entry
  per *thing that can break*. A connector with forty tools is one entry, not forty.
- **Probed, so the page cannot lie.** Each entry carries a read-only shell check that runs on every
  refresh, so the page reports the machine as it is rather than as it was documented.

A tool whose hard dependency is broken is downgraded automatically, however well it probes itself.
That is how "the scheduled job is green but the credential it needs is gone" becomes visible.

## Two views

**Cards** — every tool, filtered by status, newest failure text inline.

**Map** — the access path, left to right: you → the agent that owns the tool → the tool → what it
depends on. Dashed edges are fallbacks you can switch to; amber edges are hard dependencies.
Clicking a box dims everything it does not touch.

Both are plain HTML served locally. Nothing is published, because a page naming your credentials
and what is currently broken is not something to put on the internet.

## Install

As a Claude Code skill:

```bash
claude install-skill https://github.com/razbakov/x-ray-workspace
```

Then `/x-ray-workspace`. It reads your transcripts, probes what it can, and writes the registry.

Or clone it and drive the pieces yourself:

```bash
git clone https://github.com/razbakov/x-ray-workspace
python3 x-ray-workspace/scripts/scan.py > /tmp/usage.json        # what you actually use
cp x-ray-workspace/assets/registry.example.json ops/toolsdash/registry.json   # then edit
python3 x-ray-workspace/scripts/validate.py ops/toolsdash/registry.json
TOOLSDASH_REGISTRY=ops/toolsdash/registry.json python3 x-ray-workspace/scripts/server.py
```

`http://localhost:3523` for the cards, `/map` for the access map.

## Your registry is not in this repo

`registry.json` names a particular person's accounts, hosts and credentials — and in a delivery
workspace, its clients. It belongs in the workspace it describes, and the server is pointed at it
with `TOOLSDASH_REGISTRY`. This repo ships the machinery and a three-entry example; `.gitignore`
keeps a real one out by accident.

## What is here

| Path | Role |
|---|---|
| `SKILL.md` | The procedure: measure, card, probe, wire, validate. |
| `references/REGISTRY.md` | The schema every entry must satisfy, and the rules a probe must obey. |
| `scripts/scan.py` | Reads the session transcripts. Calls, errors, last use, last failure with its text, session, project. |
| `scripts/server.py` | Merges registry + scan + live probes. Serves `/`, `/map`, `/api/state`. |
| `scripts/validate.py` | The gate: schema, resolvable references, valid regexes, read-only probes, unmatched keys. |
| `assets/` | The two views, and the example registry. |

Python standard library only. No pip installs, no network access, no telemetry.

## Writing a probe

The probe is what makes the page live rather than a document. Four rules, in order:

1. **Read-only.** It runs unattended on every refresh. Check that a credential *authenticates*,
   never that it can write.
2. **Fast.** Hard-killed at 20 seconds.
3. **Quiet about secrets.** Print lengths and names, never values — the output is rendered on the
   page.
4. **Honest.** If nothing is checkable from this machine, `null` and say why. A fabricated green is
   worse than no dashboard.

The pattern worth copying catches the failure this tool exists for — a service that works
interactively while its unattended path is dead:

```json
"probe": "zsh -ic 'print -r -- \"tok=${#SERVICE_API_TOKEN}\"' 2>/dev/null | tail -1",
"probe_ok": "tok=(?!0\\b)\\d+"
```

An interactive shell is the only way to see what the environment actually holds — the agent's own
environment is not the one cron and scripts get — and the regex insists on a non-zero length, so an
unset variable fails instead of matching `tok=0`.

Full details in [`references/REGISTRY.md`](references/REGISTRY.md).

## License

MIT.
