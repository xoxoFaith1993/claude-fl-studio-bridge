# Reading the vibe check

`vibe.py` gives Claude model "ears" for what plain measurements miss. It runs at the end of every Reference Ears
report (`--no-vibe` skips it, `--vibe-ref file` adds a closeness score), or alone:

    python tools\reference_ears\vibe.py "label=song.mp3" ... [--ref reference.mp3] [--watch Electronic---House,...]

It takes about a minute per new song and is cached after that (`<data_dir>/library/_vibe_cache`).

## The three ears

| Ear | Model | How much to trust it |
|---|---|---|
| **Style meter** | Essentia Discogs-EffNet (400 Discogs styles, 0-1) | **High** for the genre family; individual style scores are relative. |
| **Closeness** | Discogs-EffNet 1280-d embeddings, cosine | **Good** for "is this in the same world as the reference". |
| **Quality** | Meta Audiobox Aesthetics (PQ, PC, CE, CU; 1-10) | **Low for verdicts.** See below. |

LAION CLAP (larger_clap_music) was tried and isn't used: its text side gave nearly identical scores to every
prompt.

## Rules of thumb (from testing on known-genre tracks)

- **Genre target:** compare your track's score for the reference's main style with the reference's own score.
  A third of the way there usually matches "right idea, far from it".
- **Closeness:** about 0.8-0.95 for the same artist, about 0.75 for the same genre family, and 0.3-0.45 for unrelated
  genres. 0.6-0.7 is the "close but not it" zone; aim for 0.75 or more.
- **Audiobox PQ (production quality) rewards clean, not good.** Clean, uncompressed renders can score above
  professional masters. Only a big drop means something (a degraded or broken render).
- **Audiobox PC (production complexity) is the useful Audiobox number.** Sparse, simple arrangements score
  clearly lower than full professional productions. Around 6 or more is typical for full non-orchestral mixes.
- None of these measure taste. A track can hit every number and still be dull. Your ears decide; these only catch
  obvious misses early.
