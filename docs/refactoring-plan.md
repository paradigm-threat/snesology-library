# SNESology Library Refactoring Plan

## Goal

Split the monolithic one-preset-per-game library into individual instrument presets — one per unique SNES sample — plus one drum kit per game. Fix broken pitch/keyRange values, preserve ADSR envelope metadata, and use community-sourced sample names where available. Archive samples into per-game ZIPs to stay within GitHub Pages' 200K file limit.

## Background: SNES Audio Architecture

The SNES S-DSP is an 8-voice sampler with fixed characteristics that directly affect how we process and present these samples:

- **32 kHz ceiling** — the DSP's maximum sample rate. All samples are already at or below this.
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

## Completed Work

### Phase 0 — Community sample names ✅

Scraped the [Repository of SNES Sample Rips](https://samplemance.rs/snesguide/samples/repo/) to obtain human-assigned instrument names.

- **194 games** downloaded and cataloged ✓
- **10,141 labeled WAV files** across 194 game folders ✓
- 148 games matched between samplemance and generated presets ✓
- 4,010 instruments renamed with community names ✓
- Matching by BRR PCM length (exact + ±32 sample tolerance) ✓

### Phase 1 — Per-instrument presets (generated/) ✅

Refactored `convert-spc.py` to produce one preset per `srcn`, plus Drum_Kit:

- **1,514 games** processed from RSN archives ✓
- **99,789 instrument presets** generated ✓
- Fixed pitch: `rootNote=60`, `keyRange: {low:0, high:127}` ✓
- Drum kits: sequential mapping from C2 (MIDI 36), `oneShot: true` ✓
- ADSR/GAIN metadata preserved in `metadata.snes` ✓
- Community names applied where available ✓

### Phase 2 — Hierarchical indexes ✅

- Root `index.json` → 2 sub-indexes (Auto-Ripped + Community) ✓
- `generated/index.json` → 1,514 game entries ✓
- `samplemance.rs/index.json` → 194 game entries ✓
- Per-game `index.json` files with instrument lists ✓

### Phase 3 — Shared components (songwalker-js) ✅

- `PresetBrowser` moved to songwalker-js as shared component ✓
- `setupFullscreen()` utility added to songwalker-js ✓
- Both sites (songwalker-site, snesology-site) updated to import from package ✓

### Phase 4 — Archive samples into ZIPs ✅

Reduced file count from ~231K to ~103K to fit GitHub Pages' 200K limit:

- All sample audio files archived into per-game `samples.zip` (ZIP_STORED) ✓
- Instrument subdirectories flattened: `Game/Instrument.json` instead of `Game/Instrument/preset.json` ✓
- Preset audio references updated to `type: "archive"` with `archive: "samples.zip"` ✓
- `songwalker-js` updated with `fflate` dependency for browser ZIP extraction ✓
- New `AudioReference` variant: `{ type: 'archive'; archive: string; entry: string; codec?: AudioCodec }` ✓
- `PresetLoader.fetchArchive()` caches fetched+unzipped archives per URL ✓
- `convert-spc.py` updated to produce flat+archive format directly ✓

## Current Repository Structure

```
snesology-library/
  index.json               # root index → 2 sub-indexes
  convert-spc.py           # extraction pipeline (RSN → flat presets + ZIP archive)
  generate-index.py        # index generator
  archive-samples.py       # repackages loose samples into per-game ZIPs
  flatten-presets.py        # flattens instrument subdirs into game-level JSON files
  scrape-samplemance.py    # community sample name scraper
  README.md
  docs/
    refactoring-plan.md    # this file
    drive-links.json       # scraped samplemance game→Drive link mapping
    name-mapping.json      # community sample name catalog
  generated/               # output of convert-spc.py (1,514 games, ~103K files)
    Chrono_Trigger/
      index.json           # game-level index listing all instruments
      samples.zip          # all sample MP3s in one archive
      Strings_00.json      # single-zone, rootNote=60, archives ref
      Trumpet_01.json
      Drum_Kit.json        # multi-zone, oneShot: true, C2-up mapping
    Final_Fantasy_VI/
      index.json
      samples.zip
      ...
  samplemance.rs/          # community-labeled samples (194 games, gitignored)
    Doom/
      samples.zip          # all WAV samples in one archive
    Chrono_Trigger/
      samples.zip
    ...
```

## Remaining Work

### Phase 5 — Generate presets for samplemance.rs

The community library (`samplemance.rs/`) has 194 games with 10,141 WAV samples archived into per-game ZIPs, but **no preset JSON files or per-game indexes yet**. Each game directory only contains `samples.zip`.

Tasks:
1. **Write preset generator** — analyze each WAV in `samples.zip`:
   - Read WAV sample rate (encodes pitch tuning relative to 32kHz)
   - Classify as melodic vs percussion by filename prefix (`perc_`, `sfx_`)
   - Use the community filename (minus `.wav`) as the preset name
   - Generate one preset JSON per melodic instrument (rootNote=60, full keyRange)
   - Combine all `perc_*` samples into one Drum Kit preset (C2-up, oneShot)
   - Set `sampleRate` from the WAV header for correct pitch
2. **Generate per-game index.json** — list all instrument presets
3. **Update `samplemance.rs/index.json`** — change entry type from `"folder"` to `"index"` with preset counts
4. **Test in snesology-site** — verify browsing and playback
5. **Commit and push**

### Phase 6 — Deploy and validate

1. Publish songwalker-js to NPM (version bump to 0.1.4+)
2. Update snesology-site and songwalker-site dependencies
3. Deploy sites and verify preset browsing + playback
4. Verify GitHub Pages file count is under 200K

## Key Decisions

| Decision | Chosen | Alternative | Rationale |
|----------|--------|-------------|-----------|
| Root note | `rootNote=60` (C4) for all | FFT pitch detection | SNES instruments are single-sample, always pitch-shifted |
| Data source | Re-extract from RSN | Reorganize existing MP3s | Recovers ADSR, srcn, character data that was discarded |
| Naming | Community names + heuristic fallback | Raw srcn numbers | Human names are more browsable |
| Drum mapping | Sequential from C2 | GM-style auto-assignment | No reliable BRR→drum-type classification |
| Index structure | Hierarchical (game → instruments) | Flat with compound names | Matches songwalker-library format |
| One srcn = one preset | Yes | Group by similar ADSR | Matches SNES hardware model |
| Sample storage | Per-game ZIP archives | Individual files | GitHub Pages 200K file limit |
| Archive format | ZIP_STORED (no compression) | ZIP_DEFLATED | Audio is already compressed (MP3) or incompressible (WAV PCM) |
| Preset layout | Flat (Game/Instrument.json) | Nested (Game/Instrument/preset.json) | Fewer directories, simpler structure |
| Libraries | Keep generated/ and samplemance.rs/ separate | Merge into one | Different data sources, different quality levels |

## RSN Source Files

Located at `/home/ari/dev/samples/spcsets/` (~1,500+ `.rsn` archives from [SNESology](https://snesology.net)).

## Related Files

- `convert-spc.py` — extraction pipeline (RSN → SPC → BRR → PCM → flat presets + ZIP archive)
- `generate-index.py` — index generator (reads `generated/`, writes hierarchical indexes)
- `archive-samples.py` — repackages loose sample files into per-game ZIP archives
- `flatten-presets.py` — flattens instrument subdirectories into game-level JSON files
- `scrape-samplemance.py` — community sample name scraper (writes to `samplemance.rs/`)
- `../songwalker-js/src/preset-loader.ts` — fetches presets + ZIP archives with LRU caching
- `../songwalker-js/src/preset-types.ts` — TypeScript type definitions (incl. `archive` AudioReference)
- `../songwalker-core/src/preset.rs` — Rust preset descriptor types
- `../snesology-site/src/main.ts` — site preset browser and playback
