# Demo Project

A deliberately small multi-language project used to exercise FlashPack:

- Python application code
- JavaScript helper
- Markdown docs (outside the default include whitelist)
- JSON config (outside the default include whitelist)
- Jupyter notebook (outside the default include whitelist)
- docx report (binary, outside the default include whitelist)
- binary image
- no real secrets are stored in this demo; `.env` is intentionally gitignored

Run the default XML snapshot:

```bash
python -m flashpack examples/demo-project --output examples/demo-snapshot.xml
```

Run the Markdown snapshot:

```bash
python -m flashpack examples/demo-project --format markdown --output examples/demo-snapshot.md
```
