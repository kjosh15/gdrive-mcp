import json
from unittest.mock import patch, MagicMock

from gdrive_mcp.drive import get_drive_service


def test_get_drive_service_from_env(monkeypatch):
    fake_creds = {"type": "service_account", "project_id": "test"}
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", json.dumps(fake_creds))

    with (
        patch(
            "gdrive_mcp.drive.service_account.Credentials.from_service_account_info"
        ) as mock_creds,
        patch("gdrive_mcp.drive.build") as mock_build,
    ):
        mock_creds.return_value = MagicMock()
        mock_build.return_value = MagicMock()

        service = get_drive_service()
        mock_creds.assert_called_once()
        mock_build.assert_called_once_with(
            "drive", "v3", credentials=mock_creds.return_value
        )
        assert service is not None


def test_get_drive_service_missing_env_raises(monkeypatch):
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    try:
        get_drive_service()
        assert False, "Should have raised"
    except ValueError as e:
        assert "GOOGLE_SERVICE_ACCOUNT_JSON" in str(e)
