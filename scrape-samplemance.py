#!/usr/bin/env python3
"""
scrape-samplemance.py — Download labeled SNES sample rips from samplemance.rs

Scrapes https://samplemance.rs/snesguide/samples/repo/ for game entries
with Google Drive links, downloads each sample pack, extracts it, and
catalogs the sample filenames for use as instrument names.

Usage:
    python3 scrape-samplemance.py [--output-dir DIR] [--limit N] [--list-only] [--skip-existing]

Output:
    samplemance.rs/             — extracted sample packs per game (gitignored)
    docs/drive-links.json       — scraped game→drive link mapping
    docs/name-mapping.json      — final (game, sample_index) → name mapping
"""

import argparse
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path

import requests

try:
    import gdown
except ImportError:
    print("ERROR: gdown not installed. Run: pip install gdown", file=sys.stderr)
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("ERROR: beautifulsoup4 not installed. Run: pip install beautifulsoup4", file=sys.stderr)
    sys.exit(1)


REPO_URL = "https://samplemance.rs/snesguide/samples/repo/"
DEFAULT_OUTPUT_DIR = "samplemance.rs"
DRIVE_LINKS_FILE = "docs/drive-links.json"
NAME_MAPPING_FILE = "docs/name-mapping.json"


def sanitize_dirname(name: str) -> str:
    """Convert a game name to a filesystem-safe directory name."""
    # Replace characters that are problematic in filenames
    name = name.replace(":", " -")
    name = name.replace("/", "-")
    name = name.replace("\\", "-")
    name = re.sub(r'[<>"|?*]', '', name)
    # Collapse multiple spaces
    name = re.sub(r'\s+', ' ', name).strip()
    # Replace spaces with underscores for consistency with snesology-library
    name = name.replace(' ', '_')
    return name


def scrape_drive_links() -> list[dict]:
    """Scrape the samplemance repo page for game names and Google Drive links."""
    print(f"Fetching {REPO_URL} ...")
    resp = requests.get(REPO_URL, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, 'html.parser')

    games = []
    # Find all links that point to Google Drive
    for link in soup.find_all('a', href=True):
        href = link['href']
        if 'drive.google.com/file/d/' not in href:
            continue

        # Extract game name from link text
        game_name = link.get_text(strip=True)
        if not game_name:
            continue

        # Extract file ID from Drive URL
        match = re.search(r'/file/d/([a-zA-Z0-9_-]+)', href)
        if not match:
            continue

        file_id = match.group(1)
        games.append({
            'name': game_name,
            'dir_name': sanitize_dirname(game_name),
            'drive_url': href,
            'file_id': file_id,
        })

    print(f"Found {len(games)} games with Drive links")
    return games


