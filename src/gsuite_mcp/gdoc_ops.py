"""Google Doc operations — template populate and suggest-edit via .docx export."""

import asyncio
from typing import Any

from gsuite_mcp.retry import retry_transient


GOOGLE_DOC_MIME = "application/vnd.google-apps.document"


async def template_populate(
    drive_service,
    docs_service,
    template_file_id: str,
    parent_folder_id: str,
    new_title: str,
    replacements: dict[str, str],
) -> dict[str, Any]:
    """Copy a template file as a native Google Doc and replace placeholders.

    Uses Drive files.copy with mimeType conversion, then a single
    Docs batchUpdate with replaceAllText for each placeholder.
    """
    copy_body = {
        "name": new_title,
        "parents": [parent_folder_id],
        "mimeType": GOOGLE_DOC_MIME,
    }
    copied = await asyncio.to_thread(
        lambda: drive_service.files()
        .copy(
            fileId=template_file_id,
            body=copy_body,
            fields="id,name,webViewLink",
        )
        .execute()
    )
    new_file_id = copied["id"]

    if not replacements:
        return {
            "file_id": new_file_id,
            "web_view_link": copied.get("webViewLink", ""),
            "replacements_made": {},
        }

    requests = [
        {
            "replaceAllText": {
                "containsText": {"text": placeholder, "matchCase": True},
                "replaceText": value,
            }
        }
        for placeholder, value in replacements.items()
    ]

    resp = await retry_transient(
        lambda: docs_service.documents()
        .batchUpdate(documentId=new_file_id, body={"requests": requests})
        .execute()
    )

    replacements_made = {}
    for (placeholder, _), reply in zip(replacements.items(), resp.get("replies", [])):
        count = reply.get("replaceAllText", {}).get("occurrencesChanged", 0)
        replacements_made[placeholder] = count

    return {
        "file_id": new_file_id,
        "web_view_link": copied.get("webViewLink", ""),
        "replacements_made": replacements_made,
    }
