#!/usr/bin/env python3
"""
convert-spc.py — Extract SNES SPC samples and build songwalker presets.

Reads RSN archives (RAR of SPC files) from a source directory,
extracts unique BRR samples, decodes them to WAV/MP3, and generates
songwalker-compatible preset.json files.

Output structure (per-instrument presets):
    generated/
        Game_Name/
            index.json          # game-level index listing all instruments
            Strings_00/
                preset.json     # single-zone, rootNote=60, keyRange 0-127
                sample.mp3
            Trumpet_01/
                preset.json
                sample.mp3
            Drum_Kit/
                preset.json     # multi-zone, oneShot=true, C2-up mapping
                sample_000.mp3
                ...

Usage:
    python3 convert-spc.py <rsn_dir> <output_dir> [--limit N] [--dry-run]

Example:
    python3 convert-spc.py /home/ari/dev/samples/spcsets . --limit 5 -v
"""

import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path


# ── BRR Decoder ──────────────────────────────────────────────────────────

def decode_brr_block(block_bytes, prev1=0, prev2=0):
    """Decode a single 9-byte BRR block into 16 PCM samples."""
    if len(block_bytes) < 9:
        return [], prev1, prev2, False, False

    header = block_bytes[0]
    shift = (header >> 4) & 0x0F
    filt = (header >> 2) & 0x03
    loop_flag = bool(header & 0x02)
    end_flag = bool(header & 0x01)

    samples = []
    for i in range(1, 9):
        byte = block_bytes[i]
        for nibble_idx in range(2):
            if nibble_idx == 0:
                nib = (byte >> 4) & 0x0F
            else:
                nib = byte & 0x0F

            # Sign-extend nibble
            if nib >= 8:
                nib -= 16

            # Apply shift
            if shift <= 12:
                sample = nib << shift >> 1
            else:
                sample = (-1 if nib < 0 else 0) >> 11  # Clamp for invalid shifts

            # Apply filter
            if filt == 1:
                sample += (prev1 * 15) >> 5
            elif filt == 2:
                sample += ((prev1 * 61) >> 6) - ((prev2 * 15) >> 5)
            elif filt == 3:
                sample += ((prev1 * 115) >> 7) - ((prev2 * 13) >> 4)

            # Clamp to 16-bit signed
            sample = max(-32768, min(32767, sample))

            prev2 = prev1
            prev1 = sample
            samples.append(sample)

    return samples, prev1, prev2, end_flag, loop_flag


def decode_brr(brr_data, loop_offset=None):
    """Decode full BRR sample data into 16-bit PCM samples."""
    pcm = []
    prev1, prev2 = 0, 0
    offset = 0

    while offset + 9 <= len(brr_data):
        block = brr_data[offset:offset + 9]
        samples, prev1, prev2, end_flag, loop_flag = decode_brr_block(block, prev1, prev2)
        pcm.extend(samples)
        offset += 9

        if end_flag:
            break

    return pcm


# ── SPC Parser ───────────────────────────────────────────────────────────

# Minimum PCM sample count to keep (8 BRR blocks = 128 samples ≈ 4ms)
MIN_PCM_SAMPLES = 128
# Minimum peak amplitude (out of 32767) to consider non-silence
MIN_PEAK = 500
# For PCM-similarity dedup: compare first N samples
DEDUP_WINDOW = 256


def pcm_fingerprint(pcm, window=DEDUP_WINDOW):
    """Create a fingerprint for deduplication by hashing coarsened PCM.
    
    Quantizes to 6-bit resolution (64 levels) to tolerate minor BRR decoding
    differences across SPCs that load the same instrument.
    """
    # Normalize peak to ±1 first, then quantize to 6 bits
    peak = max(abs(s) for s in pcm[:window]) if pcm[:window] else 1
    if peak == 0:
        peak = 1
    coarse = bytes(max(0, min(63, int((s / peak + 1) * 31.5))) for s in pcm[:window])
    return hashlib.sha256(coarse).hexdigest()[:16]


