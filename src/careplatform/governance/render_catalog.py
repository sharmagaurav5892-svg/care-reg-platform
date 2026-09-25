"""Generates docs/03_data_catalog.md from config/catalog.yaml and dq_rules.yaml.

The YAML is the source of truth. Never edit docs/03 by hand; change the YAML
and rerun:

    python -m careplatform.governance.render_catalog
"""
from __future__ import annotations

from careplatform import config

OUT = config.REPO_ROOT / "docs" / "03_data_catalog.md"


def render() -> str:
    cat = config.catalog()
    rules = config.dq_rules()["rules"]
    lines: list[str] = [
        "# 03 Data Catalog",
        "",
        "> Generated from `config/catalog.yaml`. Do not edit by hand.",
        "> Regenerate with `python -m careplatform.governance.render_catalog`.",
        "",
        "## Domains and owners",
        "",
        "| Domain | Data Owner | Data Steward |",
        "|--------|-----------|--------------|",
    ]
    for d, o in cat["domains"].items():
        lines.append(f"| {d} | {o['data_owner']} | {o['data_steward']} |")

    lines += [
        "",
        "## Table summary",
        "",
        "| Table | Layer | Domain | Classification | Retention (days) | Grain | DQ rules |",
        "|-------|-------|--------|----------------|------------------|-------|----------|",
    ]
    for t in cat["tables"]:
        n_rules = sum(1 for r in rules if r["table"] == t["name"])
        lines.append(
            f"| [{t['name']}](#{t['name'].replace('.', '')}) | {t['layer']} | {t['domain']} "
            f"| {t['classification']} | {t['retention_days']} | {t['grain']} | {n_rules} |"
        )

    for t in cat["tables"]:
        owners = cat["domains"][t["domain"]]
        lines += [
            "",
            f"## {t['name']}",
            "",
            t["description"],
            "",
            f"- **Owner:** {owners['data_owner']}  ",
            f"- **Steward:** {owners['data_steward']}  ",
            f"- **Classification:** {t['classification']}  ",
            f"- **Retention:** {t['retention_days']} days  ",
            f"- **Grain:** {t['grain']}  ",
            f"- **Primary key:** {', '.join(t['primary_key'])}  ",
            f"- **Write mode:** {t['write_mode']}",
            "",
            "| Column | Type | Nullable | Description |",
            "|--------|------|:--------:|-------------|",
        ]
        for c in t["columns"]:
            lines.append(
                f"| {c['name']} | {c['type']} | {'yes' if c['nullable'] else 'no'} | {c['description']} |"
            )
        trules = [r for r in rules if r["table"] == t["name"]]
        if trules:
            lines += ["", "**Data quality rules**", "", "| Rule | Severity | Dimension | Description |",
                      "|------|----------|-----------|-------------|"]
            for r in trules:
                lines.append(f"| {r['id']} | {r['severity']} | {r['dimension']} | {r['description']} |")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    OUT.write_text(render(), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(config.REPO_ROOT)}")
