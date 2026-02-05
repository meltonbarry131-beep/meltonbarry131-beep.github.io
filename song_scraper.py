#!/usr/bin/env python3
"""
Song Scraper - Pull songs from YouTube URLs
============================================
Extracts metadata and downloads audio from YouTube videos.
Built for pulling tracks like "You Know Me" and similar songs.

Usage:
    python3 song_scraper.py <youtube_url>
    python3 song_scraper.py <youtube_url> --audio-only
    python3 song_scraper.py <youtube_url> --info-only
    python3 song_scraper.py --batch urls.txt
    python3 song_scraper.py --search "artist - song name"

Requirements:
    pip install yt-dlp

Optional (for higher quality audio conversion):
    Install ffmpeg: sudo apt install ffmpeg
"""

import argparse
import json
import os
import re
import sys
import subprocess
from pathlib import Path
from datetime import datetime

try:
    import yt_dlp
except ImportError:
    print("ERROR: yt-dlp is required. Install it with: pip install yt-dlp")
    sys.exit(1)


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

DEFAULT_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scraped_songs")
DEFAULT_AUDIO_FORMAT = "mp3"
DEFAULT_AUDIO_QUALITY = "192"  # kbps

SUPPORTED_AUDIO_FORMATS = ["mp3", "wav", "flac", "aac", "opus", "m4a", "ogg"]

# Patterns to clean up YouTube titles into artist - track format
TITLE_SEPARATORS = [" - ", " – ", " — ", " | ", " // ", " ~ "]
TITLE_JUNK = [
    r"\(official\s*(music\s*)?video\)",
    r"\(official\s*audio\)",
    r"\(official\s*lyric\s*video\)",
    r"\(official\s*visualizer\)",
    r"\(lyrics?\)",
    r"\(lyric\s*video\)",
    r"\(audio\)",
    r"\(visualizer\)",
    r"\(prod\.?\s*by\s*[^)]+\)",
    r"\[official\s*(music\s*)?video\]",
    r"\[official\s*audio\]",
    r"\[lyrics?\]",
    r"\[audio\]",
    r"\[visualizer\]",
    r"\[prod\.?\s*by\s*[^]]+\]",
    r"\bofficial\s*(music\s*)?video\b",
    r"\bofficial\s*audio\b",
    r"\blyric\s*video\b",
    r"\bhd\b",
    r"\b4k\b",
    r"\bremix\b(?![\w])",  # keep "remix" if it's part of a word
    r"\bft\.?\s*",
    r"\bfeat\.?\s*",
]


# ──────────────────────────────────────────────
# Title Parser
# ──────────────────────────────────────────────

def clean_title(title):
    """Remove common YouTube junk from video titles."""
    cleaned = title
    for pattern in TITLE_JUNK:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    # Collapse whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # Remove trailing/leading dashes and pipes
    cleaned = cleaned.strip("-–—| ")
    return cleaned


def parse_artist_track(title, uploader=None):
    """
    Try to extract artist and track name from a YouTube video title.
    Falls back to uploader name as artist if separator not found.
    """
    cleaned = clean_title(title)

    # Try each separator
    for sep in TITLE_SEPARATORS:
        if sep in cleaned:
            parts = cleaned.split(sep, 1)
            artist = parts[0].strip()
            track = parts[1].strip()
            if artist and track:
                return artist, track

    # No separator found - use uploader as artist, cleaned title as track
    if uploader:
        # Clean up uploader name (remove "VEVO", "Official", "Topic" suffixes)
        artist = re.sub(
            r"\s*(VEVO|Official|Topic|Music|-\s*Topic)$", "", uploader, flags=re.IGNORECASE
        ).strip()
        return artist, cleaned

    return "Unknown Artist", cleaned


# ──────────────────────────────────────────────
# Song Scraper
# ──────────────────────────────────────────────

