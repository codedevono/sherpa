# Setup Spec

The distilled form of a project's setup docs. Write it as `setup-spec.md`,
about one page. Everything the plan needs, nothing it does not.

```markdown
# <project name> setup spec

- Source: <repo URL> @ <commit or tag>
- Evidence used: <e.g. ci.yml, Dockerfile, README sections>
- Disagreements between sources: <or "none">

## Prerequisites
- <tool> >= <version>  (needed for: <why>)

## Install variants
### docker   (the variant to use when the target is docker)
1. `<command>`   verify: `<check>`
### from-source
1. `<command>`   verify: `<check>`
### package  (pip / npm / apt / binary release)
1. `<command>`   verify: `<check>`

## Configuration
- ENV_VAR: <meaning>, default <value>, required: yes/no
- Config file: <path>, <what to set>

## Runtime
- Ports: <port> (<purpose>)
- Data directories: <path>
- Process model: <foreground process / needs a service manager>

## Verification
- <command or URL> should return <expected result>

## Known pitfalls
- <thing the docs warn about or that issues show people hit>
```

Rules:
- Quote commands exactly as the source gives them. Do not paraphrase commands.
- Mark anything you inferred rather than read with `(inferred)`.
- If a section does not apply, write "n/a" instead of deleting it.
