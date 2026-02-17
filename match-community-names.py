#!/usr/bin/env python3
"""
match-community-names.py — Apply community sample names to generated presets.

Matches WAV files from samplemance.rs/ to generated instruments by comparing
PCM sample lengths (BRR decode produces deterministic frame counts). When a
unique match is found, renames the instrument directory and updates preset.json
with the community name.

Usage:
    python3 match-community-names.py [library_dir] [--dry-run] [-v]
"""

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import wave
from difflib import SequenceMatcher
from pathlib import Path


# ── Helpers ──────────────────────────────────────────────────────────────────

def sanitize_dirname(name: str) -> str:
    """Convert a name to a filesystem-safe directory name."""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = re.sub(r'\s+', '_', name)
    name = name.strip('._')
    return name


def prettify_label(label: str) -> str:
    """Convert a community sample label to a nice preset name.
    
    'bass_slap' -> 'Bass Slap'
    'sfx_kapow' -> 'SFX Kapow'
    'strings_violins_atk' -> 'Strings Violins Atk'
    """
    # Remove file extension if present
    label = re.sub(r'\.\w+$', '', label)
    # Split on underscores and hyphens
    parts = re.split(r'[_\-]+', label)
    # Title-case each part, but keep common abbreviations uppercase
    upper_words = {'sfx', 'fx', 'sq', 'lfo', 'hi', 'lo', 'mid'}
    result = []
    for p in parts:
        if p.lower() in upper_words:
            result.append(p.upper())
        else:
            result.append(p.capitalize())
    return ' '.join(result)


def get_wav_frames(wav_path: str) -> int:
    """Get the number of PCM frames in a WAV file."""
    try:
        with wave.open(wav_path, 'rb') as w:
            return w.getnframes()
    except Exception:
        return -1


def get_wav_pcm(wav_path: str, max_samples: int = 256) -> list:
    """Read first N PCM samples from a WAV file, normalized to [-1, 1]."""
    try:
        with wave.open(wav_path, 'rb') as w:
            n = min(w.getnframes(), max_samples)
            raw = w.readframes(n)
            sw = w.getsampwidth()
            if sw == 2:
                samples = list(struct.unpack(f'<{n}h', raw[:n * 2]))
                return [s / 32768.0 for s in samples]
            elif sw == 1:
                samples = list(struct.unpack(f'{n}B', raw[:n]))
                return [(s - 128) / 128.0 for s in samples]
    except Exception:
        pass
    return []


