#!/usr/bin/env python3
"""
Backfill historical version data from GitHub Releases.

Generates per-version manifests and a versions.json index for all existing
GitHub Releases. This is a one-time migration tool to populate the new
download page data structure from historical releases.

Channel detection heuristics (since old releases didn't follow the new flow):
  - GitHub "latest" release  → stable
  - Tag with -rc/-beta/-alpha suffix → pre-release
  - GitHub prerelease=true (no suffix) → dev
  - Otherwise → stable (if only one non-prerelease exists)

Usage:
    # Generate all manifests locally for review
    python scripts/backfill_versions.py --repo openakita/openakita --output-dir ./backfill-out

    # With CDN URL rewriting
    python scripts/backfill_versions.py --repo openakita/openakita --output-dir ./backfill-out \\
        --cdn-base-url https://dl-cn.openakita.ai

    # Upload to OSS after review
    ossutil cp -r ./backfill-out/api/ oss://{bucket}/api/ -f
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

try:
    import urllib.error
    import urllib.request
except ImportError:
    pass

# Reuse the manifest generation logic
sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_release_manifest import (
    build_grouped_downloads,
    build_updater_platforms,
    flatten_downloads,
    update_version_index,
)

GITHUB_API = "https://api.github.com"
DEFAULT_REPO = "openakita/openakita"
PRE_RELEASE_SUFFIX = re.compile(r"-(?:rc|beta|alpha)\.", re.IGNORECASE)


def fetch_json(url: str, token: str | None = None) -> dict | list:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_all_releases(repo: str, token: str | None = None) -> list[dict]:
    """Fetch all releases via GitHub API (paginated)."""
    releases = []
    page = 1
    while True:
        url = f"{GITHUB_API}/repos/{repo}/releases?per_page=100&page={page}"
        print(f"Fetching page {page}: {url}")
        data = fetch_json(url, token)
        if not data:
            break
        releases.extend(data)
        if len(data) < 100:
            break
        page += 1
    return releases


def detect_channel(release: dict) -> str:
    tag = release.get("tag_name", "")
    is_prerelease = release.get("prerelease", False)
    is_latest = release.get("is_the_latest", False)

    if PRE_RELEASE_SUFFIX.search(tag):
        return "pre-release"
    if is_latest or (not is_prerelease and not release.get("draft", False)):
        return "stable"
    return "dev"


def main():
    parser = argparse.ArgumentParser(description="Backfill historical version data")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--output-dir", required=True, help="Output directory (e.g. ./backfill-out)")
    parser.add_argument("--cdn-base-url", default=os.environ.get("CDN_BASE_URL", ""))
    parser.add_argument(
        "--channel-override", default="",
        help="Force all releases to a specific channel (for manual correction)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Print plan without writing files")
    args = parser.parse_args()

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    cdn_base = args.cdn_base_url.strip()
    out_dir = Path(args.output_dir) / "api"

    releases = fetch_all_releases(args.repo, token)
    print(f"\nFound {len(releases)} releases total\n")

    # Filter out drafts
    releases = [r for r in releases if not r.get("draft", False)]

    # Mark the "latest" release explicitly (GitHub API returns it first for non-prerelease)
    latest_found = False
    for r in releases:
        if not r.get("prerelease", False) and not latest_found:
            r["is_the_latest"] = True
            latest_found = True
        else:
            r["is_the_latest"] = False

    # Build index
    index: dict = {"generated_at": "", "stable": [], "pre_release": [], "dev": []}
    stats = {"stable": 0, "pre-release": 0, "dev": 0, "skipped": 0}

    for release in releases:
        tag = release.get("tag_name", "")
        if not tag:
            stats["skipped"] += 1
            continue

        version = tag.lstrip("v")
        channel = args.channel_override or detect_channel(release)
        assets = release.get("assets", [])
        notes = release.get("body", "") or ""
        pub_date = release.get("published_at") or ""

        print(f"  {tag} → {channel} ({len(assets)} assets)")

        if args.dry_run:
            stats[channel] = stats.get(channel, 0) + 1
            continue

        # Build manifest
        updater = build_updater_platforms(assets, cdn_base, tag)
        downloads = build_grouped_downloads(assets, cdn_base, tag)

        manifest = {
            "version": version,
            "channel": channel,
            "pub_date": pub_date,
            "notes": notes,
            "platforms": updater,
            "downloads": downloads,
        }

        # Write per-version manifest
        releases_dir = out_dir / "releases"
        releases_dir.mkdir(parents=True, exist_ok=True)
        version_file = releases_dir / f"v{version}.json"
        with open(version_file, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

        # Update index
        available_platforms = list(downloads.keys())
        index = update_version_index(index, version, channel, pub_date, available_platforms)

        stats[channel] = stats.get(channel, 0) + 1

    if not args.dry_run:
        # Write versions.json
        out_dir.mkdir(parents=True, exist_ok=True)
        index_file = out_dir / "versions.json"
        with open(index_file, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, ensure_ascii=False)
        print(f"\nWritten index: {index_file}")

        # Write channel manifests for the latest of each channel
        for channel_key in ["stable", "pre_release", "dev"]:
            entries = index.get(channel_key, [])
            if not entries:
                continue
            latest_version = entries[0]["version"]
            latest_manifest_path = out_dir / "releases" / f"v{latest_version}.json"
            if latest_manifest_path.exists():
                channel_filename = channel_key.replace("_", "-") + ".json"
                channel_file = out_dir / channel_filename
                with open(latest_manifest_path, encoding="utf-8") as src:
                    data = json.load(src)
                with open(channel_file, "w", encoding="utf-8") as dst:
                    json.dump(data, dst, indent=2, ensure_ascii=False)
                print(f"Written channel: {channel_file} (v{latest_version})")

                # Backward-compat release.json for stable
                if channel_key == "stable":
                    compat = {
                        "version": data["version"],
                        "notes": data["notes"],
                        "pub_date": data["pub_date"],
                        "platforms": data["platforms"],
                        "downloads": flatten_downloads(data["downloads"]),
                    }
                    compat_file = out_dir / "release.json"
                    with open(compat_file, "w", encoding="utf-8") as f:
                        json.dump(compat, f, indent=2, ensure_ascii=False)
                    print(f"Written compat: {compat_file}")

    print(f"\nSummary: stable={stats.get('stable',0)}, "
          f"pre-release={stats.get('pre-release',0)}, dev={stats.get('dev',0)}, "
          f"skipped={stats.get('skipped',0)}")
    if args.dry_run:
        print("(dry run — no files written)")


if __name__ == "__main__":
    main()
