# generate .meta for downloads folder, archives tab

from __future__ import annotations


def _normalize_direct_url(url: object) -> str | None:
    if not isinstance(url, str) or not url:
        return None
    return url.replace("%20", " ")


def generate_meta(archive_item: dict, *, include_installed: bool = True) -> str | None:
    """Generate .meta file content for a Wabbajack downloads folder archive entry.

    Returns ``None`` when no meaningful content beyond the fixed header lines
    can be produced (i.e. the downloader type is unknown or has no useful fields).

    Content is built from the ``State`` sub-object:

    ManualDownloader:
        manualURL=<State.Url>
        prompt=<State.Prompt>

    GoogleDriveDownloader:
        directURL=https://drive.google.com/uc?id=<State.Id>&export=download

    Any other downloader with State.Url (HttpDownloader, WabbajackCDNDownloader, …):
        directURL=<State.Url>

    Any downloader with State.ModID / State.FileID (NexusDownloader, …):
        gameName is mapped from State.GameName for Nexus entries:
            Skyrim -> skyrim
            SkyrimSpecialEdition -> skyrimse
        modID=<State.ModID>
        fileID=<State.FileID>
    """
    state = archive_item.get("State")
    if not isinstance(state, dict):
        state = {}

    state_type = state.get("$type", "")

    lines = ["[General]"]
    if include_installed:
        lines.append("installed=true")

    if "ManualDownloader" in state_type:
        url = state.get("Url")
        prompt = state.get("Prompt")
        if not prompt and url:
            name = archive_item.get("Name", "archive")
            filename = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            prompt = f"Please download the following: {filename}"
        if url:
            lines.append(f"manualURL={url}")
        if prompt:
            lines.append(f"prompt={prompt}")
    elif "GoogleDriveDownloader" in state_type:
        gdrive_id = state.get("Id")
        if gdrive_id:
            direct_url = _normalize_direct_url(
                f"https://drive.google.com/uc?id={gdrive_id}&export=download"
            )
            if direct_url:
                lines.append(f"directURL={direct_url}")
    else:
        url = state.get("Url")
        if url:
            direct_url = _normalize_direct_url(url)
            if direct_url:
                lines.append(f"directURL={direct_url}")
        else:
            mod_id = state.get("ModID")
            file_id = state.get("FileID")
            if mod_id is not None or file_id is not None:
                state_game_name = state.get("GameName")
                if "NexusDownloader" in state_type:
                    if state_game_name == "Skyrim":
                        lines.append("gameName=skyrim")
                    elif state_game_name == "SkyrimSpecialEdition":
                        lines.append("gameName=skyrimse")
                    elif state_game_name is not None:
                        name = archive_item.get("Name", "archive")
                        print(
                            "[generate_meta:error] unknown Nexus GameName "
                            f"'{state_game_name}' for archive '{name}'"
                        )
                else:
                    lines.append("gameName=skyrimse")
            if mod_id is not None:
                lines.append(f"modID={mod_id}")
            if file_id is not None:
                lines.append(f"fileID={file_id}")

    # If nothing beyond the fixed header was added, report nothing to generate.
    if lines == ["[General]"] or lines == ["[General]", "installed=true"]:
        return None

    lines.append("")  # trailing newline
    return "\n".join(lines)
