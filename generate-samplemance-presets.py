#!/usr/bin/env python3
"""
Generate songwalker presets for samplemance.rs community samples.

Reads each game's samples.zip, analyzes WAV files, and generates:
- One preset JSON per melodic instrument
- One Drum_Kit.json combining all percussion/SFX samples
- Per-game index.json
- Updated samplemance.rs/index.json
"""

import hashlib
import io
import json
import os
import re
import struct
import sys
import wave
import zipfile
from pathlib import Path


# ── Percussion Classification ──────────────────────────────────

PERC_PREFIXES = ('perc_', 'sfx_')

# Keywords that indicate percussion even without prefix
PERC_KEYWORDS = {
    'kick', 'snare', 'hat', 'hi-hat', 'hihat', 'cymbal', 'crash', 'ride',
    'tom', 'drum', 'bongo', 'conga', 'tambourine', 'shaker', 'maraca',
    'cowbell', 'clave', 'timbale', 'tabla', 'taiko', 'castanet',
    'woodblock', 'wood block', 'clap', 'rim', 'triangle',
    'bass drum', 'closed hi', 'open hi',
}


def is_percussion(filename: str) -> bool:
    """Classify a sample as percussion based on filename."""
    base = filename.lower().replace('.brr', '').replace('.wav', '')

    # Check prefixes
    if any(base.startswith(p) for p in PERC_PREFIXES):
        return True

    # Check keywords
    for kw in PERC_KEYWORDS:
        if kw in base:
            return True

    return False


def prettify_name(filename: str) -> str:
    """Convert WAV filename to a human-readable preset name.

    Examples:
        bass_picked.wav  →  Bass Picked
        perc_kick.wav    →  Kick
        sfx_bark.wav     →  Bark
        Guitar (High).brr.wav  →  Guitar (High)
    """
    base = filename
    # Strip extensions
    base = re.sub(r'\.brr\.wav$', '', base, flags=re.IGNORECASE)
    base = re.sub(r'\.wav$', '', base, flags=re.IGNORECASE)

    # Strip perc_/sfx_ prefix for drum kit zones
    base = re.sub(r'^perc_', '', base, flags=re.IGNORECASE)
    base = re.sub(r'^sfx_', '', base, flags=re.IGNORECASE)

    # Replace underscores with spaces
    base = base.replace('_', ' ')

    # Title-case, but preserve parenthetical suffixes
    base = base.strip()
    if base:
        base = base[0].upper() + base[1:]

    return base


def sanitize_filename(name: str) -> str:
    """Make a filesystem-safe filename from a sample name."""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = re.sub(r'\s+', '_', name)
    name = re.sub(r'_+', '_', name)
    name = name.strip('_.')
    return name or 'Unknown'


# ── WAV Analysis ───────────────────────────────────────────────

def parse_wav_info(wav_data: bytes) -> dict:
    """Extract sample rate, frame count, and loop points from WAV data."""
    buf = io.BytesIO(wav_data)
    with wave.open(buf) as wf:
        info = {
            'sample_rate': wf.getframerate(),
            'num_frames': wf.getnframes(),
            'channels': wf.getnchannels(),
            'sample_width': wf.getsampwidth(),
        }

    # Look for smpl chunk with loop points
    info['has_loop'] = False
    info['loop_start'] = None
    info['loop_end'] = None

    idx = wav_data.find(b'smpl')
    if idx >= 0:
        try:
            # smpl chunk layout:
            # +0: 'smpl' (4 bytes)
            # +4: chunk size (4 bytes)
            # +8: manufacturer (4) + product (4) + sample_period (4) +
            #      midi_unity_note (4) + midi_pitch_fraction (4) +
            #      smpte_format (4) + smpte_offset (4) = 28 bytes
            # +36: num_sample_loops (4)
            # +40: sampler_data_size (4)
            # +44: loop data starts
            num_loops = struct.unpack_from('<I', wav_data, idx + 36)[0]
            if num_loops > 0:
                # Each loop: cue_point_id(4) + type(4) + start(4) + end(4) + fraction(4) + play_count(4)
                loop_start = struct.unpack_from('<I', wav_data, idx + 52)[0]
                loop_end = struct.unpack_from('<I', wav_data, idx + 56)[0]
                info['has_loop'] = True
                info['loop_start'] = loop_start
                info['loop_end'] = loop_end
        except (struct.error, IndexError):
            pass

    return info


