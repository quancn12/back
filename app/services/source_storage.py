"""Raw-source storage abstraction: local now, S3-compatible implementation later."""
from __future__ import annotations

import hashlib
import re
import shutil
import unicodedata
from pathlib import Path

from app.core.config import settings


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_key(value: str) -> str:
    """Turn a caller-supplied source key into a relative storage path.

    Slashes are kept on purpose: real keys mirror the corpus folders
    (``ho-thac-ba/hvnt-1n.pdf``) and existing documents are stored under those
    paths. What must never survive is a segment made only of dots. The old
    version stripped dots from the ends of the whole key but kept them in the
    middle, so ``a/../../x`` came out unchanged and wrote outside the storage
    root. For every key without such a segment the output is identical to
    before, so no stored document moves.
    """
    normalized = unicodedata.normalize("NFD", value.lower())
    normalized = "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    ).replace("đ", "d")
    normalized = re.sub(r"[^a-z0-9._/-]+", "-", normalized)
    normalized = re.sub(r"-+", "-", normalized)
    segments = [
        segment
        for segment in normalized.split("/")
        if segment and set(segment) != {"."}
    ]
    return "/".join(segments).strip("-./") or "document"


class LocalSourceStorage:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or settings.source_storage_dir).resolve()

    def store(self, source_path: Path, source_key: str, content_hash: str) -> Path:
        if source_path.suffix.lower() != ".pdf":
            raise ValueError(f"Chỉ hỗ trợ PDF: {source_path.name}")
        destination_dir = (self.root / safe_key(source_key)).resolve()
        # Second line of defence, checked before anything touches the disk:
        # whatever safe_key produces, and whatever symlinks sit under the root,
        # the resolved directory must still be inside it.
        if not destination_dir.is_relative_to(self.root):
            raise ValueError(f"Khóa nguồn không hợp lệ: {source_key!r}")
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"{content_hash}.pdf"
        if not destination.exists():
            shutil.copy2(source_path, destination)
        return destination


source_storage = LocalSourceStorage()
