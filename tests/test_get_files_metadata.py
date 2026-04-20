from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def mock_drive():
    with patch("gsuite_mcp.auth.get_drive_service") as mock:
        service = MagicMock()
        mock.return_value = service
        yield service


@pytest.mark.asyncio
async def test_get_files_metadata_batch_success(mock_drive):
    """Batch get metadata for multiple file IDs returns all results."""
    def fake_get(fileId, fields):
        mock_exec = MagicMock()
        mock_exec.execute.return_value = {
            "id": fileId,
            "name": f"file_{fileId}.docx",
            "mimeType": "application/vnd.google-apps.document",
            "size": "100",
            "modifiedTime": "2026-04-01T10:00:00Z",
        }
        return mock_exec

    mock_drive.files().get.side_effect = fake_get

    from gsuite_mcp.server import get_files_metadata

    result = await get_files_metadata(file_ids=["a", "b", "c"])

    assert len(result["results"]) == 3
    assert {r["file_id"] for r in result["results"]} == {"a", "b", "c"}
    assert result["errors"] == []


@pytest.mark.asyncio
async def test_get_files_metadata_partial_failure(mock_drive):
    """One failing file doesn't abort the rest."""
    def fake_get(fileId, fields):
        mock_exec = MagicMock()
        if fileId == "bad":
            mock_exec.execute.side_effect = RuntimeError("boom")
        else:
            mock_exec.execute.return_value = {
                "id": fileId,
                "name": fileId,
                "mimeType": "x",
                "size": "0",
                "modifiedTime": "2026-04-01T10:00:00Z",
            }
        return mock_exec

    mock_drive.files().get.side_effect = fake_get

    from gsuite_mcp.server import get_files_metadata

    result = await get_files_metadata(file_ids=["good1", "bad", "good2"])

    assert len(result["results"]) == 2
    assert {r["file_id"] for r in result["results"]} == {"good1", "good2"}
    assert len(result["errors"]) == 1
    assert result["errors"][0]["file_id"] == "bad"
    assert "boom" in result["errors"][0]["error"]


@pytest.mark.asyncio
async def test_get_files_metadata_empty_list(mock_drive):
    from gsuite_mcp.server import get_files_metadata
    result = await get_files_metadata(file_ids=[])
    assert result == {"results": [], "errors": []}
