# temporal-workdir

Shared working directories for Temporal activities across distributed workers.

## The Problem

Temporal activities that read and write files break when you run multiple workers — each worker has its own disk. An activity that writes `output.csv` on Worker A won't find it when retried on Worker B.

`temporal-workdir` solves this by syncing a local directory with remote storage (GCS, S3, local filesystem, etc.) before and after each activity execution.

## Install

```bash
pip install temporal-workdir

# Add your cloud storage backend:
pip install gcsfs    # Google Cloud Storage
pip install s3fs     # Amazon S3
pip install adlfs    # Azure Blob Storage
```

## Quick Start

### Context Manager

Use `Workspace` anywhere you need shared file state between activities:

```python
import json
from temporal_workdir import Workspace

# Pull remote files → work locally → push changes back
async with Workspace("gs://my-bucket/jobs/job-123") as ws:
    config = json.loads((ws.path / "config.json").read_text())
    (ws.path / "result.csv").write_text("id,score\n1,0.95")
# Clean exit → changes uploaded automatically
# Exception → nothing uploaded, remote state unchanged
```

### Activity Decorator

The `@workspace` decorator handles pull/push around your activity. Template variables are resolved from `activity.info()`:

```python
from temporalio import activity
from temporal_workdir import workspace, get_workspace_path

@workspace("gs://my-bucket/{workflow_id}/{activity_type}")
@activity.defn
async def process_data(order_id: str) -> str:
    ws = get_workspace_path()  # local Path to synced directory
    (ws / "orders.txt").write_text(order_id)
    return "done"
```

Available template variables: `{workflow_id}`, `{activity_id}`, `{activity_type}`, `{task_queue}`.

### Custom Template Variables

Use `key_fn` to add your own template variables from the activity arguments:

```python
@workspace(
    "gs://my-bucket/{workflow_id}/users/{user_id}",
    key_fn=lambda user_id, **_: {"user_id": user_id},
)
@activity.defn
async def process_user(user_id: str) -> None:
    ws = get_workspace_path()
    # Each user gets their own workspace
    ...
```

### Read-Only Access

Pull without pushing back — useful for reading shared state:

```python
async with Workspace("gs://my-bucket/shared/config", read_only=True) as ws:
    config = json.loads((ws.path / "settings.json").read_text())
    # No upload on exit, even if you modify files
```

### Managing Workspaces

```python
from temporal_workdir import list_workspace_names, delete_workspace

# List workspace names under a prefix
names = list_workspace_names("gs://my-bucket/jobs")
# → ["job-001", "job-002", "job-003"]

# Delete a workspace
delete_workspace("gs://my-bucket/jobs/job-001")  # → True if deleted
```

## How It Works

```
Activity starts
  ↓
Pull: download {url}.tar.gz → unpack to temp dir
  ↓
Execute: your code reads/writes files at ws.path
  ↓
Push: pack temp dir → upload {url}.tar.gz
  ↓
Cleanup: delete temp dir
```

- First run (no archive exists): starts with an empty directory
- Exception during execution: no push — remote state stays unchanged
- Empty workspace after execution: remote archive is deleted

## Storage Backends

Uses [fsspec](https://filesystem-spec.readthedocs.io/) for storage. Any fsspec-compatible backend works:

| Scheme | Backend | Package |
|--------|---------|---------|
| `gs://` | Google Cloud Storage | `gcsfs` |
| `s3://` | Amazon S3 | `s3fs` |
| `az://` | Azure Blob Storage | `adlfs` |
| `file://` | Local filesystem | — |
| `memory://` | In-memory (testing) | — |

Pass backend-specific options as keyword arguments:

```python
Workspace("gs://bucket/key", project="my-gcp-project", token="cloud")
```