def parse_spc(spc_path):
    """Parse an SPC file and extract metadata + BRR sample data."""
    with open(spc_path, 'rb') as f:
        data = f.read()

    if len(data) < 0x10200:
        return None

    # Check header
    if data[:33] != b'SNES-SPC700 Sound File Data v0.30':
        return None

    # ID666 tags
    song_title = data[0x2E:0x4E].split(b'\x00')[0].decode('ascii', errors='replace').strip()
    game_title = data[0x4E:0x6E].split(b'\x00')[0].decode('ascii', errors='replace').strip()
    dumper = data[0x6E:0x7E].split(b'\x00')[0].decode('ascii', errors='replace').strip()

    # RAM starts at offset 0x100 (64KB)
    ram = data[0x100:0x10100]

    # DSP registers at offset 0x10100 (128 bytes)
    dsp = data[0x10100:0x10180]

    if len(ram) < 0x10000 or len(dsp) < 128:
        return None

    # Source directory address from DSP register 0x5D (DIR)
    dir_page = dsp[0x5D]
    dir_addr = dir_page << 8

    # Extract sample directory (up to 256 entries)
    samples = []
    seen_hashes = set()

    for srcn in range(256):
        entry_addr = dir_addr + srcn * 4
        if entry_addr + 4 > len(ram):
            break

        start_addr = struct.unpack_from('<H', ram, entry_addr)[0]
        loop_addr = struct.unpack_from('<H', ram, entry_addr + 2)[0]

        if start_addr == 0 or start_addr >= len(ram):
            continue

        # Read BRR data from start to end flag
        brr_data = bytearray()
        offset = start_addr
        has_loop = False

        while offset + 9 <= len(ram):
            block = ram[offset:offset + 9]
            brr_data.extend(block)
            header = block[0]
            end_flag = bool(header & 0x01)
            has_loop = bool(header & 0x02)

            offset += 9
            if end_flag:
                break

            # Safety limit: 64KB max
            if len(brr_data) > 65536:
                break

        if len(brr_data) < 9:
            continue

        # Decode BRR to PCM first, then filter
        pcm = decode_brr(bytes(brr_data), loop_addr if has_loop else None)
        if not pcm or len(pcm) < MIN_PCM_SAMPLES:
            continue

        # Check if it's silence or too quiet
        peak = max(abs(s) for s in pcm)
        if peak < MIN_PEAK:
            continue

        # PCM-based fingerprint for better cross-SPC deduplication
        fp = pcm_fingerprint(pcm)
        if fp in seen_hashes:
            continue
        seen_hashes.add(fp)

        # Compute loop points in PCM samples
        loop_start_pcm = None
        if has_loop and loop_addr >= start_addr:
            brr_offset = loop_addr - start_addr
            if brr_offset % 9 == 0:
                block_idx = brr_offset // 9
                loop_start_pcm = block_idx * 16

        # Get channel ADSR/pitch info from DSP for any voice using this source
        voice_info = []
        for v in range(8):
            voice_srcn = dsp[v * 0x10 + 0x04]  # Voice SRCN register
            if voice_srcn == srcn:
                adsr1 = dsp[v * 0x10 + 0x05]
                adsr2 = dsp[v * 0x10 + 0x06]
                gain = dsp[v * 0x10 + 0x07]
                pitch_lo = dsp[v * 0x10 + 0x02]
                pitch_hi = dsp[v * 0x10 + 0x03]
                pitch_val = pitch_lo | ((pitch_hi & 0x3F) << 8)
                voice_info.append({
                    'voice': v,
                    'adsr1': adsr1,
                    'adsr2': adsr2,
                    'gain': gain,
                    'pitch': pitch_val,
                })

        samples.append({
            'srcn': srcn,
            'fingerprint': fp,
            'pcm': pcm,
            'pcm_length': len(pcm),
            'brr_length': len(brr_data),
            'has_loop': has_loop,
            'loop_start_pcm': loop_start_pcm,
            'peak': peak,
            'voice_info': voice_info,
        })

    return {
        'song_title': song_title,
        'game_title': game_title,
        'dumper': dumper,
        'samples': samples,
    }


