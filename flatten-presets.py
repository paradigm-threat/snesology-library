#!/usr/bin/env python3
"""
Flatten instrument directories: move preset.json up into the game folder.

Before:  generated/Game/Instrument_01/preset.json
After:   generated/Game/Instrument_01.json

Also updates:
- archive refs: ../samples.zip → samples.zip
- index.json entry paths: Instrument_01/preset.json → Instrument_01.json
"""

import json
import os
import shutil
import sys
from pathlib import Path


def flatten_game(game_dir: Path, dry_run: bool = False) -> dict:
    stats = {"moved": 0, "skipped": 0, "errors": 0}

    # Find instrument subdirectories (skip index.json, samples.zip, etc.)
    instrument_dirs = sorted(
        d for d in game_dir.iterdir()
        if d.is_dir()
    )

    for inst_dir in instrument_dirs:
        preset_path = inst_dir / "preset.json"
        if not preset_path.exists():
            stats["skipped"] += 1
            continue

        target_path = game_dir / f"{inst_dir.name}.json"

        # Read and update the preset
        try:
            with open(preset_path, "r") as f:
                preset = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ERROR reading {preset_path}: {e}", file=sys.stderr)
            stats["errors"] += 1
            continue

        # Update archive references: ../samples.zip → samples.zip
        updated = _update_archive_paths(preset)

        if dry_run:
            stats["moved"] += 1
            continue

        # Write to new location
        with open(target_path, "w") as f:
            json.dump(preset, f, indent=2)
            f.write("\n")

        # Remove old directory
        shutil.rmtree(inst_dir)
        stats["moved"] += 1

    # Update index.json paths
    index_path = game_dir / "index.json"
    if index_path.exists() and not dry_run:
        try:
            with open(index_path, "r") as f:
                index = json.load(f)

            for entry in index.get("entries", []):
                path = entry.get("path", "")
                # Instrument_01/preset.json → Instrument_01.json
                if path.endswith("/preset.json"):
                    entry["path"] = path.replace("/preset.json", ".json")

            with open(index_path, "w") as f:
                json.dump(index, f, indent=2)
                f.write("\n")
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ERROR updating index: {e}", file=sys.stderr)
            stats["errors"] += 1

    return stats


def _update_archive_paths(preset: dict) -> bool:
    """Change ../samples.zip → samples.zip in audio refs."""
    updated = False
    node = preset.get("node", {})
    config = node.get("config", {})
    zones = config.get("zones", [])

    for zone in zones:
        audio = zone.get("audio", {})
        if audio.get("type") == "archive" and audio.get("archive") == "../samples.zip":
            audio["archive"] = "samples.zip"
            updated = True
        # Also handle external refs that might still exist
        elif audio.get("type") == "external":
            url = audio.get("url") or audio.get("path")
            if url and "/" in url:
                # e.g. sample.mp3 stays the same (already relative to preset)
                pass

    return updated


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Flatten instrument dirs into preset JSON files")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, help="Process only N games")
    args = parser.parse_args()

    base = Path(__file__).parent
    generated_dir = base / "generated"

    if not generated_dir.exists():
        print("No generated/ directory found", file=sys.stderr)
        sys.exit(1)

    game_dirs = sorted(d for d in generated_dir.iterdir() if d.is_dir())
    if args.limit:
        game_dirs = game_dirs[: args.limit]

    total_moved = 0
    total_errors = 0

    print(f"Flattening {len(game_dirs)} games...")
    for i, game_dir in enumerate(game_dirs, 1):
        stats = flatten_game(game_dir, dry_run=args.dry_run)
        total_moved += stats["moved"]
        total_errors += stats["errors"]
        if i % 100 == 0 or i == len(game_dirs):
            print(f"  [{i}/{len(game_dirs)}] {game_dir.name}: {stats['moved']} presets flattened")

    action = "Would flatten" if args.dry_run else "Flattened"
    print(f"\n{action} {total_moved} presets across {len(game_dirs)} games")
    if total_errors:
        print(f"Errors: {total_errors}")


if __name__ == "__main__":
    main()
