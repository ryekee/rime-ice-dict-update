.PHONY: dev test install uninstall clean

# Create a local dev environment with pytest.
dev:
	python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# Run the test suite.
test:
	.venv/bin/pytest -q

# Install the `rimeup` command for the current user (requires pipx).
install:
	pipx install .

uninstall:
	pipx uninstall rime-ice-dict-update

clean:
	rm -rf .venv build dist *.egg-info .pytest_cache __pycache__ tests/__pycache__
