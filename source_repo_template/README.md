# care-reg-source-docs

The source document repo for the CareReg Intelligence Platform. The platform reads it through the GitHub REST API. It never clones it.

**Keep this repo private.** The files are public laws, but the e-Laws terms of use limit redistribution (`redistribute_raw: false` in the platform's `config/sources.yaml`). A private repo is how we respect that.

## Layout

One folder per source. The folder name must match a `repo_path` in the platform's `config/sources.yaml`:

```
regulations/
  on_rha_2010/                 Retirement Homes Act, 2010
  on_oreg_166_11/              O. Reg. 166/11
  on_fltca_2021/               Fixing Long-Term Care Act, 2021
  on_oreg_246_22/              O. Reg. 246/22
  on_ltc_inspection_reports/   not approved yet: files here are ignored
```

Allowed file types: `.pdf .html .htm .txt .md` (README files are ignored).

## Updating a source

When a law is amended, put the new file in its folder (new name, or replace the old one) and commit. On the next run the platform:

- sees the new commit
- downloads only the changed file
- keeps both versions in bronze
