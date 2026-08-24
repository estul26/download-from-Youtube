#!/usr/bin/env bash

# Interactive YouTube downloader for macOS.
# Uses Chrome cookies because that works well with YouTube's current access checks.

set -u

BROWSER="chrome"
OUTDIR="$HOME/Downloads/YouTube"

DUAL_FFMPEG=""
DUAL_FFPROBE=""
FC_MATCH=""
DUAL_WORKDIR=""
VIDEO_SOURCE=""
FINAL_OUTPUT=""
OUTPUT_BASE=""

LANG1_REQUEST=""
LANG2_REQUEST=""
LANG1_LABEL=""
LANG2_LABEL=""
LANG1_OUTPUT=""
LANG2_OUTPUT=""
LANG1_IS_SIMPLIFIED=0
LANG2_IS_SIMPLIFIED=0
LANG1_IS_UYGHUR=0
LANG2_IS_UYGHUR=0
TRACK1_CODE=""
TRACK2_CODE=""
TRACK1_NAME=""
TRACK2_NAME=""
TRACK1_CATEGORY=""
TRACK2_CATEGORY=""

mkdir -p "$OUTDIR"

# ---------- General helpers ----------
need_command() {
  command -v "$1" >/dev/null 2>&1
}

ask_yes_no() {
  local prompt="$1"
  local default_answer="${2:-no}"
  local answer

  while true; do
    if [ "$default_answer" = "yes" ]; then
      read -r -p "$prompt [Y/n]: " answer || return 1
      answer="${answer:-y}"
    else
      read -r -p "$prompt [y/N]: " answer || return 1
      answer="${answer:-n}"
    fi

    case "$answer" in
      y|Y|yes|YES|Yes) return 0 ;;
      n|N|no|NO|No) return 1 ;;
      *) echo "Please answer y or n." ;;
    esac
  done
}

prompt_url() {
  while true; do
    read -r -p "Paste YouTube video link: " URL
    if [ -n "$URL" ]; then
      return 0
    fi
    echo "Please enter a YouTube URL."
  done
}

choose_video_quality() {
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
      1) HEIGHT="" ; LABEL="Best available" ; return 0 ;;
      2) HEIGHT="2160" ; LABEL="2160p / 4K" ; return 0 ;;
      3) HEIGHT="1440" ; LABEL="1440p / 2K" ; return 0 ;;
      4) HEIGHT="1080" ; LABEL="1080p" ; return 0 ;;
      5) HEIGHT="720"  ; LABEL="720p" ; return 0 ;;
      6) HEIGHT="480"  ; LABEL="480p" ; return 0 ;;
      *) echo "Please choose 1 through 6." ;;
    esac
  done
}

check_standard_dependencies() {
  if ! need_command yt-dlp; then
    echo "yt-dlp is not installed."
    echo "Install it with:"
    echo "  brew install yt-dlp"
    return 1
  fi
  if ! need_command ffmpeg; then
    echo "ffmpeg is not installed."
    echo "Install it with:"
    echo "  brew install ffmpeg"
    return 1
  fi
}

# ---------- Existing download modes ----------
run_standard_download() {
  local type="$1"
  local format
  local audio_quality
  local subtitle_language

  check_standard_dependencies || return 1
  prompt_url

  local common_args=(
    --cookies-from-browser "$BROWSER"
    --no-playlist
    --newline
    -o "$OUTDIR/%(title)s [%(id)s].%(ext)s"
  )

  if [ "$type" = "1" ] || [ "$type" = "2" ]; then
    choose_video_quality
    echo
    echo "Download type : $([ "$type" = "1" ] && echo "MP4" || echo "MKV")"
    echo "Quality       : $LABEL"
    echo "Save folder   : $OUTDIR"
    echo

    if [ "$type" = "1" ]; then
      if [ -z "$HEIGHT" ]; then
        format='bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b'
      else
        format="bv*[ext=mp4][height<=${HEIGHT}]+ba[ext=m4a]/b[ext=mp4][height<=${HEIGHT}]/bv*[height<=${HEIGHT}]+ba/b[height<=${HEIGHT}]"
      fi
      yt-dlp "${common_args[@]}" -f "$format" --merge-output-format mp4 "$URL"
    else
      if [ -z "$HEIGHT" ]; then
        format='bv*+ba/b'
      else
        format="bv*[height<=${HEIGHT}]+ba/b[height<=${HEIGHT}]"
      fi
      yt-dlp "${common_args[@]}" -f "$format" --merge-output-format mkv "$URL"
    fi

  elif [ "$type" = "3" ]; then
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
        1) audio_quality="320K" ; LABEL="320 kbps" ; break ;;
        2) audio_quality="192K" ; LABEL="192 kbps" ; break ;;
        3) audio_quality="128K" ; LABEL="128 kbps" ; break ;;
        *) echo "Please choose 1, 2, or 3." ;;
      esac
    done

    echo
    echo "Download type : MP3 audio"
    echo "Quality       : $LABEL"
    echo "Save folder   : $OUTDIR"
    echo
    yt-dlp "${common_args[@]}" -f "bestaudio/best" -x \
      --audio-format mp3 --audio-quality "$audio_quality" "$URL"

  else
    echo
    echo "Enter a subtitle language code."
    echo "Examples: en (English), fr (French), es (Spanish), or all"
    read -r -p "Language [en]: " subtitle_language
    subtitle_language="${subtitle_language:-en}"
    echo
    echo "Download type : SRT subtitles"
    echo "Language      : $subtitle_language"
    echo "Save folder   : $OUTDIR"
    echo
    yt-dlp "${common_args[@]}" --skip-download --write-subs --write-auto-subs \
      --sub-langs "$subtitle_language" --sub-format "srt/best" \
      --convert-subs srt "$URL"
  fi
}

# ---------- Dual-subtitle dependencies ----------
ffmpeg_has_subtitles_filter() {
  "$1" -hide_banner -filters 2>/dev/null | grep -Eq '[[:space:]]subtitles[[:space:]]'
}

