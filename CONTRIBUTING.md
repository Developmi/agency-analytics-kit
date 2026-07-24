# Contributing to Agency Analytics Kit

Thank you for your interest in contributing. This project follows the Developmi engineering standard.

## Development setup

```bash
# Clone and install
git clone https://github.com/Developmi/agency-analytics-kit.git
cd agency-analytics-kit
uv sync
```

## Commit standard

This project uses [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add new connector for LinkedIn Ads
fix: resolve rate-limit deadlock in Meta connector
docs: update README with Docker instructions
chore: bump ruff to v0.15.x
test: add coverage for Google Ads pagination
```

Types: `feat` · `fix` · `docs` · `chore` · `refactor` · `perf` · `test`

## Branch naming

```
feat/short-description
fix/issue-number-description
docs/update-readme
chore/bump-dependencies
```

## How to Contribute

### 1. Report a Bug

Open a [GitHub Issue](https://github.com/Developmi/agency-analytics-kit/issues/new?template=bug_report.md)
with:
- A clear title and description
- Steps to reproduce
- Expected vs actual behavior
- Environment details (OS, Docker version, Python version)

### 2. Suggest a Feature

Open a [Feature Request](https://github.com/Developmi/agency-analytics-kit/issues/new?template=feature_request.md)
with:
- What problem it solves
- How it fits into the existing architecture
- Any prior art or references

### 3. Submit a PR

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Run the quality gate before committing:
   ```bash
   make quality
   ```
4. Open a Pull Request against `main`
5. Reference any related issues
6. A maintainer will review within 5 business days

## Adding a New Connector

See [DEVELOPMENT.md](DEVELOPMENT.md#adding-a-new-connector) for the step-by-step guide.

## Code Standards

- **Python 3.12+**, typed with `mypy --strict` where practical
- **ruff** for linting (config in `pyproject.toml`)
- **pytest** with mock-based tests (no live API calls)
- All new code must include tests
- Update `.env.example` if adding environment variables
- Update `clients/_template.yml` if adding a new connector

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE).

## Code of Conduct

This project adheres to the [Contributor Covenant](CODE_OF_CONDUCT.md).
