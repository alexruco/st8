#!/usr/bin/env python3
"""
ST8 - Minimalist State-Based Versioning

A workflow-oriented state versioning system that manages three states:
  - dev   : the working directory (where st8 runs)
  - stage : snapshot of last promoted state (stored in .st8/stage/)
  - prod  : release-ready snapshot (stored in .st8/prod/)

The working directory IS your dev state. No separate dev/ folder needed.
All snapshots and metadata are stored inside .st8/
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from difflib import unified_diff
from fnmatch import fnmatch
from pathlib import Path
from typing import Dict, List, Optional, Tuple

__version__ = "1.0.0"

# =============================================================================
# Constants and Defaults
# =============================================================================

ST8_DIR = ".st8"
CONFIG_FILE = "config.json"
STATE_FILE = "state.json"
HISTORY_FILE = "history.json"

DEFAULT_CONFIG = {
    "current_version": "0.1.0",
    "thresholds": {
        "patch": 0.05,   # 0-5% change = patch
        "minor": 0.25    # 5-25% change = minor, >25% = major
    },
    "weighted_paths": {
        "src/": 1.0,
        "lib/": 1.0,
        "tests/": 0.3,
        "docs/": 0.1,
        "config/": 0.5
    },
    "exclude": ["node_modules/", ".st8/", "build/", "dist/", ".git/", "__pycache__/", "*.pyc"]
}

DEFAULT_STATE = {
    "version": "0.1.0",
    "dev_hash": None,
    "stage_hash": None,
    "prod_hash": None,
    "last_promoted": None
}

# =============================================================================
# Utility Functions - Path and Root Finding
# =============================================================================

def get_st8_root() -> Optional[Path]:
    """Find the ST8 root directory by walking up from current directory."""
    current = Path.cwd()
    while current != current.parent:
        if (current / ST8_DIR).is_dir():
            return current
        current = current.parent
    # Check root
    if (current / ST8_DIR).is_dir():
        return current
    return None


def require_st8_root() -> Path:
    """Get ST8 root or exit with error if not initialized."""
    root = get_st8_root()
    if root is None:
        print("Error: Not an ST8 project. Run 'st8 init' first.", file=sys.stderr)
        sys.exit(1)
    return root


def get_paths(root: Path) -> dict:
    """
    Get all standard ST8 paths.

    Structure:
      project/           <- root (this IS the dev state)
      ├── (user files)   <- dev state files
      └── .st8/
          ├── config.json
          ├── state.json
          ├── history.json
          ├── dev/       <- metadata only (hashes)
          ├── stage/     <- full snapshot
          └── prod/      <- full snapshot
    """
    st8_dir = root / ST8_DIR
    return {
        "root": root,
        "dev": root,                    # Working directory IS dev
        "st8": st8_dir,
        "dev_meta": st8_dir / "dev",    # Metadata for dev state
        "stage": st8_dir / "stage",     # Full snapshot
        "prod": st8_dir / "prod",       # Full snapshot
        "config": st8_dir / CONFIG_FILE,
        "state": st8_dir / STATE_FILE,
        "history": st8_dir / HISTORY_FILE,
    }

# =============================================================================
# Utility Functions - Config and State Management
# =============================================================================

def load_json(path: Path, default: dict) -> dict:
    """Load JSON file or return default if not exists."""
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default.copy()


def save_json(path: Path, data: dict) -> None:
    """Save data to JSON file."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def load_config(root: Path) -> dict:
    """Load config.json."""
    paths = get_paths(root)
    return load_json(paths["config"], DEFAULT_CONFIG)


def save_config(root: Path, config: dict) -> None:
    """Save config.json."""
    paths = get_paths(root)
    save_json(paths["config"], config)


def load_state(root: Path) -> dict:
    """Load state.json."""
    paths = get_paths(root)
    return load_json(paths["state"], DEFAULT_STATE)


def save_state(root: Path, state: dict) -> None:
    """Save state.json."""
    paths = get_paths(root)
    save_json(paths["state"], state)


