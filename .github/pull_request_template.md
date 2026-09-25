## What changed and why

## Change type
- [ ] Standard
- [ ] Governance (source, classification, DQ rule)
- [ ] Model (prompt or model version)
- [ ] Emergency

## Governance checklist
- [ ] New or changed table is in `config/catalog.yaml` and `docs/03` is regenerated
- [ ] New or changed DQ rule has a one line reason for its threshold
- [ ] Prompt changes create a new version file, with eval results attached below
- [ ] New sources are approved in `config/sources.yaml` (approved_by, approved_on)
- [ ] No secrets, no raw source files, no personal data in the diff
- [ ] Cost impact estimated (if this adds model calls)

## Eval results (model changes only)