locate_ffmpeg_full() {
  local prefix
  local candidate
  local candidates=()

  if need_command brew; then
    prefix="$(brew --prefix ffmpeg-full 2>/dev/null || true)"
    [ -n "$prefix" ] && candidates+=("$prefix/bin/ffmpeg")
  fi
  candidates+=(
    "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
    "/usr/local/opt/ffmpeg-full/bin/ffmpeg"
  )
  need_command ffmpeg && candidates+=("$(command -v ffmpeg)")

  for candidate in "${candidates[@]}"; do
    if [ -x "$candidate" ] && ffmpeg_has_subtitles_filter "$candidate"; then
      DUAL_FFMPEG="$candidate"
      DUAL_FFPROBE="$(dirname "$candidate")/ffprobe"
      [ -x "$DUAL_FFPROBE" ] && return 0
    fi
  done
  return 1
}

locate_fc_match() {
  local prefix
  if need_command fc-match; then
    FC_MATCH="$(command -v fc-match)"
    return 0
  fi
  if need_command brew; then
    prefix="$(brew --prefix fontconfig 2>/dev/null || true)"
    if [ -x "$prefix/bin/fc-match" ]; then
      FC_MATCH="$prefix/bin/fc-match"
      return 0
    fi
  fi
  FC_MATCH=""
  return 1
}

font_is_installed() {
  local family="$1"
  local matched
  local font_file

  if [ -n "$FC_MATCH" ]; then
    matched="$("$FC_MATCH" -f '%{family}\n' "$family" 2>/dev/null || true)"
    printf '%s\n' "$matched" | grep -Fq "$family" && return 0
  fi
  if [ "$family" = "Songti SC" ]; then
    [ -f "/System/Library/Fonts/Supplemental/Songti.ttc" ] && return 0
  fi
  if [ "$family" = "UKIJ Tuz Tom" ]; then
    for font_file in \
      "$HOME/Library/Fonts/UKIJTuT.ttf" \
      /Library/Fonts/UKIJTuT.ttf \
      /System/Library/Fonts/UKIJTuT.ttf \
      /System/Library/Fonts/Supplemental/UKIJTuT.ttf; do
      [ -f "$font_file" ] && return 0
    done
  fi
  if [ "$family" = "Noto Sans" ]; then
    for font_file in \
      "$HOME"/Library/Fonts/NotoSans*.ttf \
      /Library/Fonts/NotoSans*.ttf \
      /System/Library/Fonts/NotoSans*.ttf; do
      [ -f "$font_file" ] && return 0
    done
  fi
  return 1
}

check_dual_dependencies() {
  if ! need_command osascript; then
    echo "Option 5 requires macOS AppleScript support (osascript)."
    return 1
  fi
  if ! locate_ffmpeg_full; then
    echo "Option 5 needs FFmpeg with the subtitles/libass filter."
    echo "The regular Homebrew ffmpeg build may not include that filter."
    echo "Install the full build with:"
    echo "  brew install ffmpeg-full"
    return 1
  fi
  if ! "$DUAL_FFMPEG" -hide_banner -encoders 2>/dev/null | grep -q 'h264_videotoolbox'; then
    echo "The selected FFmpeg build does not provide h264_videotoolbox."
    return 1
  fi
  if ! "$DUAL_FFMPEG" -hide_banner -loglevel error \
      -f lavfi -i 'color=c=black:s=64x64:d=0.04' \
      -frames:v 1 -an -c:v h264_videotoolbox -q:v 75 -f null -; then
    echo "Apple VideoToolbox failed its quality-encoding test."
    return 1
  fi

  locate_fc_match || true
  if ! font_is_installed "Songti SC"; then
    echo "The macOS font 'Songti SC' could not be found."
    echo "It is required to render Simplified Chinese without empty boxes."
    return 1
  fi
  if ! font_is_installed "Noto Sans"; then
    echo "The font 'Noto Sans' is not installed."
    echo "Install it with:"
    echo "  brew install --cask font-noto-sans"
    return 1
  fi
  if ! font_is_installed "UKIJ Tuz Tom"; then
    echo "The font 'UKIJ Tuz Tom' could not be found."
    echo "Install UKIJ Tuz Tom (UKIJTuT.ttf) with Font Book, then try again."
    echo "Download it from: https://ukij.org/fonts/"
    return 1
  fi
}

# ---------- Languages and YouTube track metadata ----------
valid_language_code() {
  case "$1" in
    ""|*[!A-Za-z0-9_-]*) return 1 ;;
    *) return 0 ;;
  esac
}

is_simplified_chinese_code() {
  local code
  code="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  case "$code" in
    zh-hans|zh-cn|zh-sg) return 0 ;;
    *) return 1 ;;
  esac
}

is_uyghur_code() {
  local code
  code="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  case "$code" in
    ug|ug-*) return 0 ;;
    *) return 1 ;;
  esac
}

