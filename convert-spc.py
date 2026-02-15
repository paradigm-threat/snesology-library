#!/usr/bin/env python3
"""
convert-spc.py — Extract SNES SPC samples and build songwalker presets.

Reads RSN archives (RAR of SPC files) from a source directory,
extracts unique BRR samples, decodes them to WAV/MP3, and generates
songwalker-compatible preset.json files.

Usage:
    python3 convert-spc.py <rsn_dir> <output_dir> [--limit N] [--dry-run]

Example:
    python3 convert-spc.py /home/ari/dev/samples/spcsets ./SNES --limit 5
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


def build_preset(game_title, samples, output_dir, song_list):
    """Build a songwalker preset directory for a game's sample set."""
    preset_dir = Path(output_dir) / sanitize_dirname(game_title)
    preset_dir.mkdir(parents=True, exist_ok=True)

    zones = []
    sample_files = []

    for idx, sample in enumerate(sorted(samples, key=lambda s: s['srcn'])):
        # Determine pitch from DSP registers
        pitch_hz = 0
        if sample['voice_info']:
            # Use the most common pitch value
            pitches = [vi['pitch'] for vi in sample['voice_info'] if vi['pitch'] > 0]
            if pitches:
                # Use median pitch
                pitches.sort()
                median_pitch = pitches[len(pitches) // 2]
                pitch_hz = dsp_pitch_to_hz(median_pitch)

        midi_note = hz_to_midi(pitch_hz) if pitch_hz > 20 else 60  # Default to C4

        # Determine character
        character = estimate_sample_character(sample['pcm'], sample['has_loop'])

        # Write WAV then convert to MP3
        safe_name = f"sample_{idx:03d}"
        wav_path = preset_dir / f"{safe_name}.wav"
        mp3_path = preset_dir / f"{safe_name}.mp3"

        write_wav(sample['pcm'], wav_path)

        if convert_wav_to_mp3(wav_path, mp3_path):
            wav_path.unlink()  # Remove WAV, keep MP3
            audio_url = f"{safe_name}.mp3"
            audio_type = "external"
            codec = "mp3"
        else:
            audio_url = f"{safe_name}.wav"
            audio_type = "external"
            codec = "wav"

        # Compute file hash
        audio_path = preset_dir / audio_url
        with open(audio_path, 'rb') as f:
            file_hash = hashlib.sha256(f.read()).hexdigest()

        # Build zone
        zone = {
            "keyRange": {
                "low": max(0, midi_note - 6),
                "high": min(127, midi_note + 5)
            },
            "pitch": {
                "rootNote": midi_note,
                "fineTuneCents": 0
            },
            "sampleRate": SNES_SAMPLE_RATE,
            "audio": {
                "type": audio_type,
                "url": audio_url,
                "codec": codec,
                "sha256": file_hash
            }
        }

        # Add loop info if present
        if sample['has_loop'] and sample['loop_start_pcm'] is not None:
            zone["loop"] = {
                "start": sample['loop_start_pcm'],
                "end": sample['pcm_length']
            }

        zones.append(zone)
        sample_files.append(audio_url)

    # Build tags
    characters = [estimate_sample_character(s['pcm'], s['has_loop']) for s in samples]
    has_melodic = any(c in ('melodic', 'tone') for c in characters)
    has_percussion = any(c == 'percussion' for c in characters)

    tags = ['snes', 'spc', 'game-audio']
    if has_melodic:
        tags.append('melodic')
    if has_percussion:
        tags.append('percussion')

    # Build preset.json
    preset = {
        "format": "songwalker-preset",
        "version": 1,
        "name": game_title,
        "category": "sampler",
        "tags": tags,
        "metadata": {
            "source": "SNES",
            "platform": "Super Nintendo",
            "format": "SPC/BRR",
            "sampleRate": SNES_SAMPLE_RATE,
            "totalSamples": len(samples),
            "songs": song_list[:10],  # Cap at 10 song titles
            "license": "Game samples - for educational/preservation purposes"
        },
        "node": {
            "type": "sampler",
            "config": {
                "oneShot": not has_melodic,
                "zones": zones
            }
        }
    }

    preset_path = preset_dir / "preset.json"
    with open(preset_path, 'w') as f:
        json.dump(preset, f, indent=2)

    return str(preset_dir), len(zones)


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Convert SNES SPC samples to songwalker presets')
    parser.add_argument('rsn_dir', help='Directory containing .rsn files')
    parser.add_argument('output_dir', help='Output directory for presets')
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

    print(f"Processing {len(rsn_files)} RSN files → {output_dir}")

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    total_presets = 0
    total_samples = 0
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
                print(f"  {progress} {game_title}: {len(samples)} unique samples")
                total_presets += 1
                total_samples += len(samples)
                continue

            try:
                preset_path, zone_count = build_preset(
                    game_title, samples, output_dir, song_list
                )
                total_presets += 1
                total_samples += zone_count
                print(f"  {progress} {game_title}: {zone_count} zones → {preset_path}")
            except Exception as e:
                print(f"  {progress} {rsn_name}: ERROR: {e}", file=sys.stderr)
                errors += 1

    print(f"\n{'═' * 50}")
    print(f"  Presets created:  {total_presets}")
    print(f"  Total samples:    {total_samples}")
    print(f"  Errors:           {errors}")
    print(f"{'═' * 50}")


if __name__ == '__main__':
    main()