class SongScraper:
    """YouTube song scraper using yt-dlp."""

    def __init__(self, output_dir=None, audio_format=None, audio_quality=None, verbose=False):
        self.output_dir = output_dir or DEFAULT_OUTPUT_DIR
        self.audio_format = audio_format or DEFAULT_AUDIO_FORMAT
        self.audio_quality = audio_quality or DEFAULT_AUDIO_QUALITY
        self.verbose = verbose
        self.scraped = []

        os.makedirs(self.output_dir, exist_ok=True)

    def _get_base_opts(self):
        """Base yt-dlp options."""
        return {
            "quiet": not self.verbose,
            "no_warnings": not self.verbose,
            "extract_flat": False,
        }

    def get_info(self, url):
        """
        Extract metadata from a YouTube URL without downloading.
        Returns a dict with song info.
        """
        opts = self._get_base_opts()
        opts["skip_download"] = True

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as e:
            print(f"ERROR: Could not fetch info for {url}")
            print(f"  {e}")
            return None

        if not info:
            return None

        # Extract useful fields
        title = info.get("title", "Unknown Title")
        uploader = info.get("uploader") or info.get("channel")
        artist_meta = info.get("artist")
        track_meta = info.get("track")
        album_meta = info.get("album")

        # Use embedded metadata if available, otherwise parse title
        if artist_meta and track_meta:
            artist, track = artist_meta, track_meta
        else:
            artist, track = parse_artist_track(title, uploader)

        song_info = {
            "url": url,
            "video_id": info.get("id"),
            "title": title,
            "artist": artist,
            "track": track,
            "album": album_meta or "Unknown Album",
            "uploader": uploader,
            "channel": info.get("channel"),
            "duration": info.get("duration"),
            "duration_string": info.get("duration_string"),
            "view_count": info.get("view_count"),
            "like_count": info.get("like_count"),
            "upload_date": info.get("upload_date"),
            "description": (info.get("description") or "")[:500],
            "thumbnail": info.get("thumbnail"),
            "categories": info.get("categories", []),
            "tags": info.get("tags", [])[:20],
            "webpage_url": info.get("webpage_url"),
        }

        return song_info

    def download_audio(self, url, filename=None):
        """
        Download audio from a YouTube URL.
        Returns path to downloaded file.
        """
        # First get info for naming
        info = self.get_info(url)
        if not info:
            return None

        if not filename:
            safe_artist = re.sub(r'[<>:"/\\|?*]', '_', info["artist"])
            safe_track = re.sub(r'[<>:"/\\|?*]', '_', info["track"])
            filename = f"{safe_artist} - {safe_track}"

        output_template = os.path.join(self.output_dir, f"{filename}.%(ext)s")

        opts = self._get_base_opts()
        opts.update({
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": self.audio_format,
                "preferredquality": self.audio_quality,
            }],
            "postprocessor_args": [
                "-metadata", f"artist={info['artist']}",
                "-metadata", f"title={info['track']}",
                "-metadata", f"album={info['album']}",
            ],
        })

        try:
            print(f"  Downloading: {info['artist']} - {info['track']}")
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])

            output_path = os.path.join(self.output_dir, f"{filename}.{self.audio_format}")
            if os.path.exists(output_path):
                info["local_path"] = output_path
                info["file_size"] = os.path.getsize(output_path)
                self.scraped.append(info)
                print(f"  Saved: {output_path}")
                return output_path
            else:
                # yt-dlp might have kept the original format
                for ext in SUPPORTED_AUDIO_FORMATS + ["webm", "m4a"]:
                    alt_path = os.path.join(self.output_dir, f"{filename}.{ext}")
                    if os.path.exists(alt_path):
                        info["local_path"] = alt_path
                        info["file_size"] = os.path.getsize(alt_path)
                        self.scraped.append(info)
                        print(f"  Saved: {alt_path}")
                        return alt_path

                print("  WARNING: Download completed but file not found at expected path")
                return None

        except yt_dlp.utils.DownloadError as e:
            print(f"  ERROR: Download failed for {url}")
            print(f"    {e}")
            return None

    def download_video(self, url, filename=None):
        """
        Download video+audio from a YouTube URL.
        Returns path to downloaded file.
        """
        info = self.get_info(url)
        if not info:
            return None

        if not filename:
            safe_artist = re.sub(r'[<>:"/\\|?*]', '_', info["artist"])
            safe_track = re.sub(r'[<>:"/\\|?*]', '_', info["track"])
            filename = f"{safe_artist} - {safe_track}"

        output_template = os.path.join(self.output_dir, f"{filename}.%(ext)s")

        opts = self._get_base_opts()
        opts.update({
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": output_template,
            "merge_output_format": "mp4",
        })

        try:
            print(f"  Downloading video: {info['artist']} - {info['track']}")
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])

            output_path = os.path.join(self.output_dir, f"{filename}.mp4")
            if os.path.exists(output_path):
                info["local_path"] = output_path
                info["file_size"] = os.path.getsize(output_path)
                self.scraped.append(info)
                print(f"  Saved: {output_path}")
                return output_path

        except yt_dlp.utils.DownloadError as e:
            print(f"  ERROR: Download failed for {url}")
            print(f"    {e}")
            return None

    def search_and_download(self, query, max_results=5, download=False):
        """
        Search YouTube for a song and optionally download it.
        """
        search_url = f"ytsearch{max_results}:{query}"

        opts = self._get_base_opts()
        opts["skip_download"] = True
        opts["extract_flat"] = True

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                results = ydl.extract_info(search_url, download=False)
        except yt_dlp.utils.DownloadError as e:
            print(f"ERROR: Search failed for '{query}'")
            print(f"  {e}")
            return []

        if not results or "entries" not in results:
            print(f"No results found for: {query}")
            return []

        entries = list(results["entries"])
        print(f"\nSearch results for: {query}")
        print("=" * 60)

        song_list = []
        for i, entry in enumerate(entries, 1):
            vid_url = entry.get("url") or f"https://www.youtube.com/watch?v={entry.get('id')}"
            title = entry.get("title", "Unknown")
            duration = entry.get("duration")
            dur_str = f"{duration // 60}:{duration % 60:02d}" if duration else "??:??"

            print(f"  [{i}] {title} ({dur_str})")
            print(f"      {vid_url}")

            song_list.append({
                "title": title,
                "url": vid_url,
                "duration": duration,
                "video_id": entry.get("id"),
            })

        if download and song_list:
            print(f"\nDownloading top result...")
            self.download_audio(song_list[0]["url"])

        return song_list

    def batch_download(self, urls, audio_only=True):
        """Download multiple songs from a list of URLs."""
        total = len(urls)
        successful = 0
        failed = []

        print(f"\nBatch download: {total} songs")
        print("=" * 60)

        for i, url in enumerate(urls, 1):
            url = url.strip()
            if not url or url.startswith("#"):
                continue

            print(f"\n[{i}/{total}] Processing: {url}")

            if audio_only:
                result = self.download_audio(url)
            else:
                result = self.download_video(url)

            if result:
                successful += 1
            else:
                failed.append(url)

        print(f"\n{'=' * 60}")
        print(f"Complete: {successful}/{total} successful")
        if failed:
            print(f"Failed ({len(failed)}):")
            for url in failed:
                print(f"  - {url}")

        return successful, failed

    def export_metadata(self, filepath=None):
        """Export all scraped metadata to JSON."""
        if not filepath:
            filepath = os.path.join(self.output_dir, "song_metadata.json")

        data = {
            "scraped_at": datetime.now().isoformat(),
            "total_songs": len(self.scraped),
            "songs": self.scraped,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"\nMetadata exported to: {filepath}")
        return filepath


