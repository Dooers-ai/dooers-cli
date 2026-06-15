# dooers (CLI)

The Dooers CLI. Installed by agent creators.

```bash
pip install dooers
```

## Commands

```bash
# auth (email prompts if omitted)
dooers login you@example.com
dooers whoami
dooers logout

# agents
dooers agents list
dooers agents create --name my-agent
dooers agents show ag_8h2k

# push (reads dooers.yaml in cwd unless agent_id given)
dooers push [<agent_id>] [--tag latest] [--env prod|stg|dev]
```

## Global config (precedence: flag > env > default)

| Setting | Flag | Env var | Default |
|---|---|---|---|
| Core API host | `--core-url` | `DOOERS_CORE_URL` | `https://api.dooers.ai` |
| dooers-push host | `--push-url` | `DOOERS_PUSH_URL` | `https://host.dooers.ai` |
| Target environment | `--env` | `DOOERS_ENV` | `prod` |

## Development

```bash
uv sync --extra dev
uv run poe dev
```
