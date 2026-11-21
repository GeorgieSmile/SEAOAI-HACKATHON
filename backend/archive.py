from pathlib import PurePosixPath
from stat import S_IFDIR, S_IFMT, S_IFREG
from struct import Struct
import re
import unicodedata
import zipfile


MiB = 1024 * 1024
MAX_ZIP_BYTES = 20 * MiB
MAX_ENTRIES = 1_000
MAX_EXPANDED_BYTES = 50 * MiB
MAX_HTML_BYTES = 5 * MiB
MAX_IMAGE_BYTES = 10 * MiB
ZIP_END = Struct("<4s4H2IH")
CENTRAL_ENTRY_MIN_BYTES = 46
CENTRAL_ENTRY_FIELDS = Struct("<4H")


class IntakeError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def safe_path(name: str) -> PurePosixPath:
    normalized = unicodedata.normalize("NFC", name)
    parts = normalized.split("/")
    if (
        any(part in {"", ".", ".."} for part in parts)
        or re.match(r"[A-Za-z]:", normalized)
        or "\\" in normalized
        or any(ord(char) < 32 or ord(char) == 127 for char in normalized)
    ):
        raise IntakeError("invalid_archive", "The ZIP contains an unsafe entry path.")
    return PurePosixPath(normalized)


def preflight_directory(payload: bytes) -> None:
    """Count central entries before ZipFile allocates its entry inventory."""
    end = payload.rfind(b"PK\x05\x06", max(0, len(payload) - ZIP_END.size - 65_535))
    if end < 0 or end + ZIP_END.size > len(payload):
        raise IntakeError("invalid_archive", "The ZIP directory is unreadable.")

    _, disk, start_disk, disk_entries, entries, directory_size, directory_offset, comment_size = ZIP_END.unpack_from(
        payload, end,
    )
    if entries > MAX_ENTRIES:
        raise IntakeError("archive_limit", "The ZIP contains more than 1,000 entries.")
    start = end - directory_size
    if (
        end + ZIP_END.size + comment_size != len(payload)
        or disk != 0 or start_disk != 0 or disk_entries != entries
        or start < directory_offset
        or (end >= 20 and payload[end - 20:end - 16] == b"PK\x06\x07")
    ):
        raise IntakeError("invalid_archive", "The ZIP directory is invalid or uses unsupported volume settings.")

    position = start
    actual_entries = 0
    while position < end:
        if actual_entries == MAX_ENTRIES:
            raise IntakeError("archive_limit", "The ZIP contains more than 1,000 entries.")
        if position + CENTRAL_ENTRY_MIN_BYTES > end or payload[position:position + 4] != b"PK\x01\x02":
            raise IntakeError("invalid_archive", "The ZIP directory contains an invalid entry.")
        name_size, extra_size, note_size, entry_disk = CENTRAL_ENTRY_FIELDS.unpack_from(payload, position + 28)
        if name_size == 0 or entry_disk != 0:
            raise IntakeError("invalid_archive", "The ZIP directory contains an invalid entry.")
        position += CENTRAL_ENTRY_MIN_BYTES + name_size + extra_size + note_size
        actual_entries += 1

    if position != end or entries != actual_entries:
        raise IntakeError("invalid_archive", "The ZIP directory does not match its declared entries.")


class ListingArchive:
    """Inspect ZIP entries in place and count the bytes processed during inflation."""

    def __init__(self, archive: zipfile.ZipFile):
        self.archive = archive
        self.files: dict[PurePosixPath, zipfile.ZipInfo] = {}
        self.expanded_bytes = 0
        self.checked: set[PurePosixPath] = set()

        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES:
            raise IntakeError("archive_limit", "The ZIP contains more than 1,000 entries.")
        if sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
            raise IntakeError("archive_limit", "The expanded ZIP exceeds 50 MiB.")

        paths: dict[str, bool] = {}
        for entry in entries:
            is_directory = entry.is_dir()
            raw_name = entry.orig_filename
            path = safe_path(raw_name.removesuffix("/") if is_directory else raw_name)
            key = path.as_posix().casefold()
            file_type = S_IFMT(entry.external_attr >> 16)
            if (
                key in paths
                or (file_type and file_type != (S_IFDIR if is_directory else S_IFREG))
                or entry.flag_bits & 1
                or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
            ):
                raise IntakeError("invalid_archive", "The ZIP contains conflicting or unsupported entries.")
            paths[key] = is_directory
            if not is_directory:
                self.files[path] = entry
            elif entry.file_size:
                raise IntakeError("invalid_archive", "A ZIP directory entry contains file data.")

        for key in paths:
            if any(paths.get(parent.as_posix()) is False for parent in PurePosixPath(key).parents):
                raise IntakeError("invalid_archive", "A ZIP file conflicts with an entry directory.")

    def main_html(self) -> PurePosixPath:
        candidates = [
            path for path in self.files
            if path.suffix.lower() in {".html", ".htm"}
            and not path.name.startswith(".")
            and not any(part.casefold().endswith("_files") or part.casefold() == "__macosx" for part in path.parts[:-1])
        ]
        if len(candidates) != 1 or len(candidates[0].parts) > 2:
            raise IntakeError("unsupported_layout", "Save one TTP Cargo product page and upload its ZIP.")
        return candidates[0]

    def read(self, path: PurePosixPath, limit: int) -> bytes:
        return self._inflate(path, limit)

    def validate_remaining(self) -> None:
        for path in self.files.keys() - self.checked:
            self._inflate(path, MAX_EXPANDED_BYTES, keep=False)

    def _inflate(self, path: PurePosixPath, limit: int, *, keep: bool = True) -> bytes:
        entry = self.files[path]
        if entry.file_size > limit:
            raise IntakeError("archive_limit", "A ZIP entry exceeds the extraction limit.")
        total = 0
        payload = bytearray()
        with self.archive.open(entry) as member:
            while chunk := member.read(64 * 1024):
                total += len(chunk)
                if path not in self.checked:
                    self.expanded_bytes += len(chunk)
                if total > limit or self.expanded_bytes > MAX_EXPANDED_BYTES:
                    raise IntakeError("archive_limit", "The ZIP exceeds an extraction limit.")
                if keep:
                    payload.extend(chunk)
        if total != entry.file_size:
            raise IntakeError("invalid_archive", "The ZIP contains an incomplete entry.")
        self.checked.add(path)
        return bytes(payload)
