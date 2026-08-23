#!/usr/bin/env bash

# ytgrab.sh
# Interactive YouTube downloader for macOS
# Uses Chrome cookies because that works well with YouTube's current access checks.

set -u

BROWSER="chrome"
OUTDIR="$HOME/Downloads/YouTube"

mkdir -p "$OUTDIR"

# ---------- Helpers ----------
die() {
  echo
  echo "Error: $1"
  exit 1
}

need_command() {
  command -v "$1" >/dev/null 2>&1 || return 1
}

echo "========================================"
echo "         YouTube CLI Downloader"
echo "========================================"
echo

# ---------- Dependency checks ----------
if ! need_command yt-dlp; then
  echo "yt-dlp is not installed."
  echo "Install it with:"
  echo "  brew install yt-dlp"
  exit 1
fi

if ! need_command ffmpeg; then
  echo "ffmpeg is not installed."
  echo "Install it with:"
  echo "  brew install ffmpeg"
  exit 1
fi

# ---------- Ask for URL ----------
while true; do
  read -r -p "Paste YouTube video link: " URL
  if [ -n "$URL" ]; then
    break
  fi
  echo "Please enter a YouTube URL."
done

echo
echo "Choose download type:"
echo "  1) MP4 video  - best Mac/iPhone compatibility"
echo "  2) MKV video  - absolute best available quality"
echo "  3) MP3 audio"
echo

while true; do
  read -r -p "Type [1-3]: " TYPE
  case "$TYPE" in
    1|2|3) break ;;
    *) echo "Please choose 1, 2, or 3." ;;
  esac
done

COMMON_ARGS=(
  --cookies-from-browser "$BROWSER"
  --no-playlist
  --newline
  -o "$OUTDIR/%(title)s [%(id)s].%(ext)s"
)

# ---------- Video ----------
if [ "$TYPE" = "1" ] || [ "$TYPE" = "2" ]; then
  echo
  echo "Choose video quality:"
  echo "  1) Best available"
  echo "  2) 2160p (4K)"
  echo "  3) 1440p (2K)"
  echo "  4) 1080p (Full HD)"
  echo "  5) 720p  (HD)"
  echo "  6) 480p"
  echo

  while true; do
    read -r -p "Quality [1-6]: " QUALITY
    case "$QUALITY" in
      1) HEIGHT="" ; LABEL="Best available" ; break ;;
      2) HEIGHT="2160" ; LABEL="2160p / 4K" ; break ;;
      3) HEIGHT="1440" ; LABEL="1440p / 2K" ; break ;;
      4) HEIGHT="1080" ; LABEL="1080p" ; break ;;
      5) HEIGHT="720"  ; LABEL="720p" ; break ;;
      6) HEIGHT="480"  ; LABEL="480p" ; break ;;
      *) echo "Please choose 1 through 6." ;;
    esac
  done

  echo
  echo "Download type : $([ "$TYPE" = "1" ] && echo "MP4" || echo "MKV")"
  echo "Quality       : $LABEL"
  echo "Save folder   : $OUTDIR"
  echo

  if [ "$TYPE" = "1" ]; then
    # Prefer MP4 video + M4A audio for Mac/QuickTime/iPhone compatibility.
    # If that exact combination is unavailable, use the best compatible fallback.
    if [ -z "$HEIGHT" ]; then
      FORMAT='bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b'
    else
      FORMAT="bv*[ext=mp4][height<=${HEIGHT}]+ba[ext=m4a]/b[ext=mp4][height<=${HEIGHT}]/bv*[height<=${HEIGHT}]+ba/b[height<=${HEIGHT}]"
    fi

    yt-dlp "${COMMON_ARGS[@]}" \
      -f "$FORMAT" \
      --merge-output-format mp4 \
      "$URL"

  else
    # MKV can hold essentially any YouTube video/audio codec combination,
    # so this is the safest choice for absolute maximum quality.
    if [ -z "$HEIGHT" ]; then
      FORMAT='bv*+ba/b'
    else
      FORMAT="bv*[height<=${HEIGHT}]+ba/b[height<=${HEIGHT}]"
    fi

    yt-dlp "${COMMON_ARGS[@]}" \
      -f "$FORMAT" \
      --merge-output-format mkv \
      "$URL"
  fi

# ---------- Audio ----------
else
  echo
  echo "Choose MP3 quality:"
  echo "  1) High   (320 kbps)"
  echo "  2) Good   (192 kbps)"
  echo "  3) Small  (128 kbps)"
  echo
  echo "Note: converting to 320 kbps cannot add quality that YouTube"
  echo "did not provide; it only controls the MP3 conversion bitrate."
  echo

  while true; do
    read -r -p "Quality [1-3]: " QUALITY
    case "$QUALITY" in
      1) AUDIO_QUALITY="320K" ; LABEL="320 kbps" ; break ;;
      2) AUDIO_QUALITY="192K" ; LABEL="192 kbps" ; break ;;
      3) AUDIO_QUALITY="128K" ; LABEL="128 kbps" ; break ;;
      *) echo "Please choose 1, 2, or 3." ;;
    esac
  done

  echo
  echo "Download type : MP3"
  echo "Quality       : $LABEL"
  echo "Save folder   : $OUTDIR"
  echo

  yt-dlp "${COMMON_ARGS[@]}" \
    -f "bestaudio/best" \
    -x \
    --audio-format mp3 \
    --audio-quality "$AUDIO_QUALITY" \
    "$URL"
fi

STATUS=$?

echo
if [ "$STATUS" -eq 0 ]; then
  echo "========================================"
  echo "Download finished!"
  echo "Saved to:"
  echo "  $OUTDIR"
  echo "========================================"

  # Open the folder in Finder on macOS.
  if command -v open >/dev/null 2>&1; then
    open "$OUTDIR"
  fi
else
  echo "========================================"
  echo "Download failed."
  echo "Try updating yt-dlp:"
  echo "  brew upgrade yt-dlp"
  echo "========================================"
  exit "$STATUS"
fi
