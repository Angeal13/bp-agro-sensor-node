# Contributing

## Branch Strategy
- `main` — production-ready code only
- `develop` — integration branch
- `feature/<name>` — individual features
- `fix/<name>` — bug fixes

## Commit Convention
```
type(scope): short description

feat:     new feature
fix:      bug fix
docs:     documentation only
test:     adding or updating tests
refactor: code change without feature or fix
chore:    maintenance (deps, CI)
```

## Pull Request Checklist
- [ ] Tests pass (`pytest tests/ -v`)
- [ ] `.env` values not committed
- [ ] CHANGELOG.md updated under `[Unreleased]`
- [ ] No hardcoded credentials
