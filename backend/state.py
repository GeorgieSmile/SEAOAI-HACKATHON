import json
from pathlib import Path
import sqlite3

from pydantic import ValidationError

from .case_models import CaseRecord, OperationBinding
from .contracts import ListingSnapshot
from .intake import ImageData


class Store:
    """The SQLite owner for the current snapshot, image bytes, and case."""

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
                CREATE TABLE IF NOT EXISTS active_case (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    case_id TEXT,
                    revision INTEGER,
                    operation_id TEXT,
                    operation_status TEXT,
                    data TEXT
                );
                INSERT OR IGNORE INTO active_case (singleton) VALUES (1);
                CREATE TABLE IF NOT EXISTS operation_bindings (
                    operation_id TEXT PRIMARY KEY,
                    stage TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    result_operation_id TEXT NOT NULL
                );
            """)
        except BaseException:
            connection.close()
            raise
        self.connection = connection

    def close(self) -> None:
        self.connection.close()

    def begin_import(self) -> int:
        generation = self._clear_workspace(None)
        assert generation is not None
        return generation

    def reset(self, generation: int) -> bool:
        return self._clear_workspace(generation) is not None

    def _clear_workspace(self, expected_generation: int | None) -> int | None:
        with self.connection:
            cursor = self.connection.execute("""
                UPDATE active_import
                SET generation = generation + 1, snapshot_id = NULL, snapshot = NULL
                WHERE singleton = 1 AND (? IS NULL OR generation = ?)
                RETURNING generation
            """, (expected_generation, expected_generation))
            row = cursor.fetchone()
            cursor.close()
            if row is None:
                return None
            self.connection.execute("""
                UPDATE active_case SET case_id = NULL, revision = NULL, operation_id = NULL,
                    operation_status = NULL, data = NULL WHERE singleton = 1
            """)
            self.connection.execute("DELETE FROM operation_bindings")
            self.connection.execute("DELETE FROM image_sources")
            self.connection.execute("DELETE FROM image_blobs")
        return row[0]

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
        try:
            return ListingSnapshot.model_validate_json(serialized) if serialized is not None else None
        except ValidationError:
            raise sqlite3.DatabaseError("The saved snapshot could not be read.") from None

    def workspace(self) -> tuple[int, ListingSnapshot | None, CaseRecord | None]:
        generation, snapshot, case = self._workspace_rows()
        try:
            return (
                generation,
                ListingSnapshot.model_validate_json(snapshot) if snapshot is not None else None,
                CaseRecord.model_validate_json(case) if case is not None else None,
            )
        except ValidationError:
            raise sqlite3.DatabaseError("The saved workspace could not be read.") from None

    def workspace_for_recovery(self) -> tuple[int, ListingSnapshot | None, CaseRecord | None, object, object, bool]:
        """Decode the saved case separately from its potentially legacy or invalid report."""
        generation, snapshot, serialized = self._workspace_rows()
        try:
            listing = ListingSnapshot.model_validate_json(snapshot) if snapshot is not None else None
            if serialized is None:
                return generation, listing, None, None, None, False
            data = json.loads(serialized)
            if not isinstance(data, dict) or not isinstance(data.get("view"), dict):
                raise sqlite3.DatabaseError("The saved workspace could not be read.")
            report = data["view"].pop("report", None)
            saved_review = data.pop("saved_review", None)
            invalid_confirmation = False
            try:
                record = CaseRecord.model_validate(data)
            except ValidationError as error:
                confirmation_fields = {"confirmed_review_digest", "review_input"}
                locations = (
                    item["loc"] for item in error.errors(include_url=False, include_context=False, include_input=False)
                )
                if any(
                    location[:2] != ("view", "confirmed_revision")
                    and (not location or location[0] not in confirmation_fields)
                    for location in locations
                ):
                    raise
                # Confirmation data can be replaced once the buyer reviews the intact case again.
                for field in confirmation_fields:
                    data.pop(field, None)
                data["view"].pop("confirmed_revision", None)
                record = CaseRecord.model_validate(data)
                invalid_confirmation = True
            return generation, listing, record, report, saved_review, invalid_confirmation
        except (TypeError, ValueError):
            raise sqlite3.DatabaseError("The saved workspace could not be read.") from None

    def _workspace_rows(self) -> tuple[int, str | None, str | None]:
        return self.connection.execute("""
            SELECT active_import.generation, active_import.snapshot, active_case.data
            FROM active_import JOIN active_case USING (singleton)
            WHERE active_import.singleton = 1
        """).fetchone()

    def operation_binding(self, operation_id: str) -> OperationBinding | None:
        row = self.connection.execute("""
            SELECT operation_id, stage, fingerprint, case_id, revision, result_operation_id
            FROM operation_bindings WHERE operation_id = ?
        """, (operation_id,)).fetchone()
        return OperationBinding(*row) if row is not None else None

    def save_case(
        self, generation: int, previous: CaseRecord | None, case: CaseRecord,
        *, binding: OperationBinding | None = None,
    ) -> bool:
        prior = previous.view if previous is not None else None
        next_case = case.view
        operation = next_case.last_operation
        with self.connection:
            update = self.connection.execute("""
                UPDATE active_case
                SET case_id = ?, revision = ?, operation_id = ?, operation_status = ?, data = ?
                WHERE singleton = 1 AND case_id IS ? AND revision IS ?
                    AND operation_id IS ? AND operation_status IS ?
                    AND EXISTS (
                        SELECT 1 FROM active_import
                        WHERE singleton = 1 AND generation = ? AND snapshot_id = ?
                    )
            """, (
                next_case.case_id, next_case.revision, operation.operation_id, operation.status, case.model_dump_json(),
                prior.case_id if prior is not None else None,
                prior.revision if prior is not None else None,
                prior.last_operation.operation_id if prior is not None else None,
                prior.last_operation.status if prior is not None else None,
                generation, next_case.snapshot_id,
            ))
            if update.rowcount != 1:
                return False
            if binding is not None:
                self.connection.execute("""
                    INSERT INTO operation_bindings VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    binding.operation_id, binding.stage, binding.fingerprint, binding.case_id, binding.revision,
                    binding.result_operation_id,
                ))
        return True

    def image(self, snapshot_id: str, source_id: str) -> tuple[str, bytes] | None:
        return self.connection.execute("""
            SELECT image_blobs.mime, image_blobs.payload FROM active_import
            CROSS JOIN image_sources JOIN image_blobs USING (digest)
            WHERE active_import.snapshot_id = ? AND image_sources.source_id = ?
        """, (snapshot_id, source_id)).fetchone()
