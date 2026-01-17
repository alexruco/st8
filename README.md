# ST8 - Minimalist State-Based Versioning

A workflow-oriented state versioning system for indie hackers and solo developers. This is NOT a git replacement but a complementary tool for managing local development states.

## Core Concept

**Three states** managed by ST8:
- **Working directory** – your code (this IS the dev state)
- **stage** – snapshot of last promoted state (stored in `.st8/stage/`)
- **prod** – release-ready snapshot (stored in `.st8/prod/`)

**Task system** for isolated work:
- **Backlog** – queue of planned tasks (no snapshot yet)
- **Task snapshot** – frozen copy of working directory at task start
- Work freely, then either **abort** (restore snapshot) or **stop** (keep changes)

**Hotfix workflow** for urgent production fixes:
- Start from prod, make fixes, publish to both stage and prod simultaneously

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
st8 init "My Project"    # With custom name
# or just: st8 init      # Uses directory name

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
├── .st8ignore              # Additional exclude patterns (optional)
├── ...
└── .st8/                   # ST8 metadata and snapshots
    ├── config.json         # Configuration
    ├── state.json          # Current version and hashes
    ├── history.json        # Commit log
    ├── deploy.json         # Deployment & AI config
    ├── MISSION.md          # Instructions for AI coding agent
    ├── REPORT.md           # Agent feedback template
    ├── active.json         # Active task reference (if any)
    ├── backlog.json        # Task backlog queue
    ├── dev/                # Dev state metadata (hashes only)
    │   └── hashes.json
    ├── stage/              # Full snapshot of promoted state
    │   └── (files...)
    ├── prod/               # Full snapshot of released state
    │   └── (files...)
    ├── tasks/              # Task snapshots
    │   └── <task_id>/
    │       ├── meta.json   # Task metadata
    │       └── snapshot/   # Frozen copy of working dir
    ├── task_archives/      # Archived task snapshots (zip files)
    └── hotfixes/           # Hotfix branch metadata
```

## Commands

### `st8 init [name]`
Initialize ST8 in the current directory with an optional project name.

```bash
st8 init                  # Uses directory name as project name
st8 init "My Project"     # Custom project name
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

