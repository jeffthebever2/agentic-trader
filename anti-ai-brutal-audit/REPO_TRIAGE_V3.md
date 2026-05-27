# Repo Triage v3

Target: `.`  
Mode: `repo`

## Best next scan targets

Run the auditor on actual UI source folders, not generated/cache folders.

Recommended commands:

```bash
OUT_DIR=anti-ai-web-audit-v3 STRICTNESS=3 bash anti-ai-design-auditor-v3.sh web
OUT_DIR=anti-ai-src-audit-v3 STRICTNESS=3 bash anti-ai-design-auditor-v3.sh src
OUT_DIR=anti-ai-static-audit-v3 STRICTNESS=3 bash anti-ai-design-auditor-v3.sh public
OUT_DIR=anti-ai-full-audit-v3 MAX_FILES=4500 STRICTNESS=2 bash anti-ai-design-auditor-v3.sh .
```

## Scan stats

```json
{
  "target": ".",
  "mode": "repo",
  "strictness": 3,
  "scanned_files": 65,
  "scanned_bytes": 10151155,
  "extensions": {
    ".json": 31,
    ".yml": 3,
    ".css": 3,
    ".html": 1,
    ".js": 8,
    ".mjs": 19
  },
  "skipped": {
    "non_frontend_ext": 367,
    "too_large": 2
  },
  "domain_noun_hits": 44,
  "has_motion": true,
  "has_reduced_motion": true,
  "has_focus_system": true,
  "has_state_depth": true,
  "has_semantic_tokens": true,
  "combo_findings": [
    {
      "combo": "hero_gradient_badge_cta",
      "hits": 2,
      "points": 15.0
    },
    {
      "combo": "blob_glass_card_stack",
      "hits": 2,
      "points": 15.0
    },
    {
      "combo": "three_feature_lucide_grid",
      "hits": 2,
      "points": 15.0
    },
    {
      "combo": "shadcn_default_dark_saas",
      "hits": 3,
      "points": 22.5
    }
  ]
}
```

## Ignore these during design audits

- `node_modules`
- `.venv`, `venv`, `.venv-torch`
- `dist`, `build`, `.next`
- cache folders
- generated artifacts
- test snapshots unless testing visual output
