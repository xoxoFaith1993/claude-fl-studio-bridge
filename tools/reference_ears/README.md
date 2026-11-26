# Reference Ears (optional)

Local "ears" for song references. Give it a song you want to learn from (a file, a YouTube link, or
"Artist - Title"), and it hands Claude what a producer would hear:
- tempo, key and chords;
- sections, and which parts play where;
- loudness and frequency balance;
- for a chosen moment, the lead line as notes and a `.mid` file, with bends, vibrato and phrasing.

Optional local AI models add a description in words and a style/quality "vibe check". Everything runs on your PC,
and nothing is uploaded.

From Claude: `fl_reference` (e.g. *"listen to 'Artist - Title' from 2:55 to 3:25, focus on the guitar"*).
By hand:

```powershell
<env>\Scripts\python.exe tools\reference_ears\refears.py "Artist - Title"                  # whole-song overview
<env>\Scripts\python.exe tools\reference_ears\refears.py song.mp3 --start 2:55 --end 3:25 --focus guitar
<env>\Scripts\python.exe tools\reference_ears\refears.py <link | file> [--no-ai] [--no-split] [--no-vibe] [--ask "..."]
```

## What it does

1. **Fetch:** uses your audio file, or (optional) finds and downloads the song with yt-dlp. Only download music
   you have the right to use for your own study. `reference_ears.fetch: "files"` turns downloading off.
2. **Split:** Demucs `htdemucs_6s` separates drums, bass, vocals, guitar, piano and other. On a CPU it takes a
   few minutes per song; the parts are cached, so later runs on the same song skip this.
3. **Measure:**
   - tempo (with its half and double readings);
   - key (Krumhansl profiles on the pitched parts);
   - chords per bar;
   - sections (A, B, C… for similar material, with the parts playing, level and chords of each);
   - LUFS, peak, crest, frequency balance and stereo width;
   - per part: share of energy, how much of the song it plays, width and brightness.
4. **Lead line** (with `--start`/`--end` or `--focus`): Basic Pitch finds every note, and the top line is kept.
   It then reports:
   - range, density and scale fit;
   - motifs, phrases and legato;
   - bends and vibrato;
   - tone and space.

   It writes `notes_*.mid` (drag it into FL's piano roll) and a `lead_*.png` plot that Claude can look at.
5. **Listen** (optional local AI): Qwen3-Omni runs through llama.cpp and hears 30-second clips of the mix and the
   isolated part. It describes genre, instruments, tone, playing techniques and feel.
6. **Vibe** (optional): style tags and closeness to another reference (Essentia Discogs-EffNet), plus production
   scores (Meta Audiobox Aesthetics). See [READING_THE_VIBE.md](READING_THE_VIBE.md).
7. **Report:** `report.md` + `report.json` in `<data_dir>/library/<song>/`.

## Trust levels

- **Reliable:** notes, scale, key, tempo (may come out at half or double), loudness, frequency balance. The top
  line sometimes catches a rhythm note during a pause in the lead.
- **Rough:** chords on dense or distorted mixes.
- **Second opinion only:** the listening AI's words. It does better when it's given the song title, and its tempo
  guesses are often wrong.
- **Not measured:** distortion. It can't be measured reliably from a separated part, so the AI describes it.

## Set up

It's heavy (PyTorch, and optionally a 20 GB listening model), so it lives in its own Python environment,
separate from the bridge.

1. Make an environment and install the packages:
   ```powershell
   python -m venv "$env:LOCALAPPDATA\claude-fl-studio-bridge\reference-ears\env"
   & "$env:LOCALAPPDATA\claude-fl-studio-bridge\reference-ears\env\Scripts\python.exe" -m pip install -r tools\reference_ears\requirements.txt
   ```
2. Tell the bridge where it is, in `%APPDATA%\claude-fl-studio-bridge\config.json`:
   ```json
   { "reference_ears": { "python": "%LOCALAPPDATA%\\claude-fl-studio-bridge\\reference-ears\\env\\Scripts\\python.exe",
                         "data_dir": "%LOCALAPPDATA%\\claude-fl-studio-bridge\\reference-ears" } }
   ```
   `data_dir` holds the library and models. Put it on a drive with room.
3. Optional models (each shows its licence first): `python tools\reference_ears\get_models.py --list`.
   - **Listening AI:** `get_models.py listen` (about 20 GB). Then unzip a llama.cpp Windows release
     (from github.com/ggml-org/llama.cpp/releases) into `reference_ears.llama_dir`, and set
     `"llama_args"`, e.g. `["-ngl", "99"]` to run on the GPU.
   - **Style model:** `get_models.py style --accept-noncommercial`. It's licensed for non-commercial use only.
   - **Quality model:** `pip install audiobox_aesthetics` in the environment.

`fl_doctor` doesn't check Reference Ears. If `fl_reference` fails, its error says which step is missing.

## Licences (none of these are bundled)

| Part | Licence |
|---|---|
| Demucs | MIT code; the htdemucs_6s weights are published by its authors without a stated licence |
| Basic Pitch | Apache-2.0 |
| librosa / soundfile / pyloudnorm / mido / onnxruntime / matplotlib | ISC / BSD-3 / MIT / MIT / MIT / PSF-style |
| yt-dlp (optional) / ffmpeg | Unlicense / LGPL or GPL, depending on the build |
| Qwen3-Omni-30B-A3B-Instruct (optional listening AI) | Apache-2.0 |
| llama.cpp (optional) | MIT |
| Essentia Discogs-EffNet (optional style model) | **CC BY-NC-SA 4.0: non-commercial only**, opt-in download |
| Meta Audiobox Aesthetics (optional quality model) | CC-BY-4.0 |
