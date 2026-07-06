#!/usr/bin/env python3
"""Fetch Corrective Measures from Jira and rewrite the review-queue CSV.

Requires JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN in .env (repo root).
Run from repo root:

    uv run python scripts/fill_review_queue_corrective_measures.py

If the API still returns encrypted values for customfield_10994, that column
stays empty for those rows (same as before).

Optional: set JIRA_PARTNER_CUSTOM_FIELD to a Jira custom field id (e.g.
customfield_12345) to fill the partner_or_customer column from the API; if
unset, that column is left empty for manual paste from Jira.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

JQL = (
    'project = "EcoSystem Engineering Close Loop" and type = "Closed Loop" '
    'and (resolution in (Unresolved, Done, Done-Errata, "Test Pending") '
    'or resolution not in ("Not a Bug", Duplicate)) '
    "and created <= startOfDay() and status = Review "
    "order by created desc"
)

CORRECTIVE_FIELD = "customfield_10994"
OUT_CSV = REPO_ROOT / "reports" / "review-queue-corrective-component-ocp-rhel.csv"
PARTNER_FIELD = os.environ.get("JIRA_PARTNER_CUSTOM_FIELD", "").strip()


def _fmt_corrective_measures(raw: object) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        s = raw.strip()
        if len(s) > 64 and re.fullmatch(r"[A-Za-z0-9+/=\n_-]+", s.replace("\n", "")):
            return ""
        return s
    if isinstance(raw, list):
        parts: list[str] = []
        for x in raw:
            if isinstance(x, str):
                if len(x) > 64 and re.fullmatch(r"[A-Za-z0-9+/=_-]+", x):
                    return ""
                parts.append(x)
            elif isinstance(x, dict):
                parts.append(str(x.get("value") or x.get("name") or x))
            else:
                parts.append(str(x))
        return "; ".join(parts)
    if isinstance(raw, dict):
        return str(raw.get("value") or raw.get("name") or json.dumps(raw))
    return str(raw)


def _ocp_rhel_category(summary: str, comp_names: list[str]) -> str:
    s = (summary or "").lower()
    comps = " ".join(comp_names).lower()
    blob = s + " " + comps
    has_rhel = bool(
        re.search(r"\brhel\b|rhel-\d|\[rhel", blob)
        or re.search(r"kernel\s*:|kernel /|ceph|mds\b|cephfs|bz#", blob)
        or re.search(r"\bice:|phc2sys|qemu-kvm|live migration", blob)
    )
    has_ocp = bool(
        re.search(
            r"\bocp\b|openshift|acm\b|baremetalhost|multus|whereabouts|"
            r"assisted installer|clusterinstance|lifecycle-agent|odf--|"
            r"release-4\.|[^0-9]4\.\d{2}\b|gitops|kube-rbac|alertmanager|"
            r"prometheus|service monitor|egress ip|hostnetwork|routingviahost|"
            r"ironic|mcc\b|\bbmo\b|oauth-apiserver|apiserver-library-go|"
            r"sdn|ovs-configuration|ovs-conf|tmm pod|spk ",
            blob,
        )
    )
    if "merge_requests" in s and "rhel" in s:
        has_rhel = True
    if has_ocp and has_rhel:
        return "OCP + RHEL"
    if has_ocp:
        return "OCP"
    if has_rhel:
        return "RHEL"
    return "Unclassified"


def main() -> int:
    try:
        from jira import JIRA
    except ImportError:
        print("Install dependencies: uv sync", file=sys.stderr)
        return 1

    token = os.environ.get("JIRA_API_TOKEN")
    email = os.environ.get("JIRA_EMAIL")
    url = os.environ.get("JIRA_URL", "https://redhat.atlassian.net")
    if not token or not email:
        print("Set JIRA_API_TOKEN and JIRA_EMAIL in .env", file=sys.stderr)
        return 1

    jira = JIRA(server=url, basic_auth=(email, token))
    field_list = f"summary,components,{CORRECTIVE_FIELD}"
    if PARTNER_FIELD:
        field_list = f"{field_list},{PARTNER_FIELD}"
    issues = jira.search_issues(
        JQL,
        maxResults=200,
        fields=field_list,
    )

    if not issues:
        print(
            "Jira returned 0 issues for this JQL. Check Browse permission on "
            '"EcoSystem Engineering Close Loop" and that Review issues exist.',
            file=sys.stderr,
        )
        return 1

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    filled = 0
    filled_partner = 0
    with OUT_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "key",
                "partner_or_customer",
                "corrective_measures_paste_from_jira",
                "jira_component",
                "ocp_rhel_category",
                "summary",
            ]
        )
        for i in issues:
            f = i.fields
            comps = [c.name for c in (f.components or [])]
            cm = _fmt_corrective_measures(getattr(f, CORRECTIVE_FIELD, None))
            if cm:
                filled += 1
            partner = ""
            if PARTNER_FIELD:
                partner = _fmt_corrective_measures(getattr(f, PARTNER_FIELD, None))
                if partner:
                    filled_partner += 1
            w.writerow(
                [
                    i.key,
                    partner,
                    cm,
                    ", ".join(comps) if comps else "—",
                    _ocp_rhel_category(f.summary or "", comps),
                    f.summary or "",
                ]
            )

    print(
        f"Wrote {OUT_CSV} ({len(issues)} rows, {filled} with non-empty "
        f"Corrective Measures, {filled_partner} with non-empty partner/customer)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