choose_language_pair() {
  local pair_choice first_code second_code first_lower second_lower

  echo
  echo "Choose subtitle languages:"
  echo "  1) English + Simplified Chinese"
  echo "  2) English + Turkish"
  echo "  3) English + Uyghur"
  echo "  4) Custom pair"
  echo

  while true; do
    read -r -p "Language pair [1-4]: " pair_choice
    case "$pair_choice" in
      1)
        LANG1_REQUEST="english"; LANG2_REQUEST="simplified-chinese"
        LANG1_LABEL="English"; LANG2_LABEL="Simplified Chinese"
        LANG1_OUTPUT="en"; LANG2_OUTPUT="zh-Hans"
        LANG1_IS_SIMPLIFIED=0; LANG2_IS_SIMPLIFIED=1
        LANG1_IS_UYGHUR=0; LANG2_IS_UYGHUR=0
        return 0
        ;;
      2)
        LANG1_REQUEST="english"; LANG2_REQUEST="turkish"
        LANG1_LABEL="English"; LANG2_LABEL="Turkish"
        LANG1_OUTPUT="en"; LANG2_OUTPUT="tr"
        LANG1_IS_SIMPLIFIED=0; LANG2_IS_SIMPLIFIED=0
        LANG1_IS_UYGHUR=0; LANG2_IS_UYGHUR=0
        return 0
        ;;
      3)
        LANG1_REQUEST="english"; LANG2_REQUEST="uyghur"
        LANG1_LABEL="English"; LANG2_LABEL="Uyghur"
        LANG1_OUTPUT="en"; LANG2_OUTPUT="ug"
        LANG1_IS_SIMPLIFIED=0; LANG2_IS_SIMPLIFIED=0
        LANG1_IS_UYGHUR=0; LANG2_IS_UYGHUR=1
        return 0
        ;;
      4)
        while true; do
          read -r -p "Language 1 code: " first_code
          valid_language_code "$first_code" && break
          echo "Use letters, numbers, hyphens, or underscores only."
        done
        while true; do
          read -r -p "Language 2 code: " second_code
          if ! valid_language_code "$second_code"; then
            echo "Use letters, numbers, hyphens, or underscores only."
            continue
          fi
          first_lower="$(printf '%s' "$first_code" | tr '[:upper:]' '[:lower:]')"
          second_lower="$(printf '%s' "$second_code" | tr '[:upper:]' '[:lower:]')"
          if [ "$first_lower" = "$second_lower" ]; then
            echo "Choose two different language codes."
            continue
          fi
          break
        done
        LANG1_REQUEST="exact:$first_code"; LANG2_REQUEST="exact:$second_code"
        LANG1_LABEL="$first_code"; LANG2_LABEL="$second_code"
        LANG1_OUTPUT="$first_code"; LANG2_OUTPUT="$second_code"
        LANG1_IS_SIMPLIFIED=0; LANG2_IS_SIMPLIFIED=0
        LANG1_IS_UYGHUR=0; LANG2_IS_UYGHUR=0
        is_simplified_chinese_code "$first_code" && LANG1_IS_SIMPLIFIED=1
        is_simplified_chinese_code "$second_code" && LANG2_IS_SIMPLIFIED=1
        is_uyghur_code "$first_code" && LANG1_IS_UYGHUR=1
        is_uyghur_code "$second_code" && LANG2_IS_UYGHUR=1
        return 0
        ;;
      *) echo "Please choose 1, 2, 3, or 4." ;;
    esac
  done
}

# JXA classifies captions without adding jq or Python as dependencies.
metadata_query() {
  local action="$1"
  local request="${2:-}"

  osascript -l JavaScript - "$DUAL_WORKDIR/metadata.json" "$action" "$request" <<'JXA'
ObjC.import('Foundation');
function clean(v) { return String(v || '').replace(/[\t\r\n]+/g, ' ').trim(); }
function loadJSON(path) {
  var data = $.NSData.dataWithContentsOfFile(path);
  if (!data) throw new Error('Unable to read metadata file');
  var text = $.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding).js;
  return JSON.parse(text);
}
function hasQuery(url, key) {
  return new RegExp('[?&]' + key + '=').test(String(url || ''));
}
function rowsFor(info) {
  var rows = [], manual = info.subtitles || {}, automatic = info.automatic_captions || {};
  Object.keys(manual).forEach(function(code) {
    if (code === 'live_chat') return;
    var f = manual[code] || [];
    rows.push({category: 'creator', code: code, name: f[0] && f[0].name || ''});
  });
  Object.keys(automatic).forEach(function(code) {
    if (code === 'live_chat' || /-orig$/i.test(code) || manual[code]) return;
    var f = automatic[code] || [];
    var translated = f.length && f.every(function(x) { return hasQuery(x.url, 'tlang'); });
    var auto = f.some(function(x) { return /[?&]kind=asr(?:&|$)/.test(String(x.url || '')); });
    rows.push({
      category: translated ? (auto ? 'translated-automatic' : 'translated-creator') : 'automatic',
      code: code,
      name: f[0] && f[0].name || ''
    });
  });
  var p = {creator: 0, automatic: 1, 'translated-creator': 2, 'translated-automatic': 3};
  rows.sort(function(a, b) { return p[a.category] - p[b.category] || a.code.localeCompare(b.code); });
  return rows;
}
function matches(row, request) {
  var code = row.code.toLowerCase(), name = row.name.toLowerCase();
  var translated = row.category.indexOf('translated-') === 0;
  if (request.indexOf('exact:') === 0) {
    var exact = request.substring(6).toLowerCase();
    return code === exact || (translated && code.indexOf(exact + '-') === 0);
  }
  if (request === 'english') return code === 'en' || code.indexOf('en-') === 0;
  if (request === 'turkish') return code === 'tr' || code.indexOf('tr-') === 0 || name === 'turkish';
  if (request === 'uyghur') return code === 'ug' || code.indexOf('ug-') === 0 || name === 'uyghur';
  if (request === 'simplified-chinese') {
    if (/traditional|繁體|繁体/.test(name) || /^(zh-hant|zh-tw|zh-hk)(-|$)/.test(code)) return false;
    return /^(zh-hans|zh-cn|zh-sg)(-|$)/.test(code) || /simplified|简体|簡體/.test(name);
  }
  return false;
}
function safeBase(info) {
  var title = clean(info.title || 'YouTube Video').replace(/[\/:\u0000-\u001f]/g, '-').substring(0, 160).trim();
  var id = clean(info.id || 'unknown').replace(/[^A-Za-z0-9_-]/g, '');
  return title + ' [' + id + ']';
}
function run(argv) {
  var info = loadJSON(argv[0]), action = argv[1], request = argv[2] || '', rows;
  if (action === 'base') return safeBase(info);
  rows = rowsFor(info);
  if (action === 'candidates') rows = rows.filter(function(row) { return matches(row, request); });
  return rows.map(function(row) {
    return [clean(row.category), clean(row.code), clean(row.name)].join('\t');
  }).join('\n');
}
JXA
}

display_youtube_tracks() {
  local category code name previous="" heading count=0
  echo
  echo "Available subtitle tracks:"
  while IFS=$'\t' read -r category code name; do
    [ -z "$category" ] && continue
    if [ "$category" != "$previous" ]; then
      case "$category" in
        creator) heading="Creator-provided" ;;
        automatic) heading="Native automatic captions" ;;
        translated-creator) heading="YouTube translations from creator captions" ;;
        translated-automatic) heading="YouTube translations from automatic captions" ;;
        *) heading="$category" ;;
      esac
      echo
      echo "  $heading:"
      previous="$category"
    fi
    echo "    $code${name:+ - $name}"
    count=$((count + 1))
  done < <(metadata_query list)
  [ "$count" -eq 0 ] && echo "  No caption tracks were reported by YouTube."
}

