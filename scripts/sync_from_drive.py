#!/usr/bin/env python3
"""Copy real files from a Google Drive folder into this website repository.

Google Docs, Sheets, and Slides are skipped. Those files live in Google's
format and are not the HTML pages GitHub Pages publishes.

Required environment variables:
  GOOGLE_SERVICE_ACCOUNT_JSON  Service account key JSON
  DRIVE_FOLDER_ID              Drive folder to start from. This can be the
                               parent Projects folder; the website child is
                               chosen automatically (the subfolder that
                               contains index.html).

Optional:
  DRIVE_SUBFOLDER              Child folder name to copy, e.g. Website (DotsLog).
                               Use this if more than one subfolder has index.html.
  DRIVE_SYNC_DELETE=true       Remove repo files that disappeared from Drive
  DRY_RUN=true                 Print actions without writing files
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

SKIP_DIR_NAMES = {".git", ".github"}
SKIP_FILE_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
SKIP_EXTENSIONS = {".gdoc", ".gsheet", ".gslides", ".gform", ".gmap", ".gsite"}
GOOGLE_APPS_PREFIX = "application/vnd.google-apps."
FOLDER_MIME = "application/vnd.google-apps.folder"

ALLOWED_EXTENSIONS = {
    ".html",
    ".css",
    ".js",
    ".json",
    ".txt",
    ".md",
    ".ico",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".webp",
    ".pdf",
    ".csv",
    ".xml",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
}

ALWAYS_ALLOWED_NAMES = {"cname"}

# Site chrome that should survive Drive overlays of older HTML/CSS.
PRESERVE_RELATIVE_PATHS = {
    "css/theme.css",
    "js/scripts.js",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def is_skipped_dir(name: str) -> bool:
    return name.lower() in SKIP_DIR_NAMES or name.startswith(".")


def is_google_workspace_file(mime_type: str) -> bool:
    return mime_type.startswith(GOOGLE_APPS_PREFIX) and mime_type != FOLDER_MIME


def is_allowed_file(name: str, mime_type: str) -> bool:
    if is_google_workspace_file(mime_type):
        return False
    lowered = name.lower()
    if lowered in SKIP_FILE_NAMES:
        return False
    suffix = Path(name).suffix.lower()
    if suffix in SKIP_EXTENSIONS:
        return False
    if lowered in ALWAYS_ALLOWED_NAMES:
        return True
    return suffix in ALLOWED_EXTENSIONS


def safe_join(root: Path, relative: str) -> Path | None:
    """Return root/relative if it stays inside root, otherwise None."""
    if not relative or relative.startswith("/"):
        return None
    parts = Path(relative).parts
    if any(part in {".", ".."} or is_skipped_dir(part) for part in parts):
        return None
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def load_credentials():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        return None
    from google.oauth2 import service_account

    info = json.loads(raw)
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def normalize_folder_name(name: str) -> str:
    return "".join(name.lower().split())


def folder_names_match(left: str, right: str) -> bool:
    return normalize_folder_name(left) == normalize_folder_name(right)


def has_index_html(items: list[dict]) -> bool:
    return any(
        item.get("name", "").lower() == "index.html" and item.get("mimeType") != FOLDER_MIME
        for item in items
    )


def find_named_child_folder(children: list[dict], name: str) -> dict | None:
    matches = [
        item
        for item in children
        if item.get("mimeType") == FOLDER_MIME and folder_names_match(item.get("name", ""), name)
    ]
    return matches[0] if matches else None


def resolve_sync_folder_id(service, folder_id: str, subfolder: str = "") -> str:
    """Return the Drive folder whose contents should map to the repo root.

    The website repo must not copy every Projects child (Work / AI sandbox)
    onto the public site. Prefer a named child, otherwise the folder that
    already contains index.html, otherwise the only child folder that does.
    """
    children = list_children(service, folder_id)
    if subfolder:
        current_id = folder_id
        current_children = children
        for part in Path(subfolder).parts:
            match = find_named_child_folder(current_children, part)
            if match is None:
                names = [item["name"] for item in current_children if item.get("mimeType") == FOLDER_MIME]
                raise ValueError(
                    f"DRIVE_SUBFOLDER part {part!r} was not found. Folder names here: {names}"
                )
            current_id = match["id"]
            current_children = list_children(service, current_id)
        print(f"using Drive subfolder: {subfolder}")
        return current_id

    if has_index_html(children):
        return folder_id

    website_children = []
    for item in children:
        if item.get("mimeType") != FOLDER_MIME or is_skipped_dir(item["name"]):
            continue
        grandchildren = list_children(service, item["id"])
        if has_index_html(grandchildren):
            website_children.append(item)

    if len(website_children) == 1:
        chosen = website_children[0]
        print(f"using website subfolder: {chosen['name']}")
        return chosen["id"]

    if len(website_children) > 1:
        names = [item["name"] for item in website_children]
        raise ValueError(
            "Several subfolders contain index.html. Set GitHub secret "
            f"DRIVE_SUBFOLDER to one of: {names}"
        )

    return folder_id


def list_children(service, folder_id: str) -> list[dict]:
    files: list[dict] = []
    page_token = None
    query = f"'{folder_id}' in parents and trashed = false"
    while True:
        response = (
            service.files()
            .list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType)",
                pageToken=page_token,
                pageSize=1000,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return files


def download_file(service, file_id: str) -> bytes:
    from googleapiclient.http import MediaIoBaseDownload

    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


def collect_drive_files(service, folder_id: str, prefix: str = "") -> dict[str, str]:
    """Map relative repo paths to Drive file IDs."""
    mapping: dict[str, str] = {}
    for item in list_children(service, folder_id):
        name = item["name"]
        mime_type = item.get("mimeType", "")
        relative = f"{prefix}/{name}" if prefix else name

        if mime_type == FOLDER_MIME:
            if is_skipped_dir(name):
                print(f"skip folder: {relative}")
                continue
            mapping.update(collect_drive_files(service, item["id"], relative))
            continue

        if not is_allowed_file(name, mime_type):
            print(f"skip: {relative} ({mime_type})")
            continue

        if relative in mapping:
            print(f"warning: duplicate Drive name, last one wins: {relative}")
        mapping[relative] = item["id"]
    return mapping


def write_file(path: Path, content: bytes, dry_run: bool) -> str:
    existed = path.exists()
    if existed and path.read_bytes() == content:
        return "unchanged"
    if dry_run:
        return "would-update" if existed else "would-add"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return "updated" if existed else "added"


def synced_paths_under(root: Path) -> set[str]:
    paths: set[str] = set()
    for file_path in root.rglob("*"):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(root).as_posix()
        parts = Path(rel).parts
        if any(is_skipped_dir(part) for part in parts):
            continue
        if not is_allowed_file(file_path.name, ""):
            continue
        paths.add(rel)
    return paths


def sync(
    root: Path,
    service,
    folder_id: str,
    delete_missing: bool,
    dry_run: bool,
    subfolder: str = "",
) -> int:
    sync_folder_id = resolve_sync_folder_id(service, folder_id, subfolder)
    drive_files = collect_drive_files(service, sync_folder_id)
    changes = 0

    for relative, file_id in sorted(drive_files.items()):
        dest = safe_join(root, relative)
        if dest is None:
            print(f"skip unsafe path: {relative}")
            continue
        if relative.replace("\\", "/") in PRESERVE_RELATIVE_PATHS:
            print(f"preserve: {relative}")
            continue
        content = download_file(service, file_id)
        action = write_file(dest, content, dry_run)
        if action != "unchanged":
            changes += 1
        print(f"{action}: {relative}")

    if delete_missing:
        for relative in sorted(synced_paths_under(root) - set(drive_files)):
            if relative.replace("\\", "/") in PRESERVE_RELATIVE_PATHS:
                continue
            dest = safe_join(root, relative)
            if dest is None or not dest.exists():
                continue
            changes += 1
            if dry_run:
                print(f"would-delete: {relative}")
            else:
                dest.unlink()
                print(f"deleted: {relative}")

    return changes


def main() -> int:
    folder_id = os.environ.get("DRIVE_FOLDER_ID", "").strip()
    credentials = None
    try:
        credentials = load_credentials()
    except json.JSONDecodeError:
        print("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON.", file=sys.stderr)
        return 1

    if not folder_id or credentials is None:
        print(
            "Drive sync is not configured yet. Add GitHub secrets "
            "GOOGLE_SERVICE_ACCOUNT_JSON and DRIVE_FOLDER_ID, then run this workflow again."
        )
        return 0

    dry_run = os.environ.get("DRY_RUN", "").lower() in {"1", "true", "yes"}
    delete_missing = os.environ.get("DRIVE_SYNC_DELETE", "").lower() in {"1", "true", "yes"}
    subfolder = os.environ.get("DRIVE_SUBFOLDER", "").strip()
    root = repo_root()
    from googleapiclient.discovery import build

    service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    try:
        changes = sync(
            root,
            service,
            folder_id,
            delete_missing=delete_missing,
            dry_run=dry_run,
            subfolder=subfolder,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"done: {changes} change(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
