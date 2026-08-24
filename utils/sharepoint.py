"""
Optional Microsoft Graph uploader for pushing reports.xlsx into a SharePoint /
OneDrive folder identified by a *sharing link*.

Auth model: application (client-credentials) with an Entra (Azure AD) app that
has the Graph application permission ``Files.ReadWrite.All`` (admin-consented).

It is entirely OPT-IN: if the required env vars are not set, ``is_configured()``
returns False and the bot simply keeps its local reports.xlsx. This keeps the
container runnable even before the SharePoint app registration is finished.

Required env vars:
    SHAREPOINT_TENANT_ID     - Entra tenant (directory) id
    SHAREPOINT_CLIENT_ID     - app (client) id
    SHAREPOINT_CLIENT_SECRET - app client secret value
    SHAREPOINT_FOLDER_URL    - the sharing URL of the target folder
Optional:
    SHAREPOINT_FILE_NAME     - name to save as (default: reports.xlsx)
"""

import base64
import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
_SCOPE = ["https://graph.microsoft.com/.default"]

TENANT_ID = os.getenv("SHAREPOINT_TENANT_ID", "").strip()
CLIENT_ID = os.getenv("SHAREPOINT_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("SHAREPOINT_CLIENT_SECRET", "").strip()
FOLDER_URL = os.getenv("SHAREPOINT_FOLDER_URL", "").strip()
FILE_NAME = os.getenv("SHAREPOINT_FILE_NAME", "reports.xlsx").strip()

# Cache the resolved (driveId, folderItemId) so we only resolve the share once.
_folder_cache: Optional[Tuple[str, str]] = None


def is_configured() -> bool:
    return all([TENANT_ID, CLIENT_ID, CLIENT_SECRET, FOLDER_URL])


def _encode_share_url(url: str) -> str:
    """Encode a sharing URL into a Graph shareId (see Graph 'shares' API)."""
    b64 = base64.urlsafe_b64encode(url.encode("utf-8")).decode("utf-8").rstrip("=")
    return "u!" + b64


def _get_token() -> str:
    import msal  # imported lazily so bots without SharePoint don't need it at runtime

    app = msal.ConfidentialClientApplication(
        client_id=CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        client_credential=CLIENT_SECRET,
    )
    result = app.acquire_token_for_client(scopes=_SCOPE)
    if "access_token" not in result:
        raise RuntimeError(
            f"Graph token error: {result.get('error')} / {result.get('error_description')}"
        )
    return result["access_token"]


def _resolve_folder(token: str) -> Tuple[str, str]:
    """Resolve the sharing URL to (driveId, folderItemId), with caching."""
    global _folder_cache
    if _folder_cache is not None:
        return _folder_cache

    import requests

    share_id = _encode_share_url(FOLDER_URL)
    resp = requests.get(
        f"{GRAPH_ROOT}/shares/{share_id}/driveItem",
        headers={"Authorization": f"Bearer {token}"},
        params={"$select": "id,parentReference"},
        timeout=30,
    )
    resp.raise_for_status()
    item = resp.json()
    drive_id = item["parentReference"]["driveId"]
    folder_id = item["id"]
    _folder_cache = (drive_id, folder_id)
    return _folder_cache


def _download_sync(local_path: str) -> bool:
    """Blocking download of the remote workbook into ``local_path`` if it exists."""
    import requests

    token = _get_token()
    drive_id, folder_id = _resolve_folder(token)

    url = f"{GRAPH_ROOT}/drives/{drive_id}/items/{folder_id}:/{FILE_NAME}:/content"
    resp = requests.get(
        url, headers={"Authorization": f"Bearer {token}"}, timeout=120
    )
    if resp.status_code == 404:
        logger.info("No existing %s in SharePoint yet; starting fresh.", FILE_NAME)
        return False
    resp.raise_for_status()
    with open(local_path, "wb") as f:
        f.write(resp.content)
    logger.info("Downloaded existing %s from SharePoint.", FILE_NAME)
    return True


def _upload_sync(local_path: str) -> None:
    """Blocking upload of ``local_path`` into the target folder (overwrites)."""
    import requests

    if not os.path.exists(local_path):
        logger.warning("SharePoint upload skipped: %s not found.", local_path)
        return

    token = _get_token()
    drive_id, folder_id = _resolve_folder(token)

    with open(local_path, "rb") as f:
        content = f.read()

    # Simple upload is fine for small workbooks (< 250 MB).
    url = f"{GRAPH_ROOT}/drives/{drive_id}/items/{folder_id}:/{FILE_NAME}:/content"
    resp = requests.put(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/octet-stream",
        },
        data=content,
        timeout=120,
    )
    resp.raise_for_status()
    logger.info("Uploaded %s to SharePoint as %s.", local_path, FILE_NAME)


async def sync_from_remote(local_path: str) -> bool:
    """
    Pull the existing workbook from SharePoint into ``local_path`` on startup so
    that ephemeral hosts (e.g. Azure Container Instances) keep appending to the
    real file instead of overwriting it with a fresh one. Never raises.
    """
    if not is_configured():
        return False
    import asyncio

    try:
        return await asyncio.to_thread(_download_sync, local_path)
    except Exception as exc:
        global _folder_cache
        _folder_cache = None
        logger.error("SharePoint startup sync failed: %s", exc)
        return False


async def upload_report(local_path: str) -> bool:
    """
    Async wrapper: upload the workbook to SharePoint in a worker thread.
    Returns True on success, False on any failure (never raises).
    """
    if not is_configured():
        return False
    import asyncio

    try:
        await asyncio.to_thread(_upload_sync, local_path)
        return True
    except Exception as exc:
        # Reset cache in case the share/token became invalid; do not crash the bot.
        global _folder_cache
        _folder_cache = None
        logger.error("SharePoint upload failed: %s", exc)
        return False
