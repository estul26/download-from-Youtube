# download-from-Youtube

An interactive Bash downloader for macOS. It downloads YouTube video, audio,
and captions, and it can permanently burn two subtitle languages into one MP4.

## What it can download

Run the script and choose a mode:

```text
========================================
         YouTube CLI Downloader
========================================

Choose download type:
  1) MP4 video  - best Mac/iPhone compatibility
  2) MKV video  - absolute best available quality
  3) MP3 audio
  4) Subtitles   - SRT captions (official or auto-generated)
  5) Dual subtitles burned into video

Type [1-5]:
```

The menu now appears before the YouTube URL prompt. This lets option 5 work
with either YouTube or files already on your Mac.

## Install and run

Download `ytgrab.sh`, open Terminal, and make it executable:

```bash
cd ~/Downloads
chmod +x ytgrab.sh
./ytgrab.sh
```

Options 1 through 4 require:

```bash
brew install yt-dlp ffmpeg
```

Option 5 also requires FFmpeg's subtitle-rendering libraries, Noto Sans, and
[UKIJ Tuz Tom](https://ukij.org/fonts/):

```bash
brew install ffmpeg-full
brew install --cask font-noto-sans
```

Download UKIJ Tuz Tom from the linked font page, then open `UKIJTuT.ttf` in
Font Book and choose **Install**.

`ffmpeg-full` is a keg-only Homebrew formula. The script locates it directly,
so you do not need to change your shell's `PATH`. It verifies the
`subtitles`/libass filter, `ffprobe`, Apple VideoToolbox encoding, Songti SC,
Noto Sans, and UKIJ Tuz Tom before starting option 5. It never installs
software by itself.

Update the tools later with:

```bash
brew upgrade yt-dlp ffmpeg ffmpeg-full
```

## Downloaded videos, audio, and captions

Options 1 through 4 keep their original behavior:

- MP4 prefers MP4 video and M4A audio for Mac, QuickTime, and iPhone support.
- MKV accepts the best available YouTube video and audio codecs.
- MP3 offers 320, 192, or 128 kbps conversion.
- Subtitle mode downloads creator captions when available, otherwise automatic
  captions, and converts them to SRT.

The script uses Chrome cookies for YouTube access:

```bash
--cookies-from-browser chrome
```

## Burn two subtitle languages into a video

Choose option 5. You can then download from YouTube or select local files:

```text
Choose source for the dual-subtitle video:
  1) Download video and captions from YouTube
  2) Use a local video and two subtitle files
```

The language-pair menu offers:

```text
1) English + Simplified Chinese
2) English + Turkish
3) English + Uyghur
4) Custom pair
```

Language 1 is burned in bold white at the bottom. Language 2 is burned in bold
yellow immediately above it. Both have black outlines for readability. The
tracks share one collision-aware subtitle layer: one-line captions stay close,
while a wrapped two-line block is automatically moved to prevent overlap.

The shortcuts produce names such as:

```text
Example Video [abc123] [dual-subs-en-zh-Hans].mp4
Example Video [abc123] [dual-subs-en-tr].mp4
Example Video [abc123] [dual-subs-en-ug].mp4
```

If a filename already exists, the script keeps it and creates ` (2)`, ` (3)`,
and so on.

### YouTube workflow

The script reads the video's caption metadata and separates tracks into:

- Creator-provided captions.
- Native automatic captions.
- YouTube translations from creator captions.
- YouTube translations from automatic captions.

It prefers creator captions, then native automatic captions. If a requested
language exists only as a YouTube machine translation, the script explains
that translations can contain mistakes and asks before using it. It never
silently substitutes Traditional Chinese for Simplified Chinese.

YouTube translations can contain delayed phrases separated by empty caption
events. When the other selected language continuously covers such a short gap,
the script moves the delayed translated phrase back to cover it. Real pauses,
creator captions, native captions, and local subtitle timing are not changed.

After selecting both tracks, choose Best, 4K, 2K, 1080p, 720p, or 480p. Option
5 downloads an SDR source at or below that limit.

### Local-file workflow

Native Finder dialogs select:

1. The source video.
2. The language 1 subtitle file.
3. The language 2 subtitle file.

Local subtitles can be SRT, VTT, or ASS. They are converted to safe internal
SRT files before rendering, so paths containing spaces and punctuation are
supported. The subtitle timestamps must already match the video.

Rolling automatic captions can contain overlapping cues. The script shortens
an earlier cue when the next cue in the same language starts, preventing two
captions from that language from being stacked on screen at once.

### Fonts and Chinese characters

Simplified Chinese uses the macOS `Songti SC` family. Uyghur uses the
user-installed `UKIJ Tuz Tom` family for joined right-to-left Arabic-script
text. English, Turkish, and other custom languages use Noto Sans.

`Songti TC` may display Chinese successfully when you select that font for a
switchable subtitle track in IINA. This tool intentionally uses `Songti SC`
for burned Simplified Chinese because SC uses the appropriate Simplified-
Chinese glyph forms. This avoids missing-character boxes such as `[] [] []`.

IINA's subtitle font setting affects only switchable subtitle tracks. Burned
subtitles become pixels in the video, so they cannot be disabled, restyled, or
changed in IINA afterward.

### Preview and output quality

Option 5 can render a 20-second preview with the exact same fonts, positions,
colors, and encoder used for the final video. It finds the first time both
subtitle tracks overlap. If no overlap is found, enter a timestamp such as
`01:30` or press Enter for `00:30`.

After QuickTime opens the preview, return to Terminal and choose whether to:

1. Continue with the full video.
2. Preview another timestamp.
3. Cancel and remove the temporary files.

The final file uses:

- MP4 container.
- H.264 High Profile through Apple Silicon's VideoToolbox encoder.
- High-quality VideoToolbox setting `-q:v 75`.
- AAC audio at 192 kbps when the source has audio.
- `yuv420p` and MP4 fast-start for broad Apple-device compatibility.
- The source frame rate and resolution. An odd width or height may be reduced
  by one pixel because H.264 requires even dimensions.

## Save folder and cleanup

All outputs are saved in:

```text
~/Downloads/YouTube/
```

After a successful dual-subtitle encode, the script keeps only the finished
MP4 and removes the downloaded source, subtitle copies, preview, and other
working files. It never deletes the original files chosen in the local-file
workflow.

If an unexpected download or encoding failure occurs, working files are kept
in a reported `ytgrab-work.*` folder under `~/Downloads/YouTube` for
troubleshooting. Finder opens the output folder after a successful run.

## Limitations

- Exactly two subtitle languages are burned into the video.
- The script cannot remove subtitles that are already permanently burned into
  the source image. Selecting the same language will display both versions.
- Machine-translated captions may be inaccurate.
- Local subtitle files must already be synchronized with the source video.
- Explicit PQ or HLG HDR input is rejected. Option 5 currently creates SDR
  H.264 output and does not tone-map HDR video.
- The first video and first audio stream are used from local multi-stream files.
- YouTube availability and caption downloads can change; keep `yt-dlp` current.
