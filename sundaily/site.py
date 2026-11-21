"""Publish one night's output into the gallery tree (the gh-pages branch)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .util import log

SUBDIRS = ("clips", "posters", "cards", "tickets", "data")


def rebuild_index(gallery: Path, keep_clips: int = 0) -> list[dict]:
    """Regenerate data/cards.json (newest first) from cards/*.json.

    With keep_clips > 0, MP4 files older than the newest ``keep_clips`` are removed from the
    tree and their cards marked ``archived`` (the poster and the card stay).
    """
    cards = []
    for path in sorted((gallery / "cards").glob("*.json")):
        try:
            cards.append(json.loads(path.read_text(encoding="utf-8")))
        except ValueError:
            log.warning("skipping unreadable card %s", path.name)
    cards.sort(key=lambda c: c.get("date", ""), reverse=True)
    for i, card in enumerate(cards):
        clip = gallery / card["clip"]["file"]
        if keep_clips and i >= keep_clips and clip.exists():
            clip.unlink()
        card["clip"]["archived"] = not clip.exists()
    (gallery / "data").mkdir(parents=True, exist_ok=True)
    (gallery / "data" / "cards.json").write_text(
        json.dumps(cards, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if cards:
        (gallery / "data" / "latest.json").write_text(
            json.dumps(cards[0], ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return cards


def publish(out: Path, gallery: Path, template: Path, keep_clips: int = 0) -> dict:
    """Copy clip, poster, card and ticket from a run's output into the gallery and re-index."""
    out, gallery, template = Path(out), Path(gallery), Path(template)
    card = json.loads((out / "card.json").read_text(encoding="utf-8"))
    date = card["date"]
    for sub in SUBDIRS:
        (gallery / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy2(out / "clip.mp4", gallery / "clips" / f"{date}.mp4")
    shutil.copy2(out / "poster.jpg", gallery / "posters" / f"{date}.jpg")
    shutil.copy2(out / "card.json", gallery / "cards" / f"{date}.json")
    shutil.copy2(out / "ticket.json", gallery / "tickets" / f"{date}.json")
    for item in template.iterdir():                     # the static site itself
        if item.is_file():
            shutil.copy2(item, gallery / item.name)
    (gallery / ".nojekyll").touch()
    cards = rebuild_index(gallery, keep_clips)
    log.info("published %s (%s); gallery holds %d clips", date, card["title"], len(cards))
    return card