# ── RSN Extractor ────────────────────────────────────────────────────────

def extract_rsn(rsn_path, tmpdir):
    """Extract an RSN (RAR archive) to a temp directory, return list of SPC paths."""
    result = subprocess.run(
        ['unrar', 'x', '-o+', '-y', str(rsn_path), str(tmpdir)],
        capture_output=True, timeout=30
    )
    if result.returncode != 0:
        return []

    spc_files = sorted(Path(tmpdir).glob('*.spc'))
    return spc_files


# ── Game Sample Aggregator ───────────────────────────────────────────────

def collect_game_samples(spc_files):
    """Parse all SPC files for a game, deduplicate samples by PCM fingerprint."""
    game_title = None
    all_samples = {}  # fingerprint -> sample_info
    song_list = []

    for spc_path in spc_files:
        result = parse_spc(str(spc_path))
        if result is None:
            continue

        if game_title is None and result['game_title']:
            game_title = result['game_title']

        if result['song_title']:
            song_list.append(result['song_title'])

        for sample in result['samples']:
            fp = pcm_fingerprint(sample['pcm'])
            if fp not in all_samples:
                all_samples[fp] = sample
            else:
                # Merge voice info from additional SPCs
                existing_voices = {(vi['adsr1'], vi['adsr2'], vi['pitch']) for vi in all_samples[fp]['voice_info']}
                for vi in sample['voice_info']:
                    key = (vi['adsr1'], vi['adsr2'], vi['pitch'])
                    if key not in existing_voices:
                        all_samples[fp]['voice_info'].append(vi)
                        existing_voices.add(key)

    return game_title, list(all_samples.values()), song_list


# ── Pitch Estimation from DSP Pitch Register ─────────────────────────────

def dsp_pitch_to_hz(pitch_val, sample_rate=32000):
    """Convert a SNES DSP pitch register value to Hz.

    The DSP pitch register is a 14-bit value where 0x1000 (4096) = 32000 Hz playback.
    Frequency = pitch_val * sample_rate / 4096
    """
    if pitch_val == 0:
        return 0
    return pitch_val * sample_rate / 4096.0


def hz_to_midi(hz):
    """Convert frequency in Hz to MIDI note number."""
    import math
    if hz <= 0:
        return 0
    return round(69 + 12 * math.log2(hz / 440.0))


def midi_note_name(midi):
    """Convert MIDI note number to note name."""
    if midi < 0 or midi > 127:
        return f"?{midi}"
    names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    octave = (midi // 12) - 1
    note = names[midi % 12]
    return f"{note}{octave}"


# ── WAV/MP3 Writer ───────────────────────────────────────────────────────

SNES_SAMPLE_RATE = 32000

def write_wav(pcm_samples, wav_path, sample_rate=SNES_SAMPLE_RATE):
    """Write 16-bit mono PCM samples to a WAV file."""
    with wave.open(str(wav_path), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f'<{len(pcm_samples)}h', *pcm_samples))


def convert_wav_to_mp3(wav_path, mp3_path, sample_rate=SNES_SAMPLE_RATE):
    """Convert WAV to MP3 using ffmpeg. Returns True if successful."""
    result = subprocess.run([
        'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
        '-i', str(wav_path),
        '-codec:a', 'libmp3lame',
        '-b:a', '64k',  # 64kbps is fine for 32kHz mono samples
        '-ar', str(sample_rate),
        '-ac', '1',
        str(mp3_path)
    ], capture_output=True, timeout=30)
    return result.returncode == 0


# ── Preset Builder ───────────────────────────────────────────────────────

def sanitize_dirname(name):
    """Make a filesystem-safe directory name."""
    # Replace problematic characters
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = re.sub(r'\s+', '_', name)
    name = re.sub(r'_+', '_', name)
    name = name.strip('_.')
    return name or 'Unknown'


