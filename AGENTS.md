# Agent instructions

SNES sample library for SongWalker — presets extracted from SPC/BRR sources.

---

## Git and go-live

Catalog repo **`snesology-library`** · branch **`main`** · publish origin **`iad1`**.

| Step | Action |
|------|--------|
| 1 | Checkout **`main`**. |
| 2 | **`git push iad1 HEAD:main`** first. |
| 3 | **`git push gitlab HEAD:main`**. |

**Structure:** each game directory has **`preset.json`** + **`sample_NNN.mp3`**. See README for extraction/dedup rules. Refactoring notes: [`docs/refactoring-plan.md`](docs/refactoring-plan.md).

*Last updated: 2026-08-23*
