# SNESology Library Refactoring Plan

## Goal

Split the monolithic one-preset-per-game library into individual instrument presets — one per unique SNES sample — plus one drum kit per game. Fix broken pitch/keyRange values, preserve ADSR envelope metadata, and use community-sourced sample names where available.

## Background: SNES Audio Architecture

The SNES S-DSP is an 8-voice sampler with fixed characteristics that directly affect how we process and present these samples:

- **32 kHz ceiling** — the DSP's maximum sample rate. All samples are already at or below this. Our extracted MP3s are 32 kHz mono.
- **BRR compression** — Bit Rate Reduction encodes 32 bytes of PCM into 9 bytes (~3.5:1). The lossy encoding adds noise, which the hardware compensates for with a **global Gaussian interpolation filter** that rolls off high frequencies. This filter is a major part of the SNES's signature sound.
- **Loop constraints** — loop start and end must be multiples of 16 samples (one BRR block). There is no sample data after the loop end point — the loop end IS the sample end.
- **Source directory (`srcn`)** — each of the 8 DSP voices references a sample via a source number (0–255) into a directory table in RAM. Each `srcn` slot is effectively one instrument. This is the most reliable way to identify distinct instruments within a game.
- **ADSR/GAIN envelopes** — each voice has hardware envelope registers: `adsr1` (attack/decay rates), `adsr2` (sustain level/release rate), and `gain` (alternative envelope mode). The same sample with different ADSR settings produces a very different instrument character (e.g., a string pad vs. a plucked sound from the same waveform).
- **Pitch register** — a 14-bit value controlling playback rate relative to the 32 kHz clock. A value of `0x1000` (4096) = native rate. This does NOT encode the musical pitch of the sample content — it encodes how fast to play it back.
- **Pre-emphasis** — some games pre-EQ samples to boost high frequencies before the Gaussian filter cuts them. This is a global setting, not per-sample.

### Reference