# ── Preset Generation ─────────────────────────────────────────

def generate_game_presets(game_dir: Path, game_name: str, dry_run: bool = False) -> dict:
    """Generate presets for one game from its samples.zip.

    Returns stats dict.
    """
    stats = {'melodic': 0, 'percussion': 0, 'skipped': False}

    zip_path = game_dir / 'samples.zip'
    if not zip_path.exists():
        stats['skipped'] = True
        return stats

    # Check if presets already exist (skip if index.json present)
    index_path = game_dir / 'index.json'
    if index_path.exists():
        stats['skipped'] = True
        return stats

    with zipfile.ZipFile(zip_path, 'r') as zf:
        wav_names = sorted(n for n in zf.namelist() if n.lower().endswith('.wav'))
        if not wav_names:
            return stats

        melodic_samples = []
        percussion_samples = []

        for wav_name in wav_names:
            wav_data = zf.read(wav_name)
            wav_info = parse_wav_info(wav_data)
            wav_info['filename'] = wav_name
            wav_info['sha256'] = hashlib.sha256(wav_data).hexdigest()

            if is_percussion(wav_name):
                percussion_samples.append(wav_info)
            else:
                melodic_samples.append(wav_info)

    instruments = []  # index entries

    if dry_run:
        stats['melodic'] = len(melodic_samples)
        stats['percussion'] = len(percussion_samples)
        return stats

    # ── Generate melodic presets ──
    for sample in melodic_samples:
        preset_name = prettify_name(sample['filename'])
        safe_name = sanitize_filename(preset_name)
        preset_filename = f'{safe_name}.json'

        zone = {
            'keyRange': {'low': 0, 'high': 127},
            'pitch': {'rootNote': 60, 'fineTuneCents': 0},
            'sampleRate': sample['sample_rate'],
            'audio': {
                'type': 'archive',
                'archive': 'samples.zip',
                'entry': sample['filename'],
                'codec': 'wav',
                'sha256': sample['sha256'],
            },
        }

        if sample['has_loop'] and sample['loop_start'] is not None:
            zone['loop'] = {
                'start': sample['loop_start'],
                'end': sample['loop_end'],
            }

        tags = ['snes', 'community']
        if sample['has_loop']:
            tags.append('looped')

        preset = {
            'format': 'songwalker-preset',
            'version': 1,
            'name': preset_name,
            'category': 'sampler',
            'tags': tags,
            'metadata': {
                'source': 'samplemance.rs',
                'game': game_name,
            },
            'node': {
                'type': 'sampler',
                'config': {
                    'oneShot': not sample['has_loop'],
                    'zones': [zone],
                },
            },
        }

        preset_path = game_dir / preset_filename
        with open(preset_path, 'w') as f:
            json.dump(preset, f, indent=2)
            f.write('\n')

        instruments.append({
            'type': 'preset',
            'name': preset_name,
            'path': preset_filename,
            'category': 'sampler',
            'tags': tags,
        })
        stats['melodic'] += 1

    # ── Generate drum kit ──
    if percussion_samples:
        drum_zones = []
        DRUM_BASE_NOTE = 36  # C2

        for idx, sample in enumerate(percussion_samples):
            midi_note = DRUM_BASE_NOTE + idx
            if midi_note > 127:
                break

            zone_name = prettify_name(sample['filename'])

            zone = {
                'keyRange': {'low': midi_note, 'high': midi_note},
                'pitch': {'rootNote': midi_note, 'fineTuneCents': 0},
                'sampleRate': sample['sample_rate'],
                'audio': {
                    'type': 'archive',
                    'archive': 'samples.zip',
                    'entry': sample['filename'],
                    'codec': 'wav',
                    'sha256': sample['sha256'],
                },
                'name': zone_name,
            }
            drum_zones.append(zone)

        drum_preset = {
            'format': 'songwalker-preset',
            'version': 1,
            'name': 'Drum Kit',
            'category': 'sampler',
            'tags': ['snes', 'community', 'drums', 'percussion'],
            'metadata': {
                'source': 'samplemance.rs',
                'game': game_name,
            },
            'node': {
                'type': 'sampler',
                'config': {
                    'oneShot': True,
                    'zones': drum_zones,
                },
            },
        }

        with open(game_dir / 'Drum_Kit.json', 'w') as f:
            json.dump(drum_preset, f, indent=2)
            f.write('\n')

        instruments.append({
            'type': 'preset',
            'name': 'Drum Kit',
            'path': 'Drum_Kit.json',
            'category': 'sampler',
            'tags': ['snes', 'community', 'drums', 'percussion'],
        })
        stats['percussion'] = len(percussion_samples)

    # ── Write game index ──
    game_index = {
        'format': 'songwalker-index',
        'version': 1,
        'name': game_name,
        'entries': instruments,
    }

    with open(game_dir / 'index.json', 'w') as f:
        json.dump(game_index, f, indent=2)
        f.write('\n')

    return stats


