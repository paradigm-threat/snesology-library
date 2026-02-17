#!/usr/bin/env python3
"""
Archive per-game samples into ZIP files to reduce file count.

For generated/: collects all sample*.mp3 files per game into samples.zip,
updates preset.json audio references to type=archive, deletes original MP3s.

For samplemance.rs/: collects all *.wav files per game into samples.zip,
deletes original WAVs.
"""

import json
import os
import sys
import zipfile
from pathlib import Path


def archive_generated_game(game_dir: Path, dry_run: bool = False) -> dict:
    """Archive all sample MP3s in a generated/ game directory."""
    stats = {"samples": 0, "presets_updated": 0, "skipped": False}

    # Already archived?
    zip_path = game_dir / "samples.zip"
    if zip_path.exists():
        stats["skipped"] = True
        return stats

    # Collect all sample files across instrument subdirectories
    sample_files: list[tuple[str, Path]] = []  # (entry_path, file_path)
    for instrument_dir in sorted(game_dir.iterdir()):
        if not instrument_dir.is_dir():
            continue
        for f in sorted(instrument_dir.iterdir()):
            if f.name.startswith("sample") and f.suffix == ".mp3":
                # Entry path is InstrumentDir/sample.mp3
                entry_path = f"{instrument_dir.name}/{f.name}"
                sample_files.append((entry_path, f))

    if not sample_files:
        return stats

    if dry_run:
        stats["samples"] = len(sample_files)
        return stats

    # Create ZIP archive
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        for entry_path, file_path in sample_files:
            zf.write(file_path, entry_path)
            stats["samples"] += 1

    # Update preset.json files to reference the archive
    for instrument_dir in sorted(game_dir.iterdir()):
        if not instrument_dir.is_dir():
            continue
        preset_path = instrument_dir / "preset.json"
        if not preset_path.exists():
            continue

        try:
            with open(preset_path, "r") as f:
                preset = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  WARNING: Could not read {preset_path}: {e}", file=sys.stderr)
            continue

        updated = update_preset_audio_refs(preset, instrument_dir.name)
        if updated:
            with open(preset_path, "w") as f:
                json.dump(preset, f, indent=2)
                f.write("\n")
            stats["presets_updated"] += 1

    # Delete original MP3 files
    for _, file_path in sample_files:
        file_path.unlink()

    return stats


def update_preset_audio_refs(preset: dict, instrument_dir_name: str) -> bool:
    """Update audio references in a preset to use archive type.
    Returns True if any references were updated."""
    updated = False
    node = preset.get("node", {})
    config = node.get("config", {})
    zones = config.get("zones", [])

    for zone in zones:
        audio = zone.get("audio", {})
        if audio.get("type") == "external":
            # Get the filename from url or path
            filename = audio.get("url") or audio.get("path")
            if not filename:
                continue
            entry_path = f"{instrument_dir_name}/{filename}"
            codec = audio.get("codec", "mp3")

            # Replace with archive reference
            zone["audio"] = {
                "type": "archive",
                "archive": "../samples.zip",
                "entry": entry_path,
                "codec": codec,
            }
            updated = True

    return updated


def archive_samplemance_game(game_dir: Path, dry_run: bool = False) -> dict:
    """Archive all WAV files in a samplemance.rs/ game directory."""
    stats = {"samples": 0, "skipped": False}

    zip_path = game_dir / "samples.zip"
    if zip_path.exists():
        stats["skipped"] = True
        return stats

    wav_files = sorted(game_dir.glob("*.wav"))
    if not wav_files:
        return stats

    if dry_run:
        stats["samples"] = len(wav_files)
        return stats

    # Create ZIP archive (STORED = no compression for WAV — already PCM)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        for wav_path in wav_files:
            zf.write(wav_path, wav_path.name)
            stats["samples"] += 1

    # Delete original WAV files
    for wav_path in wav_files:
        wav_path.unlink()

    return stats


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Archive samples into per-game ZIPs")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    parser.add_argument("--generated-only", action="store_true", help="Only process generated/")
    parser.add_argument("--samplemance-only", action="store_true", help="Only process samplemance.rs/")
    parser.add_argument("--limit", type=int, help="Process only N games (for testing)")
    args = parser.parse_args()

    base = Path(__file__).parent

    do_generated = not args.samplemance_only
    do_samplemance = not args.generated_only

    total_samples = 0
    total_presets = 0
    total_games = 0
    total_skipped = 0

    if do_generated:
        generated_dir = base / "generated"
        if generated_dir.exists():
            game_dirs = sorted(
                [d for d in generated_dir.iterdir() if d.is_dir()]
            )
            if args.limit:
                game_dirs = game_dirs[: args.limit]

            print(f"Processing {len(game_dirs)} games in generated/...")
            for i, game_dir in enumerate(game_dirs, 1):
                stats = archive_generated_game(game_dir, dry_run=args.dry_run)
                if stats["skipped"]:
                    total_skipped += 1
                    continue
                if stats["samples"] > 0:
                    total_samples += stats["samples"]
                    total_presets += stats.get("presets_updated", 0)
                    total_games += 1
                    if i % 100 == 0 or i == len(game_dirs):
                        print(f"  [{i}/{len(game_dirs)}] {game_dir.name}: "
                              f"{stats['samples']} samples archived")

    if do_samplemance:
        samplemance_dir = base / "samplemance.rs"
        if samplemance_dir.exists():
            game_dirs = sorted(
                [d for d in samplemance_dir.iterdir() if d.is_dir()]
            )
            if args.limit:
                game_dirs = game_dirs[: args.limit]

            print(f"Processing {len(game_dirs)} games in samplemance.rs/...")
            for i, game_dir in enumerate(game_dirs, 1):
                stats = archive_samplemance_game(game_dir, dry_run=args.dry_run)
                if stats["skipped"]:
                    total_skipped += 1
                    continue
                if stats["samples"] > 0:
                    total_samples += stats["samples"]
                    total_games += 1
                    if i % 50 == 0 or i == len(game_dirs):
                        print(f"  [{i}/{len(game_dirs)}] {game_dir.name}: "
                              f"{stats['samples']} samples archived")

    action = "Would archive" if args.dry_run else "Archived"
    print(f"\n{action} {total_samples} samples across {total_games} games")
    if total_presets:
        print(f"Updated {total_presets} preset files")
    if total_skipped:
        print(f"Skipped {total_skipped} already-archived games")


if __name__ == "__main__":
    main()