def decode_mp3_to_pcm(mp3_path: str, max_samples: int = 256) -> list:
    """Decode an MP3 file to raw PCM samples using ffmpeg."""
    try:
        result = subprocess.run(
            ['ffmpeg', '-i', str(mp3_path), '-f', 's16le', '-acodec', 'pcm_s16le',
             '-ac', '1', '-ar', '32000', '-'],
            capture_output=True, timeout=10
        )
        raw = result.stdout
        n = min(len(raw) // 2, max_samples)
        if n == 0:
            return []
        samples = list(struct.unpack(f'<{n}h', raw[:n * 2]))
        return [s / 32768.0 for s in samples]
    except Exception:
        return []


def pcm_correlation(a: list, b: list) -> float:
    """Compute normalized cross-correlation between two PCM buffers."""
    n = min(len(a), len(b))
    if n < 16:
        return 0.0
    a, b = a[:n], b[:n]
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    a = [x - mean_a for x in a]
    b = [x - mean_b for x in b]
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a < 1e-10 or norm_b < 1e-10:
        return 0.0
    return dot / (norm_a * norm_b)


# ── Game matching ────────────────────────────────────────────────────────────

def find_generated_dir(samp_dir: str, gen_dirs: set) -> str | None:
    """Find the generated/ directory matching a samplemance directory name."""
    if samp_dir in gen_dirs:
        return samp_dir

    # Try case-insensitive exact match
    lower_map = {g.lower(): g for g in gen_dirs}
    if samp_dir.lower() in lower_map:
        return lower_map[samp_dir.lower()]

    # Try fuzzy match
    best = max(gen_dirs, key=lambda g: SequenceMatcher(
        None, samp_dir.lower(), g.lower()
    ).ratio())
    ratio = SequenceMatcher(None, samp_dir.lower(), best.lower()).ratio()
    if ratio > 0.85:
        return best

    return None


# ── Sample matching ──────────────────────────────────────────────────────────

def match_samples(community_dir: Path, gen_game_dir: Path,
                  verbose: bool = False, use_correlation: bool = False) -> list:
    """Match community WAV files to generated instrument presets.
    
    Returns list of (community_label, gen_inst_dir, confidence).
    """
    # 1. Collect community WAV info: {filename: (label, frames, pcm)}
    community = {}
    for f in sorted(os.listdir(community_dir)):
        if not f.lower().endswith('.wav'):
            continue
        wav_path = community_dir / f
        frames = get_wav_frames(str(wav_path))
        if frames <= 0:
            continue
        label = os.path.splitext(f)[0]
        community[f] = {'label': label, 'frames': frames, 'path': wav_path}

    # 2. Collect generated instrument info: {dir_name: (pcmLength, preset_path)}
    generated = {}
    for d in sorted(os.listdir(gen_game_dir)):
        if d == 'index.json' or d.startswith('.'):
            continue
        preset_path = gen_game_dir / d / 'preset.json'
        if not preset_path.exists():
            continue
        try:
            with open(preset_path) as fp:
                preset = json.load(fp)
        except Exception:
            continue
        pcml = preset.get('metadata', {}).get('snes', {}).get('pcmLength')
        if pcml is None:
            continue
        generated[d] = {'pcmLength': pcml, 'preset_path': preset_path, 'preset': preset}

    if not community or not generated:
        return []

    # 3. Build pcmLength → [gen_dirs] index
    length_index = {}
    for gdir, info in generated.items():
        length_index.setdefault(info['pcmLength'], []).append(gdir)

    # 4. Match each community sample
    matches = []
    used_gen = set()

    # Pass 1: exact unique matches (pcmLength == frames)
    for fname, cinfo in community.items():
        candidates = length_index.get(cinfo['frames'], [])
        available = [c for c in candidates if c not in used_gen]
        if len(available) == 1:
            matches.append((cinfo['label'], available[0], 1.0))
            used_gen.add(available[0])
            if verbose:
                print(f"    EXACT: {cinfo['label']:30s} -> {available[0]}")

    # Pass 2: close matches (within ±32 samples = 2 BRR blocks)
    TOLERANCE = 32
    for fname, cinfo in community.items():
        if any(m[0] == cinfo['label'] for m in matches):
            continue  # already matched
        candidates = []
        for gdir, ginfo in generated.items():
            if gdir in used_gen:
                continue
            diff = abs(ginfo['pcmLength'] - cinfo['frames'])
            if 0 < diff <= TOLERANCE:
                candidates.append((gdir, diff))
        if len(candidates) == 1:
            gdir, diff = candidates[0]
            matches.append((cinfo['label'], gdir, 0.9))
            used_gen.add(gdir)
            if verbose:
                print(f"    CLOSE: {cinfo['label']:30s} -> {gdir} (±{diff})")

    # Pass 3: for ambiguous cases (multiple candidates at same length), use PCM correlation
    if not use_correlation:
        return matches

    for fname, cinfo in community.items():
        if any(m[0] == cinfo['label'] for m in matches):
            continue
        # Gather all close candidates
        candidates = []
        for gdir, ginfo in generated.items():
            if gdir in used_gen:
                continue
            diff = abs(ginfo['pcmLength'] - cinfo['frames'])
            if diff <= TOLERANCE:
                candidates.append((gdir, diff))

        if len(candidates) < 2:
            continue

        # Decode community WAV PCM
        comm_pcm = get_wav_pcm(str(cinfo['path']), 256)
        if len(comm_pcm) < 32:
            continue

        best_corr = -1
        best_dir = None
        for gdir, _ in candidates:
            # Find the generated MP3/WAV
            inst_dir = gen_game_dir / gdir
            audio_file = None
            for ext in ['sample.mp3', 'sample.wav']:
                if (inst_dir / ext).exists():
                    audio_file = inst_dir / ext
                    break
            if not audio_file:
                continue

            if str(audio_file).endswith('.mp3'):
                gen_pcm = decode_mp3_to_pcm(str(audio_file), 256)
            else:
                gen_pcm = get_wav_pcm(str(audio_file), 256)

            if len(gen_pcm) < 32:
                continue

            corr = pcm_correlation(comm_pcm, gen_pcm)
            if corr > best_corr:
                best_corr = corr
                best_dir = gdir

        if best_dir and best_corr > 0.5:
            matches.append((cinfo['label'], best_dir, best_corr))
            used_gen.add(best_dir)
            if verbose:
                print(f"    CORR:  {cinfo['label']:30s} -> {best_dir} (r={best_corr:.3f})")

    return matches


# ── Apply names ──────────────────────────────────────────────────────────────

def apply_community_names(gen_game_dir: Path, matches: list,
                          dry_run: bool = False, verbose: bool = False) -> int:
    """Rename instrument directories and update preset.json with community names.
    
    Returns number of instruments renamed.
    """
    renamed = 0

    for label, old_dirname, confidence in matches:
        old_dir = gen_game_dir / old_dirname
        if not old_dir.exists():
            continue

        preset_path = old_dir / 'preset.json'
        if not preset_path.exists():
            continue

        # Read existing preset
        with open(preset_path) as f:
            preset = json.load(f)

        # Extract srcn from old dir name (e.g., "Tone_02" -> 02)
        srcn_match = re.search(r'_(\d+)$', old_dirname)
        srcn_suffix = srcn_match.group(1) if srcn_match else ''

        # Build new directory name
        pretty_name = prettify_label(label)
        new_dirname = f"{sanitize_dirname(pretty_name)}_{srcn_suffix}" if srcn_suffix else sanitize_dirname(pretty_name)
        new_dir = gen_game_dir / new_dirname

        if new_dir.exists() and new_dir != old_dir:
            # Collision — add srcn to disambiguate
            new_dirname = f"{sanitize_dirname(pretty_name)}_{srcn_suffix}_v2"
            new_dir = gen_game_dir / new_dirname
            if new_dir.exists():
                continue

        # Update preset name
        preset['name'] = pretty_name
        if 'metadata' not in preset:
            preset['metadata'] = {}
        preset['metadata']['communityName'] = label
        preset['metadata']['matchConfidence'] = round(confidence, 3)

        if dry_run:
            if verbose:
                print(f"    RENAME: {old_dirname} -> {new_dirname} ({pretty_name})")
            renamed += 1
            continue

        # Write updated preset
        with open(preset_path, 'w') as f:
            json.dump(preset, f, indent=2)

        # Rename directory
        if old_dir != new_dir:
            try:
                old_dir.rename(new_dir)
                renamed += 1
            except OSError as e:
                print(f"    ERROR renaming {old_dir} -> {new_dir}: {e}", file=sys.stderr)
        else:
            renamed += 1

    return renamed


def rebuild_game_index(gen_game_dir: Path, verbose: bool = False):
    """Rebuild a game's index.json after renaming instruments."""
    index_path = gen_game_dir / 'index.json'
    if not index_path.exists():
        return

    with open(index_path) as f:
        game_index = json.load(f)

    # Rebuild entries by scanning directory
    entries = []
    for d in sorted(os.listdir(gen_game_dir)):
        if d == 'index.json' or d.startswith('.'):
            continue
        preset_path = gen_game_dir / d / 'preset.json'
        if not preset_path.exists():
            continue
        try:
            with open(preset_path) as f:
                preset = json.load(f)
        except Exception:
            continue

        entries.append({
            "type": "preset",
            "name": preset.get('name', d),
            "path": f"{d}/preset.json",
            "category": preset.get('category', 'sampler'),
            "tags": preset.get('tags', []),
        })

    game_index['entries'] = entries
    with open(index_path, 'w') as f:
        json.dump(game_index, f, indent=2)

    if verbose:
        print(f"    Rebuilt index: {len(entries)} entries")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Apply community names to generated presets')
    parser.add_argument('library_dir', nargs='?', default='.',
                        help='Library root directory (default: .)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be done without making changes')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Show detailed matching output')
    parser.add_argument('--correlate', action='store_true',
                        help='Use PCM correlation for ambiguous matches (slow)')
    args = parser.parse_args()

    lib = Path(args.library_dir)
    samplemance_dir = lib / 'samplemance.rs'
    generated_dir = lib / 'generated'

    if not samplemance_dir.is_dir():
        print(f"Error: {samplemance_dir} not found", file=sys.stderr)
        sys.exit(1)
    if not generated_dir.is_dir():
        print(f"Error: {generated_dir} not found", file=sys.stderr)
        sys.exit(1)

    # Load name mapping
    mapping_path = lib / 'docs' / 'name-mapping.json'
    if not mapping_path.exists():
        print(f"Error: {mapping_path} not found", file=sys.stderr)
        sys.exit(1)
    with open(mapping_path) as f:
        name_mapping = json.load(f)

    gen_dirs = set(os.listdir(generated_dir))

    total_games = 0
    total_matched = 0
    total_renamed = 0
    total_community_samples = 0

    print(f"{'=' * 60}")
    print(f"  Community name matching")
    print(f"  {'(DRY RUN)' if args.dry_run else ''}")
    print(f"{'=' * 60}")

    for samp_dir_name in sorted(name_mapping.keys()):
        comm_dir = samplemance_dir / samp_dir_name
        if not comm_dir.is_dir():
            continue

        gen_dir_name = find_generated_dir(samp_dir_name, gen_dirs)
        if not gen_dir_name:
            if args.verbose:
                print(f"  SKIP: {samp_dir_name} (no matching generated dir)")
            continue

        gen_game_dir = generated_dir / gen_dir_name
        if not gen_game_dir.is_dir():
            continue

        total_games += 1
        comm_wavs = [f for f in os.listdir(comm_dir) if f.lower().endswith('.wav')]
        total_community_samples += len(comm_wavs)

        if args.verbose:
            print(f"\n  [{total_games}] {samp_dir_name} -> {gen_dir_name} ({len(comm_wavs)} community WAVs)")

        # Match samples
        matches = match_samples(comm_dir, gen_game_dir,
                                verbose=args.verbose,
                                use_correlation=args.correlate)
        total_matched += len(matches)

        if not matches:
            if args.verbose:
                print(f"    No matches found")
            continue

        # Apply names
        renamed = apply_community_names(gen_game_dir, matches,
                                        dry_run=args.dry_run, verbose=args.verbose)
        total_renamed += renamed

        # Rebuild game index
        if not args.dry_run and renamed > 0:
            rebuild_game_index(gen_game_dir, verbose=args.verbose)

        if not args.verbose:
            print(f"  {samp_dir_name:45s} {len(matches):3d}/{len(comm_wavs):3d} matched, {renamed:3d} renamed")

    print(f"\n{'=' * 60}")
    print(f"  Games processed:     {total_games}")
    print(f"  Community samples:   {total_community_samples}")
    print(f"  Matched:             {total_matched}")
    print(f"  Renamed:             {total_renamed}")
    print(f"{'=' * 60}")

    if not args.dry_run and total_renamed > 0:
        print(f"\nRun 'python3 generate-index.py .' to rebuild the root index.")


if __name__ == '__main__':
    main()