def estimate_sample_character(pcm, has_loop):
    """Rough classification of a sample's character based on waveform."""
    if not pcm:
        return 'unknown'

    length = len(pcm)

    # Very short + no loop = likely percussion
    if length < 800 and not has_loop:
        return 'percussion'

    # Short looped = likely a sustained tone
    if has_loop and length < 4000:
        return 'tone'

    # Check for noise-like content (high zero-crossing rate)
    crossings = 0
    for i in range(1, min(length, 2000)):
        if (pcm[i] >= 0) != (pcm[i-1] >= 0):
            crossings += 1
    crossing_rate = crossings / min(length, 2000)

    if crossing_rate > 0.4:
        return 'noise'
    elif has_loop:
        return 'melodic'
    else:
        return 'one-shot'


def build_preset(game_title, samples, output_dir, song_list, name_mapping=None):
    """Build per-instrument songwalker presets for a game's sample set.
    
    Creates one preset directory per srcn (instrument), plus a Drum_Kit if
    percussion samples exist.
    
    Returns (game_dir_path, instrument_count).
    """
    game_dirname = sanitize_dirname(game_title)
    game_dir = Path(output_dir) / 'generated' / game_dirname
    game_dir.mkdir(parents=True, exist_ok=True)

    sorted_samples = sorted(samples, key=lambda s: s['srcn'])

    # Classify all samples
    melodic_samples = []
    percussion_samples = []
    for sample in sorted_samples:
        character = estimate_sample_character(sample['pcm'], sample['has_loop'])
        sample['character'] = character
        if character == 'percussion':
            percussion_samples.append(sample)
        else:
            melodic_samples.append(sample)

    instruments = []  # list of {name, dir_name, preset_path} for game index

    # ── Build one preset per melodic instrument ──
    for sample in melodic_samples:
        instrument_name = _get_instrument_name(
            sample, game_dirname, name_mapping
        )
        inst_dirname = f"{sanitize_dirname(instrument_name)}_{sample['srcn']:02d}"
        inst_dir = game_dir / inst_dirname
        inst_dir.mkdir(parents=True, exist_ok=True)

        # Write sample audio
        audio_url, codec = _write_sample_audio(sample['pcm'], inst_dir, "sample")

        # Compute file hash
        with open(inst_dir / audio_url, 'rb') as f:
            file_hash = hashlib.sha256(f.read()).hexdigest()

        # Build single-zone preset
        zone = {
            "keyRange": {"low": 0, "high": 127},
            "pitch": {"rootNote": 60, "fineTuneCents": 0},
            "sampleRate": SNES_SAMPLE_RATE,
            "audio": {
                "type": "external",
                "url": audio_url,
                "codec": codec,
                "sha256": file_hash,
            },
        }
        if sample['has_loop'] and sample['loop_start_pcm'] is not None:
            zone["loop"] = {
                "start": sample['loop_start_pcm'],
                "end": sample['pcm_length'],
            }

        # ADSR metadata from voices
        snes_meta = _build_snes_metadata(sample)

        preset = {
            "format": "songwalker-preset",
            "version": 1,
            "name": instrument_name,
            "category": "sampler",
            "tags": _sample_tags(sample),
            "metadata": {
                "source": "SNES",
                "game": game_title,
                "snes": snes_meta,
            },
            "node": {
                "type": "sampler",
                "config": {
                    "oneShot": sample['character'] == 'one-shot',
                    "zones": [zone],
                },
            },
        }

        preset_path = inst_dir / "preset.json"
        with open(preset_path, 'w') as f:
            json.dump(preset, f, indent=2)

        instruments.append({
            "type": "preset",
            "name": instrument_name,
            "path": f"{inst_dirname}/preset.json",
            "category": "sampler",
            "tags": _sample_tags(sample),
        })

    # ── Build drum kit ──
    if percussion_samples:
        drum_dir = game_dir / "Drum_Kit"
        drum_dir.mkdir(parents=True, exist_ok=True)

        drum_zones = []
        DRUM_BASE_NOTE = 36  # C2

        for idx, sample in enumerate(percussion_samples):
            midi_note = DRUM_BASE_NOTE + idx
            if midi_note > 127:
                break

            audio_url, codec = _write_sample_audio(
                sample['pcm'], drum_dir, f"sample_{idx:03d}"
            )
            with open(drum_dir / audio_url, 'rb') as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()

            zone = {
                "keyRange": {"low": midi_note, "high": midi_note},
                "pitch": {"rootNote": midi_note, "fineTuneCents": 0},
                "sampleRate": SNES_SAMPLE_RATE,
                "audio": {
                    "type": "external",
                    "url": audio_url,
                    "codec": codec,
                    "sha256": file_hash,
                },
            }
            drum_zones.append(zone)

        drum_preset = {
            "format": "songwalker-preset",
            "version": 1,
            "name": "Drum Kit",
            "category": "sampler",
            "tags": ["snes", "drums", "percussion"],
            "metadata": {
                "source": "SNES",
                "game": game_title,
            },
            "node": {
                "type": "sampler",
                "config": {
                    "oneShot": True,
                    "zones": drum_zones,
                },
            },
        }

        with open(drum_dir / "preset.json", 'w') as f:
            json.dump(drum_preset, f, indent=2)

        instruments.append({
            "type": "preset",
            "name": "Drum Kit",
            "path": "Drum_Kit/preset.json",
            "category": "sampler",
            "tags": ["snes", "drums", "percussion"],
        })

    # ── Build game-level index ──
    game_index = {
        "format": "songwalker-index",
        "version": 1,
        "name": game_title,
        "type": "index",
        "metadata": {
            "source": "SNES",
            "platform": "Super Nintendo",
            "songs": song_list[:10],
        },
        "entries": instruments,
    }

    with open(game_dir / "index.json", 'w') as f:
        json.dump(game_index, f, indent=2)

    return str(game_dir), len(instruments)


