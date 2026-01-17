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
import ftplib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from difflib import unified_diff
from fnmatch import fnmatch
from pathlib import Path
from typing import Dict, List, Optional, Tuple

__version__ = "1.3.1"

# =============================================================================
# Constants and Defaults
# =============================================================================

ST8_DIR = ".st8"
CONFIG_FILE = "config.json"
STATE_FILE = "state.json"
HISTORY_FILE = "history.json"
DEPLOY_FILE = "deploy.json"
TASKS_DIR = "tasks"
TASK_ARCHIVES_DIR = "task_archives"
ACTIVE_TASK_FILE = "active.json"
BACKLOG_FILE = "backlog.json"
HOTFIXES_DIR = "hotfixes"
MISSION_FILE = "MISSION.md"
REPORT_FILE = "REPORT.md"
GLOBAL_TASKS_FILE = "global_tasks.json"  # Centralized tasks file (sibling to st8.py)

# Performance optimization: skip diff calculation for files larger than this
MAX_DIFF_SIZE = 10 * 1024 * 1024  # 10MB

DEFAULT_CONFIG = {
    "name": None,  # Project name (set via st8 init "name")
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

DEFAULT_DEPLOY_IGNORE = [
    # Development files
    ".DS_Store",
    ".st8",
    ".git",
    ".gitignore",
    ".vscode",
    ".idea",
    ".claude",
    ".tracy",
    "LICENSE",
    "README.md",
    "CLAUDE.md",
    "MISSION.md",
    "REPORT.md",
    # Images
    "*.jpg",
    "*.jpeg",
    "*.png",
    "*.gif",
    "*.svg",
    "*.webp",
    "*.ico",
    # Videos
    "*.mp4",
    "*.mov",
    "*.avi",
    "*.wmv",
    "*.flv",
    "*.mkv",
    # Audio
    "*.mp3",
    "*.wav",
    "*.ogg",
    # Documents
    "*.pdf",
    # Archives
    "*.zip",
    "*.tar",
    "*.gz",
    "*.rar",
    "*.7z"
]

DEFAULT_DEPLOY_CONFIG = {
    "environments": {
        "dev": {
            "enabled": False,
            "auto_deploy": False,  # Auto-deploy on save (not yet implemented)
            "protocol": "ssh",  # "ssh" (rsync/scp) or "ftp"
            "host": "",
            "port": 22,
            "username": "",
            "password": "",  # For FTP; SSH uses key_path
            "remote_path": "",
            "ignore": DEFAULT_DEPLOY_IGNORE.copy()
        },
        "stg": {
            "enabled": False,
            "auto_deploy": False,  # Auto-deploy on promote
            "protocol": "ssh",
            "host": "",
            "port": 22,
            "username": "",
            "password": "",
            "remote_path": "",
            "ignore": DEFAULT_DEPLOY_IGNORE.copy()
        },
        "prod": {
            "enabled": False,
            "auto_deploy": False,  # Auto-deploy on release
            "protocol": "ssh",
            "host": "",
            "port": 22,
            "username": "",
            "password": "",
            "remote_path": "",
            "ignore": DEFAULT_DEPLOY_IGNORE.copy()
        }
    },
    "auth": {
        "method": "key",  # "key" for SSH, "password" for FTP
        "key_path": "~/.ssh/id_rsa"
    },
    "options": {
        "backup_before_deploy": True,
        "post_deploy_command": ""
    },
    "git": {
        "enabled": False,
        "stage_branch": "stage",
        "prod_branch": "main",
        "auto_push": True
    },
    "ai": {
        "simple": {
            "provider": "ollama",  # "ollama", "openai", "anthropic"
            "model": "llama3.2",   # Model name for the provider
            "endpoint": "http://localhost:11434"  # For ollama
        },
        "agent": {
            "provider": "claude-code",  # "claude-code", "aider", "cursor"
            "command": "claude"  # Command to invoke the agent
        }
    }
}

# =============================================================================
# Mission and Report Templates
# =============================================================================

MISSION_TEMPLATE = """# Mission

## Objective
<!-- What do you want to accomplish? Be specific and measurable. -->


## Context
<!-- What's the current state? What problem are you solving? -->


## Requirements
<!-- List specific requirements. Use checkboxes for tracking. -->
- [ ]


## Constraints
<!-- Technical constraints, time limits, dependencies, etc. -->


## Success Criteria
<!-- How will you know when this is done correctly? -->


## Files to Focus On
<!-- Which files should the agent prioritize? -->


## Out of Scope
<!-- What should the agent NOT touch or change? -->


## Additional Context
<!-- Any other relevant information: API docs, examples, related issues, etc. -->


---
*Tips for effective missions:*
- *Be specific: "Add logout button to navbar" > "improve auth"*
- *Provide examples of desired output when possible*
- *Mention edge cases you're aware of*
- *Reference existing patterns in the codebase*
"""

REPORT_TEMPLATE = """# Mission Report

## Status
<!-- SUCCESS | PARTIAL | BLOCKED | FAILED -->


## Summary
<!-- 1-2 sentence overview of what was accomplished -->


## Changes Made
<!-- List of files modified and what was changed -->


## Challenges Encountered
<!-- What was difficult? What slowed progress? -->


## Decisions Made
<!-- Key decisions and rationale -->


## Testing Done
<!-- How was the work validated? -->


## Known Issues
<!-- Any remaining problems or edge cases -->


## Improvement Opportunities
<!-- Suggestions for future enhancements -->


## Ideas for Future
<!-- Related features or refactors worth considering -->


## Mission Feedback
<!-- How could the MISSION.md have been clearer or more helpful? -->


---
*Completed: YYYY-MM-DD HH:MM*
"""

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
      project/           <- root (this IS the dev state / Working Environment)
      ├── (user files)   <- Working Environment files
      └── .st8/
          ├── config.json
          ├── state.json
          ├── history.json
          ├── active.json       <- active task reference
          ├── backlog.json      <- task backlog queue
          ├── dev/              <- metadata only (hashes)
          ├── stage/            <- promoted snapshot
          ├── prod/             <- release snapshot
          ├── tasks/            <- task folders (frozen snapshots)
          │   └── <task_id>/
          │       ├── meta.json
          │       └── snapshot/  <- frozen copy of WE at task creation
          ├── task_archives/    <- archived task snapshots (zip files)
          └── hotfixes/         <- hotfix branches
    """
    st8_dir = root / ST8_DIR
    return {
        "root": root,
        "dev": root,                    # Working directory IS dev (Working Environment)
        "st8": st8_dir,
        "dev_meta": st8_dir / "dev",    # Metadata for dev state
        "stage": st8_dir / "stage",     # Full snapshot
        "prod": st8_dir / "prod",       # Full snapshot
        "config": st8_dir / CONFIG_FILE,
        "state": st8_dir / STATE_FILE,
        "history": st8_dir / HISTORY_FILE,
        "deploy": st8_dir / DEPLOY_FILE,
        "tasks": st8_dir / TASKS_DIR,
        "task_archives": st8_dir / TASK_ARCHIVES_DIR,
        "active_task": st8_dir / ACTIVE_TASK_FILE,
        "backlog": st8_dir / BACKLOG_FILE,
        "hotfixes": st8_dir / HOTFIXES_DIR,
        "mission": st8_dir / MISSION_FILE,
        "report": st8_dir / REPORT_FILE,
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


def load_deploy_config(root: Path) -> dict:
    """Load deploy.json."""
    paths = get_paths(root)
    return load_json(paths["deploy"], DEFAULT_DEPLOY_CONFIG)


def save_deploy_config(root: Path, config: dict) -> None:
    """Save deploy.json."""
    paths = get_paths(root)
    save_json(paths["deploy"], config)

# =============================================================================
# Utility Functions - Task Management
# =============================================================================
#
# Task System Semantics:
#   - Working Environment (WE): The live filesystem where user works (project root)
#   - Task Folder (TF): A frozen snapshot of WE at task creation time
#
# Task Lifecycle:
#   1. CREATE: Copy entire WE to TF (frozen snapshot)
#   2. ACTIVE: User works only in WE; TF remains untouched
#   3. RESOLVE: Either CANCEL (restore from TF) or PROMOTE (keep WE, dispose TF)
#
# =============================================================================

def generate_task_id() -> str:
    """Generate a timestamp-based task ID."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def get_task_dir(root: Path, task_id: str) -> Path:
    """Get the directory for a specific task."""
    paths = get_paths(root)
    return paths["tasks"] / task_id


def get_task_snapshot_path(root: Path, task_id: str) -> Path:
    """Get the snapshot directory for a task (frozen copy of WE)."""
    return get_task_dir(root, task_id) / "snapshot"


def get_active_task(root: Path) -> Optional[dict]:
    """Load active task reference, or None if no active task."""
    paths = get_paths(root)
    if paths["active_task"].exists():
        with open(paths["active_task"], "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_active_task(root: Path, task_data: dict) -> None:
    """Save active task reference."""
    paths = get_paths(root)
    save_json(paths["active_task"], task_data)


def clear_active_task(root: Path) -> None:
    """Remove active task file."""
    paths = get_paths(root)
    if paths["active_task"].exists():
        paths["active_task"].unlink()


def load_task_meta(root: Path, task_id: str) -> Optional[dict]:
    """Load task metadata."""
    task_dir = get_task_dir(root, task_id)
    meta_file = task_dir / "meta.json"
    if meta_file.exists():
        with open(meta_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_task_meta(root: Path, task_id: str, meta: dict) -> None:
    """Save task metadata."""
    task_dir = get_task_dir(root, task_id)
    task_dir.mkdir(parents=True, exist_ok=True)
    save_json(task_dir / "meta.json", meta)


def create_task_snapshot(root: Path, task_id: str) -> int:
    """
    Create a frozen snapshot of the Working Environment for a task.
    Copies entire WE to the task's snapshot directory.
    Ignores .st8/ to prevent recursion.
    Returns number of files copied.
    """
    paths = get_paths(root)
    snapshot_dir = get_task_snapshot_path(root, task_id)

    # Clear snapshot if it exists (shouldn't happen for new tasks)
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    for item in paths["root"].rglob("*"):
        if item.is_file():
            relpath = str(item.relative_to(paths["root"]))
            # Always skip .st8/ to prevent recursion
            if relpath.startswith(".st8/") or "/.st8/" in relpath:
                continue
            dst_file = snapshot_dir / relpath
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dst_file)
            copied += 1

    return copied


def get_task_snapshot_hashes(root: Path, task_id: str) -> Dict[str, str]:
    """
    Get file hashes from the task snapshot.
    """
    snapshot_dir = get_task_snapshot_path(root, task_id)
    return get_file_hashes(snapshot_dir, [".st8/"])


def get_working_env_hashes(root: Path) -> Dict[str, str]:
    """
    Get file hashes from the Working Environment.
    Excludes .st8/ directory.
    """
    paths = get_paths(root)
    return get_file_hashes(paths["root"], [".st8/"])


def clear_working_environment(root: Path) -> int:
    """
    Delete all contents of the Working Environment (except .st8/).
    This is a destructive operation - use with caution.
    Returns number of items deleted.
    """
    paths = get_paths(root)
    deleted = 0

    # Collect all top-level items to delete (skip .st8/)
    items_to_delete = []
    for item in paths["root"].iterdir():
        if item.name == ".st8":
            continue
        items_to_delete.append(item)

    # Delete all collected items
    for item in items_to_delete:
        if item.is_file():
            item.unlink()
            deleted += 1
        elif item.is_dir():
            # Count files before deleting
            for _ in item.rglob("*"):
                deleted += 1
            shutil.rmtree(item)

    return deleted


def restore_from_task_snapshot(root: Path, task_id: str) -> int:
    """
    Fully restore Working Environment from task snapshot.
    This is a FULL REPLACEMENT:
      1. Delete all WE contents (except .st8/)
      2. Copy all snapshot contents to WE
    Returns number of files restored.
    """
    paths = get_paths(root)
    snapshot_dir = get_task_snapshot_path(root, task_id)

    if not snapshot_dir.exists():
        return 0

    # Step 1: Clear the Working Environment
    clear_working_environment(root)

    # Step 2: Copy snapshot to Working Environment
    restored = 0
    for item in snapshot_dir.rglob("*"):
        if item.is_file():
            relpath = str(item.relative_to(snapshot_dir))
            dst_file = paths["root"] / relpath
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dst_file)
            restored += 1

    return restored


def archive_task_snapshot(root: Path, task_id: str, message: str = "") -> Optional[Path]:
    """
    Archive the task snapshot as a zip file.
    Returns the path to the archive file, or None if snapshot doesn't exist.
    """
    paths = get_paths(root)
    snapshot_dir = get_task_snapshot_path(root, task_id)

    if not snapshot_dir.exists():
        return None

    # Ensure archive directory exists
    paths["task_archives"].mkdir(parents=True, exist_ok=True)

    # Create archive filename with timestamp
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_message = re.sub(r"[^\w\-]", "_", message)[:30] if message else ""
    archive_name = f"{task_id}_{timestamp}"
    if safe_message:
        archive_name += f"_{safe_message}"
    archive_name += ".zip"
    archive_path = paths["task_archives"] / archive_name

    # Create zip archive
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in snapshot_dir.rglob("*"):
            if item.is_file():
                arcname = str(item.relative_to(snapshot_dir))
                zf.write(item, arcname)

    return archive_path


def delete_task_folder(root: Path, task_id: str) -> bool:
    """
    Delete the entire task folder (snapshot + metadata).
    Returns True if deleted, False if didn't exist.
    """
    task_dir = get_task_dir(root, task_id)
    if task_dir.exists():
        shutil.rmtree(task_dir)
        return True
    return False


def list_tasks(root: Path) -> List[dict]:
    """List all tasks with their metadata."""
    paths = get_paths(root)
    tasks = []

    if not paths["tasks"].exists():
        return tasks

    for task_dir in sorted(paths["tasks"].iterdir(), reverse=True):
        if task_dir.is_dir():
            meta = load_task_meta(root, task_dir.name)
            if meta:
                tasks.append(meta)

    return tasks


# =============================================================================
# Utility Functions - Backlog Management
# =============================================================================

def load_backlog(root: Path) -> List[dict]:
    """Load the task backlog."""
    paths = get_paths(root)
    if paths["backlog"].exists():
        with open(paths["backlog"], "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_backlog(root: Path, backlog: List[dict]) -> None:
    """Save the task backlog."""
    paths = get_paths(root)
    save_json(paths["backlog"], backlog)


def add_backlog_item(root: Path, message: str, tags: List[str]) -> dict:
    """Add a new item to the backlog. Returns the created item."""
    backlog = load_backlog(root)

    # Generate backlog ID (sequential within backlog)
    next_id = 1
    if backlog:
        existing_ids = [item.get("id", 0) for item in backlog]
        next_id = max(existing_ids) + 1

    item = {
        "id": next_id,
        "message": message,
        "tags": tags,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending"
    }

    backlog.append(item)
    save_backlog(root, backlog)
    return item


def get_backlog_item(root: Path, item_id: int) -> Optional[dict]:
    """Get a specific backlog item by ID."""
    backlog = load_backlog(root)
    for item in backlog:
        if item.get("id") == item_id:
            return item
    return None


def remove_backlog_item(root: Path, item_id: int) -> bool:
    """Remove an item from the backlog. Returns True if removed."""
    backlog = load_backlog(root)
    original_len = len(backlog)
    backlog = [item for item in backlog if item.get("id") != item_id]
    if len(backlog) < original_len:
        save_backlog(root, backlog)
        return True
    return False


# =============================================================================
# Utility Functions - Global Task Tracking (Centralized across all projects)
# =============================================================================

def get_st8_install_dir() -> Path:
    """Get the directory where st8.py is installed."""
    return Path(__file__).resolve().parent


def get_global_tasks_path() -> Path:
    """Get path to the centralized global_tasks.json file."""
    return get_st8_install_dir() / GLOBAL_TASKS_FILE


def load_global_tasks() -> dict:
    """
    Load global tasks data from the centralized file.

    Structure:
    {
        "projects": {
            "/path/to/project": {
                "name": "Project Name",
                "tasks": [...]
            }
        },
        "summary": {
            "total_tasks": 0,
            "total_time_seconds": 0,
            "last_updated": "..."
        }
    }
    """
    path = get_global_tasks_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {
        "projects": {},
        "summary": {
            "total_tasks": 0,
            "total_time_seconds": 0,
            "last_updated": None
        }
    }


def save_global_tasks(data: dict) -> None:
    """Save global tasks data to the centralized file."""
    path = get_global_tasks_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def record_global_task_event(
    root: Path,
    event_type: str,
    task_id: str,
    message: str = "",
    tags: List[str] = None,
    started_at: str = None,
    finished_at: str = None,
    duration_seconds: float = None,
    finalized: bool = False,
    extra: dict = None
) -> None:
    """
    Record a task event to the global tasks file.

    Args:
        root: Project root directory
        event_type: "start", "stop", or "abort"
        task_id: Unique task identifier
        message: Task description
        tags: List of tags
        started_at: ISO timestamp when task started
        finished_at: ISO timestamp when task ended
        duration_seconds: Duration in seconds
        finalized: Whether task was finalized (vs returned to backlog)
        extra: Additional data to store
    """
    tags = tags or []
    extra = extra or {}

    global_data = load_global_tasks()
    project_path = str(root.resolve())

    # Get project name from config if available
    config_path = root / ST8_DIR / CONFIG_FILE
    project_name = root.name
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                project_name = config.get("name") or root.name
        except (json.JSONDecodeError, IOError):
            pass

    # Initialize project entry if needed
    if project_path not in global_data["projects"]:
        global_data["projects"][project_path] = {
            "name": project_name,
            "tasks": []
        }
    else:
        # Update project name in case it changed
        global_data["projects"][project_path]["name"] = project_name

    # Create task event record
    event = {
        "event_type": event_type,
        "task_id": task_id,
        "message": message,
        "tags": tags,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": duration_seconds,
        "finalized": finalized,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **extra
    }

    global_data["projects"][project_path]["tasks"].append(event)

    # Update summary
    global_data["summary"]["total_tasks"] += 1 if event_type == "start" else 0
    if duration_seconds:
        global_data["summary"]["total_time_seconds"] = (
            global_data["summary"].get("total_time_seconds", 0) + duration_seconds
        )
    global_data["summary"]["last_updated"] = datetime.now(timezone.utc).isoformat()

    save_global_tasks(global_data)


# =============================================================================
# Utility Functions - Hashing and File Operations
# =============================================================================

def load_st8ignore(root: Path) -> List[str]:
    """
    Load additional exclude patterns from .st8ignore file.

    .st8ignore uses gitignore-style patterns (one per line).
    Lines starting with # are comments.
    Empty lines are ignored.
    """
    ignore_file = root / ".st8ignore"
    if not ignore_file.exists():
        return []

    patterns = []
    try:
        with open(ignore_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if not line or line.startswith("#"):
                    continue
                patterns.append(line)
    except (IOError, OSError):
        pass

    return patterns


def get_exclude_patterns(root: Path, config: dict) -> List[str]:
    """
    Get combined exclude patterns from config and .st8ignore.

    Returns a deduplicated list of patterns.
    """
    # Start with config excludes
    patterns = list(config.get("exclude", []))

    # Add .st8ignore patterns
    st8ignore = load_st8ignore(root)
    for pattern in st8ignore:
        if pattern not in patterns:
            patterns.append(pattern)

    return patterns


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

    For files larger than MAX_DIFF_SIZE, returns total line count as a
    performance optimization (assumes entire file changed).
    """
    # Check file sizes for performance optimization
    try:
        size1 = path1.stat().st_size if path1.exists() else 0
        size2 = path2.stat().st_size if path2.exists() else 0
        if size1 > MAX_DIFF_SIZE or size2 > MAX_DIFF_SIZE:
            # For very large files, skip diff and assume fully changed
            return count_lines(path1) + count_lines(path2)
    except (IOError, OSError):
        pass

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


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable string."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"

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

    # Get project name from args or use directory name as default
    project_name = args.name if args.name else root.name

    # Create directory structure inside .st8/
    paths["st8"].mkdir(parents=True)
    paths["dev_meta"].mkdir(exist_ok=True)
    paths["stage"].mkdir(exist_ok=True)
    paths["prod"].mkdir(exist_ok=True)

    # Write config with project name
    config = DEFAULT_CONFIG.copy()
    config["name"] = project_name
    save_json(paths["config"], config)

    # Write initial state
    state = DEFAULT_STATE.copy()
    state["version"] = "0.1.0"
    save_json(paths["state"], state)

    # Write empty history
    save_json(paths["history"], [])

    # Write deploy config (optional feature, disabled by default)
    save_json(paths["deploy"], DEFAULT_DEPLOY_CONFIG)

    # Write MISSION.md and REPORT.md templates
    with open(paths["mission"], "w", encoding="utf-8") as f:
        f.write(MISSION_TEMPLATE)
    with open(paths["report"], "w", encoding="utf-8") as f:
        f.write(REPORT_TEMPLATE)

    # Save initial dev hashes (empty or current state)
    exclude = DEFAULT_CONFIG["exclude"]
    dev_hashes = get_file_hashes(root, exclude)
    save_dev_hashes(root, dev_hashes)

    print(f"Initialized ST8: {project_name}")
    print(f"  Location: {root}")
    print("")
    print("Directory structure created:")
    print("  ./         - Your working directory (dev state)")
    print("  .st8/      - ST8 metadata and snapshots")
    print("    dev/     - Dev state metadata (hashes)")
    print("    stage/   - Promoted snapshot")
    print("    prod/    - Release snapshot")
    print("    MISSION.md  - Instructions for AI coding agent")
    print("    REPORT.md   - Agent feedback template")
    print("    deploy.json - Deployment & AI config")
    print("")

    # Ask about git setup
    if shutil.which("git"):
        print("Git Setup")
        print("-" * 40)
        try:
            response = input("Would you like to set up git? [Y/n]: ").strip().lower()
            if response in ("", "y", "yes"):
                # Check if gh is available for repo creation
                if shutil.which("gh"):
                    visibility = input("Create repo as (1) Public or (2) Private? [1]: ").strip()
                    private = visibility == "2"
                else:
                    private = False
                    print("Note: Install 'gh' CLI to auto-create GitHub repos")

                # Create a simple args object for git setup
                class GitSetupArgs:
                    def __init__(self, private_repo):
                        self.private = private_repo
                        self.message = ["Initial commit"]

                print("")
                cmd_git_setup(GitSetupArgs(private))
        except (KeyboardInterrupt, EOFError):
            print("\nSkipping git setup.")
        print("")

    print("Next steps:")
    print("  1. Edit your code in this directory")
    print("  2. Run 'st8 promote \"Initial commit\"' to create first snapshot")
    print("  3. Configure .st8/deploy.json for remote deployment (optional)")

    return 0


def cmd_promote(args) -> int:
    """Promote working directory to stage."""
    root = require_st8_root()
    paths = get_paths(root)
    config = load_config(root)
    state = load_state(root)
    history = load_history(root)

    exclude = get_exclude_patterns(root, config)

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

    # Auto-deploy stg if enabled
    deploy_config = load_deploy_config(root)
    stg_env = deploy_config.get("environments", {}).get("stg", {})
    if stg_env.get("auto_deploy") and stg_env.get("enabled"):
        print(f"\n[Auto-deploy] Deploying to stg...")

        # Create a simple args object for deploy
        class DeployArgs:
            def __init__(self):
                self.list = False
                self.environment = "stg"
                self.dry_run = False
                self.yes = True  # Skip confirmation for auto-deploy

        deploy_result = cmd_deploy(DeployArgs())
        if deploy_result != 0:
            print(f"Warning: Auto-deploy to stg failed", file=sys.stderr)

    # Git auto-commit stage if enabled
    git_config = deploy_config.get("git", {})
    if git_config.get("enabled"):
        print(f"\n[Git] Committing stage...")
        git_auto_commit(
            source_dir=paths["stage"],
            branch=git_config.get("stage_branch", "stage"),
            version=new_version,
            message=clean_message,
            auto_push=git_config.get("auto_push", False),
            root=root
        )

    return 0


def cmd_restore(args) -> int:
    """Restore working directory from stage."""
    root = require_st8_root()
    paths = get_paths(root)
    config = load_config(root)

    exclude = get_exclude_patterns(root, config)

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

    exclude = get_exclude_patterns(root, config)

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

    exclude = get_exclude_patterns(root, config)

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

    # Auto-deploy prod if enabled
    deploy_config = load_deploy_config(root)
    prod_env = deploy_config.get("environments", {}).get("prod", {})
    if prod_env.get("auto_deploy") and prod_env.get("enabled"):
        print(f"\n[Auto-deploy] Deploying to prod...")

        # Create a simple args object for deploy
        class DeployArgs:
            def __init__(self):
                self.list = False
                self.environment = "prod"
                self.dry_run = False
                self.yes = True  # Skip confirmation for auto-deploy

        deploy_result = cmd_deploy(DeployArgs())
        if deploy_result != 0:
            print(f"Warning: Auto-deploy to prod failed", file=sys.stderr)

    # Git auto-commit prod if enabled
    git_config = deploy_config.get("git", {})
    if git_config.get("enabled"):
        print(f"\n[Git] Committing prod...")
        git_auto_commit(
            source_dir=paths["prod"],
            branch=git_config.get("prod_branch", "main"),
            version=version,
            message=clean_message,
            auto_push=git_config.get("auto_push", False),
            root=root,
            force_push=True  # Prod is always authoritative
        )

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
# FTP Deployment Helper
# =============================================================================

def ftp_upload_directory(ftp: ftplib.FTP, local_dir: Path, remote_path: str,
                         ignore_patterns: List[str] = None) -> Tuple[int, int]:
    """
    Recursively upload a directory to FTP server.

    Returns (files_uploaded, errors).
    """
    if ignore_patterns is None:
        ignore_patterns = []

    files_uploaded = 0
    errors = 0

    def should_ignore(path: str) -> bool:
        """Check if path matches any ignore pattern."""
        for pattern in ignore_patterns:
            if fnmatch(path, pattern) or fnmatch(os.path.basename(path), pattern):
                return True
        return False

    def ensure_remote_dir(ftp: ftplib.FTP, path: str):
        """Ensure remote directory exists, creating it if needed."""
        dirs = path.strip("/").split("/")
        current = ""
        for d in dirs:
            if not d:
                continue
            current = f"{current}/{d}"
            try:
                ftp.cwd(current)
            except ftplib.error_perm:
                try:
                    ftp.mkd(current)
                    ftp.cwd(current)
                except ftplib.error_perm:
                    pass  # Directory might already exist

    def upload_recursive(local_path: Path, remote_base: str):
        nonlocal files_uploaded, errors

        for item in local_path.iterdir():
            rel_path = str(item.relative_to(local_dir))

            # Check ignore patterns
            if should_ignore(rel_path):
                continue

            remote_item = f"{remote_base}/{item.name}"

            if item.is_dir():
                # Create remote directory and recurse
                try:
                    ftp.mkd(remote_item)
                except ftplib.error_perm:
                    pass  # Directory might already exist
                upload_recursive(item, remote_item)
            else:
                # Upload file
                try:
                    with open(item, "rb") as f:
                        ftp.storbinary(f"STOR {remote_item}", f)
                    files_uploaded += 1
                    print(f"    {rel_path}")
                except Exception as e:
                    print(f"    ERROR: {rel_path} - {e}", file=sys.stderr)
                    errors += 1

    # Ensure base remote path exists
    ensure_remote_dir(ftp, remote_path)
    ftp.cwd(remote_path)

    # Upload all files
    upload_recursive(local_dir, remote_path)

    return files_uploaded, errors


# =============================================================================
# Git Auto-Commit Helper
# =============================================================================

def git_auto_commit(source_dir: Path, branch: str, version: str, message: str,
                    repo_url: str = "", auto_push: bool = False, root: Path = None,
                    force_push: bool = False) -> bool:
    """
    Auto-commit a directory to git.

    Args:
        source_dir: Directory to commit (e.g., .st8/stage or .st8/prod)
        branch: Git branch name
        version: Version string for auto-generated message
        message: User message (if empty, auto-generates)
        repo_url: Remote repo URL (optional, inherits from main project if empty)
        auto_push: Whether to push after commit
        root: Project root to inherit origin from (optional)
        force_push: Always force push (for prod, which is always authoritative)

    Returns:
        True if successful, False otherwise
    """
    # Check if git is available
    if not shutil.which("git"):
        print("  Warning: git not found, skipping auto-commit", file=sys.stderr)
        return False

    # Get origin from main project if not specified
    if not repo_url and root:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root, capture_output=True, text=True
        )
        if result.returncode == 0:
            repo_url = result.stdout.strip()

    # Generate commit message if empty
    if not message:
        if "prod" in branch.lower() or branch == "main":
            message = f"Release v{version}"
        else:
            message = f"Promote v{version}"

    try:
        # Check if git repo exists in source_dir
        git_dir = source_dir / ".git"
        if not git_dir.exists():
            # Initialize git repo
            subprocess.run(["git", "init"], cwd=source_dir, capture_output=True, check=True)
            print(f"  Initialized git repo in {source_dir.name}/")

            # Add remote if available
            if repo_url:
                subprocess.run(
                    ["git", "remote", "add", "origin", repo_url],
                    cwd=source_dir, capture_output=True
                )
        else:
            # Update origin if it doesn't exist or differs
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=source_dir, capture_output=True, text=True
            )
            if result.returncode != 0 and repo_url:
                # No origin, add it
                subprocess.run(
                    ["git", "remote", "add", "origin", repo_url],
                    cwd=source_dir, capture_output=True
                )

        # Create/checkout branch
        result = subprocess.run(
            ["git", "rev-parse", "--verify", branch],
            cwd=source_dir, capture_output=True
        )
        if result.returncode != 0:
            # Branch doesn't exist, create it
            subprocess.run(
                ["git", "checkout", "-b", branch],
                cwd=source_dir, capture_output=True
            )
        else:
            # Branch exists, checkout
            subprocess.run(
                ["git", "checkout", branch],
                cwd=source_dir, capture_output=True
            )

        # Add all files
        subprocess.run(["git", "add", "-A"], cwd=source_dir, capture_output=True, check=True)

        # Check if there are changes to commit
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=source_dir, capture_output=True
        )
        if result.returncode == 0:
            print(f"  No changes to commit")
            return True

        # Commit
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=source_dir, capture_output=True, check=True
        )
        print(f"  Committed: {message}")

        # Push if enabled and remote exists
        if auto_push:
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=source_dir, capture_output=True
            )
            if result.returncode == 0:
                # Fetch remote first
                subprocess.run(
                    ["git", "fetch", "origin"],
                    cwd=source_dir, capture_output=True
                )

                # Check if remote branch exists
                result = subprocess.run(
                    ["git", "rev-parse", "--verify", f"origin/{branch}"],
                    cwd=source_dir, capture_output=True
                )
                if force_push:
                    # Prod is always authoritative, force push directly
                    result = subprocess.run(
                        ["git", "push", "--force", "-u", "origin", branch],
                        cwd=source_dir, capture_output=True, text=True
                    )
                    if result.returncode == 0:
                        print(f"  Force pushed to origin/{branch}")
                    else:
                        print(f"  Warning: Force push failed: {result.stderr}", file=sys.stderr)
                else:
                    # Try normal push first
                    result = subprocess.run(
                        ["git", "push", "-u", "origin", branch],
                        cwd=source_dir, capture_output=True, text=True
                    )
                    if result.returncode == 0:
                        print(f"  Pushed to origin/{branch}")
                    elif result.returncode != 0 and "fetch first" in result.stderr:
                        # Remote diverged - snapshot is authoritative, force push
                        print(f"  Remote diverged, force pushing snapshot...")
                        result = subprocess.run(
                            ["git", "push", "--force-with-lease", "-u", "origin", branch],
                            cwd=source_dir, capture_output=True, text=True
                        )
                        if result.returncode == 0:
                            print(f"  Force pushed to origin/{branch}")
                        else:
                            print(f"  Warning: Force push failed: {result.stderr}", file=sys.stderr)
                    else:
                        print(f"  Warning: Push failed: {result.stderr}", file=sys.stderr)
            else:
                print(f"  Warning: No remote configured, skipping push", file=sys.stderr)

        return True

    except subprocess.CalledProcessError as e:
        print(f"  Git error: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"  Git error: {e}", file=sys.stderr)
        return False


# =============================================================================
# AI Helper Functions
# =============================================================================

def call_simple_ai(prompt: str, ai_config: dict) -> Optional[str]:
    """
    Call a simple/cheap AI for generating descriptions and commit messages.

    Supports:
      - ollama: Local LLM via REST API
      - openai: OpenAI API (requires OPENAI_API_KEY env var)
      - anthropic: Anthropic API (requires ANTHROPIC_API_KEY env var)

    Returns the AI response text, or None on failure.
    """
    provider = ai_config.get("provider", "ollama")
    model = ai_config.get("model", "llama3.2")

    try:
        if provider == "ollama":
            endpoint = ai_config.get("endpoint", "http://localhost:11434")
            import urllib.request
            import urllib.error

            url = f"{endpoint}/api/generate"
            data = json.dumps({
                "model": model,
                "prompt": prompt,
                "stream": False
            }).encode("utf-8")

            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as response:
                    result = json.loads(response.read().decode("utf-8"))
                    return result.get("response", "").strip()
            except urllib.error.URLError as e:
                print(f"  Warning: Ollama not available: {e}", file=sys.stderr)
                return None

        elif provider == "openai":
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                print("  Warning: OPENAI_API_KEY not set", file=sys.stderr)
                return None

            import urllib.request
            url = "https://api.openai.com/v1/chat/completions"
            data = json.dumps({
                "model": model or "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 500
            }).encode("utf-8")

            req = urllib.request.Request(url, data=data, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            })
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
                return result["choices"][0]["message"]["content"].strip()

        elif provider == "anthropic":
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                print("  Warning: ANTHROPIC_API_KEY not set", file=sys.stderr)
                return None

            import urllib.request
            url = "https://api.anthropic.com/v1/messages"
            data = json.dumps({
                "model": model or "claude-3-haiku-20240307",
                "max_tokens": 500,
                "messages": [{"role": "user", "content": prompt}]
            }).encode("utf-8")

            req = urllib.request.Request(url, data=data, headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01"
            })
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
                return result["content"][0]["text"].strip()

        else:
            print(f"  Warning: Unknown AI provider: {provider}", file=sys.stderr)
            return None

    except Exception as e:
        print(f"  Warning: AI call failed: {e}", file=sys.stderr)
        return None


def invoke_agent_ai(prompt: str, agent_config: dict, cwd: Path) -> int:
    """
    Invoke the agent AI (Claude Code, aider, etc.) interactively.

    The agent runs in the foreground with full terminal access.
    Returns the exit code from the agent process.
    """
    provider = agent_config.get("provider", "claude-code")
    command = agent_config.get("command", "claude")

    if provider == "claude-code":
        # Invoke Claude Code with the prompt
        cmd = [command, prompt]
    elif provider == "aider":
        # Invoke aider with the prompt
        cmd = [command, "--message", prompt]
    elif provider == "cursor":
        # Cursor doesn't have CLI, just inform user
        print(f"\nOpen Cursor and run this prompt:")
        print(f"  {prompt}")
        print("")
        input("Press Enter when done...")
        return 0
    else:
        # Generic: just pass prompt as argument
        cmd = [command, prompt]

    # Check if command exists
    if not shutil.which(cmd[0]):
        print(f"Error: Agent command '{cmd[0]}' not found in PATH", file=sys.stderr)
        print(f"Install {provider} or configure a different agent in .st8/deploy.json", file=sys.stderr)
        return 1

    # Run interactively (not captured)
    try:
        result = subprocess.run(cmd, cwd=cwd)
        return result.returncode
    except KeyboardInterrupt:
        print("\nAgent interrupted by user.")
        return 130
    except Exception as e:
        print(f"Error running agent: {e}", file=sys.stderr)
        return 1


def get_task_changes_summary(root: Path) -> Tuple[List[str], List[str], List[str]]:
    """
    Get a summary of changes in the current task.

    Returns (added, modified, deleted) file lists.
    """
    active = get_active_task(root)
    if not active:
        return [], [], []

    task_id = active["id"]
    snapshot_hashes = get_task_snapshot_hashes(root, task_id)
    working_hashes = get_working_env_hashes(root)

    all_files = set(snapshot_hashes.keys()) | set(working_hashes.keys())
    added = []
    modified = []
    deleted = []

    for relpath in all_files:
        in_snapshot = relpath in snapshot_hashes
        in_working = relpath in working_hashes

        if in_working and not in_snapshot:
            added.append(relpath)
        elif in_snapshot and not in_working:
            deleted.append(relpath)
        elif snapshot_hashes.get(relpath) != working_hashes.get(relpath):
            modified.append(relpath)

    return added, modified, deleted


# =============================================================================
# Mission Command
# =============================================================================

def cmd_mission(args) -> int:
    """
    Shortcut for AI-assisted development using MISSION.md.

    Equivalent to: st8 prompt "do what is described on .st8/MISSION.md, then update .st8/REPORT.md"
    """
    root = require_st8_root()
    st8_dir = root / ".st8"
    mission_file = st8_dir / "MISSION.md"
    report_file = st8_dir / "REPORT.md"

    # Check that MISSION.md exists
    if not mission_file.exists():
        print(f"Error: {mission_file} does not exist.", file=sys.stderr)
        print("Run 'st8 init' to create the mission template.", file=sys.stderr)
        return 1

    # Read the mission content
    mission_content = mission_file.read_text()

    # Construct the prompt
    prompt = f"""Do what is described in MISSION.md below, then update .st8/REPORT.md with your feedback.

=== MISSION.md ===
{mission_content}
=== END MISSION.md ===

After completing the mission tasks, update .st8/REPORT.md with:
- Summary of changes made
- Any issues encountered
- Suggestions for next steps"""

    # Create args object for cmd_prompt
    class PromptArgs:
        def __init__(self):
            self.prompt = [prompt]
            self.task = args.task if hasattr(args, 'task') else None
            self.simple = args.simple if hasattr(args, 'simple') else None
            self.agent = args.agent if hasattr(args, 'agent') else None
            self.no_promote = args.no_promote if hasattr(args, 'no_promote') else False
            self.dry_run = args.dry_run if hasattr(args, 'dry_run') else False

    print("ST8 Mission")
    print("=" * 60)
    print(f"Mission file: {mission_file}")
    print(f"Report file: {report_file}")
    print("")

    return cmd_prompt(PromptArgs())


# =============================================================================
# Prompt Command
# =============================================================================

def cmd_prompt(args) -> int:
    """
    AI-assisted development workflow.

    This command orchestrates:
    1. Task creation/resumption with AI-generated description
    2. Agent AI execution (Claude Code, aider, etc.)
    3. Summary generation from changes
    4. Human review and optional promotion
    """
    root = require_st8_root()
    deploy_config = load_deploy_config(root)
    ai_config = deploy_config.get("ai", {})

    # Get AI configurations
    simple_config = ai_config.get("simple", {"provider": "ollama", "model": "llama3.2"})
    agent_config = ai_config.get("agent", {"provider": "claude-code", "command": "claude"})

    # Override with CLI args if provided
    if args.simple:
        simple_config["provider"] = args.simple
    if args.agent:
        agent_config["provider"] = args.agent

    # Get the prompt
    prompt = " ".join(args.prompt) if args.prompt else ""
    if not prompt.strip():
        print("Error: Prompt is required.", file=sys.stderr)
        print("Usage: st8 prompt \"your prompt here\"", file=sys.stderr)
        return 1

    print("ST8 Prompt")
    print("=" * 60)
    print(f"Prompt: {prompt[:80]}{'...' if len(prompt) > 80 else ''}")
    print("")

    # Dry run mode
    if args.dry_run:
        print("[DRY RUN] Would execute the following workflow:")
        print("")
        if args.task:
            print(f"  1. Resume task #{args.task}")
        else:
            print(f"  1. Generate task description using {simple_config['provider']}")
            print(f"     Then run: st8 task start \"<description>\"")
        print(f"  2. Invoke {agent_config['provider']} with prompt")
        print(f"  3. Generate commit message using {simple_config['provider']}")
        print(f"  4. Human review: abort / stop / promote")
        return 0

    # =========================================================================
    # Step 1: Task Setup
    # =========================================================================

    active_task = get_active_task(root)

    if args.task:
        # Resume existing task
        try:
            task_id = int(args.task)
            backlog_item = get_backlog_item(root, task_id)
            if backlog_item:
                # Start from backlog
                print(f"Starting task from backlog #{task_id}...")

                class TaskStartArgs:
                    def __init__(self):
                        self.backlog_id = str(task_id)
                        self.message = None

                result = cmd_task_start(TaskStartArgs())
                if result != 0:
                    return result
                print("")
            elif active_task and active_task.get("id"):
                # Already have an active task, continue with it
                print(f"Continuing active task: {active_task['id']}")
            else:
                print(f"Error: Task #{task_id} not found in backlog or active.", file=sys.stderr)
                return 1
        except ValueError:
            # Maybe it's a task ID string
            if active_task and active_task.get("id") == args.task:
                print(f"Continuing active task: {active_task['id']}")
            else:
                print(f"Error: Invalid task ID: {args.task}", file=sys.stderr)
                return 1
    elif active_task:
        # Already have an active task, ask what to do
        print(f"Active task exists: {active_task['id']}")
        response = input("Continue with this task? [Y/n]: ").strip().lower()
        if response in ("n", "no"):
            print("Aborting. Resolve the active task first.")
            return 1
        print("")
    else:
        # Create new task with AI-generated description
        print(f"Generating task description...")

        desc_prompt = f"""Generate a very short (5-10 words max) task description for this development request.
Only output the description, nothing else. No quotes, no explanation.

Request: {prompt}

Description:"""

        description = call_simple_ai(desc_prompt, simple_config)

        if description:
            # Clean up the description
            description = description.strip().strip('"\'').strip()
            # Limit length
            if len(description) > 60:
                description = description[:57] + "..."
            print(f"  Description: {description}")
        else:
            # Fallback: use first 50 chars of prompt
            description = prompt[:50] + ("..." if len(prompt) > 50 else "")
            print(f"  Using prompt prefix: {description}")

        print("")
        print("Creating task snapshot...")

        # Create the task
        class TaskStartArgs:
            def __init__(self):
                self.backlog_id = None
                self.message = [description]

        result = cmd_task_start(TaskStartArgs())
        if result != 0:
            return result
        print("")

    # =========================================================================
    # Step 2: Agent Execution
    # =========================================================================

    print(f"Invoking {agent_config['provider']}...")
    print("-" * 60)

    agent_result = invoke_agent_ai(prompt, agent_config, root)

    print("-" * 60)
    if agent_result != 0:
        print(f"Agent exited with code {agent_result}")
    print("")

    # =========================================================================
    # Step 3: Summary Generation
    # =========================================================================

    print("Analyzing changes...")
    added, modified, deleted = get_task_changes_summary(root)
    total_changes = len(added) + len(modified) + len(deleted)

    if total_changes == 0:
        print("  No changes detected.")
        print("")
        response = input("No changes made. Abort task? [Y/n]: ").strip().lower()
        if response not in ("n", "no"):
            class TaskAbortArgs:
                def __init__(self):
                    self.message = ["No changes made"]
                    self.force = True

            return cmd_task_abort(TaskAbortArgs())
        return 0

    print(f"  Added:    {len(added)}")
    print(f"  Modified: {len(modified)}")
    print(f"  Deleted:  {len(deleted)}")
    print("")

    # Generate commit message
    print("Generating commit message...")

    changes_summary = []
    if added:
        changes_summary.append(f"Added: {', '.join(added[:5])}" + (f" (+{len(added)-5} more)" if len(added) > 5 else ""))
    if modified:
        changes_summary.append(f"Modified: {', '.join(modified[:5])}" + (f" (+{len(modified)-5} more)" if len(modified) > 5 else ""))
    if deleted:
        changes_summary.append(f"Deleted: {', '.join(deleted[:5])}" + (f" (+{len(deleted)-5} more)" if len(deleted) > 5 else ""))

    summary_prompt = f"""Generate a concise git commit message (1-2 lines) for these changes.
Use conventional commit style if appropriate (feat:, fix:, refactor:, etc.)
Only output the commit message, nothing else.

Original request: {prompt}

Changes made:
{chr(10).join(changes_summary)}

Commit message:"""

    commit_message = call_simple_ai(summary_prompt, simple_config)

    if commit_message:
        commit_message = commit_message.strip().strip('"\'').strip()
        print(f"  Suggested: {commit_message}")
    else:
        # Fallback
        commit_message = f"Changes from: {prompt[:40]}"
        print(f"  Fallback: {commit_message}")

    print("")

    # =========================================================================
    # Step 4: Human Review
    # =========================================================================

    print("Review Options:")
    print("  [1] Abort   - Discard all changes, restore snapshot")
    print("  [2] Stop    - Keep changes, return task to backlog")
    print("  [3] Promote - Keep changes, promote to stage")
    print("  [4] Edit    - Edit commit message, then promote")
    print("")

    while True:
        choice = input("Choice [1-4]: ").strip()

        if choice == "1":
            # Abort
            print("")
            class TaskAbortArgs:
                def __init__(self):
                    self.message = ["Aborted after review"]
                    self.force = True

            return cmd_task_abort(TaskAbortArgs())

        elif choice == "2":
            # Stop (return to backlog)
            print("")
            class TaskStopArgs:
                def __init__(self):
                    self.finalize = False
                    self.delete = False
                    self.archive = False
                    self.message = [commit_message]
                    self.force = True

            return cmd_task_stop(TaskStopArgs())

        elif choice == "3" or choice == "4":
            # Edit message if choice 4
            if choice == "4":
                print(f"\nCurrent message: {commit_message}")
                new_message = input("New message (Enter to keep): ").strip()
                if new_message:
                    commit_message = new_message
                print("")

            # Finalize task first
            print("Finalizing task...")
            class TaskStopArgs:
                def __init__(self):
                    self.finalize = True
                    self.delete = True
                    self.archive = False
                    self.message = [commit_message]
                    self.force = True

            result = cmd_task_stop(TaskStopArgs())
            if result != 0:
                return result

            # Promote if not --no-promote
            if not args.no_promote:
                print("")
                print("Promoting to stage...")
                class PromoteArgs:
                    def __init__(self):
                        self.message = [commit_message]
                        self.major = False
                        self.minor = False
                        self.patch = False

                return cmd_promote(PromoteArgs())
            else:
                print("Skipping promotion (--no-promote)")
                return 0

        else:
            print("Invalid choice. Enter 1, 2, 3, or 4.")


# =============================================================================
# Git Command
# =============================================================================

def cmd_git(args) -> int:
    """Main git command handler."""
    if not hasattr(args, 'git_func') or args.git_func is None:
        # No subcommand provided, show status
        return cmd_git_status(args)
    return args.git_func(args)


def cmd_git_status(args) -> int:
    """Show git configuration and status."""
    root = require_st8_root()
    deploy_config = load_deploy_config(root)
    git_config = deploy_config.get("git", {})

    print("Git Configuration:")
    print("-" * 40)
    print(f"  Enabled:      {git_config.get('enabled', False)}")
    print(f"  Stage branch: {git_config.get('stage_branch', 'stage')}")
    print(f"  Prod branch:  {git_config.get('prod_branch', 'prod')}")
    print(f"  Auto-push:    {git_config.get('auto_push', False)}")

    # Check if origin is set
    if shutil.which("git"):
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root, capture_output=True, text=True
        )
        if result.returncode == 0:
            print(f"\nCurrent origin: {result.stdout.strip()}")
        else:
            print("\nNo origin remote configured.")

        # Check existing branches
        result = subprocess.run(
            ["git", "branch", "-a"],
            cwd=root, capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            print(f"\nBranches:\n{result.stdout}")
    else:
        print("\nWarning: git not found in PATH")

    return 0


def cmd_git_setup(args) -> int:
    """
    Setup git repository using gh CLI.

    This function:
    1. Initializes local git repo if needed
    2. Creates remote repo on GitHub (using gh CLI)
    3. Creates stage and prod branches if they don't exist
    4. Makes initial commit if there are changes
    5. Pushes if auto_push is enabled
    """
    root = require_st8_root()
    paths = get_paths(root)
    config = load_config(root)
    deploy_config = load_deploy_config(root)
    git_config = deploy_config.get("git", {})

    # Check if git is available
    if not shutil.which("git"):
        print("Error: git not found in PATH", file=sys.stderr)
        return 1

    print("Git Setup")
    print("=" * 40)

    # Check if .git exists
    git_dir = root / ".git"
    if not git_dir.exists():
        print("Initializing git repository...")
        result = subprocess.run(["git", "init"], cwd=root, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Error: Failed to init git: {result.stderr}", file=sys.stderr)
            return 1
        print("  Initialized empty git repository")
    else:
        print("Git repository already exists")

    # Check if origin already exists locally
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=root, capture_output=True, text=True
    )
    has_local_origin = result.returncode == 0
    local_origin_url = result.stdout.strip() if has_local_origin else ""
    if has_local_origin:
        print(f"  Local origin configured: {local_origin_url}")

    # Setup GitHub repository using gh CLI
    # IMPORTANT: Local origin presence does NOT imply remote repo exists
    # We must verify with GitHub directly using 'gh repo view'
    gh_available = shutil.which("gh")
    has_origin = False  # Will be set True only after verifying GitHub repo exists

    if gh_available:
        # Determine repo name from config or directory
        project_name = config.get("name") or root.name

        # Get current GitHub user
        result = subprocess.run(
            ["gh", "api", "user", "-q", ".login"],
            cwd=root, capture_output=True, text=True
        )
        if result.returncode != 0:
            print("\n  Warning: gh CLI not authenticated. Run 'gh auth login' first.")
            print("  Skipping remote repo verification/creation.")
            # Fall back to local origin if it exists
            has_origin = has_local_origin
        else:
            gh_user = result.stdout.strip()
            full_repo_name = f"{gh_user}/{project_name}"
            print(f"\nGitHub user: {gh_user}")
            print(f"Target repository: {full_repo_name}")

            # ALWAYS check if GitHub repo actually exists (source of truth)
            result = subprocess.run(
                ["gh", "repo", "view", full_repo_name],
                cwd=root, capture_output=True, text=True
            )
            github_repo_exists = result.returncode == 0

            if github_repo_exists:
                # GitHub repo exists - get its URL
                result = subprocess.run(
                    ["gh", "repo", "view", full_repo_name, "--json", "url", "-q", ".url"],
                    cwd=root, capture_output=True, text=True
                )
                if result.returncode == 0:
                    repo_url = result.stdout.strip()
                    print(f"  GitHub repository exists: {repo_url}")

                    # Ensure local origin is set correctly
                    if not has_local_origin:
                        subprocess.run(
                            ["git", "remote", "add", "origin", repo_url],
                            cwd=root, capture_output=True
                        )
                        print(f"  Added origin: {repo_url}")
                    has_origin = True
            else:
                # GitHub repo does NOT exist - need to create it
                print(f"  GitHub repository not found, creating...")

                # If local origin exists but points to non-existent repo, remove it
                # (gh repo create --remote=origin will fail if origin already exists)
                if has_local_origin:
                    print(f"  Removing stale local origin...")
                    subprocess.run(
                        ["git", "remote", "remove", "origin"],
                        cwd=root, capture_output=True
                    )

                # Create the repo (private by default, --public to override)
                visibility = "--public" if hasattr(args, 'public') and args.public else "--private"
                result = subprocess.run(
                    ["gh", "repo", "create", full_repo_name, visibility, "--source=.", "--remote=origin"],
                    cwd=root, capture_output=True, text=True
                )
                if result.returncode == 0:
                    # Get the created repo URL for logging
                    result = subprocess.run(
                        ["gh", "repo", "view", full_repo_name, "--json", "url", "-q", ".url"],
                        cwd=root, capture_output=True, text=True
                    )
                    if result.returncode == 0:
                        repo_url = result.stdout.strip()
                        print(f"  Created repository: {repo_url}")
                        print(f"  Repository name: {full_repo_name}")
                        has_origin = True
                else:
                    print(f"  Warning: Failed to create repo: {result.stderr.strip()}")
    elif not has_local_origin:
        print("\nNote: Install gh CLI to auto-create GitHub repos")
    else:
        # No gh CLI but local origin exists - use it (can't verify)
        print("\nNote: Install gh CLI to verify remote repo exists")
        has_origin = has_local_origin

    # Check if we have any commits
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root, capture_output=True
    )
    has_commits = result.returncode == 0

    # Make initial commit if needed
    if not has_commits:
        print("\nMaking initial commit...")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)

        # Check if there are changes to commit
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=root, capture_output=True
        )
        if result.returncode != 0:
            # There are staged changes
            message = args.message if hasattr(args, 'message') and args.message else "Initial commit"
            if isinstance(message, list):
                message = " ".join(message)
            result = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=root, capture_output=True, text=True
            )
            if result.returncode == 0:
                print(f"  Committed: {message}")
            else:
                print(f"  Warning: Commit failed: {result.stderr}", file=sys.stderr)
        else:
            print("  No changes to commit")

    # Note: stage_branch and prod_branch are NOT created here
    # They are managed by st8 promote/release via snapshot git repos in .st8/stage/ and .st8/prod/

    # Sync with remote if we have origin
    if has_origin:
        print(f"\nSyncing with remote...")

        # Fetch all remote branches first
        result = subprocess.run(
            ["git", "fetch", "origin"],
            cwd=root, capture_output=True, text=True
        )
        if result.returncode == 0:
            print("  Fetched remote branches")
        else:
            print(f"  Warning: fetch failed: {result.stderr.strip()}")

        # Get current branch
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=root, capture_output=True, text=True
        )
        main_branch = result.stdout.strip() if result.returncode == 0 else "main"

        # Only push the working branch (main/master)
        # stage_branch and prod_branch are managed by st8 promote/release via snapshot git repos
        for branch in [main_branch]:
            # Check if local branch exists
            result = subprocess.run(
                ["git", "rev-parse", "--verify", branch],
                cwd=root, capture_output=True
            )
            if result.returncode != 0:
                continue  # Branch doesn't exist locally, skip

            # Checkout the branch
            result = subprocess.run(
                ["git", "checkout", branch],
                cwd=root, capture_output=True, text=True
            )
            if result.returncode != 0:
                print(f"  Warning: could not checkout {branch}")
                continue

            # Check if remote branch exists
            result = subprocess.run(
                ["git", "rev-parse", "--verify", f"origin/{branch}"],
                cwd=root, capture_output=True
            )
            remote_branch_exists = result.returncode == 0

            if remote_branch_exists:
                # Try to merge remote changes (fast-forward only for safety)
                result = subprocess.run(
                    ["git", "merge", "--ff-only", f"origin/{branch}"],
                    cwd=root, capture_output=True, text=True
                )
                if result.returncode == 0:
                    print(f"  Synced {branch} with remote")
                else:
                    # Fast-forward not possible, try rebase
                    result = subprocess.run(
                        ["git", "rebase", f"origin/{branch}"],
                        cwd=root, capture_output=True, text=True
                    )
                    if result.returncode == 0:
                        print(f"  Rebased {branch} onto remote")
                    else:
                        # Abort rebase if it failed
                        subprocess.run(
                            ["git", "rebase", "--abort"],
                            cwd=root, capture_output=True
                        )
                        print(f"  Warning: {branch} diverged from remote, manual merge needed")
                        print(f"    Run: git checkout {branch} && git pull")
                        continue

            # Push the branch
            result = subprocess.run(
                ["git", "push", "-u", "origin", branch],
                cwd=root, capture_output=True, text=True
            )
            if result.returncode == 0:
                print(f"  Pushed: {branch}")
            else:
                print(f"  Failed to push {branch}: {result.stderr.strip()}")

        # Return to original branch
        if main_branch:
            subprocess.run(
                ["git", "checkout", main_branch],
                cwd=root, capture_output=True
            )

    # Update deploy.json to mark git as enabled
    if not git_config.get("enabled", False):
        git_config["enabled"] = True
        deploy_config["git"] = git_config
        save_json(paths["deploy"], deploy_config)
        print(f"\nEnabled git in deploy.json")

    print("\nGit setup complete!")
    return 0


def cmd_deploy(args) -> int:
    """Deploy to remote server via FTP or SSH (rsync/scp)."""
    root = require_st8_root()
    paths = get_paths(root)
    deploy_config = load_deploy_config(root)
    state = load_state(root)
    config = load_config(root)

    environments = deploy_config.get("environments", {})

    # List environments
    if args.list:
        print("Configured Environments:")
        print("-" * 40)
        for env_name, env_cfg in environments.items():
            enabled = env_cfg.get("enabled", False)
            status = "enabled" if enabled else "disabled"
            host = env_cfg.get("host") or "(not configured)"
            remote = env_cfg.get("remote_path") or env_cfg.get("remotePath") or "(not configured)"
            # Detect protocol
            protocol = env_cfg.get("protocol", "ssh")
            if protocol == "ssh" and env_cfg.get("port", 22) == 21:
                protocol = "ftp"  # Auto-detect FTP from port
            # Determine source: dev = working dir, stg = stage, prod = prod
            if env_name == "prod":
                source_display = ".st8/prod/"
            elif env_name == "stg":
                source_display = ".st8/stage/"
            else:
                source_display = "./ (working directory)"
            print(f"\n  {env_name.upper()} [{status}]")
            print(f"    Protocol: {protocol.upper()}")
            print(f"    Host: {host}")
            print(f"    Path: {remote}")
            print(f"    Source: {source_display}")
        print("")
        print("To enable an environment, edit .st8/deploy.json")
        return 0

    # Determine target environment
    env_name = args.environment
    if not env_name:
        print("Error: Please specify an environment (dev, stg, prod)", file=sys.stderr)
        print("Usage: st8 deploy <environment>", file=sys.stderr)
        print("       st8 deploy --list", file=sys.stderr)
        return 1

    if env_name not in environments:
        print(f"Error: Unknown environment '{env_name}'", file=sys.stderr)
        print(f"Available: {', '.join(environments.keys())}", file=sys.stderr)
        return 1

    env_cfg = environments[env_name]

    # Check if environment is configured
    if not env_cfg.get("enabled"):
        print(f"Error: Environment '{env_name}' is not enabled.", file=sys.stderr)
        print(f"Edit .st8/deploy.json to enable and configure it.", file=sys.stderr)
        return 1

    host = env_cfg.get("host")
    port = env_cfg.get("port", 22)
    username = env_cfg.get("username")
    password = env_cfg.get("password", "")
    # Support both remote_path and remotePath (backwards compatibility)
    remote_path = env_cfg.get("remote_path") or env_cfg.get("remotePath")

    # Detect protocol: explicit setting, or auto-detect from port
    protocol = env_cfg.get("protocol", "ssh")
    if protocol == "ssh" and port == 21:
        protocol = "ftp"  # Auto-detect FTP from port 21

    if not all([host, username, remote_path]):
        print(f"Error: Environment '{env_name}' is not fully configured.", file=sys.stderr)
        print("Required: host, username, remote_path", file=sys.stderr)
        return 1

    # FTP requires password
    if protocol == "ftp" and not password:
        print(f"Error: FTP deployment requires password in config.", file=sys.stderr)
        return 1

    # Determine source directory
    # dev = working directory, stg = stage, prod = prod
    if env_name == "prod":
        source = "prod"
        source_dir = paths["prod"]
    elif env_name == "stg":
        source = "stage"
        source_dir = paths["stage"]
    else:
        # dev deploys from working directory
        source = "dev"
        source_dir = paths["dev"]

    # Check source has content
    exclude = get_exclude_patterns(root, config)
    source_hashes = get_file_hashes(source_dir, exclude)
    if not source_hashes:
        if source == "dev":
            print(f"Error: No files in working directory.", file=sys.stderr)
        else:
            print(f"Error: No files in .st8/{source}/", file=sys.stderr)
            if source == "stage":
                print("Run 'st8 promote' first.", file=sys.stderr)
            else:
                print("Run 'st8 release' first.", file=sys.stderr)
        return 1

    version = state.get("version", "0.1.0")

    # Display source path
    source_display = "./" if source == "dev" else f".st8/{source}/"

    print(f"\nDeploying to: {env_name.upper()}")
    print(f"  Protocol: {protocol.upper()}")
    print(f"  Version: v{version}")
    print(f"  Source: {source_display}")
    print(f"  Target: {username}@{host}:{remote_path}")
    print(f"  Files: {len(source_hashes)}")
    print("")

    # Dry run
    if args.dry_run:
        print("[DRY RUN] Would upload the following files:")
        for f in sorted(source_hashes.keys())[:20]:
            print(f"  {f}")
        if len(source_hashes) > 20:
            print(f"  ... and {len(source_hashes) - 20} more")
        return 0

    # Confirm unless --yes flag
    if not args.yes:
        response = input("Proceed with deployment? [y/N] ").strip().lower()
        if response != "y":
            print("Deployment cancelled.")
            return 0

    # Execute deployment based on protocol
    print("Uploading files...")

    if protocol == "ftp":
        # FTP deployment
        print("  Using FTP...")
        ignore_patterns = env_cfg.get("ignore", [])

        try:
            ftp = ftplib.FTP()
            ftp.connect(host, port)
            ftp.login(username, password)

            # Upload the directory
            files_uploaded, errors = ftp_upload_directory(
                ftp, source_dir, remote_path, ignore_patterns
            )

            ftp.quit()

            if errors > 0:
                print(f"\n  Uploaded {files_uploaded} files with {errors} errors", file=sys.stderr)
                return 1
            else:
                print(f"\n  Uploaded {files_uploaded} files")

        except ftplib.error_perm as e:
            print(f"FTP permission error: {e}", file=sys.stderr)
            return 1
        except ftplib.error_temp as e:
            print(f"FTP temporary error: {e}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"FTP error: {e}", file=sys.stderr)
            return 1

    else:
        # SSH deployment - try rsync first, fallback to scp
        # Get auth options
        auth = deploy_config.get("auth", {})
        key_path = os.path.expanduser(auth.get("key_path", "~/.ssh/id_rsa"))

        # Build SSH options
        ssh_opts = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"]
        if os.path.exists(key_path):
            ssh_opts.extend(["-i", key_path])

        # Check if rsync is available
        rsync_available = shutil.which("rsync") is not None

        if rsync_available:
            print("  Using rsync (delta transfer)...")
            # Build rsync command
            rsync_cmd = [
                "rsync", "-avz", "--delete",
                "-e", f"ssh -p {port} {' '.join(ssh_opts)}"
            ]

            # Add source path (with trailing slash to sync contents)
            rsync_cmd.append(f"{source_dir}/")
            rsync_cmd.append(f"{username}@{host}:{remote_path}/")

            result = subprocess.run(rsync_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"rsync failed: {result.stderr}", file=sys.stderr)
                print("Falling back to scp...", file=sys.stderr)
                rsync_available = False
            else:
                # Print rsync summary
                if result.stdout:
                    lines = result.stdout.strip().split("\n")
                    # Show last few lines which contain the summary
                    for line in lines[-5:]:
                        if line.strip():
                            print(f"  {line}")

        if not rsync_available:
            # Fallback to scp
            print("  Using scp (full copy)...")
            scp_cmd = ["scp", "-r", "-P", str(port)]
            scp_cmd.extend(ssh_opts)

            # Upload entire source directory contents
            for item in source_dir.iterdir():
                if item.name.startswith("."):
                    continue
                scp_cmd_item = scp_cmd + [str(item), f"{username}@{host}:{remote_path}/"]
                result = subprocess.run(scp_cmd_item, capture_output=True, text=True)
                if result.returncode != 0:
                    print(f"Error uploading {item.name}: {result.stderr}", file=sys.stderr)
                    return 1
                print(f"  Uploaded: {item.name}")

        # Run post-deploy command if configured (SSH only)
        post_cmd = deploy_config.get("options", {}).get("post_deploy_command")
        if post_cmd:
            print(f"\nRunning post-deploy: {post_cmd}")
            ssh_cmd = ["ssh", "-p", str(port)]
            ssh_cmd.extend(ssh_opts)
            ssh_cmd.append(f"{username}@{host}")
            ssh_cmd.append(post_cmd)

            result = subprocess.run(ssh_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"Warning: Post-deploy failed: {result.stderr}", file=sys.stderr)
            else:
                print("  Post-deploy complete.")

    print(f"\n[SUCCESS] Deployed v{version} to {env_name}")
    return 0


def cmd_sync(args) -> int:
    """
    Sync Working Environment from stage or prod.

    This overwrites the Working Environment with the contents of stage or prod.
    A safety snapshot is created first (unless --no-snapshot) so you can recover
    if needed.
    """
    root = require_st8_root()
    paths = get_paths(root)
    config = load_config(root)
    history = load_history(root)

    exclude = get_exclude_patterns(root, config)

    # Determine source
    source = args.source or "stage"
    if source not in ("stage", "prod"):
        print(f"Error: Invalid source '{source}'. Use 'stage' or 'prod'.", file=sys.stderr)
        return 1

    source_dir = paths["stage"] if source == "stage" else paths["prod"]

    # Check source has content
    source_hashes = get_file_hashes(source_dir, exclude)
    if not source_hashes:
        print(f"Error: No files in .st8/{source}/", file=sys.stderr)
        if source == "stage":
            print("Run 'st8 promote' first.", file=sys.stderr)
        else:
            print("Run 'st8 release' first.", file=sys.stderr)
        return 1

    # Check for active task
    active = get_active_task(root)
    if active:
        print(f"Error: Active task exists: {active['id']}", file=sys.stderr)
        print("Resolve the task first with 'st8 task abort' or 'st8 task finalize'.", file=sys.stderr)
        return 1

    # Get current WE state
    we_hashes = get_file_hashes(paths["dev"], exclude)

    # Check if there are changes
    if we_hashes == source_hashes:
        print(f"Working Environment already matches {source}.")
        return 0

    # Calculate what will change
    all_files = set(we_hashes.keys()) | set(source_hashes.keys())
    added = [f for f in source_hashes if f not in we_hashes]
    removed = [f for f in we_hashes if f not in source_hashes]
    modified = [f for f in all_files if f in we_hashes and f in source_hashes and we_hashes[f] != source_hashes[f]]

    print(f"Syncing Working Environment from {source}...")
    print("")
    print(f"Changes to apply:")
    print(f"  Files to add:    {len(added)}")
    print(f"  Files to modify: {len(modified)}")
    print(f"  Files to remove: {len(removed)}")
    print("")

    # Create safety snapshot (unless --no-snapshot)
    snapshot_path = None
    if not args.no_snapshot:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        snapshot_name = f"sync_backup_{timestamp}"
        snapshot_path = paths["task_archives"] / f"{snapshot_name}.zip"
        paths["task_archives"].mkdir(parents=True, exist_ok=True)

        print(f"Creating safety snapshot...")
        with zipfile.ZipFile(snapshot_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for relpath in we_hashes:
                src_file = paths["dev"] / relpath
                if src_file.exists():
                    zf.write(src_file, relpath)
        print(f"  Saved to: {snapshot_path.relative_to(paths['st8'])}")
        print("")

    # Confirmation (unless --force)
    if not args.force:
        response = input(f"Overwrite Working Environment from {source}? [y/N] ").strip().lower()
        if response != "y":
            print("Sync cancelled.")
            if snapshot_path and snapshot_path.exists():
                snapshot_path.unlink()
                print("Safety snapshot removed.")
            return 0

    print("")
    print("Syncing files...")

    # Smart sync: only copy changed files, remove deleted files
    synced = 0

    # Remove files not in source
    for relpath in removed:
        dst_file = paths["dev"] / relpath
        if dst_file.exists():
            dst_file.unlink()
            synced += 1
            # Clean up empty directories
            parent = dst_file.parent
            while parent != paths["dev"] and parent.exists():
                try:
                    if not any(parent.iterdir()):
                        parent.rmdir()
                    parent = parent.parent
                except OSError:
                    break

    # Copy new and modified files
    for relpath in added + modified:
        src_file = source_dir / relpath
        dst_file = paths["dev"] / relpath
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)
        synced += 1

    # Update dev metadata
    save_dev_hashes(root, source_hashes)

    # Add to history
    history_entry = {
        "type": "sync",
        "source": source,
        "files_synced": synced,
        "files_added": len(added),
        "files_modified": len(modified),
        "files_removed": len(removed),
        "snapshot_path": str(snapshot_path) if snapshot_path else None,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    history.append(history_entry)
    save_history(root, history)

    print(f"  Synced {synced} files")
    print("")
    print(f"[SUCCESS] Working Environment synced from {source}")
    if snapshot_path:
        print(f"Safety snapshot: {snapshot_path.relative_to(paths['st8'])}")

    return 0


# =============================================================================
# Hotfix Commands
# =============================================================================
#
# Hotfix Workflow:
#   1. hotfix start  - Create hotfix branch from prod
#   2. (make fixes in Working Environment)
#   3. hotfix publish - Apply hotfix to stage and prod
#   4. hotfix finish  - Clean up hotfix branch
#
# =============================================================================

def get_active_hotfix(root: Path) -> Optional[dict]:
    """Load active hotfix metadata, or None if no active hotfix."""
    paths = get_paths(root)
    hotfix_file = paths["hotfixes"] / "active.json"
    if hotfix_file.exists():
        with open(hotfix_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_active_hotfix(root: Path, hotfix_data: dict) -> None:
    """Save active hotfix metadata."""
    paths = get_paths(root)
    paths["hotfixes"].mkdir(parents=True, exist_ok=True)
    save_json(paths["hotfixes"] / "active.json", hotfix_data)


def clear_active_hotfix(root: Path) -> None:
    """Remove active hotfix file."""
    paths = get_paths(root)
    hotfix_file = paths["hotfixes"] / "active.json"
    if hotfix_file.exists():
        hotfix_file.unlink()


def cmd_hotfix_start(args) -> int:
    """
    Start a hotfix from prod.

    This creates a hotfix branch by:
      1. Saving current Working Environment state (if dirty)
      2. Syncing Working Environment from prod
      3. Recording the hotfix state

    After starting, make your fixes in the Working Environment.
    """
    root = require_st8_root()
    paths = get_paths(root)
    config = load_config(root)
    state = load_state(root)
    history = load_history(root)

    exclude = get_exclude_patterns(root, config)

    # Check for active task
    active_task = get_active_task(root)
    if active_task:
        print(f"Error: Active task exists: {active_task['id']}", file=sys.stderr)
        print("Resolve the task first before starting a hotfix.", file=sys.stderr)
        return 1

    # Check for existing hotfix
    active_hotfix = get_active_hotfix(root)
    if active_hotfix:
        print(f"Error: Active hotfix already exists: {active_hotfix['id']}", file=sys.stderr)
        print("Finish the current hotfix first with 'st8 hotfix finish'.", file=sys.stderr)
        return 1

    # Check if prod exists
    prod_hashes = get_file_hashes(paths["prod"], exclude)
    if not prod_hashes:
        print("Error: No prod snapshot exists. Run 'st8 release' first.", file=sys.stderr)
        return 1

    # Parse message
    message = " ".join(args.message) if args.message else ""
    tags = parse_hashtags(message)
    clean_message = re.sub(r"#\w+\s*", "", message).strip()

    # Generate hotfix ID
    hotfix_id = datetime.now(timezone.utc).strftime("hotfix_%Y%m%d_%H%M%S")

    # Get current WE state
    we_hashes = get_file_hashes(paths["dev"], exclude)

    # Save WE backup if different from prod
    backup_path = None
    if we_hashes != prod_hashes:
        print("Saving current Working Environment state...")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = paths["task_archives"] / f"hotfix_backup_{timestamp}.zip"
        paths["task_archives"].mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for relpath in we_hashes:
                src_file = paths["dev"] / relpath
                if src_file.exists():
                    zf.write(src_file, relpath)
        print(f"  Saved to: {backup_path.relative_to(paths['st8'])}")

    # Sync WE from prod
    print("Syncing Working Environment from prod...")

    # Smart sync: remove deleted, copy changed
    all_files = set(we_hashes.keys()) | set(prod_hashes.keys())
    removed = [f for f in we_hashes if f not in prod_hashes]
    added_or_modified = [f for f in prod_hashes if f not in we_hashes or we_hashes.get(f) != prod_hashes[f]]

    for relpath in removed:
        dst_file = paths["dev"] / relpath
        if dst_file.exists():
            dst_file.unlink()
            parent = dst_file.parent
            while parent != paths["dev"] and parent.exists():
                try:
                    if not any(parent.iterdir()):
                        parent.rmdir()
                    parent = parent.parent
                except OSError:
                    break

    for relpath in added_or_modified:
        src_file = paths["prod"] / relpath
        dst_file = paths["dev"] / relpath
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)

    # Save hotfix metadata
    hotfix_meta = {
        "id": hotfix_id,
        "message": clean_message or message,
        "tags": tags,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "prod_hash": hash_directory(paths["prod"], exclude),
        "backup_path": str(backup_path) if backup_path else None,
        "status": "active"
    }
    save_active_hotfix(root, hotfix_meta)

    # Add to history
    history_entry = {
        "type": "hotfix_start",
        "hotfix_id": hotfix_id,
        "message": clean_message or message,
        "tags": tags,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    history.append(history_entry)
    save_history(root, history)

    print("")
    print(f"Started hotfix: {hotfix_id}")
    if clean_message:
        print(f"  Message: {clean_message}")
    print(f"  Working Environment synced from prod")
    print("")
    print("Make your fixes, then:")
    print("  st8 hotfix publish - Apply fix to stage and prod")
    print("  st8 hotfix finish  - Abort and restore previous state")

    return 0


def cmd_hotfix_publish(args) -> int:
    """
    Publish the hotfix to stage and prod.

    This promotes the current Working Environment to both stage and prod.
    """
    root = require_st8_root()
    paths = get_paths(root)
    config = load_config(root)
    state = load_state(root)
    history = load_history(root)

    exclude = get_exclude_patterns(root, config)

    # Check for active hotfix
    hotfix = get_active_hotfix(root)
    if not hotfix:
        print("Error: No active hotfix.", file=sys.stderr)
        print("Start a hotfix first with 'st8 hotfix start'.", file=sys.stderr)
        return 1

    # Parse message
    message = " ".join(args.message) if args.message else ""
    tags = parse_hashtags(message)
    clean_message = re.sub(r"#\w+\s*", "", message).strip()

    # If no message provided, use hotfix message
    if not clean_message:
        clean_message = hotfix.get("message", "Hotfix")
        tags = hotfix.get("tags", [])

    # Get current hashes
    dev_hashes = get_file_hashes(paths["dev"], exclude)
    stage_hashes = get_file_hashes(paths["stage"], exclude)
    prod_hashes = get_file_hashes(paths["prod"], exclude)

    # Check if there are changes
    if dev_hashes == prod_hashes:
        print("No changes to publish. Working Environment matches prod.")
        return 0

    # Show summary
    print(f"Publishing hotfix: {hotfix['id']}")
    print("")

    # Calculate change percentage
    percent_changed, files_changed, _ = calculate_change_percentage(
        paths["dev"], paths["prod"], dev_hashes, prod_hashes, config
    )

    # Version bump (hotfixes are always patch)
    current_version = state.get("version", "0.1.0")
    new_version = bump_version(current_version, "patch")

    print(f"Changes to apply:")
    print(f"  Files changed: {files_changed}")
    print(f"  Version: v{current_version} -> v{new_version}")
    print("")

    # Confirmation
    if not args.force:
        response = input("Publish hotfix to stage AND prod? [y/N] ").strip().lower()
        if response != "y":
            print("Publish cancelled.")
            return 0

    print("")
    print("Publishing hotfix...")

    # Copy to stage
    print("  Updating stage...")
    copy_changed_files(paths["dev"], paths["stage"], dev_hashes, stage_hashes, exclude)

    # Copy to prod
    print("  Updating prod...")
    copy_changed_files(paths["dev"], paths["prod"], dev_hashes, prod_hashes, exclude)

    # Update state
    state["version"] = new_version
    state["dev_hash"] = hash_directory(paths["dev"], exclude)
    state["stage_hash"] = hash_directory(paths["stage"], exclude)
    state["prod_hash"] = hash_directory(paths["prod"], exclude)
    state["last_promoted"] = datetime.now(timezone.utc).isoformat()
    save_state(root, state)

    # Update config version
    config["current_version"] = new_version
    save_config(root, config)

    # Add to history (both promote and release)
    timestamp = datetime.now(timezone.utc).isoformat()
    history.append({
        "version": new_version,
        "timestamp": timestamp,
        "message": f"[HOTFIX] {clean_message}",
        "tags": tags + ["hotfix"],
        "percent_changed": round(percent_changed, 2),
        "files_changed": files_changed,
        "type": "promote",
        "hotfix_id": hotfix["id"]
    })
    history.append({
        "version": new_version,
        "timestamp": timestamp,
        "message": f"[HOTFIX] {clean_message}",
        "tags": tags + ["hotfix"],
        "percent_changed": round(percent_changed, 2),
        "files_changed": files_changed,
        "type": "release",
        "hotfix_id": hotfix["id"]
    })
    save_history(root, history)

    print("")
    print(f"[SUCCESS] Hotfix published as v{new_version}")
    print("  Stage and Prod updated")
    print("")
    print("Next: Run 'st8 hotfix finish' to complete the hotfix")

    return 0


def cmd_hotfix_finish(args) -> int:
    """
    Finish the hotfix workflow.

    If hotfix was published: clean up hotfix metadata
    If hotfix was NOT published: restore Working Environment from backup
    """
    root = require_st8_root()
    paths = get_paths(root)
    config = load_config(root)
    history = load_history(root)

    exclude = get_exclude_patterns(root, config)

    # Check for active hotfix
    hotfix = get_active_hotfix(root)
    if not hotfix:
        print("Error: No active hotfix.", file=sys.stderr)
        return 1

    # Check if hotfix was published
    dev_hashes = get_file_hashes(paths["dev"], exclude)
    prod_hashes = get_file_hashes(paths["prod"], exclude)

    hotfix_published = dev_hashes == prod_hashes

    if hotfix_published:
        # Hotfix was published - just clean up
        print(f"Finishing hotfix: {hotfix['id']}")
        print("  Hotfix was published successfully")

        # Clean up backup if it exists
        if hotfix.get("backup_path"):
            backup = Path(hotfix["backup_path"])
            if backup.exists():
                backup.unlink()
                print("  Removed backup snapshot")
    else:
        # Hotfix was not published - offer to restore
        print(f"Hotfix not published: {hotfix['id']}")

        if hotfix.get("backup_path"):
            backup = Path(hotfix["backup_path"])
            if backup.exists():
                if not args.force:
                    response = input("Restore Working Environment from backup? [y/N] ").strip().lower()
                    if response != "y":
                        print("Keeping current Working Environment state.")
                    else:
                        print("Restoring from backup...")
                        # Extract backup
                        with zipfile.ZipFile(backup, "r") as zf:
                            # Clear WE first
                            clear_working_environment(root)
                            zf.extractall(paths["dev"])
                        print(f"  Restored from: {backup.relative_to(paths['st8'])}")
                        backup.unlink()
                else:
                    print("Skipping restore (use without --force to restore).")
            else:
                print("  Backup not found")
        else:
            print("  No backup available")

    # Clear hotfix
    clear_active_hotfix(root)

    # Add to history
    history_entry = {
        "type": "hotfix_finish",
        "hotfix_id": hotfix["id"],
        "published": hotfix_published,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    history.append(history_entry)
    save_history(root, history)

    print("")
    print(f"Hotfix {hotfix['id']} finished.")

    return 0


def cmd_hotfix(args) -> int:
    """Router for hotfix subcommands."""
    if hasattr(args, 'hotfix_func'):
        return args.hotfix_func(args)
    else:
        print("Usage: st8 hotfix <subcommand>")
        print("")
        print("Hotfix Workflow:")
        print("  1. st8 hotfix start  - Create hotfix from prod")
        print("  2. (make your fixes)")
        print("  3. st8 hotfix publish - Apply to stage and prod")
        print("  4. st8 hotfix finish  - Clean up")
        print("")
        print("Subcommands:")
        print("  start    Start a hotfix from prod")
        print("  publish  Apply hotfix to stage and prod")
        print("  finish   Complete or abort the hotfix")
        return 0


# =============================================================================
# Task Commands
# =============================================================================
#
# Task Lifecycle:
#   1. task add    - Queue a task in backlog (no snapshot yet)
#   2. task start  - Creates a frozen snapshot (from backlog or new)
#   3. (user works in Working Environment)
#   4. task abort OR task finalize - Resolves the task
#
# Resolution Rules (IRREVERSIBLE):
#   - ABORT: Restore WE from snapshot, delete task folder
#   - FINALIZE: Keep WE as-is, delete or archive task folder
#
# =============================================================================

def cmd_task_add(args) -> int:
    """
    Add a new task to the backlog queue without creating a snapshot.

    Backlog items are lightweight placeholders. Use 'task start <id>' to
    transition a backlog item into an active task with a snapshot.
    """
    root = require_st8_root()
    history = load_history(root)

    # Parse message and tags
    message = " ".join(args.message) if args.message else ""
    if not message.strip():
        print("Error: Message is required for backlog items.", file=sys.stderr)
        print("Usage: st8 task add <message>", file=sys.stderr)
        return 1

    tags = parse_hashtags(message)
    clean_message = re.sub(r"#\w+\s*", "", message).strip()

    # Add to backlog
    item = add_backlog_item(root, clean_message or message, tags)

    # Add to history
    history_entry = {
        "type": "task_backlog_add",
        "backlog_id": item["id"],
        "message": clean_message or message,
        "tags": tags,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    history.append(history_entry)
    save_history(root, history)

    # Print summary
    print(f"Added to backlog: #{item['id']}")
    if clean_message:
        print(f"  Message: {clean_message}")
    if tags:
        print(f"  Tags: {', '.join('#' + t for t in tags)}")
    print("")
    print("To start working on this task:")
    print(f"  st8 task start {item['id']}")

    return 0


def cmd_task_start(args) -> int:
    """
    Start a new task by creating a frozen snapshot of the Working Environment.

    Can be called with:
      - No arguments: Create new task with message from -m flag
      - Backlog ID: Transition a backlog item to active task

    The snapshot captures the current state of all files (except .st8/).
    After creation, all work continues in the Working Environment only.
    The Task Folder remains untouched until task resolution.
    """
    root = require_st8_root()
    history = load_history(root)

    # Check if there's already an active task
    active = get_active_task(root)
    if active:
        print(f"Error: Active task already exists: {active['id']}", file=sys.stderr)
        print("Resolve the current task first with 'st8 task abort' or 'st8 task finalize'.", file=sys.stderr)
        return 1

    # Check if starting from backlog
    backlog_item = None
    if args.backlog_id:
        try:
            backlog_id = int(args.backlog_id)
            backlog_item = get_backlog_item(root, backlog_id)
            if not backlog_item:
                print(f"Error: Backlog item #{backlog_id} not found.", file=sys.stderr)
                print("Use 'st8 task list' to see available backlog items.", file=sys.stderr)
                return 1
        except ValueError:
            print(f"Error: Invalid backlog ID '{args.backlog_id}'.", file=sys.stderr)
            return 1

    # Generate task ID
    task_id = generate_task_id()

    # Get message and tags from backlog or args
    if backlog_item:
        message = backlog_item.get("message", "")
        tags = backlog_item.get("tags", [])
        clean_message = message
    else:
        message = " ".join(args.message) if args.message else ""
        tags = parse_hashtags(message)
        clean_message = re.sub(r"#\w+\s*", "", message).strip()

    print(f"Creating task snapshot...")

    # Create the frozen snapshot
    files_copied = create_task_snapshot(root, task_id)

    # Create task metadata
    meta = {
        "id": task_id,
        "message": clean_message or message,
        "tags": tags,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "files_count": files_copied,
        "status": "active",
        "finished_at": None,
        "finish_message": None,
        "finish_type": None,
        "from_backlog": backlog_item["id"] if backlog_item else None
    }

    # Save metadata
    save_task_meta(root, task_id, meta)

    # Set as active task
    save_active_task(root, {"id": task_id})

    # Remove from backlog if applicable
    if backlog_item:
        remove_backlog_item(root, backlog_item["id"])

    # Add to history
    history_entry = {
        "type": "task_start",
        "task_id": task_id,
        "message": clean_message or message,
        "tags": tags,
        "files_count": files_copied,
        "from_backlog": backlog_item["id"] if backlog_item else None,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    history.append(history_entry)
    save_history(root, history)

    # Record to global tasks file
    record_global_task_event(
        root=root,
        event_type="start",
        task_id=task_id,
        message=clean_message or message,
        tags=tags,
        started_at=meta["started_at"],
        extra={"files_count": files_copied, "from_backlog": backlog_item["id"] if backlog_item else None}
    )

    # Print summary
    print(f"")
    print(f"Started task: {task_id}")
    if backlog_item:
        print(f"  From backlog: #{backlog_item['id']}")
    print(f"  Snapshot files: {files_copied}")
    if clean_message:
        print(f"  Message: {clean_message}")
    if tags:
        print(f"  Tags: {', '.join('#' + t for t in tags)}")
    print("")
    print("Work in your project directory. The snapshot is frozen.")
    print("When done, resolve with:")
    print("  st8 task abort             - Restore to snapshot (discard changes)")
    print("  st8 task stop              - Keep changes, return to backlog")
    print("  st8 task stop --finalize   - Keep changes, complete task permanently")

    return 0


def cmd_task_status(_args) -> int:
    """
    Show task info and diff between Working Environment and frozen snapshot.

    This shows what has changed in the Working Environment since the task started.
    """
    root = require_st8_root()

    # Check for active task
    active = get_active_task(root)
    if not active:
        print("No active task.")
        print("Use 'st8 task start' to create one, or 'st8 task list' to see past tasks.")
        return 0

    task_id = active["id"]
    meta = load_task_meta(root, task_id)
    if not meta:
        print(f"Error: Task metadata not found for {task_id}.", file=sys.stderr)
        return 1

    # Print task info
    print(f"Active Task: {task_id}")
    print(f"  Started: {meta.get('started_at', 'unknown')[:19].replace('T', ' ')}")
    print(f"  Snapshot files: {meta.get('files_count', 'unknown')}")
    if meta.get("message"):
        print(f"  Message: {meta['message']}")
    if meta.get("tags"):
        print(f"  Tags: {', '.join('#' + t for t in meta['tags'])}")
    print("")

    # Get snapshot hashes (Task Folder - frozen state)
    snapshot_hashes = get_task_snapshot_hashes(root, task_id)

    if not snapshot_hashes:
        print("Snapshot is empty (no files were captured at task start).")
        return 0

    # Get current Working Environment hashes
    working_hashes = get_working_env_hashes(root)

    # Calculate diff: what changed in WE since snapshot was taken
    all_files = set(snapshot_hashes.keys()) | set(working_hashes.keys())
    added = []      # New files in WE (not in snapshot)
    modified = []   # Changed files
    deleted = []    # Files removed from WE (were in snapshot)

    for relpath in all_files:
        in_snapshot = relpath in snapshot_hashes
        in_working = relpath in working_hashes

        if in_working and not in_snapshot:
            added.append(relpath)
        elif in_snapshot and not in_working:
            deleted.append(relpath)
        elif snapshot_hashes.get(relpath) != working_hashes.get(relpath):
            modified.append(relpath)

    total_changes = len(added) + len(modified) + len(deleted)

    # Print diff summary
    print(f"Changes since task started:")
    if total_changes == 0:
        print("  No changes detected.")
    else:
        print(f"  Modified: {len(modified)}")
        print(f"  Added:    {len(added)}")
        print(f"  Deleted:  {len(deleted)}")
        print("")

        # Show details (capped)
        max_shown = 10

        if modified:
            print("Modified files:")
            for p in sorted(modified)[:max_shown]:
                print(f"  ~ {p}")
            if len(modified) > max_shown:
                print(f"  ... and {len(modified) - max_shown} more")

        if added:
            print("Added files:")
            for p in sorted(added)[:max_shown]:
                print(f"  + {p}")
            if len(added) > max_shown:
                print(f"  ... and {len(added) - max_shown} more")

        if deleted:
            print("Deleted files:")
            for p in sorted(deleted)[:max_shown]:
                print(f"  - {p}")
            if len(deleted) > max_shown:
                print(f"  ... and {len(deleted) - max_shown} more")

    print("")
    print("Resolution options:")
    print("  st8 task abort             - Restore Working Environment to snapshot")
    print("  st8 task stop              - Keep changes, return to backlog")
    print("  st8 task stop --finalize   - Keep changes, complete task permanently")

    return 0


def cmd_task_abort(args) -> int:
    """
    ABORT the task: Restore Working Environment from the frozen snapshot.

    This is a FULL REPLACEMENT operation:
      1. Delete all contents of Working Environment (except .st8/)
      2. Copy all snapshot contents to Working Environment
      3. Delete the Task Folder

    WARNING: This is irreversible. All changes since task start will be lost.
    """
    root = require_st8_root()
    history = load_history(root)

    # Check for active task
    active = get_active_task(root)
    if not active:
        print("Error: No active task to abort.", file=sys.stderr)
        return 1

    task_id = active["id"]
    meta = load_task_meta(root, task_id)
    if not meta:
        print(f"Error: Task metadata not found for {task_id}.", file=sys.stderr)
        return 1

    # Parse message
    message = " ".join(args.message) if args.message else ""
    tags = parse_hashtags(message)
    clean_message = re.sub(r"#\w+\s*", "", message).strip()

    # Calculate duration
    started_at = meta.get("started_at")
    duration_str = ""
    if started_at:
        try:
            start_time = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            duration = datetime.now(timezone.utc) - start_time
            duration_str = format_duration(duration.total_seconds())
        except (ValueError, TypeError):
            pass

    # Show what will happen
    print(f"Task: {task_id}")
    print(f"Action: ABORT (restore from snapshot)")
    if duration_str:
        print(f"Duration: {duration_str}")
    print("")
    print("This will:")
    print("  1. DELETE all current files in Working Environment")
    print("  2. RESTORE files from the task snapshot")
    print("  3. DELETE the task folder")
    print("")
    print("WARNING: All changes since task start will be LOST.")
    print("")

    # Confirmation (unless --force)
    if not args.force:
        response = input("Proceed with abort? [y/N] ").strip().lower()
        if response != "y":
            print("Abort cancelled.")
            return 0

    print("")
    print("Restoring from snapshot...")

    # Step 1 & 2: Restore Working Environment from snapshot
    restored = restore_from_task_snapshot(root, task_id)
    print(f"  Restored {restored} files to Working Environment.")

    # Step 3: Delete the Task Folder
    delete_task_folder(root, task_id)
    print(f"  Deleted task folder.")

    # Clear active task
    clear_active_task(root)

    # Update task meta before deletion (for history purposes)
    finished_at = datetime.now(timezone.utc).isoformat()

    # Add to history
    history_entry = {
        "type": "task_abort",
        "task_id": task_id,
        "message": clean_message or message,
        "tags": tags,
        "files_restored": restored,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": (datetime.now(timezone.utc) - datetime.fromisoformat(started_at.replace("Z", "+00:00"))).total_seconds() if started_at else None,
        "timestamp": finished_at
    }
    history.append(history_entry)
    save_history(root, history)

    # Record to global tasks file
    duration_secs = history_entry.get("duration_seconds")
    record_global_task_event(
        root=root,
        event_type="abort",
        task_id=task_id,
        message=clean_message or message,
        tags=tags,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration_secs,
        extra={"files_restored": restored}
    )

    print("")
    print(f"Task {task_id} ABORTED.")
    if duration_str:
        print(f"Duration: {duration_str}")
    print("Working Environment restored to snapshot state.")

    return 0


def cmd_task_stop(args) -> int:
    """
    STOP the task: End active work on the task.

    Without --finalize:
      - Task returns to backlog with a "stopped_at" timestamp
      - Snapshot is archived for reference
      - You can resume later with 'task start <backlog_id>'

    With --finalize:
      - Task is completed permanently (gets "stopped_at" and "finished_at")
      - Task does NOT return to backlog
      - Must specify --delete or --archive for snapshot disposal
    """
    root = require_st8_root()
    history = load_history(root)

    # Check for active task
    active = get_active_task(root)
    if not active:
        print("Error: No active task to stop.", file=sys.stderr)
        return 1

    task_id = active["id"]
    meta = load_task_meta(root, task_id)
    if not meta:
        print(f"Error: Task metadata not found for {task_id}.", file=sys.stderr)
        return 1

    # Parse message
    message = " ".join(args.message) if args.message else ""
    tags = parse_hashtags(message)
    clean_message = re.sub(r"#\w+\s*", "", message).strip()

    # Calculate duration
    started_at = meta.get("started_at")
    duration_str = ""
    duration_seconds = None
    if started_at:
        try:
            start_time = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            duration = datetime.now(timezone.utc) - start_time
            duration_seconds = duration.total_seconds()
            duration_str = format_duration(duration_seconds)
        except (ValueError, TypeError):
            pass

    stopped_at = datetime.now(timezone.utc).isoformat()

    if args.finalize:
        # FINALIZE: Complete the task permanently
        # Must specify either --delete or --archive
        if not args.delete and not args.archive:
            print("Error: With --finalize, must specify --delete or --archive for snapshot disposal.", file=sys.stderr)
            print("")
            print("Usage:")
            print("  st8 task stop --finalize --delete   # Complete and delete snapshot")
            print("  st8 task stop --finalize --archive  # Complete and archive snapshot")
            return 1

        if args.delete and args.archive:
            print("Error: Cannot specify both --delete and --archive.", file=sys.stderr)
            return 1

        disposal_method = "archive" if args.archive else "delete"

        # Show what will happen
        print(f"Task: {task_id}")
        print(f"Action: STOP + FINALIZE (complete task permanently)")
        if duration_str:
            print(f"Duration: {duration_str}")
        print(f"Snapshot disposal: {disposal_method.upper()}")
        print("")
        print("This will:")
        print("  1. Keep Working Environment unchanged")
        print("  2. Mark task as FINISHED (will NOT return to backlog)")
        if args.archive:
            print("  3. Archive snapshot to .st8/task_archives/")
        else:
            print("  3. Permanently DELETE the snapshot")
        print("")

        # Confirmation (unless --force)
        if not args.force:
            response = input("Finalize task? [y/N] ").strip().lower()
            if response != "y":
                print("Stop cancelled.")
                return 0

        print("")
        archive_path = None

        if args.archive:
            print("Archiving snapshot...")
            archive_path = archive_task_snapshot(root, task_id, clean_message)
            if archive_path:
                print(f"  Archived to: {archive_path.relative_to(get_paths(root)['st8'])}")
            else:
                print("  Warning: No snapshot found to archive.")

        # Delete the Task Folder
        print("Deleting task folder...")
        delete_task_folder(root, task_id)
        print("  Done.")

        # Clear active task
        clear_active_task(root)

        finished_at = datetime.now(timezone.utc).isoformat()

        # Add to history
        history_entry = {
            "type": "task_stop",
            "task_id": task_id,
            "finalized": True,
            "disposal": disposal_method,
            "archive_path": str(archive_path) if archive_path else None,
            "message": clean_message or meta.get("message", ""),
            "tags": tags or meta.get("tags", []),
            "started_at": started_at,
            "stopped_at": stopped_at,
            "finished_at": finished_at,
            "duration_seconds": duration_seconds,
            "timestamp": finished_at
        }
        history.append(history_entry)
        save_history(root, history)

        # Record to global tasks file
        record_global_task_event(
            root=root,
            event_type="stop",
            task_id=task_id,
            message=clean_message or meta.get("message", ""),
            tags=tags or meta.get("tags", []),
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration_seconds,
            finalized=True,
            extra={"disposal": disposal_method}
        )

        print("")
        print(f"Task {task_id} STOPPED and FINALIZED.")
        if duration_str:
            print(f"Duration: {duration_str}")
        print("Task completed. Working Environment accepted as authoritative state.")

    else:
        # STOP only: Return task to backlog
        # Show what will happen
        print(f"Task: {task_id}")
        print(f"Action: STOP (pause task, return to backlog)")
        if duration_str:
            print(f"Active duration: {duration_str}")
        print("")
        print("This will:")
        print("  1. Keep Working Environment unchanged")
        print("  2. Archive snapshot for reference")
        print("  3. Return task to backlog (can resume later)")
        print("")

        # Confirmation (unless --force)
        if not args.force:
            response = input("Stop and return to backlog? [y/N] ").strip().lower()
            if response != "y":
                print("Stop cancelled.")
                return 0

        print("")

        # Archive the snapshot
        print("Archiving snapshot...")
        archive_path = archive_task_snapshot(root, task_id, f"stopped_{clean_message}" if clean_message else "stopped")
        if archive_path:
            print(f"  Archived to: {archive_path.relative_to(get_paths(root)['st8'])}")

        # Delete the Task Folder
        print("Deleting task folder...")
        delete_task_folder(root, task_id)
        print("  Done.")

        # Clear active task
        clear_active_task(root)

        # Add back to backlog with stop timestamp
        backlog = load_backlog(root)
        backlog_item = {
            "id": max([item.get("id", 0) for item in backlog] + [0]) + 1,
            "message": clean_message or meta.get("message", ""),
            "tags": tags or meta.get("tags", []),
            "created_at": meta.get("started_at", stopped_at),
            "stopped_at": stopped_at,
            "previous_task_id": task_id,
            "archive_path": str(archive_path) if archive_path else None,
            "total_duration_seconds": duration_seconds,
            "status": "stopped"
        }
        backlog.append(backlog_item)
        save_backlog(root, backlog)

        # Add to history
        history_entry = {
            "type": "task_stop",
            "task_id": task_id,
            "finalized": False,
            "backlog_id": backlog_item["id"],
            "message": clean_message or meta.get("message", ""),
            "tags": tags or meta.get("tags", []),
            "started_at": started_at,
            "stopped_at": stopped_at,
            "duration_seconds": duration_seconds,
            "timestamp": stopped_at
        }
        history.append(history_entry)
        save_history(root, history)

        # Record to global tasks file
        record_global_task_event(
            root=root,
            event_type="stop",
            task_id=task_id,
            message=clean_message or meta.get("message", ""),
            tags=tags or meta.get("tags", []),
            started_at=started_at,
            finished_at=stopped_at,
            duration_seconds=duration_seconds,
            finalized=False,
            extra={"backlog_id": backlog_item["id"]}
        )

        print("")
        print(f"Task {task_id} STOPPED.")
        if duration_str:
            print(f"Active duration: {duration_str}")
        print(f"Returned to backlog as #{backlog_item['id']}")
        print("")
        print("To resume later:")
        print(f"  st8 task start {backlog_item['id']}")

    return 0


def cmd_task_list(_args) -> int:
    """List backlog, active, and recent tasks."""
    root = require_st8_root()

    # Get backlog
    backlog = load_backlog(root)

    # Get active task
    active = get_active_task(root)

    # Get all tasks
    tasks = list_tasks(root)

    if not tasks and not active and not backlog:
        print("No tasks found.")
        print("")
        print("Commands:")
        print("  st8 task add <message>  - Add to backlog (no snapshot)")
        print("  st8 task start          - Start new task (creates snapshot)")
        return 0

    # Print backlog
    if backlog:
        print("Backlog:")
        for item in backlog:
            msg = item.get('message', '(no message)')[:40]
            item_id = item.get('id', '?')
            tags = item.get('tags', [])
            status = item.get('status', 'pending')
            tags_str = f" [{', '.join('#' + t for t in tags)}]" if tags else ""
            # Show stopped indicator if task was previously stopped
            stopped_indicator = " (stopped)" if status == "stopped" else ""
            duration_str = ""
            if item.get("total_duration_seconds"):
                duration_str = f" [{format_duration(item['total_duration_seconds'])} worked]"
            print(f"  #{item_id} - {msg}{tags_str}{stopped_indicator}{duration_str}")
        print("")
        print("  Start a backlog item: st8 task start <id>")
        print("")

    # Print active task
    if active:
        print("Active Task:")
        meta = load_task_meta(root, active["id"])
        if meta:
            msg = meta.get('message', '(no message)')[:40]
            files = meta.get('files_count', '?')
            started = meta.get('started_at', '')
            duration_str = ""
            if started:
                try:
                    start_time = datetime.fromisoformat(started.replace("Z", "+00:00"))
                    duration = datetime.now(timezone.utc) - start_time
                    duration_str = f" ({format_duration(duration.total_seconds())})"
                except (ValueError, TypeError):
                    pass
            print(f"  [{meta['id']}] {files} files - {msg}")
            print(f"    Started: {started[:19].replace('T', ' ')}{duration_str}")
            if meta.get("from_backlog"):
                print(f"    From backlog: #{meta['from_backlog']}")
        print("")

    # Print recent tasks (excluding active)
    other_tasks = [t for t in tasks if t.get("id") != (active or {}).get("id")]

    if other_tasks:
        print("Recent Tasks:")
        for task in other_tasks[:10]:
            status = task.get("status", "unknown")
            status_icon = {
                "active": "[A]",
                "finalized": "[F]",
                "aborted": "[X]",
                # Legacy support
                "promoted": "[F]",
                "canceled": "[X]",
            }.get(status, "[?]")
            msg = task.get("message", "(no message)")[:40]
            duration_str = ""
            if task.get("finished_at") and task.get("started_at"):
                try:
                    start = datetime.fromisoformat(task["started_at"].replace("Z", "+00:00"))
                    end = datetime.fromisoformat(task["finished_at"].replace("Z", "+00:00"))
                    duration_str = f" ({format_duration((end - start).total_seconds())})"
                except (ValueError, TypeError):
                    pass
            print(f"  {status_icon} {task['id']} - {msg}")
            if task.get("finished_at"):
                print(f"      Finished: {task['finished_at'][:19].replace('T', ' ')}{duration_str}")

        if len(other_tasks) > 10:
            print(f"  ... and {len(other_tasks) - 10} more")
        print("")
        print("Status: [A] Active, [F] Finalized, [X] Aborted")

    return 0


def cmd_task(args) -> int:
    """Router for task subcommands."""
    if hasattr(args, 'task_func'):
        return args.task_func(args)
    else:
        print("Usage: st8 task <subcommand>")
        print("")
        print("Task Workflow:")
        print("  1. Add tasks to backlog (no snapshot)")
        print("  2. Start a task (creates frozen snapshot)")
        print("  3. Work in your project (snapshot stays untouched)")
        print("  4. ABORT (restore snapshot) or STOP (keep changes)")
        print("")
        print("Subcommands:")
        print("  add      Add task to backlog (no snapshot yet)")
        print("  start    Start a task (from backlog or new)")
        print("  status   Show task info and changes since snapshot")
        print("  abort    Restore Working Environment from snapshot")
        print("  stop     Stop task (returns to backlog, or --finalize to complete)")
        print("  list     List backlog, active, and recent tasks")
        return 0


# =============================================================================
# Global Tasks Commands
# =============================================================================

def cmd_global(args) -> int:
    """
    Display or export centralized global tasks data from all projects.
    Can be run from any directory (doesn't require ST8 initialization).
    """
    global_data = load_global_tasks()

    if args.export:
        # Export to JSON file
        export_path = Path(args.export)
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(global_data, f, indent=2)
            f.write("\n")
        print(f"Global tasks exported to: {export_path}")
        return 0

    if args.json:
        # Output as JSON to stdout
        print(json.dumps(global_data, indent=2))
        return 0

    # Display summary
    projects = global_data.get("projects", {})
    summary = global_data.get("summary", {})

    if not projects:
        print("No global task data found.")
        print("")
        print(f"Global tasks file: {get_global_tasks_path()}")
        print("")
        print("Task data is recorded when you use:")
        print("  st8 task start  - Start a new task")
        print("  st8 task stop   - Stop or finalize a task")
        print("  st8 task abort  - Abort a task")
        return 0

    print("Global Task Summary")
    print("=" * 60)
    print("")

    # Overall summary
    total_time = summary.get("total_time_seconds", 0)
    total_tasks = summary.get("total_tasks", 0)
    last_updated = summary.get("last_updated", "Never")
    if last_updated and last_updated != "Never":
        last_updated = last_updated[:19].replace("T", " ")

    print(f"Total task starts: {total_tasks}")
    print(f"Total time tracked: {format_duration(total_time) if total_time else '0s'}")
    print(f"Last updated: {last_updated}")
    print(f"Projects tracked: {len(projects)}")
    print("")

    # Per-project summary
    print("Projects:")
    print("-" * 60)

    for project_path, project_data in sorted(projects.items()):
        project_name = project_data.get("name", Path(project_path).name)
        tasks = project_data.get("tasks", [])

        # Calculate project totals
        project_time = 0
        task_starts = 0
        task_stops = 0
        task_aborts = 0

        for task in tasks:
            if task.get("event_type") == "start":
                task_starts += 1
            elif task.get("event_type") == "stop":
                task_stops += 1
                if task.get("duration_seconds"):
                    project_time += task["duration_seconds"]
            elif task.get("event_type") == "abort":
                task_aborts += 1
                if task.get("duration_seconds"):
                    project_time += task["duration_seconds"]

        time_str = format_duration(project_time) if project_time else "0s"

        print(f"")
        print(f"  {project_name}")
        print(f"    Path: {project_path}")
        print(f"    Tasks: {task_starts} started, {task_stops} stopped, {task_aborts} aborted")
        print(f"    Time: {time_str}")

        # Show recent tasks if --verbose
        if args.verbose and tasks:
            recent = sorted(tasks, key=lambda x: x.get("timestamp", ""), reverse=True)[:5]
            print(f"    Recent:")
            for task in recent:
                event = task.get("event_type", "?")
                tid = task.get("task_id", "?")[:15]
                msg = task.get("message", "")[:30]
                ts = task.get("timestamp", "")[:10]
                duration = task.get("duration_seconds")
                dur_str = f" ({format_duration(duration)})" if duration else ""
                print(f"      [{event}] {tid} - {msg}{dur_str} [{ts}]")

    print("")
    print(f"Global tasks file: {get_global_tasks_path()}")
    print("")
    print("Export options:")
    print("  st8 global --json         # Output as JSON")
    print("  st8 global --export FILE  # Export to file")

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
    init_parser.add_argument("name", nargs="?", help="Project name (defaults to directory name)")
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

    # global - centralized task data across all projects
    global_parser = subparsers.add_parser("global", help="View global task data from all projects")
    global_parser.add_argument("--json", action="store_true", help="Output as JSON")
    global_parser.add_argument("--export", help="Export to JSON file")
    global_parser.add_argument("--verbose", "-v", action="store_true", help="Show recent tasks per project")
    global_parser.set_defaults(func=cmd_global)

    # prompt
    prompt_parser = subparsers.add_parser("prompt", help="AI-assisted development workflow")
    prompt_parser.add_argument("prompt", nargs="*", help="The prompt for the AI agent")
    prompt_parser.add_argument("--task", "-t", help="Continue existing task (backlog ID or task ID)")
    prompt_parser.add_argument("--simple", help="Override simple AI provider (ollama, openai, anthropic)")
    prompt_parser.add_argument("--agent", help="Override agent AI provider (claude-code, aider, cursor)")
    prompt_parser.add_argument("--no-promote", action="store_true", help="Skip promotion prompt at end")
    prompt_parser.add_argument("--dry-run", action="store_true", help="Show what would happen without executing")
    prompt_parser.set_defaults(func=cmd_prompt)

    # mission - shortcut for prompt with MISSION.md
    mission_parser = subparsers.add_parser("mission", help="Run AI agent with MISSION.md instructions")
    mission_parser.add_argument("--task", "-t", help="Continue existing task (backlog ID or task ID)")
    mission_parser.add_argument("--simple", help="Override simple AI provider (ollama, openai, anthropic)")
    mission_parser.add_argument("--agent", help="Override agent AI provider (claude-code, aider, cursor)")
    mission_parser.add_argument("--no-promote", action="store_true", help="Skip promotion prompt at end")
    mission_parser.add_argument("--dry-run", action="store_true", help="Show what would happen without executing")
    mission_parser.set_defaults(func=cmd_mission)

    # deploy
    deploy_parser = subparsers.add_parser("deploy", help="Deploy to remote server via SFTP")
    deploy_parser.add_argument("environment", nargs="?", help="Target environment (dev, stg, prod)")
    deploy_parser.add_argument("--list", "-l", action="store_true", help="List configured environments")
    deploy_parser.add_argument("--dry-run", action="store_true", help="Show what would be deployed")
    deploy_parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    deploy_parser.set_defaults(func=cmd_deploy)

    # git - with subcommands
    git_parser = subparsers.add_parser("git", help="Manage git repository")
    git_parser.set_defaults(func=cmd_git)
    git_subparsers = git_parser.add_subparsers(dest="git_command", help="Git subcommands")

    # git setup
    git_setup_parser = git_subparsers.add_parser("setup", help="Setup git repo from deploy.json config")
    git_setup_parser.add_argument("-m", "--message", nargs="*", help="Initial commit message")
    git_setup_parser.add_argument("--public", action="store_true", help="Create public repo (default: private)")
    git_setup_parser.set_defaults(git_func=cmd_git_setup)

    # git status
    git_status_parser = git_subparsers.add_parser("status", help="Show git configuration and status")
    git_status_parser.set_defaults(git_func=cmd_git_status)

    # sync
    sync_parser = subparsers.add_parser("sync", help="Sync Working Environment from stage or prod")
    sync_parser.add_argument("source", nargs="?", default="stage", help="Source to sync from (stage or prod)")
    sync_parser.add_argument("--no-snapshot", action="store_true", help="Skip creating safety snapshot")
    sync_parser.add_argument("-f", "--force", action="store_true", help="Skip confirmation prompt")
    sync_parser.set_defaults(func=cmd_sync)

    # hotfix - with subcommands
    hotfix_parser = subparsers.add_parser("hotfix", help="Manage hotfix workflow")
    hotfix_parser.set_defaults(func=cmd_hotfix)
    hotfix_subparsers = hotfix_parser.add_subparsers(dest="hotfix_command", help="Hotfix subcommands")

    # hotfix start
    hotfix_start_parser = hotfix_subparsers.add_parser("start", help="Start a hotfix from prod")
    hotfix_start_parser.add_argument("-m", "--message", nargs="*", help="Hotfix message (can include #hashtags)")
    hotfix_start_parser.set_defaults(hotfix_func=cmd_hotfix_start)

    # hotfix publish
    hotfix_publish_parser = hotfix_subparsers.add_parser("publish", help="Apply hotfix to stage and prod")
    hotfix_publish_parser.add_argument("-m", "--message", nargs="*", help="Publish message (can include #hashtags)")
    hotfix_publish_parser.add_argument("-f", "--force", action="store_true", help="Skip confirmation prompt")
    hotfix_publish_parser.set_defaults(hotfix_func=cmd_hotfix_publish)

    # hotfix finish
    hotfix_finish_parser = hotfix_subparsers.add_parser("finish", help="Complete or abort the hotfix")
    hotfix_finish_parser.add_argument("-f", "--force", action="store_true", help="Skip restore prompt")
    hotfix_finish_parser.set_defaults(hotfix_func=cmd_hotfix_finish)

    # task - with subcommands
    task_parser = subparsers.add_parser("task", help="Manage task snapshots and backlog")
    task_parser.set_defaults(func=cmd_task)
    task_subparsers = task_parser.add_subparsers(dest="task_command", help="Task subcommands")

    # task add
    task_add_parser = task_subparsers.add_parser("add", help="Add task to backlog (no snapshot)")
    task_add_parser.add_argument("message", nargs="*", help="Task message (can include #hashtags)")
    task_add_parser.set_defaults(task_func=cmd_task_add)

    # task start
    task_start_parser = task_subparsers.add_parser("start", help="Start a task (from backlog or new)")
    task_start_parser.add_argument("backlog_id", nargs="?", help="Backlog item ID to start (optional)")
    task_start_parser.add_argument("-m", "--message", nargs="*", help="Task message (can include #hashtags)")
    task_start_parser.set_defaults(task_func=cmd_task_start)

    # task status
    task_status_parser = task_subparsers.add_parser("status", help="Show task info and changes since snapshot")
    task_status_parser.set_defaults(task_func=cmd_task_status)

    # task abort
    task_abort_parser = task_subparsers.add_parser("abort", help="Restore Working Environment from snapshot")
    task_abort_parser.add_argument("-m", "--message", nargs="*", help="Abort message (can include #hashtags)")
    task_abort_parser.add_argument("-f", "--force", action="store_true", help="Skip confirmation prompt")
    task_abort_parser.set_defaults(task_func=cmd_task_abort)

    # task stop
    task_stop_parser = task_subparsers.add_parser("stop", help="Stop task (returns to backlog, or --finalize to complete)")
    task_stop_parser.add_argument("--finalize", action="store_true", help="Complete task permanently (doesn't return to backlog)")
    task_stop_parser.add_argument("--delete", action="store_true", help="Delete snapshot permanently (requires --finalize)")
    task_stop_parser.add_argument("--archive", action="store_true", help="Archive snapshot as zip file (requires --finalize)")
    task_stop_parser.add_argument("-m", "--message", nargs="*", help="Stop message (can include #hashtags)")
    task_stop_parser.add_argument("-f", "--force", action="store_true", help="Skip confirmation prompt")
    task_stop_parser.set_defaults(task_func=cmd_task_stop)

    # task list
    task_list_parser = task_subparsers.add_parser("list", help="List backlog, active, and recent tasks")
    task_list_parser.set_defaults(task_func=cmd_task_list)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
