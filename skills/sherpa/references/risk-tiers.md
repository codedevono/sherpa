# Risk tiers

Every planned step gets one tier. The tier decides whether to ask.

| Tier | Meaning | Examples | Ask? |
|---|---|---|---|
| 0 | Read-only | `ls`, `cat`, `git clone --depth 1` into a work dir, `--version` checks, reading docs, `docker ps` | No |
| 1 | Writes only inside the work dir or the user's home, reversible | `python -m venv`, `pip install` into a venv, `npm install` in the project, building inside the clone, `docker build` | No |
| 2 | Changes the system | `apt/dnf/brew install`, `systemctl enable/start`, writing under `/etc`, adding a user to a group, `docker compose up -d` that binds ports, creating system users | **Yes** |
| 3 | Destructive, irreversible, exposed, or touches secrets | `rm -rf` outside the work dir, `curl ... | sh`, firewall or SSH config changes, editing sudoers, disk or partition operations, publishing a port to the internet, reading or writing credentials | **Yes, show the exact command** |

## Rules

- When unsure between two tiers, pick the higher one.
- A command that pipes a download into a shell is always tier 3, whatever the
  script claims to do.
- Any step on a `vps` target that makes something reachable from outside the
  machine is at least tier 3.
- Approval for one step is not approval for the next. Ask again for each
  tier 2 or 3 step, unless the user explicitly approved a named group of steps.