def load_history(root: Path) -> List[dict]:
    """Load history.json."""
    paths = get_paths(root)
    if paths["history"].exists():
        with open(paths["history"], "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_history(root: Path, history: List[dict]) -> None:
    """Save history.json."""
    paths = get_paths(root)
    save_json(paths["history"], history)


def load_dev_hashes(root: Path) -> Dict[str, str]:
    """Load cached dev hashes from .st8/dev/hashes.json."""
    paths = get_paths(root)
    hashes_file = paths["dev_meta"] / "hashes.json"
    if hashes_file.exists():
        with open(hashes_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_dev_hashes(root: Path, hashes: Dict[str, str]) -> None:
    """Save dev hashes to .st8/dev/hashes.json."""
    paths = get_paths(root)
    paths["dev_meta"].mkdir(parents=True, exist_ok=True)
    save_json(paths["dev_meta"] / "hashes.json", hashes)

# =============================================================================
# Utility Functions - Hashing and File Operations
# =============================================================================

def should_exclude(path: str, exclude_patterns: List[str]) -> bool:
    """Check if a path should be excluded based on patterns."""
    for pattern in exclude_patterns:
        # Handle directory patterns (ending with /)
        if pattern.endswith("/"):
            dir_pattern = pattern.rstrip("/")
            if path.startswith(dir_pattern + "/") or path == dir_pattern:
                return True
            # Also check each path component
            parts = path.split("/")
            for part in parts:
                if part == dir_pattern:
                    return True
        # Handle glob patterns
        elif fnmatch(path, pattern) or fnmatch(os.path.basename(path), pattern):
            return True
    return False


def hash_file(path: Path) -> str:
    """Calculate SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def get_file_hashes(directory: Path, exclude: List[str]) -> Dict[str, str]:
    """
    Get a dictionary of {relative_path: hash} for all files in directory.
    Excludes files matching patterns in exclude list.
    """
    hashes = {}
    if not directory.exists():
        return hashes

    for root, dirs, files in os.walk(directory):
        # Filter out excluded directories
        dirs[:] = [d for d in dirs if not should_exclude(d + "/", exclude)]

        for filename in files:
            filepath = Path(root) / filename
            relpath = str(filepath.relative_to(directory))

            if not should_exclude(relpath, exclude):
                try:
                    hashes[relpath] = hash_file(filepath)
                except (IOError, OSError):
                    pass  # Skip unreadable files

    return hashes


def hash_directory(directory: Path, exclude: List[str]) -> str:
    """Calculate a single hash representing the entire directory state."""
    file_hashes = get_file_hashes(directory, exclude)
    # Sort for deterministic ordering
    combined = json.dumps(sorted(file_hashes.items()), sort_keys=True)
    return hashlib.sha256(combined.encode()).hexdigest()

# =============================================================================
# Utility Functions - Diff and Version Calculation
# =============================================================================

def count_lines(path: Path) -> int:
    """Count lines in a file. Returns 0 for binary or unreadable files."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except (IOError, OSError):
        return 0


def read_file_lines(path: Path) -> List[str]:
    """Read file lines. Returns empty list for binary or unreadable files."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()
    except (IOError, OSError):
        return []


def diff_files(path1: Path, path2: Path) -> int:
    """
    Count the number of changed lines between two files.
    Returns total lines added + removed.
    """
    lines1 = read_file_lines(path1)
    lines2 = read_file_lines(path2)

    diff = list(unified_diff(lines1, lines2))

    # Count lines starting with + or - (but not ++ or --)
    changed = 0
    for line in diff:
        if (line.startswith("+") and not line.startswith("+++")) or \
           (line.startswith("-") and not line.startswith("---")):
            changed += 1

    return changed


def get_path_weight(relpath: str, weighted_paths: Dict[str, float]) -> float:
    """
    Get the weight for a file based on its path.
    Returns the weight of the most specific matching pattern, or 1.0 if no match.
    """
    best_weight = 1.0  # Default weight
    best_match_len = 0

    for pattern, weight in weighted_paths.items():
        if relpath.startswith(pattern) and len(pattern) > best_match_len:
            best_weight = weight
            best_match_len = len(pattern)

    return best_weight


def calculate_change_percentage(
    dev_dir: Path,
    stage_dir: Path,
    dev_hashes: Dict[str, str],
    stage_hashes: Dict[str, str],
    config: dict
) -> Tuple[float, int, Dict[str, str]]:
    """
    Calculate the weighted percentage of code changed.

    Returns:
        - percent_changed: Weighted percentage of lines changed
        - files_changed: Number of files changed
        - change_details: Dict mapping files to change type ('added', 'removed', 'modified')

    Version Calculation Logic:
    1. For each file in both dev and stage, calculate line count * path weight
    2. Sum up total weighted lines across all files
    3. For changed files, calculate changed lines * path weight
    4. Percentage = (weighted changed lines / weighted total lines) * 100
    """
    weighted_paths = config.get("weighted_paths", {})

    all_files = set(dev_hashes.keys()) | set(stage_hashes.keys())

    total_weighted_lines = 0
    changed_weighted_lines = 0
    files_changed = 0
    change_details = {}

    for relpath in all_files:
        weight = get_path_weight(relpath, weighted_paths)
        dev_file = dev_dir / relpath
        stage_file = stage_dir / relpath

        in_dev = relpath in dev_hashes
        in_stage = relpath in stage_hashes

        if in_dev and in_stage:
            # File exists in both - count lines from dev (current)
            lines = count_lines(dev_file)
            total_weighted_lines += lines * weight

            # If hashes differ, calculate diff
            if dev_hashes[relpath] != stage_hashes[relpath]:
                diff_lines = diff_files(stage_file, dev_file)
                changed_weighted_lines += diff_lines * weight
                files_changed += 1
                change_details[relpath] = "modified"

        elif in_dev:
            # New file in dev
            lines = count_lines(dev_file)
            total_weighted_lines += lines * weight
            changed_weighted_lines += lines * weight  # All lines are "added"
            files_changed += 1
            change_details[relpath] = "added"

        else:
            # File only in stage (deleted)
            lines = count_lines(stage_file)
            # Still count toward total (from stage perspective)
            total_weighted_lines += lines * weight
            changed_weighted_lines += lines * weight  # All lines are "removed"
            files_changed += 1
            change_details[relpath] = "removed"

    if total_weighted_lines == 0:
        return 0.0, files_changed, change_details

    percent = (changed_weighted_lines / total_weighted_lines) * 100
    return percent, files_changed, change_details


def determine_version_bump(percent_changed: float, thresholds: dict) -> str:
    """
    Determine the version bump type based on percentage changed.

    Default thresholds:
    - 0-5% = patch
    - 5-25% = minor
    - >25% = major
    """
    patch_threshold = thresholds.get("patch", 0.05) * 100
    minor_threshold = thresholds.get("minor", 0.25) * 100

    if percent_changed <= patch_threshold:
        return "patch"
    elif percent_changed <= minor_threshold:
        return "minor"
    else:
        return "major"


def bump_version(version: str, bump_type: str) -> str:
    """Apply a version bump and return the new version string."""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(.*)$", version)
    if not match:
        # If version doesn't match pattern, start fresh
        return "0.1.0"

    major, minor, patch = int(match.group(1)), int(match.group(2)), int(match.group(3))
    suffix = match.group(4)  # Preserve any suffix like -beta

    if bump_type == "major":
        major += 1
        minor = 0
        patch = 0
    elif bump_type == "minor":
        minor += 1
        patch = 0
    else:  # patch
        patch += 1

    return f"{major}.{minor}.{patch}{suffix}"


def parse_hashtags(message: str) -> List[str]:
    """Extract hashtags from a message."""
    return re.findall(r"#(\w+)", message)

# =============================================================================
# Utility Functions - File Copy Operations
# =============================================================================

def copy_changed_files(
    src: Path,
    dst: Path,
    src_hashes: Dict[str, str],
    dst_hashes: Dict[str, str],
    exclude: List[str]
) -> int:
    """
    Copy only changed files from src to dst.
    Returns number of files copied.
    """
    copied = 0

    # Create dst if it doesn't exist
    dst.mkdir(parents=True, exist_ok=True)

    # Remove files that exist in dst but not in src
    for relpath in dst_hashes:
        if relpath not in src_hashes:
            dst_file = dst / relpath
            if dst_file.exists():
                dst_file.unlink()
            # Remove empty parent directories
            parent = dst_file.parent
            while parent != dst and parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent

    # Copy new or changed files
    for relpath, src_hash in src_hashes.items():
        if relpath not in dst_hashes or dst_hashes[relpath] != src_hash:
            src_file = src / relpath
            dst_file = dst / relpath

            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)
            copied += 1

    return copied


def copy_directory(src: Path, dst: Path, exclude: List[str]) -> None:
    """Copy entire directory from src to dst, respecting exclusions."""
    # Clear destination
    if dst.exists():
        shutil.rmtree(dst)

    dst.mkdir(parents=True, exist_ok=True)

    # Copy files
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if not should_exclude(d + "/", exclude)]

        rel_root = Path(root).relative_to(src)

        for filename in files:
            relpath = str(rel_root / filename)
            if relpath.startswith("./"):
                relpath = relpath[2:]

            if not should_exclude(relpath, exclude):
                src_file = Path(root) / filename
                dst_file = dst / rel_root / filename
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)


def restore_to_working_dir(src: Path, dst: Path, exclude: List[str]) -> None:
    """
    Restore files from snapshot to working directory.
    Only touches files that are tracked (exist in src), preserves untracked files.
    """
    src_hashes = get_file_hashes(src, exclude)
    dst_hashes = get_file_hashes(dst, exclude)

    # Remove files in dst that don't exist in src (were deleted)
    for relpath in dst_hashes:
        if relpath not in src_hashes:
            dst_file = dst / relpath
            if dst_file.exists():
                dst_file.unlink()
            # Remove empty parent directories
            parent = dst_file.parent
            while parent != dst and parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent

    # Copy/overwrite files from src
    for relpath in src_hashes:
        src_file = src / relpath
        dst_file = dst / relpath
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)

# =============================================================================
# Commands
# =============================================================================

def cmd_init(args) -> int:
    """Initialize ST8 in the current directory."""
    root = Path.cwd()
    paths = get_paths(root)

    # Check if already initialized
    if paths["st8"].exists():
        print("Error: ST8 already initialized in this directory.", file=sys.stderr)
        return 1

    # Create directory structure inside .st8/
    paths["st8"].mkdir(parents=True)
    paths["dev_meta"].mkdir(exist_ok=True)
    paths["stage"].mkdir(exist_ok=True)
    paths["prod"].mkdir(exist_ok=True)

    # Write config
    save_json(paths["config"], DEFAULT_CONFIG)

    # Write initial state
    state = DEFAULT_STATE.copy()
    state["version"] = "0.1.0"
    save_json(paths["state"], state)

    # Write empty history
    save_json(paths["history"], [])

    # Save initial dev hashes (empty or current state)
    exclude = DEFAULT_CONFIG["exclude"]
    dev_hashes = get_file_hashes(root, exclude)
    save_dev_hashes(root, dev_hashes)

    print(f"Initialized ST8 in {root}")
    print("")
    print("Directory structure created:")
    print("  ./         - Your working directory (dev state)")
    print("  .st8/      - ST8 metadata and snapshots")
    print("    dev/     - Dev state metadata (hashes)")
    print("    stage/   - Promoted snapshot")
    print("    prod/    - Release snapshot")
    print("")
    print("Next steps:")
    print("  1. Edit your code in this directory")
    print("  2. Run 'st8 promote \"Initial commit\"' to create first snapshot")

    return 0


def cmd_promote(args) -> int:
    """Promote working directory to stage."""
    root = require_st8_root()
    paths = get_paths(root)
    config = load_config(root)
    state = load_state(root)
    history = load_history(root)

    exclude = config.get("exclude", [])

    # Get hashes - dev is the working directory
    dev_hashes = get_file_hashes(paths["dev"], exclude)
    stage_hashes = get_file_hashes(paths["stage"], exclude)

    # Check if there are any files
    if not dev_hashes:
        print("Error: Working directory has no trackable files.", file=sys.stderr)
        return 1

    # Check if there are changes
    if dev_hashes == stage_hashes:
        print("Nothing to promote. Working directory matches stage.")
        return 0

    # Calculate changes
    percent_changed, files_changed, change_details = calculate_change_percentage(
        paths["dev"], paths["stage"], dev_hashes, stage_hashes, config
    )

    # Determine version bump
    if args.major:
        bump_type = "major"
    elif args.minor:
        bump_type = "minor"
    elif args.patch:
        bump_type = "patch"
    else:
        bump_type = determine_version_bump(percent_changed, config.get("thresholds", {}))

    # Get current version and bump
    current_version = state.get("version", "0.1.0")
    new_version = bump_version(current_version, bump_type)

    # Parse message and hashtags
    message = " ".join(args.message) if args.message else ""
    tags = parse_hashtags(message)

    # Clean message (remove hashtags for cleaner display)
    clean_message = re.sub(r"#\w+\s*", "", message).strip()

    # Copy changed files to stage
    copied = copy_changed_files(paths["dev"], paths["stage"], dev_hashes, stage_hashes, exclude)

    # Update dev metadata
    save_dev_hashes(root, dev_hashes)

    # Update state
    state["version"] = new_version
    state["dev_hash"] = hash_directory(paths["dev"], exclude)
    state["stage_hash"] = hash_directory(paths["stage"], exclude)
    state["last_promoted"] = datetime.now(timezone.utc).isoformat()
    save_state(root, state)

    # Update config version
    config["current_version"] = new_version
    save_config(root, config)

    # Add to history
    history_entry = {
        "version": new_version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": clean_message or message,
        "tags": tags,
        "percent_changed": round(percent_changed, 2),
        "files_changed": files_changed,
        "type": "promote"
    }
    history.append(history_entry)
    save_history(root, history)

    # Print summary
    print(f"Promoted to stage as v{new_version} ({percent_changed:.1f}% changed)")
    print(f"  Files changed: {files_changed}")
    if tags:
        print(f"  Tags: {', '.join('#' + t for t in tags)}")
    if clean_message:
        print(f"  Message: {clean_message}")

    return 0


def cmd_restore(args) -> int:
    """Restore working directory from stage."""
    root = require_st8_root()
    paths = get_paths(root)
    config = load_config(root)

    exclude = config.get("exclude", [])

    # Check if stage exists and has content
    stage_hashes = get_file_hashes(paths["stage"], exclude)
    if not stage_hashes:
        print("Error: No stage snapshot exists. Run 'st8 promote' first.", file=sys.stderr)
        return 1

    # Check for uncommitted changes
    dev_hashes = get_file_hashes(paths["dev"], exclude)

    if dev_hashes != stage_hashes and not args.force:
        print("Warning: Working directory has changes that will be lost.")
        print("")

        # Show what will be lost
        changed = set(dev_hashes.keys()) ^ set(stage_hashes.keys())
        for path in dev_hashes:
            if path in stage_hashes:
                if dev_hashes[path] != stage_hashes[path]:
                    changed.add(path)

        if changed:
            print("Changed files:")
            for path in sorted(changed)[:10]:
                print(f"  {path}")
            if len(changed) > 10:
                print(f"  ... and {len(changed) - 10} more")
            print("")

        response = input("Restore anyway? [y/N] ").strip().lower()
        if response != "y":
            print("Restore cancelled.")
            return 0

    # Restore from stage to working directory
    restore_to_working_dir(paths["stage"], paths["dev"], exclude)

    # Update dev metadata
    save_dev_hashes(root, stage_hashes)

    print("Restored working directory from stage")
    return 0


def cmd_status(args) -> int:
    """Show current status."""
    root = require_st8_root()
    paths = get_paths(root)
    config = load_config(root)
    state = load_state(root)
    history = load_history(root)

    exclude = config.get("exclude", [])

    # Current version
    version = state.get("version", "0.1.0")
    print(f"Version: v{version}")
    print("")

    # Get hashes
    dev_hashes = get_file_hashes(paths["dev"], exclude)
    stage_hashes = get_file_hashes(paths["stage"], exclude)

    # Calculate changes
    if dev_hashes and stage_hashes:
        percent_changed, files_changed, change_details = calculate_change_percentage(
            paths["dev"], paths["stage"], dev_hashes, stage_hashes, config
        )

        if files_changed > 0:
            print("Changes (compared to stage):")

            # Group by change type
            added = [p for p, t in change_details.items() if t == "added"]
            modified = [p for p, t in change_details.items() if t == "modified"]
            removed = [p for p, t in change_details.items() if t == "removed"]

            for path in sorted(added)[:5]:
                print(f"  + {path}")
            for path in sorted(modified)[:5]:
                print(f"  ~ {path}")
            for path in sorted(removed)[:5]:
                print(f"  - {path}")

            total_shown = min(5, len(added)) + min(5, len(modified)) + min(5, len(removed))
            remaining = files_changed - total_shown
            if remaining > 0:
                print(f"  ... and {remaining} more")

            print("")

            # Estimated version bump
            bump_type = determine_version_bump(percent_changed, config.get("thresholds", {}))
            next_version = bump_version(version, bump_type)
            print(f"Estimated promotion: v{version} -> v{next_version} ({bump_type}, {percent_changed:.1f}% changed)")
        else:
            print("No changes (working directory matches stage)")
    elif dev_hashes:
        print(f"Working directory has {len(dev_hashes)} files (no stage snapshot yet)")
    else:
        print("Working directory has no trackable files")

    print("")

    # Recent history
    if history:
        print("Recent history:")
        for entry in history[-3:]:
            ts = entry.get("timestamp", "")[:10]
            ver = entry.get("version", "?")
            msg = entry.get("message", "")[:40]
            entry_type = entry.get("type", "promote")
            type_marker = "[R]" if entry_type == "release" else "[P]"
            print(f"  {type_marker} v{ver} ({ts}): {msg}")
    else:
        print("No history yet")

    return 0


def cmd_release(args) -> int:
    """Release stage to prod."""
    root = require_st8_root()
    paths = get_paths(root)
    config = load_config(root)
    state = load_state(root)
    history = load_history(root)

    exclude = config.get("exclude", [])

    # Check if stage has content
    stage_hashes = get_file_hashes(paths["stage"], exclude)
    if not stage_hashes:
        print("Error: No stage snapshot exists. Run 'st8 promote' first.", file=sys.stderr)
        return 1

    # Check if stage differs from prod
    prod_hashes = get_file_hashes(paths["prod"], exclude)
    if stage_hashes == prod_hashes:
        print("Nothing to release. Stage matches prod.")
        return 0

    # Parse message and hashtags
    message = " ".join(args.message) if args.message else ""
    tags = parse_hashtags(message)
    clean_message = re.sub(r"#\w+\s*", "", message).strip()

    version = state.get("version", "0.1.0")

    # Copy stage to prod
    copy_directory(paths["stage"], paths["prod"], exclude)

    # Update state
    state["prod_hash"] = hash_directory(paths["prod"], exclude)
    save_state(root, state)

    # Calculate change percentage from previous prod
    if prod_hashes:
        percent_changed, files_changed, _ = calculate_change_percentage(
            paths["stage"], paths["prod"], stage_hashes, prod_hashes, config
        )
    else:
        files_changed = len(stage_hashes)
        percent_changed = 100.0

    # Add to history
    history_entry = {
        "version": version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": clean_message or message,
        "tags": tags,
        "percent_changed": round(percent_changed, 2),
        "files_changed": files_changed,
        "type": "release"
    }
    history.append(history_entry)
    save_history(root, history)

    # Print summary
    print(f"Released to prod as v{version}")
    if tags:
        print(f"  Tags: {', '.join('#' + t for t in tags)}")
    if clean_message:
        print(f"  Message: {clean_message}")
    print("")
    print("Prod snapshot saved to: .st8/prod/")
    print("")
    print("To deploy with git:")
    print(f"  cd .st8/prod && git init && git add . && git commit -m \"Release v{version}\"")

    return 0


def cmd_log(args) -> int:
    """Show commit history."""
    root = require_st8_root()
    history = load_history(root)

    if not history:
        print("No history yet.")
        return 0

    # Filter by tag if specified
    if args.tag:
        tag = args.tag.lstrip("#")
        history = [e for e in history if tag in e.get("tags", [])]
        if not history:
            print(f"No entries with tag #{tag}")
            return 0

    # Limit to last N if specified
    if args.last:
        history = history[-args.last:]

    # Print entries (newest first)
    for entry in reversed(history):
        version = entry.get("version", "?")
        timestamp = entry.get("timestamp", "")[:19].replace("T", " ")
        message = entry.get("message", "")
        tags = entry.get("tags", [])
        percent = entry.get("percent_changed", 0)
        files = entry.get("files_changed", 0)
        entry_type = entry.get("type", "promote")

        type_label = "RELEASE" if entry_type == "release" else "PROMOTE"

        print(f"[{type_label}] v{version}")
        print(f"  Date: {timestamp}")
        if tags:
            print(f"  Tags: {', '.join('#' + t for t in tags)}")
        if message:
            print(f"  Message: {message}")
        print(f"  Changed: {files} files ({percent:.1f}%)")
        print("")

    return 0


def cmd_stats(args) -> int:
    """Show statistics."""
    root = require_st8_root()
    history = load_history(root)

    if not history:
        print("No history yet.")
        return 0

    # Count totals
    promotes = [e for e in history if e.get("type") != "release"]
    releases = [e for e in history if e.get("type") == "release"]

    print("Statistics")
    print("=" * 40)
    print(f"Total promotions: {len(promotes)}")
    print(f"Total releases: {len(releases)}")
    print("")

    # Tag frequency
    tag_counts = {}
    for entry in history:
        for tag in entry.get("tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    if tag_counts:
        print("Tag frequency:")
        for tag, count in sorted(tag_counts.items(), key=lambda x: -x[1]):
            print(f"  #{tag}: {count}")
        print("")

    # Version history
    versions = [e.get("version") for e in history if e.get("version")]
    if versions:
        print("Version history:")
        print(f"  First: v{versions[0]}")
        print(f"  Current: v{versions[-1]}")

        # Count version bump types
        major_bumps = minor_bumps = patch_bumps = 0
        for i in range(1, len(versions)):
            prev = versions[i-1].split(".")
            curr = versions[i].split(".")
            try:
                if int(curr[0]) > int(prev[0]):
                    major_bumps += 1
                elif int(curr[1]) > int(prev[1]):
                    minor_bumps += 1
                else:
                    patch_bumps += 1
            except (ValueError, IndexError):
                pass

        print(f"  Major bumps: {major_bumps}")
        print(f"  Minor bumps: {minor_bumps}")
        print(f"  Patch bumps: {patch_bumps}")

    return 0

# =============================================================================
# CLI Setup
# =============================================================================

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="st8",
        description="ST8 - Minimalist State-Based Versioning for Solo Developers",
        epilog="Your working directory is the dev state. All snapshots stored in .st8/"
        
    )
    parser.add_argument("--version", action="version", version=f"st8 {__version__}")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init
    init_parser = subparsers.add_parser("init", help="Initialize ST8 in current directory")
    init_parser.set_defaults(func=cmd_init)

    # promote
    promote_parser = subparsers.add_parser("promote", help="Promote working directory to stage")
    promote_parser.add_argument("message", nargs="*", help="Commit message (can include #hashtags)")
    promote_parser.add_argument("--major", action="store_true", help="Force major version bump")
    promote_parser.add_argument("--minor", action="store_true", help="Force minor version bump")
    promote_parser.add_argument("--patch", action="store_true", help="Force patch version bump")
    promote_parser.set_defaults(func=cmd_promote)

    # restore
    restore_parser = subparsers.add_parser("restore", help="Restore working directory from stage")
    restore_parser.add_argument("--force", "-f", action="store_true", help="Skip confirmation")
    restore_parser.set_defaults(func=cmd_restore)

    # release
    release_parser = subparsers.add_parser("release", help="Release stage to prod")
    release_parser.add_argument("message", nargs="*", help="Release message (can include #hashtags)")
    release_parser.set_defaults(func=cmd_release)

    # status
    status_parser = subparsers.add_parser("status", help="Show current status")
    status_parser.set_defaults(func=cmd_status)

    # log
    log_parser = subparsers.add_parser("log", help="Show commit history")
    log_parser.add_argument("--tag", "-t", help="Filter by hashtag")
    log_parser.add_argument("--last", "-n", type=int, help="Show last N entries")
    log_parser.set_defaults(func=cmd_log)

    # stats
    stats_parser = subparsers.add_parser("stats", help="Show statistics")
    stats_parser.set_defaults(func=cmd_stats)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