def _get_instrument_name(sample, game_dirname, name_mapping):
    """Get instrument name from community mapping or heuristic fallback."""
    # TODO: match by PCM fingerprint against community samples
    # For now, use heuristic based on character
    character = sample.get('character', 'unknown')
    if character == 'melodic':
        if sample['has_loop']:
            loop_len = (sample['pcm_length'] - (sample.get('loop_start_pcm') or 0))
            if loop_len > 2000:
                return "Pad"
            else:
                return "Lead"
        return "Melodic"
    elif character == 'tone':
        return "Tone"
    elif character == 'noise':
        return "Noise"
    elif character == 'one-shot':
        return "Hit"
    else:
        return "Sample"


def _write_sample_audio(pcm, output_dir, name_base):
    """Write PCM to WAV then convert to MP3. Returns (filename, codec)."""
    wav_path = output_dir / f"{name_base}.wav"
    mp3_path = output_dir / f"{name_base}.mp3"

    write_wav(pcm, wav_path)

    if convert_wav_to_mp3(wav_path, mp3_path):
        wav_path.unlink()
        return f"{name_base}.mp3", "mp3"
    else:
        return f"{name_base}.wav", "wav"


def _build_snes_metadata(sample):
    """Build SNES-specific metadata dict from sample info."""
    meta = {
        "srcn": sample['srcn'],
        "character": sample.get('character', 'unknown'),
        "pcmLength": sample['pcm_length'],
        "brrLength": sample['brr_length'],
        "hasLoop": sample['has_loop'],
    }
    if sample.get('loop_start_pcm') is not None:
        meta["loopStartPcm"] = sample['loop_start_pcm']

    # Use first voice info for ADSR (representative)
    if sample['voice_info']:
        vi = sample['voice_info'][0]
        meta["adsr1"] = vi['adsr1']
        meta["adsr2"] = vi['adsr2']
        meta["gain"] = vi['gain']
        meta["pitchRegister"] = vi['pitch']
    return meta


