# Target Library Inputs

This directory is reserved for target C libraries used as RALFuzz inputs.
Third-party target libraries are not part of the RALFuzz software distribution.

The default illustrative workflow expects cJSON under `api/cJSON/`. To fetch the
pinned external cJSON snapshot used by the quick-start commands, run:

```powershell
.\pipeline\fetch_cjson.ps1
```

or:

```bash
bash pipeline/fetch_cjson.sh
```
