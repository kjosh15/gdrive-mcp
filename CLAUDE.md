# CLAUDE.md

## Commands

```bash
# Install
uv sync --all-extras

# Test
uv run pytest -q

# Lint
uv run ruff check .

# Run locally
uv run python -m gdrive_mcp
```

## Project Structure

- `src/gdrive_mcp/server.py` — FastMCP server with 4 Drive tools
- `src/gdrive_mcp/drive.py` — Google Drive API wrapper
- `tests/` — Unit tests (mocked Drive API)

## Key Constraints

- No database, no state, no LLM calls
- Service account auth via `GOOGLE_SERVICE_ACCOUNT_JSON` env var
- Streamable HTTP transport for Cloud Run