# ──────────────────────────────────────────────
# Pretty Print
# ──────────────────────────────────────────────

def print_song_info(info):
    """Pretty-print song metadata."""
    if not info:
        return

    print()
    print("=" * 60)
    print(f"  SONG INFO")
    print("=" * 60)
    print(f"  Artist  : {info['artist']}")
    print(f"  Track   : {info['track']}")
    print(f"  Album   : {info['album']}")
    print(f"  Duration: {info.get('duration_string', 'N/A')}")
    print(f"  Views   : {info.get('view_count', 'N/A'):,}" if isinstance(info.get('view_count'), int) else f"  Views   : N/A")
    print(f"  Uploaded: {info.get('upload_date', 'N/A')}")
    print(f"  Channel : {info.get('channel', 'N/A')}")
    print(f"  URL     : {info.get('webpage_url', info.get('url', 'N/A'))}")

    if info.get("thumbnail"):
        print(f"  Thumb   : {info['thumbnail']}")

    if info.get("tags"):
        print(f"  Tags    : {', '.join(info['tags'][:10])}")

    if info.get("local_path"):
        size_mb = info.get("file_size", 0) / (1024 * 1024)
        print(f"  File    : {info['local_path']} ({size_mb:.1f} MB)")

    print("=" * 60)
    print()


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Song Scraper - Pull songs from YouTube URLs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s https://youtu.be/K7gZfLJ27oU                    # Download audio
  %(prog)s https://youtu.be/K7gZfLJ27oU --info-only         # Just show info
  %(prog)s https://youtu.be/K7gZfLJ27oU --video             # Download video
  %(prog)s https://youtu.be/K7gZfLJ27oU --format flac       # Download as FLAC
  %(prog)s --search "You Know Me"                            # Search YouTube
  %(prog)s --search "You Know Me" --download                 # Search & download top result
  %(prog)s --batch urls.txt                                  # Batch download from file
  %(prog)s --batch urls.txt --video                          # Batch download videos
        """,
    )

    # Input sources
    parser.add_argument("url", nargs="?", help="YouTube URL to scrape")
    parser.add_argument("--batch", metavar="FILE", help="File with URLs (one per line)")
    parser.add_argument("--search", metavar="QUERY", help="Search YouTube for a song")

    # Mode
    parser.add_argument("--info-only", action="store_true", help="Only show metadata, don't download")
    parser.add_argument("--audio-only", action="store_true", default=True, help="Download audio only (default)")
    parser.add_argument("--video", action="store_true", help="Download video + audio")
    parser.add_argument("--download", action="store_true", help="When searching, also download the top result")

    # Options
    parser.add_argument("--format", "-f", choices=SUPPORTED_AUDIO_FORMATS, default=DEFAULT_AUDIO_FORMAT,
                        help=f"Audio format (default: {DEFAULT_AUDIO_FORMAT})")
    parser.add_argument("--quality", "-q", default=DEFAULT_AUDIO_QUALITY,
                        help=f"Audio quality in kbps (default: {DEFAULT_AUDIO_QUALITY})")
    parser.add_argument("--output", "-o", metavar="DIR", default=DEFAULT_OUTPUT_DIR,
                        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--max-results", type=int, default=5,
                        help="Max search results (default: 5)")
    parser.add_argument("--export", action="store_true", help="Export metadata to JSON after scraping")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    if not args.url and not args.batch and not args.search:
        parser.print_help()
        print("\nERROR: Provide a URL, --batch file, or --search query")
        sys.exit(1)

    scraper = SongScraper(
        output_dir=args.output,
        audio_format=args.format,
        audio_quality=args.quality,
        verbose=args.verbose,
    )

    # ── Search mode ──
    if args.search:
        results = scraper.search_and_download(
            args.search,
            max_results=args.max_results,
            download=args.download,
        )
        if args.export and results:
            scraper.export_metadata()
        sys.exit(0)

    # ── Batch mode ──
    if args.batch:
        batch_file = args.batch
        if not os.path.exists(batch_file):
            print(f"ERROR: Batch file not found: {batch_file}")
            sys.exit(1)

        with open(batch_file, "r") as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

        if not urls:
            print("ERROR: No URLs found in batch file")
            sys.exit(1)

        scraper.batch_download(urls, audio_only=not args.video)

        if args.export:
            scraper.export_metadata()
        sys.exit(0)

    # ── Single URL mode ──
    url = args.url

    if args.info_only:
        info = scraper.get_info(url)
        print_song_info(info)
    elif args.video:
        scraper.download_video(url)
        if scraper.scraped:
            print_song_info(scraper.scraped[-1])
    else:
        scraper.download_audio(url)
        if scraper.scraped:
            print_song_info(scraper.scraped[-1])

    if args.export:
        scraper.export_metadata()


if __name__ == "__main__":
    main()
