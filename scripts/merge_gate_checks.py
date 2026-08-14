#!/usr/bin/env python3
"""Deterministic checks for the Phase 2 claude-merge-gate.yml workflow.

Everything here is pure detection/validation logic, no LLM reasoning -- the
merge-gate workflow calls this as a plain script, matching the project's own
rule (cloud.md): "Deterministic logic handles detection/validation. AI
reasons on top -- AI does not execute."

CLI usage (see each subcommand's --help):
    merge_gate_checks.py required-checks <repo_root> --file PATH [--file PATH ...]
    merge_gate_checks.py plan-safety <plan.json>
    merge_gate_checks.py iam-safety <plan.json>
    merge_gate_checks.py cost-safety <plan.json>

The plan-safety/iam-safety/cost-safety subcommands print a one-line JSON
{"ok": bool, "reason": str} to stdout and exit 0 if ok, 1 if not -- callers
that just need pass/fail can check the exit code alone.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Path matching against this repo's actual workflow trigger `paths:` filters.
# Reads the CURRENT filters from the real workflow files at call time -- never
# a hardcoded copy that could drift from what the workflows actually do.
# ---------------------------------------------------------------------------


def _path_matches(path: str, pattern: str) -> bool:
    """Match a changed-file path against one GitHub Actions `paths:` glob.

    Handles exactly the pattern shapes this repo's workflows use:
    "prefix/**", "prefix/**/*.ext", bare "*.ext" (repo-root only), and a
    single "*" wildcard within one path segment (e.g. "terraform-*.yml").
    """
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(prefix + "/")
    if "/**/" in pattern:
        prefix, tail = pattern.split("/**/", 1)
        if not path.startswith(prefix + "/"):
            return False
        rest = path[len(prefix) + 1 :]
        return _path_matches_tail(rest, tail)
    if "/" not in pattern and pattern.startswith("*."):
        # bare "*.py" style -- repo-root files only, no further "/" allowed
        return "/" not in path and path.endswith(pattern[1:])
    if "*" in pattern:
        regex = "^" + re.escape(pattern).replace(r"\*", "[^/]*") + "$"
        return re.match(regex, path) is not None
    return path == pattern


def _path_matches_tail(remainder: str, tail_pattern: str) -> bool:
    if tail_pattern.startswith("*."):
        return remainder.endswith(tail_pattern[1:])
    return remainder == tail_pattern


def load_trigger_paths(workflow_file: Path, event: str) -> list[str]:
    """Read on.<event>.paths from a real workflow file.

    PyYAML parses a bare `on:` key as the boolean True (YAML 1.1), not the
    string "on" -- handled explicitly rather than assumed away.
    """
    if not workflow_file.exists():
        return []
    data = yaml.safe_load(workflow_file.read_text()) or {}
    on_block = data.get(True, data.get("on", {})) or {}
    event_block = on_block.get(event, {}) or {}
    if isinstance(event_block, dict):
        return list(event_block.get("paths", []) or [])
    return []


# Workflow file -> check name(s) it produces, keyed by the pull_request event
# that gates whether it runs at all for a given PR's changed files.
_PR_CHECK_WORKFLOWS = {
    "python-tests.yml": ["pytest", "mcp-pytest"],
    "terraform-plan.yml": ["plan"],
}


def compute_required_checks(repo_root: Path, changed_files: list[str]) -> dict:
    """Which check names a PR with these changed files is actually expected
    to produce, plus whether merging it would trigger terraform-apply.yml.
    """
    workflows_dir = repo_root / ".github" / "workflows"
    required: set[str] = set()
    for wf_name, check_names in _PR_CHECK_WORKFLOWS.items():
        patterns = load_trigger_paths(workflows_dir / wf_name, "pull_request")
        if any(_path_matches(f, p) for f in changed_files for p in patterns):
            required.update(check_names)

    apply_patterns = load_trigger_paths(workflows_dir / "terraform-apply.yml", "push")
    terraform_apply_triggers = any(
        _path_matches(f, p) for f in changed_files for p in apply_patterns
    )

    return {
        "required_checks": sorted(required),
        "terraform_apply_triggers": terraform_apply_triggers,
    }


# ---------------------------------------------------------------------------
# Terraform plan safety: destroy / replacement detection.
# Per https://developer.hashicorp.com/terraform/internals/json-format,
# resource_changes[].change.actions is one of: ["no-op"], ["create"],
# ["read"], ["update"], ["delete","create"], ["create","delete"], ["delete"].
# Checking for "delete" in actions catches pure deletes AND both replacement
# orderings in one condition.
# ---------------------------------------------------------------------------


def _real_changes(plan: dict) -> list[dict]:
    return [
        rc
        for rc in plan.get("resource_changes", [])
        if rc.get("change", {}).get("actions") not in ([], ["no-op"], None)
    ]


def classify_plan_safety(plan: dict) -> tuple[bool, str]:
    destructive = []
    for rc in _real_changes(plan):
        actions = rc.get("change", {}).get("actions", [])
        if "delete" in actions:
            destructive.append(f"{rc.get('address')} ({'+'.join(actions)})")
    if destructive:
        return False, "PLAN SAFETY STOP: destroy/replacement detected: " + "; ".join(destructive)
    return True, "No destroy/replacement actions in plan."


# ---------------------------------------------------------------------------
# IAM / security safety. Matches on "iam" appearing anywhere in the resource
# type name -- broad on purpose (covers google_project_iam_member,
# google_service_account_iam_member, google_storage_bucket_iam_member,
# google_iap_agent_registry_iam_member, google_iam_workload_identity_pool*,
# and any future google_*iam* type) rather than one exact spelling.
#
# Deliberately does NOT special-case "delete is already covered by the plan
# safety gate" -- this function flags ANY non-no-op action on an IAM
# resource, including deletes, independently of what classify_plan_safety
# finds. Two gates independently catching the same real risk is intentional
# defense in depth, not redundancy to trim.
# ---------------------------------------------------------------------------


def classify_iam_safety(plan: dict) -> tuple[bool, str]:
    iam_changes = []
    for rc in _real_changes(plan):
        rtype = rc.get("type", "")
        if "iam" in rtype.lower():
            actions = rc.get("change", {}).get("actions", [])
            iam_changes.append(f"{rc.get('address')} ({'+'.join(actions)})")
    if iam_changes:
        return False, (
            "IAM SAFETY STOP: IAM/security resource change requires manual review: "
            + "; ".join(iam_changes)
        )
    return True, "No IAM/security resource changes in plan."


# ---------------------------------------------------------------------------
# Cost safety. Not a dollar estimator -- terraform plan cannot reliably give
# one. Auto-passes a curated set of resource types already established in
# this repo's own history as $0/config-only, plus two specifically-reasoned
# exceptions (in-place Agent Engine source update; Cloud Run MCP update that
# does not raise min_instance_count). Anything else defaults to "unknown,
# stop" -- the explicit design choice, not an oversight.
# ---------------------------------------------------------------------------

_COST_SAFE_TYPES = {
    "google_logging_metric",
    "google_logging_project_bucket_config",
    "google_monitoring_alert_policy",
    "google_monitoring_notification_channel",
    "google_project_service",
    "google_project_service_identity",
    "google_model_armor_template",
    "google_network_security_authz_policy",
    "google_network_services_agent_gateway",
    "google_network_services_authz_extension",
    "google_storage_bucket_object",
    "google_artifact_registry_repository",
    "google_service_account",
    "time_sleep",
}
_REASONING_ENGINE_TYPE = "google_vertex_ai_reasoning_engine"
_CLOUD_RUN_TYPE = "google_cloud_run_v2_service"


def _unwrap_single_block(value):
    """A Terraform nested block appears as a plain object ("single" nesting
    mode) or a list of one object ("list"/"set" nesting mode), per the
    official JSON format spec. Normalize to a dict either way.
    """
    if isinstance(value, list):
        return value[0] if value else None
    return value if isinstance(value, dict) else None


def _extract_min_instance_count(side: dict | None):
    template = _unwrap_single_block((side or {}).get("template"))
    scaling = _unwrap_single_block((template or {}).get("scaling"))
    if not isinstance(scaling, dict):
        return None
    return scaling.get("min_instance_count")


def classify_cost_safety(plan: dict) -> tuple[bool, str]:
    changes = _real_changes(plan)
    if not changes:
        return True, "No infrastructure change."

    concerns = []
    for rc in changes:
        rtype = rc.get("type", "")
        actions = rc.get("change", {}).get("actions", [])
        address = rc.get("address", "?")

        if "delete" in actions:
            # Independently caught by classify_plan_safety; still flagged
            # here in case this function is ever consulted on its own.
            concerns.append(f"{address}: destroy/replace ({'+'.join(actions)})")
            continue

        if rtype in _COST_SAFE_TYPES:
            continue

        if rtype == _REASONING_ENGINE_TYPE:
            if actions == ["update"]:
                continue  # no capacity/machine-size concept on this resource type
            concerns.append(f"{address}: {rtype} {'+'.join(actions)}")
            continue

        if rtype == _CLOUD_RUN_TYPE:
            change = rc.get("change", {})
            before_min = _extract_min_instance_count(change.get("before"))
            after_min = _extract_min_instance_count(change.get("after"))
            if (
                actions == ["update"]
                and before_min is not None
                and after_min is not None
                and after_min <= before_min
            ):
                continue
            concerns.append(
                f"{address}: {rtype} {'+'.join(actions)} "
                f"(min_instance_count {before_min} -> {after_min})"
            )
            continue

        # Unrecognized type -- conservative default: unknown cost impact.
        concerns.append(f"{address}: {rtype} {'+'.join(actions)} (unrecognized type)")

    if concerns:
        return False, "COST SAFETY STOP: cost impact unknown or potentially > $20: " + "; ".join(
            concerns
        )
    return True, "Only known low/no-cost changes in plan."


# ---------------------------------------------------------------------------
# Approval marker. Exact format (see the ChatGPT hourly reviewer side):
#     CHATGPT_AUTOSHIP_APPROVED
#     SHA: <40-char hex>
# Deliberately strict, no fuzzy "contains approved" matching.
# ---------------------------------------------------------------------------

_MARKER_RE = re.compile(r"CHATGPT_AUTOSHIP_APPROVED\s*\r?\n\s*SHA:\s*([0-9a-fA-F]{40})\b")


def extract_approval_sha(comment_body: str) -> str | None:
    match = _MARKER_RE.search(comment_body or "")
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Required-check evaluation against real GitHub check-runs for one SHA.
# ---------------------------------------------------------------------------


def _run_sort_key(run: dict) -> str:
    return run.get("completed_at") or run.get("started_at") or ""


def _latest_run_per_name(check_runs: list[dict]) -> dict:
    latest: dict[str, dict] = {}
    for run in check_runs:
        name = run.get("name")
        if name is None:
            continue
        existing = latest.get(name)
        if existing is None or _run_sort_key(run) >= _run_sort_key(existing):
            latest[name] = run
    return latest


def evaluate_required_checks(required: list[str], check_runs: list[dict]) -> tuple[bool, str]:
    latest = _latest_run_per_name(check_runs)
    problems = []
    for name in required:
        run = latest.get(name)
        if run is None:
            problems.append(f"{name}: missing")
            continue
        if run.get("status") != "completed":
            problems.append(f"{name}: {run.get('status')}")
            continue
        conclusion = run.get("conclusion")
        if conclusion != "success":
            problems.append(f"{name}: {conclusion}")
    if problems:
        return False, "CI GATE STOP: required check(s) not green: " + "; ".join(problems)
    return True, "All required checks green."


# ---------------------------------------------------------------------------
# Central evaluator -- combines every gate into one verdict. This is the
# single function the workflow actually calls; everything above exists to
# feed it, and is independently unit-tested on its own too.
# ---------------------------------------------------------------------------


def evaluate_merge_gate(
    *,
    comment_body: str,
    pr_state: dict,
    repo_root: Path,
    changed_files: list[str],
    check_runs: list[dict],
    unresolved_thread_count: int,
    terraform_plan: dict | None,
) -> dict:
    """pr_state: {"number", "headRefOid", "state", "isDraft", "baseRefName",
    "headRefName"} from `gh pr view --json ...`.

    Returns {"verdict": "not_a_marker"|"stop"|"go", "reason": str,
    "pr_number", "approved_sha", "head_branch"} -- pr_number/approved_sha/
    head_branch are only meaningful when verdict == "go".
    """
    marker_sha = extract_approval_sha(comment_body)
    if marker_sha is None:
        return {"verdict": "not_a_marker", "reason": "No valid CHATGPT_AUTOSHIP_APPROVED marker."}

    pr_number = pr_state.get("number")
    current_sha = pr_state.get("headRefOid")
    head_branch = pr_state.get("headRefName")

    def stop(reason: str) -> dict:
        return {"verdict": "stop", "reason": reason, "pr_number": pr_number}

    if pr_state.get("state") != "OPEN":
        return stop(f"PR is not open (state={pr_state.get('state')}).")
    if pr_state.get("isDraft"):
        return stop("PR is a draft.")
    if pr_state.get("baseRefName") != "main":
        return stop(f"Base branch is not main (base={pr_state.get('baseRefName')}).")
    if marker_sha != current_sha:
        return stop(
            f"Stale approval: marker SHA {marker_sha} does not match current PR head "
            f"{current_sha}. A new commit invalidated this approval -- needs a fresh "
            f"ChatGPT review and a new marker on the current head."
        )

    if unresolved_thread_count > 0:
        return stop(
            f"REVIEW GATE STOP: {unresolved_thread_count} unresolved review thread(s) remain."
        )

    checks = compute_required_checks(repo_root, changed_files)
    checks_ok, checks_reason = evaluate_required_checks(checks["required_checks"], check_runs)
    if not checks_ok:
        return stop(checks_reason)

    if checks["terraform_apply_triggers"]:
        if terraform_plan is None:
            return stop(
                "This PR would trigger terraform-apply.yml on merge but no fresh "
                "safety plan was supplied -- refusing to merge without one."
            )
        for classify in (classify_plan_safety, classify_iam_safety, classify_cost_safety):
            ok, reason = classify(terraform_plan)
            if not ok:
                return stop(reason)

    return {
        "verdict": "go",
        "reason": "All gates passed.",
        "pr_number": pr_number,
        "approved_sha": marker_sha,
        "head_branch": head_branch,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_verdict(ok: bool, reason: str) -> int:
    print(json.dumps({"ok": ok, "reason": reason}))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_required = sub.add_parser("required-checks")
    p_required.add_argument("repo_root", type=Path)
    p_required.add_argument("--file", dest="files", action="append", default=[])

    p_plan = sub.add_parser("plan-safety")
    p_plan.add_argument("plan_json", type=Path)

    p_iam = sub.add_parser("iam-safety")
    p_iam.add_argument("plan_json", type=Path)

    p_cost = sub.add_parser("cost-safety")
    p_cost.add_argument("plan_json", type=Path)

    sub.add_parser("extract-marker")  # reads comment body from stdin

    p_eval = sub.add_parser("evaluate")
    p_eval.add_argument("--comment-body-file", type=Path, required=True)
    p_eval.add_argument("--pr-json-file", type=Path, required=True)
    p_eval.add_argument("--repo-root", type=Path, required=True)
    p_eval.add_argument("--changed-files-file", type=Path, required=True)
    p_eval.add_argument("--check-runs-json-file", type=Path, required=True)
    p_eval.add_argument("--unresolved-thread-count", type=int, required=True)
    p_eval.add_argument("--terraform-plan-json-file", type=Path, default=None)

    args = parser.parse_args(argv)

    if args.command == "required-checks":
        result = compute_required_checks(args.repo_root, args.files)
        print(json.dumps(result))
        return 0

    if args.command == "extract-marker":
        body = sys.stdin.read()
        sha = extract_approval_sha(body)
        if sha is None:
            print("", end="")
            return 1
        print(sha, end="")
        return 0

    if args.command == "evaluate":
        comment_body = args.comment_body_file.read_text()
        pr_state = json.loads(args.pr_json_file.read_text())
        changed_files = [
            line.strip()
            for line in args.changed_files_file.read_text().splitlines()
            if line.strip()
        ]
        check_runs = json.loads(args.check_runs_json_file.read_text())
        terraform_plan = (
            json.loads(args.terraform_plan_json_file.read_text())
            if args.terraform_plan_json_file
            else None
        )
        result = evaluate_merge_gate(
            comment_body=comment_body,
            pr_state=pr_state,
            repo_root=args.repo_root,
            changed_files=changed_files,
            check_runs=check_runs,
            unresolved_thread_count=args.unresolved_thread_count,
            terraform_plan=terraform_plan,
        )
        print(json.dumps(result))
        return 0

    plan = json.loads(args.plan_json.read_text())
    if args.command == "plan-safety":
        return _print_verdict(*classify_plan_safety(plan))
    if args.command == "iam-safety":
        return _print_verdict(*classify_iam_safety(plan))
    if args.command == "cost-safety":
        return _print_verdict(*classify_cost_safety(plan))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