def update_root_index(samplemance_dir: Path):
    """Update samplemance.rs/index.json with proper preset counts and types."""
    game_dirs = sorted(
        d for d in samplemance_dir.iterdir()
        if d.is_dir()
    )

    entries = []
    for game_dir in game_dirs:
        game_name = game_dir.name.replace('_', ' ')
        index_path = game_dir / 'index.json'
        if index_path.exists():
            with open(index_path) as f:
                game_index = json.load(f)
            preset_count = len(game_index.get('entries', []))
            entries.append({
                'type': 'index',
                'name': game_name,
                'path': f'{game_dir.name}/index.json',
                'presetCount': preset_count,
            })
        else:
            entries.append({
                'type': 'folder',
                'name': game_name,
                'path': game_dir.name,
            })

    root_index = {
        'format': 'songwalker-index',
        'version': 1,
        'name': 'Community (samplemance.rs)',
        'description': 'Community-sourced SNES samples from samplemance.rs',
        'entries': entries,
    }

    with open(samplemance_dir / 'index.json', 'w') as f:
        json.dump(root_index, f, indent=2)
        f.write('\n')

    return len(entries)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Generate presets for samplemance.rs samples')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--limit', type=int, help='Process only N games')
    args = parser.parse_args()

    base = Path(__file__).parent
    samplemance_dir = base / 'samplemance.rs'

    if not samplemance_dir.exists():
        print('No samplemance.rs/ directory found', file=sys.stderr)
        sys.exit(1)

    game_dirs = sorted(
        d for d in samplemance_dir.iterdir()
        if d.is_dir()
    )
    if args.limit:
        game_dirs = game_dirs[:args.limit]

    total_melodic = 0
    total_percussion = 0
    total_games = 0
    total_skipped = 0

    print(f'Generating presets for {len(game_dirs)} games...')
    for i, game_dir in enumerate(game_dirs, 1):
        game_name = game_dir.name.replace('_', ' ')
        stats = generate_game_presets(game_dir, game_name, dry_run=args.dry_run)

        if stats['skipped']:
            total_skipped += 1
            continue

        total_melodic += stats['melodic']
        total_percussion += stats['percussion']
        total_games += 1

        if i % 50 == 0 or i == len(game_dirs):
            print(f'  [{i}/{len(game_dirs)}] {game_name}: '
                  f'{stats["melodic"]} melodic, {stats["percussion"]} perc')

    action = 'Would generate' if args.dry_run else 'Generated'
    drum_kits = sum(1 for d in game_dirs
                    if (d / 'Drum_Kit.json').exists() or args.dry_run)
    print(f'\n{action} {total_melodic} melodic presets + '
          f'{total_percussion} percussion samples across {total_games} games')
    if total_skipped:
        print(f'Skipped {total_skipped} already-processed games')

    # Update root index
    if not args.dry_run:
        count = update_root_index(samplemance_dir)
        print(f'Updated samplemance.rs/index.json ({count} entries)')


if __name__ == '__main__':
    main()
