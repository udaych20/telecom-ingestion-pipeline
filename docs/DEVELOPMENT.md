# Development guide

## Repository layout

```text
app.py               Application and exporters
requirements.txt     Python dependencies
.env.example         Safe configuration template
docs/                Specifications and operating documentation
output/              Generated files; ignored by Git
```

## Change process

1. Start with a requirement and acceptance criterion in `SPECIFICATION.md`.
2. Document schema changes in `DATA_CONTRACTS.md`.
3. Make focused, readable code changes.
4. Test with synthetic data before using non-production Cosmos data.
5. Update user and operations documentation in the same change.

## Coding conventions

- Prefer small functions and descriptive names.
- Keep Cosmos values parameterized.
- Do not silently broaden correlation joins.
- Preserve source JSON in the complete log.
- Preserve per-interaction fault isolation in dataset mode.
- Avoid new dependencies unless they clearly reduce complexity.

## Definition of done

- Acceptance criteria are satisfied.
- Syntax and relevant tests pass.
- Configuration examples are current.
- Security and data-handling impacts are reviewed.
- Documentation matches actual behavior.
