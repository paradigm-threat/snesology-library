#!/usr/bin/env python3
"""
generate-index.py — Generate index.json for SNES sample library.

Scans the library directory for preset.json files and creates a root index.

Usage:
    python3 generate-index.py [library_dir]
"""

import json
import os
import sys
from pathlib import Path


def generate_index(library_dir):
    """Scan library_dir for preset.json files and generate index.json."""
    library_dir = Path(library_dir)
    presets = []

    for entry in sorted(library_dir.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith('.') or entry.name in ('scripts', 'node_modules'):
            continue

        preset_path = entry / 'preset.json'
        if not preset_path.exists():
            continue

        with open(preset_path) as f:
            preset = json.load(f)

        name = preset.get('name', entry.name)
        tags = preset.get('tags', [])
        category = preset.get('category', 'sampler')
        metadata = preset.get('metadata', {})

        # Count zones
        zones = []
        node = preset.get('node', preset.get('graph', {}))
        config = node.get('config', {}) if isinstance(node, dict) else {}
        zones = config.get('zones', [])

        presets.append({
            'name': name,
            'path': entry.name,
            'category': category,
            'tags': tags,
            'zones': len(zones),
            'source': metadata.get('source', 'SNES'),
            'platform': metadata.get('platform', 'Super Nintendo'),
        })

    index = {
        'format': 'songwalker-index',
        'version': 1,
        'name': 'SNES Sample Library',
        'description': 'Instrument samples extracted from Super Nintendo game soundtracks',
        'totalPresets': len(presets),
        'presets': presets,
    }

    index_path = library_dir / 'index.json'
    with open(index_path, 'w') as f:
        json.dump(index, f, indent=2)

    print(f"Generated {index_path}: {len(presets)} presets")
    return len(presets)


if __name__ == '__main__':
    lib_dir = sys.argv[1] if len(sys.argv) > 1 else '.'
    generate_index(lib_dir)
