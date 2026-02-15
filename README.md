# SNES Sample Library for SongWalker

A comprehensive library of instrument samples extracted from Super Nintendo (SNES) game soundtracks, converted to the SongWalker preset format.

## Source

Samples extracted from SPC files (SNES-SPC700 sound format) using BRR (Bit Rate Reduction) decoding. The SPC files are sourced from the [SNESology](https://snesology.net) collection.

## Structure

Each game has its own directory containing:
- `preset.json` — SongWalker preset descriptor with zone mappings
- `sample_NNN.mp3` — Individual instrument samples (32kHz mono, 64kbps MP3)

## Technical Details

- **Sample Rate**: 32,000 Hz (SNES native output rate)
- **Channels**: Mono
- **Format**: MP3 (64kbps) converted from 16-bit PCM decoded from BRR
- **Deduplication**: Samples are deduplicated across all SPC files for each game using PCM fingerprinting

## Usage

These presets are compatible with the SongWalker sampler engine. Load them using the preset loader:

```
const instrument = loadPreset("SNES/GameTitle");
```

## License

Game audio samples are provided for educational and preservation purposes. All original content is copyright of their respective owners.
