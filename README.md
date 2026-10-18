# sherpa

> Working name. A setup agent that reads the docs so you don't have to.

Installing software means wading through long READMEs, install guides, and
half-outdated docs, written for a generic reader, then adapting them to your
own machine by trial and error. **sherpa does that reading for you.** Point it
at a GitHub repo, a docs page, or another LLM's instructions, tell it where you
want the software (VPS, Docker, bare metal), and it works out the shortest
correct path *for your machine*, runs the safe steps, and asks before the risky
ones.

## The idea: context is everything

The same project needs different steps on Ubuntu with Docker than on Kali bare
metal, or on a 512 MB VPS with no sudo. So sherpa never plans from the docs
alone. It first builds a **Context Profile** of the target, then matches the
docs against it.

## Goals

- Spend your time building, not reading setup docs.
- Plans fit the actual machine: OS, architecture, RAM, disk, privileges, what is
  already installed, what ports are taken.
- Handle three target types: **VPS**, **Docker**, **bare metal**.
- Accept any source: a repo, any documentation, or another LLM's output.
- Autonomous where it is safe, asks only on risky steps.
- Leave behind something reusable: an `install.sh` of what worked and a log of
  decisions.
- Open source, and usable by other agents.

## Non-goals (for now)

- Not a new standalone agent framework. No custom LLM layer, agent loop, or
  executor. The host agent already has those (see the decision below).
- Not a package manager or config-management tool (Ansible, Nix).
- No GUI.

## Key decision: a skill, not a standalone agent

The first plan was a full standalone agent: its own LLM client, tool loop, SSH
executor, and TUI. That is a large project for one person, and most of it
already exists in agent hosts like Claude and Hermes, which provide the model,
the shell, file access, and MCP tools.

What those hosts do **not** have is the part that makes setup reliable:

1. a machine probe that produces a precise Context Profile,
2. a procedure for distilling docs into a short spec and matching it to the profile,
3. risk tiers that decide when to ask,
4. safety rules for acting on untrusted text from repos.

So sherpa is a **skill** (a folder with `SKILL.md`, scripts, and references)
plus one small script. Skills use the [agentskills.io](https://agentskills.io)
format, which Claude's skills follow and which Hermes documents as its
skill standard, so the same folder should work in either. If a standalone
service is ever worth it, it can wrap the same skill later.

Status of that compatibility claim: Claude side is known to work with this
layout; the Hermes side comes from its documentation and has not been tested
here. OpenClaw has not been checked.

## Layout

```
skills/sherpa/
  SKILL.md                  the workflow the agent follows
  scripts/probe.py          environment probe (stdlib only, read-only, offline)
  references/
    setup-spec.md           schema for the distilled docs
    risk-tiers.md           which steps need approval
tests/test_probe.py
```

## Try the probe

```sh
python3 skills/sherpa/scripts/probe.py --target docker
python3 skills/sherpa/scripts/probe.py --json -o profile.json
python3 -m unittest discover -s tests
```

It needs only Python 3.9+, changes nothing on the system, and makes no network
calls.

## Install the skill

- **Claude Code**: copy `skills/sherpa` into `~/.claude/skills/`.
- **Hermes**: copy it into `~/.hermes/skills/` (per Hermes docs; untested).

Then ask your agent something like: *"Use sherpa to set up
github.com/owner/project in Docker on this machine."*

## Roadmap

| Phase | Goal | Status |
|---|---|---|
| 1. Probe | Accurate Context Profile, no LLM needed | done |
| 2. Skill v0 | `SKILL.md` workflow, risk tiers, spec schema | drafted, untested |
| 3. Benchmarks | 3 painful real repos, run the skill end to end, fix what breaks | next |
| 4. Recovery | Better error diagnosis, retry limits, failure notes | |
| 5. Export | Reliable `install.sh` and decision log | |
| 6. Memory | Remember what worked per environment | |
| 7. Service | Optional wrapper (CLI or MCP server) for headless use | only if needed |

Phase 3 decides whether the design is right. If the plans are poor, adding
automation only makes mistakes faster.

## Safety

The agent acts on text from repos and other LLMs, and that text can contain
prompt injection, `curl | sh` lines, or destructive commands. The skill treats
all of it as untrusted data, tiers every step by risk, asks before system-level
or destructive changes, and requires showing any remote script before running
it. See `skills/sherpa/references/risk-tiers.md`.

## License

Not chosen yet. Pick one before publishing (MIT or Apache-2.0 are the usual
choices for this kind of tool).
