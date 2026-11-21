from pathlib import Path
import sqlite3

from .contracts import ListingSnapshot
from .intake import ImageData


class Store:
    """The SQLite owner for the active import and its verified image bytes."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        try:
            connection.executescript("""
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS active_import (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    generation INTEGER NOT NULL,
                    snapshot_id TEXT,
                    snapshot TEXT
                );
                INSERT OR IGNORE INTO active_import VALUES (1, 0, NULL, NULL);
                CREATE TABLE IF NOT EXISTS image_blobs (
                    digest TEXT PRIMARY KEY,
                    mime TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS image_sources (
                    source_id TEXT PRIMARY KEY,
                    digest TEXT NOT NULL REFERENCES image_blobs(digest)
                );
            """)
        except BaseException:
            connection.close()
            raise
        self.connection = connection

    def close(self) -> None:
        self.connection.close()

    def begin_import(self) -> int:
        with self.connection:
            cursor = self.connection.execute("""
                UPDATE active_import
                SET generation = generation + 1, snapshot_id = NULL, snapshot = NULL
                WHERE singleton = 1
                RETURNING generation
            """)
            generation, = cursor.fetchone()
            cursor.close()
            self.connection.execute("DELETE FROM image_sources")
            self.connection.execute("DELETE FROM image_blobs")
        return generation

    def is_current(self, generation: int) -> bool:
        current, = self.connection.execute("SELECT generation FROM active_import WHERE singleton = 1").fetchone()
        return current == generation

    def publish_import(self, generation: int, snapshot: ListingSnapshot, images: dict[str, ImageData]) -> bool:
        with self.connection:
            update = self.connection.execute("""
                UPDATE active_import SET snapshot_id = ?, snapshot = ?
                WHERE singleton = 1 AND generation = ? AND snapshot_id IS NULL
            """, (snapshot.snapshot_id, snapshot.model_dump_json(), generation))
            if update.rowcount != 1:
                return False

            blobs = {image.digest: image for image in images.values()}
            self.connection.executemany(
                "INSERT INTO image_blobs VALUES (?, ?, ?)",
                ((digest, image.mime, image.payload) for digest, image in blobs.items()),
            )
            self.connection.executemany(
                "INSERT INTO image_sources VALUES (?, ?)",
                ((source_id, image.digest) for source_id, image in images.items()),
            )
        return True

    def snapshot(self) -> ListingSnapshot | None:
        serialized, = self.connection.execute("SELECT snapshot FROM active_import WHERE singleton = 1").fetchone()
        return ListingSnapshot.model_validate_json(serialized) if serialized is not None else None

    def image(self, snapshot_id: str, source_id: str) -> tuple[str, bytes] | None:
        return self.connection.execute("""
            SELECT image_blobs.mime, image_blobs.payload FROM active_import
            CROSS JOIN image_sources JOIN image_blobs USING (digest)
            WHERE active_import.snapshot_id = ? AND image_sources.source_id = ?
        """, (snapshot_id, source_id)).fetchone()