select_track_candidate() {
  local display_name="$1"; shift
  local candidates=("$@")
  local choice index=1 category code name candidate

  if [ "${#candidates[@]}" -eq 1 ]; then
    IFS=$'\t' read -r SELECTED_CATEGORY SELECTED_CODE SELECTED_NAME <<< "${candidates[0]}"
    return 0
  fi
  echo
  echo "More than one track matches $display_name:"
  for candidate in "${candidates[@]}"; do
    IFS=$'\t' read -r category code name <<< "$candidate"
    echo "  $index) $code - ${name:-Unnamed track} [$category]"
    index=$((index + 1))
  done
  while true; do
    read -r -p "Choose track [1-${#candidates[@]}]: " choice
    case "$choice" in
      *[!0-9]*|"") echo "Enter a number from the list." ;;
      *)
        if [ "$choice" -ge 1 ] 2>/dev/null && [ "$choice" -le "${#candidates[@]}" ] 2>/dev/null; then
          IFS=$'\t' read -r SELECTED_CATEGORY SELECTED_CODE SELECTED_NAME <<< "${candidates[$((choice - 1))]}"
          return 0
        fi
        echo "Enter a number from the list."
        ;;
    esac
  done
}

resolve_youtube_track() {
  local request="$1" display_name="$2" line category
  local preferred=() translated=()

  while IFS= read -r line; do
    [ -z "$line" ] && continue
    category="${line%%$'\t'*}"
    case "$category" in
      creator|automatic) preferred+=("$line") ;;
      translated-creator|translated-automatic) translated+=("$line") ;;
    esac
  done < <(metadata_query candidates "$request")

  if [ "${#preferred[@]}" -gt 0 ]; then
    select_track_candidate "$display_name" "${preferred[@]}"
    return 0
  fi
  if [ "${#translated[@]}" -eq 0 ]; then
    echo
    echo "No creator, native automatic, or translated track matches $display_name."
    return 1
  fi
  echo
  echo "$display_name is not available as a creator or native automatic track."
  echo "YouTube machine translation is available, but it may contain mistakes."
  ask_yes_no "Use a translated $display_name track?" "no" || return 1
  select_track_candidate "$display_name" "${translated[@]}"
}

resolve_youtube_pair() {
  resolve_youtube_track "$LANG1_REQUEST" "$LANG1_LABEL" || return 1
  TRACK1_CODE="$SELECTED_CODE"; TRACK1_NAME="$SELECTED_NAME"; TRACK1_CATEGORY="$SELECTED_CATEGORY"
  printf '%s' "$TRACK1_NAME" | grep -Eqi 'simplified|简体|簡體' && LANG1_IS_SIMPLIFIED=1
  is_uyghur_code "$TRACK1_CODE" && LANG1_IS_UYGHUR=1

  resolve_youtube_track "$LANG2_REQUEST" "$LANG2_LABEL" || return 1
  TRACK2_CODE="$SELECTED_CODE"; TRACK2_NAME="$SELECTED_NAME"; TRACK2_CATEGORY="$SELECTED_CATEGORY"
  printf '%s' "$TRACK2_NAME" | grep -Eqi 'simplified|简体|簡體' && LANG2_IS_SIMPLIFIED=1
  is_uyghur_code "$TRACK2_CODE" && LANG2_IS_UYGHUR=1

  if [ "$TRACK1_CODE" = "$TRACK2_CODE" ]; then
    echo "Both choices resolved to the same YouTube track. Choose another pair."
    return 1
  fi
}

# ---------- Working files and media helpers ----------
create_dual_workdir() {
  DUAL_WORKDIR="$(mktemp -d "$OUTDIR/ytgrab-work.XXXXXX")" || return 1
}

cleanup_dual_workdir() {
  if [ -n "$DUAL_WORKDIR" ]; then
    case "$DUAL_WORKDIR" in
      "$OUTDIR"/ytgrab-work.*)
        rm -rf -- "$DUAL_WORKDIR"
        DUAL_WORKDIR=""
        ;;
      *)
        echo "Refusing to remove unexpected work path: $DUAL_WORKDIR"
        return 1
        ;;
    esac
  fi
}

handle_dual_interrupt() {
  echo
  echo "Dual-subtitle encoding was interrupted."
  if [ -n "$DUAL_WORKDIR" ]; then
    echo "Working files were preserved at:"
    echo "  $DUAL_WORKDIR"
  fi
  exit 130
}

choose_finder_file() {
  local prompt="$1"
  osascript - "$prompt" <<'APPLESCRIPT'
on run argv
  return POSIX path of (choose file with prompt (item 1 of argv))
end run
APPLESCRIPT
}

validate_video_file() {
  "$DUAL_FFPROBE" -v error -select_streams v:0 -show_entries stream=index \
    -of csv=p=0 "$1" 2>/dev/null | grep -Eq '^[0-9]+'
}

validate_subtitle_extension() {
  local extension="${1##*.}"
  extension="$(printf '%s' "$extension" | tr '[:upper:]' '[:lower:]')"
  case "$extension" in srt|vtt|ass) return 0 ;; *) return 1 ;; esac
}

normalize_subtitle() {
  local converted="${2%.srt}.converted.srt"
  local cleaned="${2%.srt}.cleaned.srt"

  if ! "$DUAL_FFMPEG" -hide_banner -loglevel error -y \
      -i "$1" -map 0:s:0 -c:s srt "$converted"; then
    rm -f -- "$converted"
    return 1
  fi

  # ASS files can carry inline font tags into SRT. Remove only those tags so
  # the selected subtitle family, size, and colors are applied consistently.
  if ! sed -E 's#</?[Ff][Oo][Nn][Tt][^>]*>##g' "$converted" > "$cleaned"; then
    rm -f -- "$converted" "$cleaned"
    return 1
  fi

  # YouTube's rolling automatic captions often overlap the following cue.
  # End each earlier cue when the next one begins so libass never stacks two
  # cues from the same language on screen at once.
  if ! remove_overlapping_subtitle_cues "$cleaned" "$2"; then
    rm -f -- "$converted" "$cleaned" "$2"
    return 1
  fi
  rm -f -- "$converted" "$cleaned"
}

