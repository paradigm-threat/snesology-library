#!/usr/bin/env python3
"""
generate-index.py — Generate hierarchical index.json for SNES sample library.

Creates:
  - generated/index.json   — lists all auto-ripped games
  - samplemance.rs/index.json — lists all community-sourced sample folders
  - index.json (root)      — two sub-index entries pointing to the above

Usage:
    python3 generate-index.py [library_dir]
"""

import json
import sys
from pathlib import Path


def scan_generated(generated_dir: Path) -> list[dict]:
    """Scan generated/ for per-game index.json files and return entries."""
    games = []

    for entry in sorted(generated_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith('.'):
            continue

        game_index_path = entry / 'index.json'
        if game_index_path.exists():
            with open(game_index_path) as f:
                game_index = json.load(f)

            name = game_index.get('name', entry.name)
            entries = game_index.get('entries', [])
            metadata = game_index.get('metadata', {})

            instrument_count = len(entries)
            has_drum_kit = any(e.get('name') == 'Drum Kit' for e in entries)

            all_tags = set()
            for inst in entries:
                all_tags.update(inst.get('tags', []))

            games.append({
                'type': 'index',
                'name': name,
                'path': f'{entry.name}/index.json',
                'instrumentCount': instrument_count,
                'hasDrumKit': has_drum_kit,
                'tags': sorted(all_tags),
                'songs': metadata.get('songs', []),
            })

    return games


def scan_samplemance(samplemance_dir: Path) -> list[dict]:
    """Scan samplemance.rs/ for community sample folders and return entries."""
    games = []

    for entry in sorted(samplemance_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith('.'):
            continue

        # Count WAV files
        wav_files = list(entry.glob('*.wav'))
        if not wav_files:
            continue

        name = entry.name.replace('_', ' ')
        games.append({
            'type': 'folder',
            'name': name,
            'path': entry.name,
            'sampleCount': len(wav_files),
        })

    return games


def write_index(path: Path, name: str, description: str, entries: list[dict]):
    """Write a songwalker-index JSON file."""
    index = {
        'format': 'songwalker-index',
        'version': 1,
        'name': name,
        'description': description,
        'entries': entries,
    }
    with open(path, 'w') as f:
        json.dump(index, f, indent=2)
    print(f"  {path}: {len(entries)} entries")


def generate_index(library_dir):
    """Generate hierarchical index files for the SNES sample library."""
    library_dir = Path(library_dir)
    generated_dir = library_dir / 'generated'
    samplemance_dir = library_dir / 'samplemance.rs'

    # ── generated/ sub-index ──────────────────────────────
    if generated_dir.is_dir():
        generated_entries = scan_generated(generated_dir)
        write_index(
            generated_dir / 'index.json',
            'Auto-Ripped',
            'Instrument samples auto-extracted from SNES game ROMs',
            generated_entries,
        )
    else:
        generated_entries = []
        print(f"Warning: {generated_dir} not found, skipping")

    # ── samplemance.rs/ sub-index ─────────────────────────
    if samplemance_dir.is_dir():
        samplemance_entries = scan_samplemance(samplemance_dir)
        write_index(
            samplemance_dir / 'index.json',
            'Community (samplemance.rs)',
            'Community-sourced SNES samples from samplemance.rs',
            samplemance_entries,
        )
    else:
        samplemance_entries = []
        print(f"Warning: {samplemance_dir} not found, skipping")

    # ── Root index ────────────────────────────────────────
    root_entries = []
    if generated_entries:
        total_instruments = sum(e.get('instrumentCount', 0) for e in generated_entries)
        root_entries.append({
            'type': 'index',
            'name': 'Auto-Ripped',
            'path': 'generated/index.json',
            'description': 'Instrument samples auto-extracted from SNES game ROMs',
            'presetCount': total_instruments,
        })
    if samplemance_entries:
        total_samples = sum(e.get('sampleCount', 0) for e in samplemance_entries)
        root_entries.append({
            'type': 'index',
            'name': 'Community (samplemance.rs)',
            'path': 'samplemance.rs/index.json',
            'description': 'Community-sourced SNES samples from samplemance.rs',
            'presetCount': total_samples,
        })

    write_index(
        library_dir / 'index.json',
        'SNES Sample Library',
        'Instrument samples extracted from Super Nintendo game soundtracks',
        root_entries,
    )

    print(f"\nRoot: {len(root_entries)} libraries")
    if generated_entries:
        print(f"  Auto-Ripped: {len(generated_entries)} games")
    if samplemance_entries:
        print(f"  Community: {len(samplemance_entries)} games")


if __name__ == '__main__':
    lib_dir = sys.argv[1] if len(sys.argv) > 1 else '.'
    generate_index(lib_dir)
