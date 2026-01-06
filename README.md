# ST8 - Minimalist State-Based Versioning

A workflow-oriented state versioning system for indie hackers and solo developers. This is NOT a git replacement but a complementary tool for managing local development states.

## Core Concept

Three states managed by ST8:
- **Working directory** – your code (this IS the dev state)
- **stage** – snapshot of last promoted state (stored in `.st8/stage/`)
- **prod** – release-ready snapshot (stored in `.st8/prod/`)

Your working directory is the dev state. You edit files normally, then promote snapshots to stage and release to prod. All metadata and snapshots are stored inside `.st8/`.

## Installation

```bash
# Copy st8.py to your PATH
cp st8.py /usr/local/bin/st8
chmod +x /usr/local/bin/st8

# Or run directly with Python
python3 st8.py <command>
```

## Quick Start

```bash
# Initialize ST8 in your project
cd myproject
st8 init

# Your working directory IS the dev state - edit files normally
echo "print('hello')" > hello.py

# Promote to stage
st8 promote "#feature Initial code"
# Output: Promoted to stage as v1.0.0 (100.0% changed)

# Check status
st8 status

# Make changes
echo "print('hello world')" > hello.py

# Promote again
st8 promote "#fix Better greeting"
# Output: Promoted to stage as v1.0.1 (2.3% changed)

# Release to prod
st8 release "#release v1.0.1"
# Output: Released to prod as v1.0.1

# Deploy the prod snapshot with git
cd .st8/prod && git init && git add . && git commit -m "Release v1.0.1"
```

## Directory Structure

```
project/                    # Your working directory (dev state)
├── src/                    # Your code files
├── package.json
├── ...
└── .st8/                   # ST8 metadata and snapshots
    ├── config.json         # Configuration
    ├── state.json          # Current version and hashes
    ├── history.json        # Commit log
    ├── dev/                # Dev state metadata (hashes only)
    │   └── hashes.json
    ├── stage/              # Full snapshot of promoted state
    │   └── (files...)
    └── prod/               # Full snapshot of released state
        └── (files...)
```

## Commands

### `st8 init`
Initialize ST8 in the current directory.

```bash
st8 init
```

### `st8 promote [message]`
Snapshot working directory to stage with automatic version bump.

```bash
st8 promote "#feature #auth Added JWT login"
st8 promote --major "Breaking API change"
st8 promote --minor "New feature"
st8 promote --patch "Bug fix"
```

Options:
- `--major` - Force major version bump
- `--minor` - Force minor version bump
- `--patch` - Force patch version bump

### `st8 restore`
Restore working directory from stage (rollback).

```bash
st8 restore           # Prompts for confirmation
st8 restore --force   # Skip confirmation
```

### `st8 release [message]`
Release stage to prod.

```bash
st8 release "#release v1.4.0"
```

### `st8 status`
Show current state and pending changes.

```bash
st8 status
```

Output:
```
Version: v1.3.24

Changes (compared to stage):
  + src/new-file.py
  ~ src/modified.py
  - src/deleted.py

Estimated promotion: v1.3.24 -> v1.4.0 (minor, 12.3% changed)

Recent history:
  [P] v1.3.24 (2026-01-05): Fixed authentication bug
  [P] v1.3.23 (2026-01-04): Added user settings
  [R] v1.3.22 (2026-01-03): Release v1.3.22
```

### `st8 log`
Show commit history.

```bash
st8 log                  # All history
st8 log --tag feature    # Filter by tag
st8 log --last 5         # Last 5 entries
```

### `st8 stats`
Show statistics.

```bash
st8 stats
```

## Automatic Semantic Versioning

Version bumps are calculated based on weighted percentage of code changed:

| Change % | Bump Type | Example |
|----------|-----------|---------|
| 0-5%     | patch     | 1.3.24 -> 1.3.25 |
| 5-25%    | minor     | 1.3.24 -> 1.4.0 |
| >25%     | major     | 1.3.24 -> 2.0.0 |

### Path Weights

Different paths contribute differently to the change calculation:

```json
{
  "weighted_paths": {
    "src/": 1.0,
    "lib/": 1.0,
    "tests/": 0.3,
    "docs/": 0.1,
    "config/": 0.5
  }
}
```

This means:
- Changes to `src/` count fully toward version bump
- Changes to `tests/` only count 30%
- Changes to `docs/` only count 10%

## Configuration

Edit `.st8/config.json` to customize:

```json
{
  "current_version": "0.1.0",
  "thresholds": {
    "patch": 0.05,
    "minor": 0.25
  },
  "weighted_paths": {
    "src/": 1.0,
    "lib/": 1.0,
    "tests/": 0.3,
    "docs/": 0.1,
    "config/": 0.5
  },
  "exclude": [
    "node_modules/",
    ".st8/",
    "build/",
    "dist/",
    ".git/",
    "__pycache__/",
    "*.pyc"
  ]
}
```

## Data Formats

### state.json
```json
{
  "version": "1.3.24",
  "dev_hash": "abc123...",
  "stage_hash": "def456...",
  "prod_hash": "789xyz...",
  "last_promoted": "2026-01-05T14:23:11Z"
}
```

### history.json
```json
[
  {
    "version": "1.4.0",
    "timestamp": "2026-01-05T14:23:11Z",
    "message": "Added JWT login",
    "tags": ["feature", "auth"],
    "percent_changed": 18.3,
    "files_changed": 12,
    "type": "promote"
  }
]
```

## Example Workflow

```bash
# Initialize in your existing project
cd myproject
st8 init

# Edit your code normally
vim src/feature.py

# Check what changed
st8 status

# Promote to stage
st8 promote "#feature #payments Added Stripe integration"
# Output: Promoted to stage as v1.4.0 (12% changed)

# Oops, made a mistake? Restore from stage
st8 restore
# Output: Warning: Working directory has changes that will be lost

# Ready to release
st8 release "#release v1.4.0"
# Output: Released to prod as v1.4.0

# Deploy the prod snapshot
cd .st8/prod
git init
git add .
git commit -m "Release v1.4.0 - Stripe integration"
git push origin main
```

## Requirements

- Python 3.8+
- No external dependencies (standard library only)

## License

MIT