remove_overlapping_subtitle_cues() {
  awk '
    BEGIN { RS=""; ORS="\n\n"; have_previous=0 }
    function seconds(timestamp, parts) {
      sub(/[[:space:]].*$/, "", timestamp)
      gsub(/,/, ".", timestamp)
      split(timestamp, parts, ":")
      return parts[1]*3600 + parts[2]*60 + parts[3]
    }
    {
      current=$0
      split(current, lines, "\n")
      split(lines[2], timing, /[[:space:]]+-->[[:space:]]+/)
      current_start=timing[1]

      if (have_previous) {
        if (seconds(previous_end) > seconds(current_start))
          sub(previous_end, current_start, previous)
        print previous
      }

      previous=current
      previous_end=timing[2]
      sub(/[[:space:]].*$/, "", previous_end)
      have_previous=1
    }
    END { if (have_previous) print previous }
  ' "$1" > "$2"
}

align_translated_subtitle_to_reference() {
  local reference="$1" translated="$2"
  local aligned="${translated%.srt}.aligned.srt"

  # YouTube machine translations sometimes delay the next translated phrase
  # while emitting empty rolling-caption events. Move that next phrase back
  # only when the reference language continuously captions the entire gap.
  # Real pauses therefore remain subtitle-free.
  if ! awk '
    BEGIN { RS=""; ORS="\n\n"; tolerance=0.05; max_gap=12; have_previous=0 }
    function seconds(timestamp, parts) {
      sub(/[[:space:]].*$/, "", timestamp)
      gsub(/,/, ".", timestamp)
      split(timestamp, parts, ":")
      return parts[1]*3600 + parts[2]*60 + parts[3]
    }
    function reference_covers(from, to, i, cursor) {
      cursor=from
      for (i=0; i<reference_count; i++) {
        if (reference_end[i] <= cursor+tolerance) continue
        if (reference_start[i] > cursor+tolerance) return 0
        if (reference_end[i] > cursor) cursor=reference_end[i]
        if (cursor >= to-tolerance) return 1
      }
      return cursor >= to-tolerance
    }
    FILENAME == ARGV[1] {
      split($0, lines, "\n")
      split(lines[2], timing, /[[:space:]]+-->[[:space:]]+/)
      reference_start[reference_count]=seconds(timing[1])
      reference_end[reference_count]=seconds(timing[2])
      reference_count++
      next
    }
    {
      current=$0
      split(current, lines, "\n")
      split(lines[2], timing, /[[:space:]]+-->[[:space:]]+/)
      start_stamp=timing[1]
      start=seconds(start_stamp)
      end_stamp=timing[2]
      sub(/[[:space:]].*$/, "", end_stamp)
      end=seconds(end_stamp)
      gap=start-previous_end

      if (have_previous && gap>tolerance && gap<=max_gap &&
          reference_covers(previous_end,start)) {
        sub(start_stamp, previous_end_stamp, current)
      }

      print current
      previous_end=end
      previous_end_stamp=end_stamp
      have_previous=1
    }
  ' "$reference" "$translated" > "$aligned"; then
    rm -f -- "$aligned"
    return 1
  fi

  mv -f -- "$aligned" "$translated"
}

is_translated_category() {
  case "$1" in translated-*) return 0 ;; *) return 1 ;; esac
}

align_youtube_translated_subtitles() {
  if is_translated_category "$TRACK1_CATEGORY" &&
      ! is_translated_category "$TRACK2_CATEGORY"; then
    align_translated_subtitle_to_reference \
      "$DUAL_WORKDIR/language2.srt" "$DUAL_WORKDIR/language1.srt"
  elif is_translated_category "$TRACK2_CATEGORY" &&
      ! is_translated_category "$TRACK1_CATEGORY"; then
    align_translated_subtitle_to_reference \
      "$DUAL_WORKDIR/language1.srt" "$DUAL_WORKDIR/language2.srt"
  fi
}

video_is_hdr() {
  local transfer
  transfer="$("$DUAL_FFPROBE" -v error -select_streams v:0 \
    -show_entries stream=color_transfer -of default=noprint_wrappers=1:nokey=1 \
    "$1" 2>/dev/null | head -n 1)"
  case "$transfer" in smpte2084|arib-std-b67) return 0 ;; *) return 1 ;; esac
}

sanitize_basename() {
  local value="$1"
  value="${value//$'\n'/ }"; value="${value//$'\r'/ }"
  value="${value//\//-}"; value="${value//:/-}"
  printf '%s' "$value" | LC_ALL=en_US.UTF-8 cut -c 1-160
}

make_unique_output_path() {
  local suffix="dual-subs-$LANG1_OUTPUT-$LANG2_OUTPUT"
  local candidate="$OUTDIR/$OUTPUT_BASE [$suffix].mp4"
  local number=2
  while [ -e "$candidate" ]; do
    candidate="$OUTDIR/$OUTPUT_BASE [$suffix] ($number).mp4"
    number=$((number + 1))
  done
  FINAL_OUTPUT="$candidate"
}

subtitle_intervals() {
  "$DUAL_FFPROBE" -v error -select_streams s:0 -show_packets \
    -show_entries packet=pts_time,duration_time -of csv=p=0 "$1" 2>/dev/null | \
    awk -F, 'NF >= 1 && $1 ~ /^[0-9.]+$/ {
      start=$1+0; duration=($2 ~ /^[0-9.]+$/) ? $2+0 : 2
      if (duration <= 0) duration=2
      printf "%.6f %.6f\n", start, start+duration
    }'
}