def download_from_drive(file_id: str, output_path: str, game_name: str) -> bool:
    """Download a file from Google Drive using gdown with timeout."""
    url = f"https://drive.google.com/uc?id={file_id}"
    try:
        import signal

        def _timeout_handler(signum, frame):
            raise TimeoutError("gdown download timed out")

        # Set 30-second timeout for the download
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(30)
        try:
            result = gdown.download(url, output_path, quiet=True, fuzzy=True)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)

        if result and os.path.exists(output_path):
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            print(f"  Downloaded {game_name}: {size_mb:.1f} MB")
            return True
        else:
            print(f"  FAILED to download {game_name}", file=sys.stderr)
            return False
    except TimeoutError:
        print(f"  TIMEOUT downloading {game_name}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"  ERROR downloading {game_name}: {e}", file=sys.stderr)
        return False


def extract_archive(archive_path: str, extract_dir: str, game_name: str) -> bool:
    """Extract a zip/rar archive to the given directory."""
    try:
        if zipfile.is_zipfile(archive_path):
            with zipfile.ZipFile(archive_path, 'r') as zf:
                zf.extractall(extract_dir)
            return True
        else:
            # Try as a rar or 7z — use system tools
            import subprocess
            # Try unrar first
            try:
                result = subprocess.run(
                    ['unrar', 'x', '-o+', archive_path, extract_dir + '/'],
                    capture_output=True, text=True, timeout=120
                )
                if result.returncode == 0:
                    return True
            except FileNotFoundError:
                pass

            # Try 7z
            try:
                result = subprocess.run(
                    ['7z', 'x', archive_path, f'-o{extract_dir}', '-y'],
                    capture_output=True, text=True, timeout=120
                )
                if result.returncode == 0:
                    return True
            except FileNotFoundError:
                pass

            print(f"  Could not extract {game_name} — not a zip and no unrar/7z available",
                  file=sys.stderr)
            return False
    except Exception as e:
        print(f"  ERROR extracting {game_name}: {e}", file=sys.stderr)
        return False


def catalog_samples(extract_dir: str) -> list[dict]:
    """Walk the extracted directory and catalog all sample files."""
    samples = []
    audio_exts = {'.wav', '.brr', '.aif', '.aiff', '.mp3', '.flac', '.ogg'}

    for root, dirs, files in os.walk(extract_dir):
        for f in sorted(files):
            ext = os.path.splitext(f)[1].lower()
            if ext not in audio_exts:
                continue

            rel_path = os.path.relpath(os.path.join(root, f), extract_dir)
            name_no_ext = os.path.splitext(f)[0]

            # Try to extract a sample number from the filename
            # Common patterns: "01 Strings.wav", "01_Strings.wav", "Strings.wav"
            num_match = re.match(r'^(\d+)[\s_.-]+(.+)$', name_no_ext)
            if num_match:
                index = int(num_match.group(1))
                label = num_match.group(2).strip()
            else:
                index = None
                label = name_no_ext.strip()

            samples.append({
                'filename': f,
                'rel_path': rel_path,
                'label': label,
                'index': index,
                'ext': ext,
            })

    return samples


def main():
    parser = argparse.ArgumentParser(description="Scrape SNES sample rips from samplemance.rs")
    parser.add_argument('--output-dir', default=DEFAULT_OUTPUT_DIR,
                        help=f"Directory for downloaded sample packs (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument('--limit', type=int, default=0,
                        help="Max number of games to download (0 = all)")
    parser.add_argument('--list-only', action='store_true',
                        help="Only scrape and list games, don't download")
    parser.add_argument('--skip-existing', action='store_true',
                        help="Skip games already downloaded")
    parser.add_argument('--start-from', type=str, default=None,
                        help="Start downloading from this game name (case-insensitive substring match)")
    parser.add_argument('--catalog-only', action='store_true',
                        help="Only catalog already-downloaded samples, skip downloading")
    parser.add_argument('--delay', type=int, default=1,
                        help="Seconds to wait between downloads (default: 1, use 10-30 to avoid rate limits)")
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    output_dir = script_dir / args.output_dir
    drive_links_path = script_dir / DRIVE_LINKS_FILE
    name_mapping_path = script_dir / NAME_MAPPING_FILE

    # Step 1: Scrape drive links (or load cached)
    if drive_links_path.exists() and (args.catalog_only or args.skip_existing):
        print(f"Loading cached drive links from {drive_links_path}")
        with open(drive_links_path) as f:
            games = json.load(f)
    else:
        games = scrape_drive_links()
        # Save drive links
        os.makedirs(drive_links_path.parent, exist_ok=True)
        with open(drive_links_path, 'w') as f:
            json.dump(games, f, indent=2)
        print(f"Saved {len(games)} game links to {drive_links_path}")

    if args.list_only:
        print(f"\n{'#':>4}  {'Game Name':<50}  {'Dir Name':<50}  File ID")
        print("-" * 140)
        for i, g in enumerate(games, 1):
            print(f"{i:>4}  {g['name']:<50}  {g['dir_name']:<50}  {g['file_id']}")
        return

    # Step 2: Download and extract
    os.makedirs(output_dir, exist_ok=True)

    # Handle --start-from
    start_index = 0
    if args.start_from:
        needle = args.start_from.lower()
        for i, g in enumerate(games):
            if needle in g['name'].lower():
                start_index = i
                print(f"Starting from game #{i+1}: {g['name']}")
                break
        else:
            print(f"WARNING: --start-from '{args.start_from}' not found, starting from beginning")

    downloaded = 0
    skipped = 0
    failed = 0
    name_mapping = {}

    # Load existing name mapping if doing catalog-only
    if args.catalog_only and name_mapping_path.exists():
        with open(name_mapping_path) as f:
            name_mapping = json.load(f)

    for i, game in enumerate(games[start_index:], start_index + 1):
        if args.limit and downloaded >= args.limit:
            break

        game_dir = output_dir / game['dir_name']

        if args.skip_existing and game_dir.exists() and any(game_dir.iterdir()):
            skipped += 1
            # Still catalog if not already in mapping
            if game['dir_name'] not in name_mapping:
                samples = catalog_samples(str(game_dir))
                if samples:
                    name_mapping[game['dir_name']] = {
                        'game_name': game['name'],
                        'samples': samples,
                    }
            continue

        if args.catalog_only:
            # Just catalog existing downloads
            if game_dir.exists():
                samples = catalog_samples(str(game_dir))
                if samples:
                    name_mapping[game['dir_name']] = {
                        'game_name': game['name'],
                        'samples': samples,
                    }
            continue

        print(f"[{i}/{len(games)}] {game['name']}")

        # Download to temp file
        os.makedirs(game_dir, exist_ok=True)
        temp_path = str(game_dir / "_download.zip")

        if download_from_drive(game['file_id'], temp_path, game['name']):
            # Extract
            if extract_archive(temp_path, str(game_dir), game['name']):
                # Clean up archive
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

                # Catalog samples
                samples = catalog_samples(str(game_dir))
                if samples:
                    name_mapping[game['dir_name']] = {
                        'game_name': game['name'],
                        'samples': samples,
                    }
                    print(f"  Found {len(samples)} samples")
                else:
                    print(f"  WARNING: No audio files found after extraction")

                downloaded += 1
            else:
                failed += 1
                # Clean up failed download
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
        else:
            failed += 1

        # Be polite to Google Drive
        time.sleep(args.delay)

    # Save name mapping
    if name_mapping:
        with open(name_mapping_path, 'w') as f:
            json.dump(name_mapping, f, indent=2, sort_keys=True)
        print(f"\nSaved name mapping for {len(name_mapping)} games to {name_mapping_path}")

    print(f"\nDone: {downloaded} downloaded, {skipped} skipped, {failed} failed")


if __name__ == '__main__':
    main()
