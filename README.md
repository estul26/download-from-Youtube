# download-from-Youtube

An interactive Bash tool for your Mac.

[Download `ytgrab.sh`]

It will automatically ask:

```text
========================================
         YouTube CLI Downloader
========================================

Paste YouTube video link: https://youtube.com/...

Choose download type:
  1) MP4 video  - best Mac/iPhone compatibility
  2) MKV video  - absolute best available quality
  3) MP3 audio

Type [1-3]: 1

Choose video quality:
  1) Best available
  2) 2160p (4K)
  3) 1440p (2K)
  4) 1080p (Full HD)
  5) 720p  (HD)
  6) 480p

Quality [1-6]: 4
```

It uses the Chrome cookie method that worked for you:

```bash
--cookies-from-browser chrome
```

### Install it

After downloading the file, open Terminal:

```bash
cd ~/Downloads
chmod +x ytgrab.sh
```

Then run:

```bash
./ytgrab.sh
```

### Requirements

Make sure these are installed:

```bash
brew install yt-dlp ffmpeg
```

You can update them later with:

```bash
brew upgrade yt-dlp ffmpeg
```

### Where videos are saved

The script automatically creates:

```text
~/Downloads/YouTube/
```

and saves videos like:

```text
~/Downloads/YouTube/
├── My YouTube Video [KJxi6Upksqs].mp4
├── CompTIA A+ Lesson [xxxxxxx].mp4
└── Networking Tutorial [xxxxxxx].mp3
```

After the download finishes, it automatically **opens that folder in Finder**.

For your MacBook, I would normally choose:

```text
Type:
1) MP4 video

Quality:
1) Best available
```

For something important that you want to archive at the absolute original maximum quality:

```text
Type:
2) MKV video

Quality:
1) Best available
```