find_first_overlap() {
  local timings1="$DUAL_WORKDIR/timings1.txt"
  local timings2="$DUAL_WORKDIR/timings2.txt"
  subtitle_intervals "$DUAL_WORKDIR/language1.srt" > "$timings1"
  subtitle_intervals "$DUAL_WORKDIR/language2.srt" > "$timings2"
  awk '
    BEGIN { ac=0; bc=0 }
    FILENAME == ARGV[1] { as[ac]=$1; ae[ac]=$2; ac++; next }
    FILENAME == ARGV[2] { bs[bc]=$1; be[bc]=$2; bc++; next }
    END {
      i=0; j=0
      while (i<ac && j<bc) {
        start=as[i]>bs[j] ? as[i] : bs[j]
        end=ae[i]<be[j] ? ae[i] : be[j]
        if (start<end) { printf "%.3f\n", start; exit }
        if (ae[i]<be[j]) i++; else j++
      }
    }' "$timings1" "$timings2"
}

parse_timestamp() {
  printf '%s\n' "$1" | awk -F: '
    function numeric(v) { return v ~ /^[0-9]+([.][0-9]+)?$/ }
    NF==1 && numeric($1) { printf "%.3f\n", $1; ok=1 }
    NF==2 && numeric($1) && numeric($2) && $2<60 { printf "%.3f\n", $1*60+$2; ok=1 }
    NF==3 && numeric($1) && numeric($2) && numeric($3) && $2<60 && $3<60 {
      printf "%.3f\n", $1*3600+$2*60+$3; ok=1
    }
    END { if (!ok) exit 1 }'
}

prompt_preview_timestamp() {
  local entered parsed
  while true; do
    read -r -p "Preview start time [00:30]: " entered
    entered="${entered:-00:30}"
    if parsed="$(parse_timestamp "$entered")"; then
      printf '%s\n' "$parsed"
      return 0
    fi
    echo "Use seconds, MM:SS, or HH:MM:SS."
  done
}

subtitle_font_names() {
  SUBTITLE_FONT1="Noto Sans"
  SUBTITLE_FONT2="Noto Sans"
  [ "$LANG1_IS_SIMPLIFIED" -eq 1 ] && SUBTITLE_FONT1="Songti SC"
  [ "$LANG2_IS_SIMPLIFIED" -eq 1 ] && SUBTITLE_FONT2="Songti SC"
  [ "$LANG1_IS_UYGHUR" -eq 1 ] && SUBTITLE_FONT1="UKIJ Tuz Tom"
  [ "$LANG2_IS_UYGHUR" -eq 1 ] && SUBTITLE_FONT2="UKIJ Tuz Tom"
}

build_combined_subtitle_ass() {
  local language1_ass="$DUAL_WORKDIR/language1.generated.ass"
  local language2_ass="$DUAL_WORKDIR/language2.generated.ass"
  local combined_partial="$DUAL_WORKDIR/combined.partial.ass"

  subtitle_font_names
  if ! "$DUAL_FFMPEG" -hide_banner -loglevel error -y \
      -i "$DUAL_WORKDIR/language1.srt" -map 0:s:0 -c:s ass "$language1_ass"; then
    rm -f -- "$language1_ass" "$language2_ass" "$combined_partial"
    return 1
  fi
  if ! "$DUAL_FFMPEG" -hide_banner -loglevel error -y \
      -i "$DUAL_WORKDIR/language2.srt" -map 0:s:0 -c:s ass "$language2_ass"; then
    rm -f -- "$language1_ass" "$language2_ass" "$combined_partial"
    return 1
  fi

  # A single ASS layer lets libass detect collisions between the languages.
  # One-line captions remain close together; wrapped blocks are moved apart.
  if ! awk -v font1="$SUBTITLE_FONT1" -v font2="$SUBTITLE_FONT2" '
    BEGIN {
      print "[Script Info]"
      print "ScriptType: v4.00+"
      print "PlayResX: 384"
      print "PlayResY: 288"
      print "WrapStyle: 0"
      print "ScaledBorderAndShadow: yes"
      print "Collisions: Normal"
      print ""
      print "[V4+ Styles]"
      print "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
      print "Style: Language1," font1 ",14,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,0,2,10,10,18,1"
      print "Style: Language2," font2 ",14,&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,0,2,10,10,32,1"
      print ""
      print "[Events]"
      print "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    }
    FILENAME == ARGV[1] && /^Dialogue:/ {
      sub(/,Default,/, ",Language1,")
      print
      next
    }
    FILENAME == ARGV[2] && /^Dialogue:/ {
      sub(/,Default,/, ",Language2,")
      print
    }
  ' "$language1_ass" "$language2_ass" > "$combined_partial"; then
    rm -f -- "$language1_ass" "$language2_ass" "$combined_partial"
    return 1
  fi

  if ! mv -f -- "$combined_partial" "$DUAL_WORKDIR/combined.ass"; then
    rm -f -- "$language1_ass" "$language2_ass" "$combined_partial"
    return 1
  fi
  rm -f -- "$language1_ass" "$language2_ass"
}

build_subtitle_filter() {
  local preview_start="${1:-}" prefix="" suffix=""
  if [ -n "$preview_start" ]; then
    prefix="setpts=PTS+${preview_start}/TB,"
    suffix=",setpts=PTS-${preview_start}/TB"
  fi
  printf "%sscale=trunc(iw/2)*2:trunc(ih/2)*2,subtitles=filename=combined.ass%s" \
    "$prefix" "$suffix"
}

render_preview() {
  local start="$1" destination="$2" filter
  filter="$(build_subtitle_filter "$start")"
  (
    cd "$DUAL_WORKDIR" || exit 1
    "$DUAL_FFMPEG" -hide_banner -y -ss "$start" -i "$VIDEO_SOURCE" -t 20 \
      -map 0:v:0 -map '0:a:0?' -vf "$filter" \
      -c:v h264_videotoolbox -profile:v high -q:v 75 -pix_fmt yuv420p -tag:v avc1 \
      -c:a aac -b:a 192k -map_metadata 0 -movflags +faststart "$destination"
  )
}

preview_workflow() {
  local overlap start preview_count=1 preview_file action
  echo
  ask_yes_no "Create a 20-second subtitle preview?" "yes" || return 0
  overlap="$(find_first_overlap)"
  if [ -n "$overlap" ]; then
    start="$(awk -v value="$overlap" 'BEGIN { value-=2; if (value<0) value=0; printf "%.3f", value }')"
    echo "Found a section where both subtitle tracks overlap at ${overlap}s."
  else
    echo "No overlapping subtitle interval was found."
    start="$(prompt_preview_timestamp)"
  fi

  while true; do
    preview_file="preview-$preview_count.mp4"
    echo
    echo "Rendering preview from ${start}s..."
    render_preview "$start" "$preview_file" || return 1
    open -a "QuickTime Player" "$DUAL_WORKDIR/$preview_file" >/dev/null 2>&1 \
      || open "$DUAL_WORKDIR/$preview_file" >/dev/null 2>&1 \
      || echo "Preview saved at: $DUAL_WORKDIR/$preview_file"
    echo
    echo "Inspect both subtitle languages in the preview, then return here."
    echo "  1) Continue with full video"
    echo "  2) Preview another timestamp"
    echo "  3) Cancel"
    while true; do
      read -r -p "Choose [1-3]: " action
      case "$action" in
        1) return 0 ;;
        2) start="$(prompt_preview_timestamp)"; preview_count=$((preview_count+1)); break ;;
        3) return 2 ;;
        *) echo "Please choose 1, 2, or 3." ;;
      esac
    done
  done
}

