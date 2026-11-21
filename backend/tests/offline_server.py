"""Launch the normal local import routes with a no-network translator for UI checks."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from backend.contracts import ListingSnapshot
from backend.main import create_app


async def offline_translation(snapshot: ListingSnapshot) -> ListingSnapshot:
    return snapshot.model_copy(update={
        "limitations": [*snapshot.limitations, "English translation is disabled in this offline test."],
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="purchase-review-") as directory:
        app = create_app(Path(directory) / "case.sqlite3", translator=offline_translation)
        uvicorn.run(app, host="127.0.0.1", port=args.port)