### `st8 global`
Show or export global task data aggregated from all ST8-managed projects. This command can be run from anywhere (doesn't require ST8 initialization).

```bash
st8 global              # Human-readable summary
st8 global --json       # JSON output to stdout
st8 global --export data.json  # Export to file
st8 global --verbose    # Show recent tasks per project
```

Options:
- `--json` - Output raw JSON to stdout
- `--export <file>` - Export data to specified file
- `--verbose` - Show detailed task list per project

### `st8 sync`
Sync working directory from stage or prod.

```bash
st8 sync              # Sync from stage (default)
st8 sync prod         # Sync from prod
st8 sync --no-snapshot # Skip safety snapshot
st8 sync -f           # Skip confirmation
```

This overwrites your working directory with the contents of stage or prod. A safety snapshot is created first (unless `--no-snapshot`) so you can recover if needed.

### `st8 deploy`
Deploy to remote server via FTP or SSH (rsync/scp).

```bash
st8 deploy --list      # List configured environments
st8 deploy dev         # Deploy to dev environment
st8 deploy stg         # Deploy to staging
st8 deploy prod        # Deploy to production
st8 deploy prod --dry-run  # Preview what would be deployed
st8 deploy prod -y     # Skip confirmation
```

Configure environments in `.st8/deploy.json`. Supports two protocols:
- **FTP** (port 21): Direct FTP upload with password authentication
- **SSH** (port 22): Uses rsync for efficient delta transfers, falling back to scp

## Task System

Tasks provide a safe way to experiment with changes. The workflow has two phases:

1. **Backlog**: Queue tasks without creating snapshots (lightweight planning)
2. **Active Task**: Create a frozen snapshot and work freely

When you start a task, ST8 creates a frozen snapshot of your working directory. You can then work freely and either:
- **Abort**: Restore to the snapshot (discard all changes)
- **Stop**: Keep your changes (pause and return to backlog, or finalize permanently)

### Task Workflow

```bash
# Option A: Add to backlog first, then start
st8 task add "Refactoring auth module #refactor"
st8 task start 1  # Start backlog item #1

# Option B: Start immediately (creates snapshot)
st8 task start -m "Refactoring auth module #refactor"

# Work on your code...
vim src/auth.py

# Check what changed since task start
st8 task status

# Option A: Pause and return to backlog (can resume later)
st8 task stop

# Option B: Complete task permanently
st8 task stop --finalize --delete   # Delete snapshot
st8 task stop --finalize --archive  # Archive snapshot

# Option C: Discard changes (restore to snapshot)
st8 task abort
```

### Task Commands

#### `st8 task add`
Add a task to the backlog queue (no snapshot created yet).

```bash
st8 task add "Working on feature X #feature"
```

#### `st8 task start`
Create a new task with a frozen snapshot of the working directory.

```bash
st8 task start -m "Working on feature X #feature"  # New task
st8 task start 1   # Start from backlog item #1 (or resume stopped task)
```

#### `st8 task status`
Show task info and what has changed since the snapshot.

```bash
st8 task status
```

Output:
```
Active Task: 20260109_143022
  Started: 2026-01-09 14:30:22
  Snapshot files: 127
  Message: Working on feature X

Changes since task started:
  Modified: 3
  Added:    1
  Deleted:  0

Modified files:
  ~ src/auth.py
  ~ src/config.py
  ~ tests/test_auth.py
Added files:
  + src/utils.py
```

#### `st8 task abort`
**ABORT**: Restore working directory from the snapshot. All changes since task start are lost.

```bash
st8 task abort              # Prompts for confirmation
st8 task abort -f           # Skip confirmation
st8 task abort -m "Didn't work out"
```

This operation:
1. Deletes all current files in working directory
2. Restores files from the task snapshot
3. Deletes the task folder

#### `st8 task stop`
**STOP**: Keep your current working directory. Two modes:

**Without `--finalize`** (pause and return to backlog):
```bash
st8 task stop              # Pause task, return to backlog
st8 task stop -f           # Skip confirmation
```
- Keeps Working Environment unchanged
- Archives snapshot for reference
- Returns task to backlog with time worked
- Resume later with `st8 task start <backlog_id>`

**With `--finalize`** (complete permanently):
```bash
st8 task stop --finalize --delete    # Complete and delete snapshot
st8 task stop --finalize --archive   # Complete and archive snapshot
st8 task stop --finalize --archive -m "Completed auth refactor #done"
```
- Task is permanently completed (doesn't return to backlog)
- Must specify `--delete` or `--archive` for snapshot disposal

Options:
- `--finalize` - Complete task permanently (doesn't return to backlog)
- `--delete` - Delete snapshot permanently (requires --finalize)
- `--archive` - Archive snapshot as zip file (requires --finalize)
- `-f, --force` - Skip confirmation prompt

#### `st8 task list`
List backlog, active, and recent tasks.

```bash
st8 task list
```

Output:
```
Backlog:
  #1 - Implement user settings [#feature]
  #2 - Fix login bug [#bug] (stopped) [2h 15m worked]

  Start a backlog item: st8 task start <id>

Active Task:
  [20260109_143022] 127 files - Working on feature X
    Started: 2026-01-09 14:30:22

Recent Tasks:
  [F] 20260108_091500 - Fixed login bug
      Finished: 2026-01-08 11:23:45
  [X] 20260107_160000 - Experimental redesign
      Finished: 2026-01-07 18:45:00
```

Status icons: `[A]` Active, `[F]` Finalized, `[X]` Aborted

### Task vs Stage/Prod

| Feature | Task | Stage/Prod |
|---------|------|------------|
| Purpose | Isolated experimentation | Version management |
| Snapshot | Full working directory | Respects exclude patterns |
| Resolution | Abort (restore) or Stop (keep) | Promote to stage, Release to prod |
| History | Task start/abort/stop logged | Version bumps logged |

## Hotfix Workflow

Hotfixes let you quickly fix production issues without disrupting your current work.

### Hotfix Commands

```bash
# Start a hotfix (syncs working dir from prod)
st8 hotfix start -m "Critical security fix #hotfix"

# Make your fixes in the working directory...
vim src/security.py

# Publish hotfix to both stage AND prod
st8 hotfix publish

# Clean up
st8 hotfix finish
```

#### `st8 hotfix start`
Start a hotfix from prod. This:
1. Saves your current working directory (if dirty)
2. Syncs working directory from prod
3. Records the hotfix state

```bash
st8 hotfix start -m "Fixing payment bug #hotfix"
```

#### `st8 hotfix publish`
Apply the hotfix to both stage and prod simultaneously. Hotfixes always create a patch version bump.

```bash
st8 hotfix publish
st8 hotfix publish -f  # Skip confirmation
```

#### `st8 hotfix finish`
Complete or abort the hotfix:
- If published: cleans up hotfix metadata
- If not published: optionally restores your previous working directory

```bash
st8 hotfix finish
st8 hotfix finish -f  # Skip restore prompt
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
  "name": "My Project",
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

### .st8ignore File

You can also add exclude patterns in a `.st8ignore` file at your project root (gitignore-style syntax):

```
# Build artifacts
*.log
*.tmp

# IDE files
.vscode/
.idea/

# Local config
.env.local
```

### Deploy Configuration

Edit `.st8/deploy.json` to configure remote deployment.

**SSH Example** (rsync/scp):
```json
{
  "environments": {
    "stg": {
      "enabled": true,
      "protocol": "ssh",
      "host": "staging.example.com",
      "port": 22,
      "username": "deploy",
      "remote_path": "/var/www/app"
    }
  },
  "auth": {
    "method": "key",
    "key_path": "~/.ssh/id_rsa"
  },
  "options": {
    "post_deploy_command": "sudo systemctl restart app"
  }
}
```

**FTP Example with Auto-Deploy**:
```json
{
  "environments": {
    "stg": {
      "enabled": true,
      "auto_deploy": true,
      "protocol": "ftp",
      "host": "ftp.example.com",
      "port": 21,
      "username": "ftpuser",
      "password": "yourpassword",
      "remote_path": "/public_html/app",
      "ignore": [".git", ".vscode", "*.log"]
    },
    "prod": {
      "enabled": true,
      "auto_deploy": true,
      "protocol": "ftp",
      "host": "ftp.example.com",
      "port": 21,
      "username": "ftpuser",
      "password": "yourpassword",
      "remote_path": "/public_html/app-prod"
    }
  }
}
```

### Auto-Deploy

Each environment can have `auto_deploy: true` to automatically deploy after certain commands:
- **stg**: Auto-deploys after `st8 promote`
- **prod**: Auto-deploys after `st8 release`

```bash
# With stg.auto_deploy enabled:
st8 promote "#feature New button"
# Output:
#   Promoted to stage as v1.2.0 (5.2% changed)
#   [Auto-deploy] Deploying to stg...
#   [SUCCESS] Deployed v1.2.0 to stg

# With prod.auto_deploy enabled:
st8 release "#release v1.2.0"
# Output:
#   Released to prod as v1.2.0
#   [Auto-deploy] Deploying to prod...
#   [SUCCESS] Deployed v1.2.0 to prod
```

Notes:
- `dev` deploys from working directory, `stg` from stage, `prod` from prod.
- Protocol auto-detects from port (21=FTP, 22=SSH) if not explicitly set.
- Each environment includes default `ignore` patterns (dev files, images, videos, archives). Customize as needed.

### Git Integration

ST8 can manage git repositories automatically, including creating remote repos on GitHub.

#### `st8 git setup`
One-command git setup that:
1. Initializes local git repo (if needed)
2. Verifies if GitHub repo exists (using `gh repo view`)
3. Creates remote repo on GitHub if it doesn't exist (private by default)
4. Auto-repairs dangling remotes (local origin pointing to non-existent repo)
5. Sets up origin remote
6. Makes initial commit (if needed)
7. Syncs and pushes the working branch (main/master)

Note: `stage` and `prod` branches are managed separately by `st8 promote` and `st8 release` via snapshot git repos in `.st8/stage/` and `.st8/prod/`.

This command is **idempotent** - safe to run multiple times.

```bash
# Auto-create private repo on GitHub and set everything up
st8 git setup

# Create public repo instead
st8 git setup --public

# With custom initial commit message
st8 git setup -m "Initial commit"
```

Requirements for auto-creating GitHub repos:
- `gh` CLI installed (`brew install gh`)
- Authenticated (`gh auth login`)

If `gh` isn't available, the command initializes local git only.

**Sync behavior**: If the remote repo has commits you don't have locally, `st8 git setup` will automatically fetch and merge (or rebase) before pushing. If there are conflicts that can't be auto-resolved, you'll be prompted to manually merge.

#### `st8 git status`
Show git configuration and current status.

```bash
st8 git status
```

#### Git Auto-Commit

ST8 can auto-commit the stage/prod folders to git branches on promote/release. Configure in `.st8/deploy.json`:

```json
{
  "git": {
    "enabled": true,
    "stage_branch": "stage",
    "prod_branch": "main",
    "auto_push": true
  }
}
```

- **enabled**: Enable git auto-commit (default: false, set to true after `st8 git setup`)
- **stage_branch**: Branch for promote commits (default: "stage")
- **prod_branch**: Branch for release commits (default: "main")
- **auto_push**: Push to remote after commit (default: true)

If no commit message is provided:
- Promote auto-generates: `Promote v1.2.0`
- Release auto-generates: `Release v1.2.0`

```bash
# With git.enabled:
st8 promote "#feature New API endpoint"
# Output:
#   Promoted to stage as v1.2.0
#   [Git] Committing stage...
#   Committed: New API endpoint
#   Pushed to origin/stage
```

### AI-Assisted Development

ST8 can orchestrate AI-assisted development using the `st8 prompt` command.

#### `st8 prompt`
AI-assisted development workflow that:
1. Creates a task with AI-generated description (or resumes existing task)
2. Invokes an AI agent (Claude Code, aider, etc.) with your prompt
3. Generates a commit message from the changes
4. Offers human review: abort, stop, or promote

```bash
# Basic usage - invoke Claude Code with a prompt
st8 prompt "Add a logout button to the navbar"

# Continue an existing task
st8 prompt --task 3 "Also add confirmation dialog"

# Use different AI providers
st8 prompt --simple openai --agent aider "Fix the login bug"

# Skip promotion at end
st8 prompt --no-promote "Refactor the API client"

# Preview workflow without executing
st8 prompt --dry-run "Add dark mode support"
```

Options:
- `--task <id>` - Continue existing task instead of creating new one
- `--simple <provider>` - Override simple AI provider (ollama, openai, anthropic)
- `--agent <provider>` - Override agent AI provider (claude-code, aider, cursor)
- `--no-promote` - Skip promotion prompt at end
- `--dry-run` - Show what would happen without executing

#### `st8 mission`
Shortcut command that runs the AI agent with instructions from `.st8/MISSION.md` and updates `.st8/REPORT.md` with results.

```bash
# Run mission from MISSION.md
st8 mission

# With task continuation
st8 mission --task 3

# Preview without executing
st8 mission --dry-run
```

This is equivalent to:
```bash
st8 prompt "do what is described on .st8/MISSION.md, then update .st8/REPORT.md"
```

The mission command:
1. Reads the instructions from `.st8/MISSION.md`
2. Passes them to the AI agent
3. Instructs the agent to update `.st8/REPORT.md` with feedback
4. Follows the same workflow as `st8 prompt` (task creation, human review, etc.)

Options (same as `st8 prompt`):
- `--task <id>` - Continue existing task
- `--simple <provider>` - Override simple AI provider
- `--agent <provider>` - Override agent AI provider
- `--no-promote` - Skip promotion prompt at end
- `--dry-run` - Show what would happen without executing

#### AI Configuration

Configure AI providers in `.st8/deploy.json`:

```json
{
  "ai": {
    "simple": {
      "provider": "ollama",
      "model": "llama3.2",
      "endpoint": "http://localhost:11434"
    },
    "agent": {
      "provider": "claude-code",
      "command": "claude"
    }
  }
}
```

**Simple AI** (for generating descriptions and commit messages):
- `ollama` - Local LLM via Ollama (default)
- `openai` - OpenAI API (requires `OPENAI_API_KEY` env var)
- `anthropic` - Anthropic API (requires `ANTHROPIC_API_KEY` env var)

**Agent AI** (for code execution):
- `claude-code` - Claude Code CLI (default)
- `aider` - Aider CLI
- `cursor` - Cursor IDE (manual prompt)

#### Mission and Report Files

ST8 creates two template files during initialization to help structure AI collaboration:

**`.st8/MISSION.md`** - Instructions for the AI coding agent:
- Objective and context
- Requirements (with checkboxes)
- Constraints and success criteria
- Files to focus on / out of scope
- Follows best prompting practices

**`.st8/REPORT.md`** - Agent feedback template:
- Status and summary
- Changes made and challenges
- Decisions and rationale
- Known issues and improvement opportunities
- Ideas for future work
- Feedback on how to write better missions

Edit `MISSION.md` before running `st8 prompt` to give the agent detailed context. After the agent completes, review and fill in `REPORT.md` to track learnings.

#### Workflow Example

```bash
$ st8 prompt "Add input validation to the login form"

ST8 Prompt
============================================================
Prompt: Add input validation to the login form

Generating task description...
  Description: Add login form input validation

Creating task snapshot...
Started task: 20260115_143022
  Snapshot files: 127

Invoking claude-code...
------------------------------------------------------------
[Claude Code runs interactively...]
------------------------------------------------------------

Analyzing changes...
  Added:    1
  Modified: 2
  Deleted:  0

Generating commit message...
  Suggested: feat: add email and password validation to login form

Review Options:
  [1] Abort   - Discard all changes, restore snapshot
  [2] Stop    - Keep changes, return task to backlog
  [3] Promote - Keep changes, promote to stage
  [4] Edit    - Edit commit message, then promote

Choice [1-4]: 3

Finalizing task...
Promoting to stage...
Promoted to stage as v1.2.0 (3.2% changed)
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

### backlog.json
```json
[
  {
    "id": 1,
    "message": "Implement user settings",
    "tags": ["feature"],
    "created_at": "2026-01-05T10:00:00Z",
    "status": "pending"
  },
  {
    "id": 2,
    "message": "Fix login bug",
    "tags": ["bug"],
    "created_at": "2026-01-06T09:00:00Z",
    "stopped_at": "2026-01-06T11:30:00Z",
    "total_duration_seconds": 9000,
    "status": "stopped"
  }
]
```

### global_tasks.json
Centralized task data from all ST8-managed projects. Stored as a sibling to `st8.py` (in the ST8 installation directory).

```json
{
  "projects": {
    "/path/to/project1": {
      "name": "Project One",
      "tasks": [
        {
          "task_id": "20260115_143022",
          "event": "start",
          "timestamp": "2026-01-15T14:30:22Z",
          "message": "Working on feature X"
        },
        {
          "task_id": "20260115_143022",
          "event": "stop",
          "timestamp": "2026-01-15T16:45:00Z",
          "duration_seconds": 8078,
          "outcome": "finalized"
        }
      ]
    }
  },
  "summary": {
    "total_tasks": 15,
    "total_time_seconds": 54000,
    "last_updated": "2026-01-17T18:20:00Z"
  }
}
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

# Deploy to production server
st8 deploy prod

# Or deploy the prod snapshot with git
cd .st8/prod
git init
git add .
git commit -m "Release v1.4.0 - Stripe integration"
git push origin main
```

## Example: Using Tasks for Risky Changes

```bash
# About to try a risky refactor? Start a task first
st8 task start -m "Refactoring database layer #refactor"
# Output: Started task: 20260109_143022
#         Snapshot files: 127

# Make your changes...
vim src/database.py
vim src/models.py

# Check what you've changed
st8 task status
# Output: Modified: 2, Added: 0, Deleted: 0

# Test your changes... they work!
# Keep your changes and complete the task
st8 task stop --finalize --delete -f
# Output: Task 20260109_143022 STOPPED and FINALIZED.

# Now promote to stage as normal
st8 promote "#refactor Database layer cleanup"

# --- OR if the refactor didn't work ---

# Uh oh, broke everything. Restore to before you started
st8 task abort -f
# Output: Restored 127 files to Working Environment.
#         Task 20260109_143022 ABORTED.
# Your code is back to exactly how it was before the task!
```

## Example: Using Backlog for Planning

```bash
# Plan your work by adding to backlog (no snapshots yet)
st8 task add "Implement user authentication #feature #auth"
st8 task add "Fix memory leak in worker #bug #performance"
st8 task add "Add API rate limiting #feature #security"

# See your backlog
st8 task list
# Output:
# Backlog:
#   #1 - Implement user authentication [#feature, #auth]
#   #2 - Fix memory leak in worker [#bug, #performance]
#   #3 - Add API rate limiting [#feature, #security]

# Start working on the first item
st8 task start 1
# Output: Started task: 20260109_150000
#         From backlog: #1
#         Snapshot files: 127

# Work on it for a while, then need to switch tasks...
st8 task stop
# Output: Task 20260109_150000 STOPPED.
#         Returned to backlog as #4

# Later, resume where you left off
st8 task start 4
# Output: Started task: 20260109_160000
#         From backlog: #4
#         Snapshot files: 127

# Finish the task
st8 task stop --finalize --delete
```

## Example: Hotfix Workflow

```bash
# You're working on a feature when a critical bug is reported
st8 task status  # Make sure no active task

# Start a hotfix from prod
st8 hotfix start -m "Critical payment bug #hotfix"
# Output: Saving current Working Environment state...
#         Syncing Working Environment from prod...
#         Started hotfix: hotfix_20260109_160000

# Fix the bug
vim src/payments.py

# Publish to both stage and prod
st8 hotfix publish
# Output: Publishing hotfix: hotfix_20260109_160000
#         Changes to apply:
#           Files changed: 1
#           Version: v1.4.0 -> v1.4.1
#         Updating stage...
#         Updating prod...
#         [SUCCESS] Hotfix published as v1.4.1

# Clean up and return to your previous work
st8 hotfix finish
# Output: Hotfix hotfix_20260109_160000 finished.

# Deploy the fix
st8 deploy prod
```

## Requirements

- Python 3.8+
- No external dependencies (standard library only)
- For SSH deployment: rsync (recommended) or scp
- For FTP deployment: built-in (uses Python's ftplib)
- For GitHub repo auto-creation: `gh` CLI (optional)

## License

MIT