render_full_video() {
  local filter
  filter="$(build_subtitle_filter)"
  echo
  echo "Encoding the full dual-subtitle video..."
  (
    cd "$DUAL_WORKDIR" || exit 1
    "$DUAL_FFMPEG" -hide_banner -y -i "$VIDEO_SOURCE" \
      -map 0:v:0 -map '0:a:0?' -vf "$filter" \
      -c:v h264_videotoolbox -profile:v high -q:v 75 -pix_fmt yuv420p -tag:v avc1 \
      -c:a aac -b:a 192k -map_metadata 0 -movflags +faststart final.partial.mp4
  )
}

download_youtube_dual_sources() {
  local format escaped1 escaped2 subtitle_pattern found
  if [ -z "$HEIGHT" ]; then
    format='bv*[dynamic_range=SDR]+ba/b[dynamic_range=SDR]'
  else
    format="bv*[dynamic_range=SDR][height<=${HEIGHT}]+ba/b[dynamic_range=SDR][height<=${HEIGHT}]"
  fi
  escaped1="$(printf '%s' "$TRACK1_CODE" | sed 's/[][\\.^$*+?{}|()]/\\&/g')"
  escaped2="$(printf '%s' "$TRACK2_CODE" | sed 's/[][\\.^$*+?{}|()]/\\&/g')"
  subtitle_pattern="^${escaped1}$,^${escaped2}$"

  echo
  echo "Downloading SDR source video and both subtitle tracks..."
  yt-dlp --cookies-from-browser "$BROWSER" --no-playlist --newline \
    --ffmpeg-location "$(dirname "$DUAL_FFMPEG")" -f "$format" \
    --merge-output-format mkv --write-subs --write-auto-subs \
    --sub-langs "$subtitle_pattern" --sub-format "srt/vtt/ass/best" --convert-subs srt \
    -o "$DUAL_WORKDIR/source.%(ext)s" \
    -o "subtitle:$DUAL_WORKDIR/subtitle.%(ext)s" "$URL" || return 1

  VIDEO_SOURCE=""
  for found in "$DUAL_WORKDIR"/source.*; do
    case "$found" in *.part|*.ytdl) continue ;; esac
    [ -f "$found" ] && VIDEO_SOURCE="$found" && break
  done
  if [ -z "$VIDEO_SOURCE" ]; then
    echo "The downloaded source video could not be located."
    return 1
  fi
  if [ ! -f "$DUAL_WORKDIR/subtitle.$TRACK1_CODE.srt" ]; then
    echo "The selected $LANG1_LABEL subtitle track was not downloaded."
    return 1
  fi
  if [ ! -f "$DUAL_WORKDIR/subtitle.$TRACK2_CODE.srt" ]; then
    echo "The selected $LANG2_LABEL subtitle track was not downloaded."
    return 1
  fi
  normalize_subtitle "$DUAL_WORKDIR/subtitle.$TRACK1_CODE.srt" "$DUAL_WORKDIR/language1.srt" || return 1
  normalize_subtitle "$DUAL_WORKDIR/subtitle.$TRACK2_CODE.srt" "$DUAL_WORKDIR/language2.srt" || return 1
  align_youtube_translated_subtitles || return 1
}

prepare_youtube_dual_workflow() {
  local pair_ready=0
  if ! need_command yt-dlp; then
    echo "YouTube mode requires yt-dlp. Install it with:"
    echo "  brew install yt-dlp"
    return 1
  fi
  prompt_url
  echo
  echo "Reading YouTube caption metadata..."
  if ! yt-dlp --cookies-from-browser "$BROWSER" --no-playlist --quiet \
      --simulate --skip-download --write-subs --write-auto-subs --dump-single-json \
      "$URL" > "$DUAL_WORKDIR/metadata.json"; then
    echo "Could not read YouTube video or caption metadata."
    return 1
  fi
  display_youtube_tracks
  while [ "$pair_ready" -eq 0 ]; do
    choose_language_pair
    if resolve_youtube_pair; then
      pair_ready=1
    else
      echo
      echo "Choose another language pair, or press Control-C to stop."
    fi
  done
  choose_video_quality
  OUTPUT_BASE="$(metadata_query base)"
  echo
  echo "Source        : YouTube"
  echo "Language 1    : $LANG1_LABEL ($TRACK1_CODE, $TRACK1_CATEGORY)"
  echo "Language 2    : $LANG2_LABEL ($TRACK2_CODE, $TRACK2_CATEGORY)"
  echo "Quality       : $LABEL / SDR"
  echo "Output        : MP4, H.264 VideoToolbox, AAC"
  echo "Save folder   : $OUTDIR"
  download_youtube_dual_sources
}

