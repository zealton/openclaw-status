# OpenClaw Status

Static status page for the Mac mini OpenClaw runtime.

## Files

- `index.html`: public status page
- `status.json`: generated machine status payload
- `generate_status.py`: reads launchd + logs and rewrites `status.json`
- `publish_status.sh`: updates `status.json` and pushes it when this folder is a git repo

## Local refresh

```bash
python3 generate_status.py
open index.html
```

## Deployment

This folder is intentionally static so it can be deployed to:

- GitHub Pages
- Vercel static hosting

For remote freshness, the Mac mini still needs to run `publish_status.sh` on a schedule.
