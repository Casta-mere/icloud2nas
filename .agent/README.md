# .agent — Unified AI Knowledge

Single source of truth for AI assistant knowledge (skills and commands)
shared across tools. Edit files **here**; the tool-specific directories are
symlinks back into this folder.

## Layout

```
.agent/
  commands/      # Slash commands (<name>.md) — user-invocable via /command-name
  skills/        # Agent Skills (<name>/SKILL.md)
```

## Symlinks

| Path               | Target               | Used by     |
| ------------------ | -------------------- | ----------- |
| `.claude/commands` | `../.agent/commands` | Claude Code |
| `.claude/skills`   | `../.agent/skills`   | Claude Code |

Do not edit files through the symlinked dirs expecting separate copies — they
all point at the same files under `.agent/`.