prepare_local_dual_workflow() {
  local local_video subtitle1 subtitle2 filename
  choose_language_pair
  TRACK1_CODE="$LANG1_OUTPUT"; TRACK2_CODE="$LANG2_OUTPUT"
  TRACK1_CATEGORY="local"; TRACK2_CATEGORY="local"

  echo
  echo "Choose the local video in Finder..."
  local_video="$(choose_finder_file "Choose the source video")" || return 2
  if ! validate_video_file "$local_video"; then
    echo "The selected file does not contain a readable video stream."
    return 1
  fi
  echo "Choose the $LANG1_LABEL subtitle file..."
  subtitle1="$(choose_finder_file "Choose the $LANG1_LABEL subtitle file")" || return 2
  if ! validate_subtitle_extension "$subtitle1"; then
    echo "Language 1 must be an SRT, VTT, or ASS subtitle file."
    return 1
  fi
  echo "Choose the $LANG2_LABEL subtitle file..."
  subtitle2="$(choose_finder_file "Choose the $LANG2_LABEL subtitle file")" || return 2
  if ! validate_subtitle_extension "$subtitle2"; then
    echo "Language 2 must be an SRT, VTT, or ASS subtitle file."
    return 1
  fi

  VIDEO_SOURCE="$local_video"
  if video_is_hdr "$VIDEO_SOURCE"; then
    echo "The selected video is HDR (PQ or HLG)."
    echo "Option 5 currently supports SDR sources only to avoid washed-out colors."
    return 1
  fi
  normalize_subtitle "$subtitle1" "$DUAL_WORKDIR/language1.srt" || {
    echo "Could not read the $LANG1_LABEL subtitle file."; return 1;
  }
  normalize_subtitle "$subtitle2" "$DUAL_WORKDIR/language2.srt" || {
    echo "Could not read the $LANG2_LABEL subtitle file."; return 1;
  }
  filename="$(basename "$VIDEO_SOURCE")"; filename="${filename%.*}"
  OUTPUT_BASE="$(sanitize_basename "$filename")"
  echo
  echo "Source        : Local video"
  echo "Language 1    : $LANG1_LABEL"
  echo "Language 2    : $LANG2_LABEL"
  echo "Resolution    : Preserve source resolution"
  echo "Output        : MP4, H.264 VideoToolbox, AAC"
  echo "Save folder   : $OUTDIR"
}

run_dual_subtitle_workflow() {
  local source_choice prepare_status preview_status
  echo
  echo "Choose source for the dual-subtitle video:"
  echo "  1) Download video and captions from YouTube"
  echo "  2) Use a local video and two subtitle files"
  echo
  while true; do
    read -r -p "Source [1-2]: " source_choice
    case "$source_choice" in 1|2) break ;; *) echo "Please choose 1 or 2." ;; esac
  done

  check_dual_dependencies || return 1
  create_dual_workdir || { echo "Could not create a working directory in $OUTDIR."; return 1; }
  trap handle_dual_interrupt INT TERM

  if [ "$source_choice" = "1" ]; then
    prepare_youtube_dual_workflow; prepare_status=$?
  else
    prepare_local_dual_workflow; prepare_status=$?
  fi
  if [ "$prepare_status" -eq 2 ]; then
    cleanup_dual_workdir; trap - INT TERM; return 2
  elif [ "$prepare_status" -ne 0 ]; then
    trap - INT TERM; return "$prepare_status"
  fi

  if video_is_hdr "$VIDEO_SOURCE"; then
    echo "The selected source is HDR (PQ or HLG), but option 5 supports SDR only."
    trap - INT TERM; return 1
  fi
  if ! build_combined_subtitle_ass; then
    echo "The two subtitle tracks could not be prepared for collision-free rendering."
    trap - INT TERM; return 1
  fi
  preview_workflow; preview_status=$?
  if [ "$preview_status" -eq 2 ]; then
    echo "Dual-subtitle encoding cancelled."
    cleanup_dual_workdir; trap - INT TERM; return 2
  elif [ "$preview_status" -ne 0 ]; then
    trap - INT TERM; return "$preview_status"
  fi

  render_full_video || { trap - INT TERM; return 1; }
  make_unique_output_path
  if ! mv "$DUAL_WORKDIR/final.partial.mp4" "$FINAL_OUTPUT"; then
    echo "The encoded video could not be moved to its final location."
    trap - INT TERM; return 1
  fi
  cleanup_dual_workdir
  trap - INT TERM
  return 0
}

# ---------- Main menu ----------
main() {
  echo "========================================"
  echo "         YouTube CLI Downloader"
  echo "========================================"
  echo
  echo "Choose download type:"
  echo "  1) MP4 video  - best Mac/iPhone compatibility"
  echo "  2) MKV video  - absolute best available quality"
  echo "  3) MP3 audio"
  echo "  4) Subtitles   - SRT captions (official or auto-generated)"
  echo "  5) Dual subtitles burned into video"
  echo

  while true; do
    read -r -p "Type [1-5]: " TYPE
    case "$TYPE" in
      1|2|3|4|5) break ;;
      *) echo "Please choose 1, 2, 3, 4, or 5." ;;
    esac
  done

  if [ "$TYPE" = "5" ]; then
    run_dual_subtitle_workflow; STATUS=$?
  else
    run_standard_download "$TYPE"; STATUS=$?
  fi

  echo
  if [ "$STATUS" -eq 0 ]; then
    echo "========================================"
    if [ "$TYPE" = "5" ]; then
      echo "Dual-subtitle video finished!"
      echo "Saved as:"
      echo "  $FINAL_OUTPUT"
    else
      echo "Download finished!"
      echo "Saved to:"
      echo "  $OUTDIR"
    fi
    echo "========================================"
    if need_command open; then
      open "$OUTDIR" || true
    fi
    return 0
  elif [ "$STATUS" -eq 2 ]; then
    echo "No output was created."
    return 0
  else
    echo "========================================"
    if [ "$TYPE" = "5" ]; then
      echo "Dual-subtitle video failed."
      if [ -n "$DUAL_WORKDIR" ]; then
        echo "Working files were preserved at:"
        echo "  $DUAL_WORKDIR"
      fi
    else
      echo "Download failed."
      echo "Try updating yt-dlp:"
      echo "  brew upgrade yt-dlp"
    fi
    echo "========================================"
    return "$STATUS"
  fi
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
