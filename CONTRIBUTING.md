# Contributing to LocalNetwork Ecosystem

Thank you for contributing! This document explains how to set up, develop,
and submit changes.

## Development Setup

```bash
# Clone
git clone https://github.com/HoomanJCode/LocalNetwork_Ecosystem.git
cd LocalNetwork_Ecosystem

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt
pip install -e .

# Run tests to verify
python -m pytest tests/ -v
```

## Project Structure

```
server/     Mediation server
client/     VPN client
proxy/      Reverse proxy
common/     Shared modules
tests/      Test suite
docs/       Documentation and implementation plan
scripts/    Development helper scripts
```

## Code Style

- Python 3.10+ only
- Follow existing conventions in the codebase
- Use type hints for all public functions
- Docstrings follow Google-style format
- Line length: 100 characters (soft), 120 (hard)

## Running Tests

```bash
# Full suite
python -m pytest tests/ -v

# Single file
python -m pytest tests/test_encryption.py -v

# Single test
python -m pytest tests/test_nat_traversal.py::TestPunchPeer::test_two_local_sockets_punch_succeeds -v

# With coverage (if installed)
python -m pytest tests/ -v --cov=. --cov-report=term-missing
```

### Test conventions

- Use `pytest` fixtures from `tests/conftest.py`
- Mark tests requiring root with `@pytest.mark.skipif(os.geteuid() != 0, reason="needs root")`
- Use `pytest.mark.asyncio` for async tests
- Test files are named `test_<module>.py`

## Commit Guidelines

- Follow the [AI_COMMIT_RULES.md](AI_COMMIT_RULES.md) for all commits
- Write clear, descriptive commit messages
- Keep changes focused — one logical change per commit

## Implementation Phases

The project follows a phased implementation plan tracked in [TODO.md](TODO.md).
Individual phases are detailed in `docs/todos/`. Before implementing a feature,
read the relevant phase document and [DESIGN.md](DESIGN.md).

### Phase status check

- ✅ Phase is fully implemented with tests
- ⬜ Phase is planned but not started

Always write tests alongside the implementation — they're listed in each phase doc.

## Adding a New Feature

1. Read the relevant phase document in `docs/todos/`
2. Read the relevant section of [DESIGN.md](DESIGN.md)
3. Implement the module
4. Write tests
5. Update the phase document's checkboxes
6. Update [TODO.md](TODO.md) if the phase is complete

## Web Panel Development

Web panels use:
- **Backend:** aiohttp + Jinja2
- **Frontend:** Vanilla HTML/CSS/JS (no framework)
- **Design system:** Shared CSS/JS in `common/web_static/`
- **Live updates:** Server-Sent Events (SSE)

Each panel (server, client, proxy) has its own templates but shares the base
design system in `common/web_static/`.

## Proxy Configuration

The reverse proxy uses YAML configuration. See [README.md](README.md) for the
full configuration reference. To validate a config without starting the proxy:

```bash
localnetwork-proxy --validate-config proxy.yaml
```

## Questions?

- Architecture: [DESIGN.md](DESIGN.md)
- Implementation plan: [TODO.md](TODO.md)
- Quick reference: [USAGE.md](USAGE.md)
- Full docs: [README.md](README.md)