- [SNES Chiptune Guide — Working with SNES Samples](https://samplemance.rs/snesguide/samples/working/)
- [SNES Chiptune Guide — Preparing and Optimizing Samples](https://samplemance.rs/snesguide/samples/optimizing/)
- [Repository of SNES Sample Rips](https://samplemance.rs/snesguide/samples/repo/) — community-maintained collection with named samples per game

## Current State (Problems)

### One preset per game
Each of the ~1,514 games produces a single `preset.json` containing ALL unique BRR samples as zones in one monolithic sampler. Users can't select individual instruments — they get the entire game's sample set as one unusable blob.

### Broken rootNote/keyRange
`hz_to_midi()` converts the DSP pitch register to a MIDI note, but the pitch register encodes *playback rate*, not *musical pitch*. This produces `rootNote` values like 138, 145, 163 — well above MIDI's 0–127 range. The `keyRange` is then computed as `rootNote ± 6`, resulting in `low > high` (e.g., `{low: 132, high: 127}`), making zones unmatchable by the sampler engine.

### Discarded metadata
The extraction pipeline collects valuable per-sample metadata but throws it away:
- **`srcn`** (source directory index) — the instrument ID; used for sorting but not written
- **ADSR/GAIN registers** — collected per-voice but never stored in `preset.json`
- **Sample character** (`estimate_sample_character()` result) — only used for top-level tags
- **Per-SPC provenance** — which song files used each sample; lost during dedup

### `oneShot` is preset-level, not per-zone
If even one melodic sample exists, the entire preset (including percussion) is marked `oneShot: false`.

## Pre-Refactor: Community Sample Names (Phase 0 — complete)

We scraped the [Repository of SNES Sample Rips](https://samplemance.rs/snesguide/samples/repo/) to obtain human-assigned instrument names. The scraper (`scrape-samplemance.py`) found **194 games** with Google Drive links containing labeled `.wav` files (e.g., "flute", "horn", "bass_slap", "perc_kick").

### Status
- **194 games** downloaded and cataloged ✓
- **10,141 labeled WAV files** across 194 game folders ✓
- Community names matched and applied to generated presets ✓

### Coverage
- 148 games matched between samplemance and generated presets
- 4,010 instruments renamed with community names
- Matching by BRR PCM length (exact + ±32 sample tolerance)

### Storage
- `samplemance.rs/` — downloaded sample packs, one folder per game (gitignored)
- `docs/drive-links.json` — scraped game→drive link mapping (194 entries, committed)
- `docs/name-mapping.json` — cataloged `(game_dir) → {game_name, samples[{label, index}]}` (committed)
- `match-community-names.py` — matches community WAVs to generated presets by PCM length

## Target State

### Per-instrument presets
Each unique `srcn` becomes its own preset with one sample zone. Users can browse games and select individual instruments.

### Repository structure
```
snesology-library/
  index.json               # root index, type: "index" per game
  convert-spc.py           # extraction pipeline (RSN → per-instrument presets)
  generate-index.py        # index generator
  scrape-samplemance.py    # community sample name scraper
  README.md
  docs/
    refactoring-plan.md    # this file
    drive-links.json       # scraped samplemance game→Drive link mapping
    name-mapping.json      # community sample name catalog
  generated/               # output of convert-spc.py (auto-generated presets)
    Chrono_Trigger/
      index.json           # type: "index", lists instrument presets
      Strings_00/
        preset.json        # single-zone, rootNote=60, keyRange 0-127
        sample.mp3
      Trumpet_01/
        preset.json
        sample.mp3
      Drum_Kit/
        preset.json        # multi-zone, oneShot: true, sequential key mapping
        sample_000.mp3
        sample_001.mp3
    Final_Fantasy_VI/
      index.json
      ...
  samplemance.rs/          # community-labeled sample packs (gitignored)
    ActRasier/
      bass_slap.wav
      flute.wav
      horn.wav
      ...
    Chrono_Trigger/
      ...
```

### Fixed pitch values
All melodic instruments: `rootNote = 60` (C4), `keyRange: { low: 0, high: 127 }`. This is correct for single-sample SNES instruments — the sampler engine pitch-shifts from C4 across the keyboard, matching how SNES games actually use these samples.

### Preserved ADSR metadata
Each preset includes `metadata.snes: { adsr1, adsr2, gain, srcn, pitchRegister }`.

### Drum kit per game
Percussion-classified samples are collected into one `Drum_Kit/preset.json` with sequential key mapping from C2 (MIDI 36) and `oneShot: true`.

### Instrument naming (priority order)
1. **Community name** from name-mapping.json (e.g., "Strings", "Trumpet")
2. **Heuristic name** from classification + ADSR analysis + srcn (e.g., "Pad_00", "Pluck_01")

## Implementation Steps

### Phase 0 — Community sample names (complete)

1. ~~Scrape https://samplemance.rs/snesguide/samples/repo/ for game entries + Drive links~~ ✓ (194 games)
2. ~~Download sample packs from each Drive link~~ ✓ (194/194)
3. ~~Catalog sample filenames into `docs/name-mapping.json`~~ ✓ (10,141 samples)
4. ~~Match community samples to generated presets by PCM length~~ ✓ (4,010 renamed across 148 games)

### Phase 1 — Fix and enrich `convert-spc.py`

5. **Fix pitch handling** — default all melodic instruments to `rootNote = 60`, `keyRange: { low: 0, high: 127 }`.

6. **Preserve `srcn` per zone** — store as `"srcn": N` in zone metadata.

7. **Preserve ADSR/GAIN per zone** — store voice registers in `metadata.snes`.

8. **Store sample character per zone** — add `"character"` field.

9. **Improve classification heuristics** — use ADSR + GAIN + loop presence + existing heuristics.

### Phase 2 — Restructure output to per-instrument presets

10. **Replace `build_preset()` with `build_game_presets()`** — one preset per `srcn`, plus drum kit.

11. **Instrument naming** — use community name if available, else heuristic:
    - `melodic` + long loop → "Pad_NN"
    - `melodic` + short loop → "Lead_NN"
    - `tone` → "Tone_NN"
    - `noise` → "Noise_NN"
    - `one-shot` → "Hit_NN"
    - `percussion` → Drum_Kit
    - NN = srcn zero-padded

12. **Drum kit** — sequential mapping from C2 (MIDI 36), `oneShot: true`.

13. **Melodic preset format** — `rootNote: 60`, `keyRange: { low: 0, high: 127 }`, `oneShot: false`, ADSR in metadata.

### Phase 3 — Update `generate-index.py`

14. **Two-level index** — root entries per game → per-game entries per instrument.

15. **Enriched entries** — character tags, instrumentCount, hasDrumKit.

### Phase 4 — Update snesology-site

16. **Hierarchical browsing** — game → instruments navigation.

17. **Updated preset references** — `"SNES Sample Library/GameName/InstrumentName"`.

### Phase 5 — Run and validate

18. **Test batch**: `python3 convert-spc.py /home/ari/dev/samples/spcsets . --limit 5 -v` (outputs to `generated/`)

19. **Full run**: `python3 convert-spc.py /home/ari/dev/samples/spcsets . --verbose`

20. **Test in snesology-site** and deploy.

## Key Decisions

| Decision | Chosen | Alternative | Rationale |
|----------|--------|-------------|-----------|
| Root note | `rootNote=60` (C4) for all | FFT pitch detection | SNES instruments are single-sample, always pitch-shifted |
| Data source | Re-extract from RSN | Reorganize existing MP3s | Recovers ADSR, srcn, character data that was discarded |
| Naming | Community names + heuristic fallback | Raw srcn numbers | Human names are more browsable |
| Drum mapping | Sequential from C2 | GM-style auto-assignment | No reliable BRR→drum-type classification |
| Index structure | Hierarchical (game → instruments) | Flat with compound names | Matches songwalker-library format |
| One srcn = one preset | Yes | Group by similar ADSR | Matches SNES hardware model |

## RSN Source Files

Located at `/home/ari/dev/samples/spcsets/` (~1,500+ `.rsn` archives from [SNESology](https://snesology.net)).

## Related Files

- `convert-spc.py` — extraction pipeline (RSN → SPC → BRR → PCM → MP3 + preset.json)
- `generate-index.py` — index generator (reads `generated/`, writes `index.json`)
- `scrape-samplemance.py` — community sample name scraper (writes to `samplemance.rs/`)
- `../songwalker-js/src/preset-loader.ts` — how the site fetches and resolves presets
- `../songwalker-js/src/preset-types.ts` — TypeScript type definitions
- `../songwalker-core/src/preset.rs` — Rust preset descriptor types
- `../snesology-site/src/main.ts` — site preset browser and playback
