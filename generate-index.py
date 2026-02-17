#!/usr/bin/env python3
"""
generate-index.py — Generate index.json for SNES sample library.

Scans the generated/ directory for per-game index.json files and creates
a root index that lists all games.

Usage:
    python3 generate-index.py [library_dir]
"""

import json
import os
import sys
from pathlib import Path


def generate_index(library_dir):
    """Scan library_dir/generated/ for per-game index.json files and generate root index.json."""
    library_dir = Path(library_dir)
    generated_dir = library_dir / 'generated'
    games = []

    if not generated_dir.is_dir():
        print(f"Error: {generated_dir} not found")
        sys.exit(1)

    for entry in sorted(generated_dir.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith('.'):
            continue

        # New format: game has its own index.json with instrument entries
        game_index_path = entry / 'index.json'
        if game_index_path.exists():
            with open(game_index_path) as f:
                game_index = json.load(f)

            name = game_index.get('name', entry.name)
            entries = game_index.get('entries', [])
            metadata = game_index.get('metadata', {})

            # Count instrument types
            instrument_count = len(entries)
            has_drum_kit = any(e.get('name') == 'Drum Kit' for e in entries)

            # Collect tags from all instruments
            all_tags = set()
            for inst in entries:
                all_tags.update(inst.get('tags', []))

            games.append({
                'type': 'index',
                'name': name,
                'path': f'generated/{entry.name}/index.json',
                'instrumentCount': instrument_count,
                'hasDrumKit': has_drum_kit,
                'tags': sorted(all_tags),
                'songs': metadata.get('songs', []),
            })
            continue

        # Legacy format: single preset.json per game
        preset_path = entry / 'preset.json'
        if preset_path.exists():
            with open(preset_path) as f:
                preset = json.load(f)

            name = preset.get('name', entry.name)
            tags = preset.get('tags', [])
            node = preset.get('node', preset.get('graph', {}))
            config = node.get('config', {}) if isinstance(node, dict) else {}
            zones = config.get('zones', [])

            games.append({
                'type': 'preset',
                'name': name,
                'path': f'generated/{entry.name}/preset.json',
                'category': preset.get('category', 'sampler'),
                'tags': tags,
                'zoneCount': len(zones),
            })

    index = {
        'format': 'songwalker-index',
        'version': 1,
        'name': 'SNES Sample Library',
        'description': 'Instrument samples extracted from Super Nintendo game soundtracks',
        'entries': games,
    }

    index_path = library_dir / 'index.json'
    with open(index_path, 'w') as f:
        json.dump(index, f, indent=2)

    new_format = sum(1 for g in games if g['type'] == 'index')
    legacy = sum(1 for g in games if g['type'] == 'preset')
    print(f"Generated {index_path}: {len(games)} games ({new_format} per-instrument, {legacy} legacy)")
    return len(games)


if __name__ == '__main__':
    lib_dir = sys.argv[1] if len(sys.argv) > 1 else '.'
    generate_index(lib_dir)
