"""
One-off (and safe to re-run): compute and save embeddings for every
posting in the database, so the deployed backend never has to do the
heavy embedding work itself.

Run from the project root, on your own machine, with the SAME
DATABASE_URL your Render backend uses (put it in your local .env):

    python -m scripts.backfill_embeddings

It embeds only postings that don't have a saved embedding yet (or whose
title/skills changed), saving as it goes -- so if it's interrupted, just
run it again and it picks up where it stopped. It also removes saved
embeddings for postings that no longer exist.
"""

import time

from dotenv import load_dotenv

load_dotenv()

from app.companies_store import get_all_postings, prune_posting_embeddings  # noqa: E402
from app.postings_search import build_postings_collection  # noqa: E402


def main() -> None:
    postings = get_all_postings()
    print(f"{len(postings)} postings in the database.")
    if not postings:
        print("Nothing to do -- refresh the companies first, then run this again.")
        return

    started = time.time()

    def progress(done: int, total: int) -> None:
        print(f"  embedded {done}/{total}  ({time.time() - started:.0f}s)")

    index = build_postings_collection(postings, progress=progress)
    removed = prune_posting_embeddings()
    print(
        f"Done in {time.time() - started:.0f}s. Index holds {index.count()} postings"
        f" ({index.vectors.nbytes / 1e6:.1f} MB); removed {removed} stale saved embeddings."
    )


if __name__ == "__main__":
    main()