def _sample_tags(sample):
    """Generate tags for a single instrument sample."""
    tags = ["snes"]
    character = sample.get('character', 'unknown')
    if character == 'melodic':
        tags.append("melodic")
    elif character == 'tone':
        tags.extend(["melodic", "tone"])
    elif character == 'noise':
        tags.append("noise")
    elif character == 'one-shot':
        tags.append("one-shot")
    elif character == 'percussion':
        tags.append("percussion")
    if sample['has_loop']:
        tags.append("looped")
    return tags


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Convert SNES SPC samples to songwalker presets')
    parser.add_argument('rsn_dir', help='Directory containing .rsn files')
    parser.add_argument('output_dir', help='Output directory (presets written to <output_dir>/generated/)')
    parser.add_argument('--limit', type=int, default=0, help='Process only N RSN files (0=all)')
    parser.add_argument('--dry-run', action='store_true', help='Parse only, don\'t write files')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    args = parser.parse_args()

    rsn_dir = Path(args.rsn_dir)
    output_dir = Path(args.output_dir)

    if not rsn_dir.is_dir():
        print(f"Error: {rsn_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    rsn_files = sorted(rsn_dir.glob('*.rsn'))
    if not rsn_files:
        print(f"No .rsn files found in {rsn_dir}", file=sys.stderr)
        sys.exit(1)

    if args.limit > 0:
        rsn_files = rsn_files[:args.limit]

    # Load community name mapping if available
    name_mapping_path = Path(__file__).parent / 'docs' / 'name-mapping.json'
    name_mapping = None
    if name_mapping_path.exists():
        with open(name_mapping_path) as f:
            name_mapping = json.load(f)
        print(f"Loaded community name mapping: {len(name_mapping)} games")

    print(f"Processing {len(rsn_files)} RSN files → {output_dir}/generated/")

    if not args.dry_run:
        (output_dir / 'generated').mkdir(parents=True, exist_ok=True)

    total_games = 0
    total_instruments = 0
    errors = 0

    for i, rsn_path in enumerate(rsn_files):
        rsn_name = rsn_path.stem
        progress = f"[{i+1}/{len(rsn_files)}]"

        with tempfile.TemporaryDirectory() as tmpdir:
            spc_files = extract_rsn(rsn_path, tmpdir)
            if not spc_files:
                if args.verbose:
                    print(f"  {progress} {rsn_name}: no SPC files extracted")
                errors += 1
                continue

            game_title, samples, song_list = collect_game_samples(spc_files)
            if not game_title:
                game_title = rsn_name
            if not samples:
                if args.verbose:
                    print(f"  {progress} {rsn_name}: no samples found")
                continue

            if args.verbose:
                print(f"  {progress} {game_title}: {len(samples)} samples from {len(spc_files)} SPCs")

            if args.dry_run:
                # Count melodic vs percussion
                chars = [estimate_sample_character(s['pcm'], s['has_loop']) for s in samples]
                n_perc = sum(1 for c in chars if c == 'percussion')
                n_mel = len(samples) - n_perc
                drum_str = f" + drum kit ({n_perc})" if n_perc else ""
                print(f"  {progress} {game_title}: {n_mel} instruments{drum_str}")
                total_games += 1
                total_instruments += n_mel + (1 if n_perc else 0)
                continue

            try:
                game_path, inst_count = build_preset(
                    game_title, samples, output_dir, song_list, name_mapping
                )
                total_games += 1
                total_instruments += inst_count
                print(f"  {progress} {game_title}: {inst_count} instruments → {game_path}")
            except Exception as e:
                print(f"  {progress} {rsn_name}: ERROR: {e}", file=sys.stderr)
                import traceback
                if args.verbose:
                    traceback.print_exc()
                errors += 1

    print(f"\n{'═' * 50}")
    print(f"  Games processed:  {total_games}")
    print(f"  Total instruments: {total_instruments}")
    print(f"  Errors:           {errors}")
    print(f"{'═' * 50}")


if __name__ == '__main__':
    main()
