"""Unit tests for scripts/merge_gate_checks.py.

Fixture-driven, no live GCP/Terraform calls -- these are exactly the
"deterministic fixture/JSON test" cases required before Phase 2 is allowed
near a real Terraform plan.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from merge_gate_checks import (  # noqa: E402
    classify_cost_safety,
    classify_iam_safety,
    classify_plan_safety,
    compute_required_checks,
    evaluate_merge_gate,
    evaluate_required_checks,
    extract_approval_sha,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

VALID_MARKER = "CHATGPT_AUTOSHIP_APPROVED\nSHA: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _pr_state(**overrides):
    base = {
        "number": 999,
        "headRefOid": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "state": "OPEN",
        "isDraft": False,
        "baseRefName": "main",
        "headRefName": "some-branch",
    }
    base.update(overrides)
    return base


def _check_run(name, status="completed", conclusion="success", completed_at="2026-01-01T00:00:00Z"):
    return {"name": name, "status": status, "conclusion": conclusion, "completed_at": completed_at}


# ---------------------------------------------------------------------------
# marker extraction -- strict, no fuzzy "contains approved" matching
# ---------------------------------------------------------------------------


def test_extract_marker_finds_valid_marker():
    sha = extract_approval_sha(VALID_MARKER)
    assert sha == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def test_extract_marker_ignores_fuzzy_approved_text():
    assert extract_approval_sha("Looks good, approved!") is None


def test_extract_marker_ignores_marker_word_without_sha_line():
    assert extract_approval_sha("CHATGPT_AUTOSHIP_APPROVED but no SHA line") is None


def test_extract_marker_rejects_short_sha():
    assert extract_approval_sha("CHATGPT_AUTOSHIP_APPROVED\nSHA: abc123") is None


def test_extract_marker_rejects_non_hex_sha():
    body = "CHATGPT_AUTOSHIP_APPROVED\nSHA: " + "g" * 40
    assert extract_approval_sha(body) is None


def test_extract_marker_tolerates_extra_surrounding_text():
    body = f"Reviewed and clean.\n\n{VALID_MARKER}\n\nNo further action needed."
    assert extract_approval_sha(body) == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


# ---------------------------------------------------------------------------
# required-check evaluation against real check-run shapes
# ---------------------------------------------------------------------------


def test_evaluate_required_checks_all_green():
    ok, _ = evaluate_required_checks(["pytest", "plan"], [_check_run("pytest"), _check_run("plan")])
    assert ok is True


def test_evaluate_required_checks_missing_check_blocks():
    ok, reason = evaluate_required_checks(["pytest", "plan"], [_check_run("pytest")])
    assert ok is False
    assert "plan: missing" in reason


def test_evaluate_required_checks_pending_blocks():
    ok, reason = evaluate_required_checks(
        ["pytest"], [_check_run("pytest", status="in_progress", conclusion=None)]
    )
    assert ok is False
    assert "in_progress" in reason


def test_evaluate_required_checks_failure_blocks():
    ok, reason = evaluate_required_checks(["pytest"], [_check_run("pytest", conclusion="failure")])
    assert ok is False
    assert "failure" in reason


def test_evaluate_required_checks_cancelled_blocks():
    ok, reason = evaluate_required_checks(
        ["pytest"], [_check_run("pytest", conclusion="cancelled")]
    )
    assert ok is False


def test_evaluate_required_checks_empty_required_set_passes_trivially():
    ok, _ = evaluate_required_checks([], [])
    assert ok is True


def test_evaluate_required_checks_uses_latest_run_when_rerun():
    runs = [
        _check_run("pytest", conclusion="failure", completed_at="2026-01-01T00:00:00Z"),
        _check_run("pytest", conclusion="success", completed_at="2026-01-02T00:00:00Z"),
    ]
    ok, _ = evaluate_required_checks(["pytest"], runs)
    assert ok is True


# ---------------------------------------------------------------------------
# central evaluator -- the function the workflow actually calls
# ---------------------------------------------------------------------------


def test_evaluate_merge_gate_not_a_marker_for_malformed_comment():
    result = evaluate_merge_gate(
        comment_body="lgtm, approved",
        pr_state=_pr_state(),
        repo_root=REPO_ROOT,
        changed_files=["docs/x.md"],
        check_runs=[],
        unresolved_thread_count=0,
        terraform_plan=None,
    )
    assert result["verdict"] == "not_a_marker"


def test_evaluate_merge_gate_stops_on_stale_sha():
    result = evaluate_merge_gate(
        comment_body="CHATGPT_AUTOSHIP_APPROVED\nSHA: " + "b" * 40,
        pr_state=_pr_state(headRefOid="a" * 40),
        repo_root=REPO_ROOT,
        changed_files=["docs/x.md"],
        check_runs=[],
        unresolved_thread_count=0,
        terraform_plan=None,
    )
    assert result["verdict"] == "stop"
    assert "Stale approval" in result["reason"]


def test_evaluate_merge_gate_stops_on_draft_pr():
    result = evaluate_merge_gate(
        comment_body=VALID_MARKER,
        pr_state=_pr_state(isDraft=True),
        repo_root=REPO_ROOT,
        changed_files=["docs/x.md"],
        check_runs=[],
        unresolved_thread_count=0,
        terraform_plan=None,
    )
    assert result["verdict"] == "stop"
    assert "draft" in result["reason"]


def test_evaluate_merge_gate_stops_on_wrong_base_branch():
    result = evaluate_merge_gate(
        comment_body=VALID_MARKER,
        pr_state=_pr_state(baseRefName="develop"),
        repo_root=REPO_ROOT,
        changed_files=["docs/x.md"],
        check_runs=[],
        unresolved_thread_count=0,
        terraform_plan=None,
    )
    assert result["verdict"] == "stop"
    assert "base" in result["reason"].lower()


def test_evaluate_merge_gate_stops_on_unresolved_threads():
    result = evaluate_merge_gate(
        comment_body=VALID_MARKER,
        pr_state=_pr_state(),
        repo_root=REPO_ROOT,
        changed_files=["docs/x.md"],
        check_runs=[],
        unresolved_thread_count=2,
        terraform_plan=None,
    )
    assert result["verdict"] == "stop"
    assert "unresolved review thread" in result["reason"]


def test_evaluate_merge_gate_stops_on_missing_required_check():
    result = evaluate_merge_gate(
        comment_body=VALID_MARKER,
        pr_state=_pr_state(),
        repo_root=REPO_ROOT,
        changed_files=["agent/main.py"],
        check_runs=[_check_run("pytest")],  # mcp-pytest and plan missing
        unresolved_thread_count=0,
        terraform_plan=None,
    )
    assert result["verdict"] == "stop"
    assert "CI GATE STOP" in result["reason"]


def test_evaluate_merge_gate_does_not_deadlock_on_no_expected_checks():
    # A path with zero required checks must still be mergeable.
    result = evaluate_merge_gate(
        comment_body=VALID_MARKER,
        pr_state=_pr_state(),
        repo_root=REPO_ROOT,
        changed_files=["docs/testing/notes.md"],
        check_runs=[],
        unresolved_thread_count=0,
        terraform_plan=None,
    )
    assert result["verdict"] == "go"
    assert result["approved_sha"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def test_evaluate_merge_gate_stops_when_apply_triggers_but_no_plan_supplied():
    result = evaluate_merge_gate(
        comment_body=VALID_MARKER,
        pr_state=_pr_state(),
        repo_root=REPO_ROOT,
        changed_files=["mcp/server.py"],
        check_runs=[_check_run("pytest"), _check_run("mcp-pytest")],
        unresolved_thread_count=0,
        terraform_plan=None,
    )
    assert result["verdict"] == "stop"
    assert "no fresh" in result["reason"]


def test_evaluate_merge_gate_stops_on_destroy_in_fresh_plan():
    plan = _plan(_resource_change("google_storage_bucket.old", "google_storage_bucket", ["delete"]))
    result = evaluate_merge_gate(
        comment_body=VALID_MARKER,
        pr_state=_pr_state(),
        repo_root=REPO_ROOT,
        changed_files=["iac/agent/agent_engine.tf"],
        check_runs=[_check_run("plan")],
        unresolved_thread_count=0,
        terraform_plan=plan,
    )
    assert result["verdict"] == "stop"
    assert "PLAN SAFETY STOP" in result["reason"]


def test_evaluate_merge_gate_goes_on_safe_infra_change():
    plan = _plan(
        _resource_change(
            "google_vertex_ai_reasoning_engine.sre_agent",
            "google_vertex_ai_reasoning_engine",
            ["update"],
        )
    )
    result = evaluate_merge_gate(
        comment_body=VALID_MARKER,
        pr_state=_pr_state(),
        repo_root=REPO_ROOT,
        changed_files=["agent/main.py"],
        check_runs=[_check_run("pytest"), _check_run("mcp-pytest"), _check_run("plan")],
        unresolved_thread_count=0,
        terraform_plan=plan,
    )
    assert result["verdict"] == "go"
    assert result["pr_number"] == 999
    assert result["head_branch"] == "some-branch"


def _resource_change(address, rtype, actions, before=None, after=None):
    return {
        "address": address,
        "type": rtype,
        "change": {"actions": actions, "before": before, "after": after},
    }


def _plan(*resource_changes):
    return {"resource_changes": list(resource_changes)}


# ---------------------------------------------------------------------------
# required-checks path awareness (uses this repo's REAL current workflow
# files, not a mock -- catches real drift between this script and the
# workflows it's supposed to track)
# ---------------------------------------------------------------------------


def test_agent_change_requires_pytest_and_mcp_pytest_and_plan_and_triggers_apply():
    # agent/** matches BOTH python-tests.yml and terraform-plan.yml's path
    # filters, unlike mcp/** below -- all three checks are legitimately
    # expected here.
    result = compute_required_checks(REPO_ROOT, ["agent/main.py"])
    assert result["required_checks"] == ["mcp-pytest", "plan", "pytest"]
    assert result["terraform_apply_triggers"] is True


def test_iac_agent_change_requires_plan_only():
    result = compute_required_checks(REPO_ROOT, ["iac/agent/agent_engine.tf"])
    assert result["required_checks"] == ["plan"]
    assert result["terraform_apply_triggers"] is True


def test_mcp_change_requires_python_checks_but_not_plan_yet_triggers_apply():
    # The real asymmetry this whole gate exists for: mcp/** triggers
    # terraform-apply.yml after merge but terraform-plan.yml never runs for
    # it, so it must get a fresh safety plan from the merge gate itself.
    result = compute_required_checks(REPO_ROOT, ["mcp/server.py"])
    assert result["required_checks"] == ["mcp-pytest", "pytest"]
    assert result["terraform_apply_triggers"] is True


def test_docs_only_change_requires_nothing_and_does_not_trigger_apply():
    result = compute_required_checks(REPO_ROOT, ["docs/testing/notes.md"])
    assert result["required_checks"] == []
    assert result["terraform_apply_triggers"] is False


def test_root_python_file_requires_python_checks():
    result = compute_required_checks(REPO_ROOT, ["conftest.py"])
    assert result["required_checks"] == ["mcp-pytest", "pytest"]


# ---------------------------------------------------------------------------
# plan safety: destroy / replacement
# ---------------------------------------------------------------------------


def test_plan_safety_passes_clean_update():
    plan = _plan(
        _resource_change(
            "google_vertex_ai_reasoning_engine.sre_agent",
            "google_vertex_ai_reasoning_engine",
            ["update"],
        )
    )
    ok, reason = classify_plan_safety(plan)
    assert ok is True


def test_plan_safety_passes_no_op_only():
    plan = _plan(
        _resource_change("google_project_service.apis", "google_project_service", ["no-op"])
    )
    ok, reason = classify_plan_safety(plan)
    assert ok is True


def test_plan_safety_blocks_pure_delete():
    plan = _plan(_resource_change("google_storage_bucket.old", "google_storage_bucket", ["delete"]))
    ok, reason = classify_plan_safety(plan)
    assert ok is False
    assert "destroy/replacement" in reason


def test_plan_safety_blocks_replace_delete_then_create():
    plan = _plan(
        _resource_change(
            "google_vertex_ai_reasoning_engine.sre_agent",
            "google_vertex_ai_reasoning_engine",
            ["delete", "create"],
        )
    )
    ok, reason = classify_plan_safety(plan)
    assert ok is False


def test_plan_safety_blocks_replace_create_then_delete():
    plan = _plan(
        _resource_change(
            "google_vertex_ai_reasoning_engine.sre_agent",
            "google_vertex_ai_reasoning_engine",
            ["create", "delete"],
        )
    )
    ok, reason = classify_plan_safety(plan)
    assert ok is False


# ---------------------------------------------------------------------------
# IAM / security safety
# ---------------------------------------------------------------------------


def test_iam_safety_passes_when_no_iam_resource_touched():
    plan = _plan(
        _resource_change("google_logging_metric.tokens", "google_logging_metric", ["create"])
    )
    ok, reason = classify_iam_safety(plan)
    assert ok is True


def test_iam_safety_blocks_project_iam_member_create():
    plan = _plan(
        _resource_change(
            "google_project_iam_member.new_grant", "google_project_iam_member", ["create"]
        )
    )
    ok, reason = classify_iam_safety(plan)
    assert ok is False
    assert "IAM SAFETY STOP" in reason


def test_iam_safety_blocks_service_account_iam_member_update():
    plan = _plan(
        _resource_change(
            "google_service_account_iam_member.grant",
            "google_service_account_iam_member",
            ["update"],
        )
    )
    ok, _ = classify_iam_safety(plan)
    assert ok is False


def test_iam_safety_blocks_iam_delete_independently_of_plan_safety_gate():
    # Explicit requirement: do not assume the general delete gate already
    # covers this -- the IAM gate must flag an IAM delete on its own.
    plan = _plan(
        _resource_change(
            "google_project_iam_member.old_grant", "google_project_iam_member", ["delete"]
        )
    )
    plan_ok, _ = classify_plan_safety(plan)
    iam_ok, iam_reason = classify_iam_safety(plan)
    assert plan_ok is False  # the general gate does catch it too...
    assert iam_ok is False  # ...but the IAM gate must catch it independently
    assert "IAM SAFETY STOP" in iam_reason


def test_iam_safety_matches_broad_iam_types_not_just_one_spelling():
    plan = _plan(
        _resource_change(
            "google_iap_agent_registry_iam_member.gw",
            "google_iap_agent_registry_iam_member",
            ["create"],
        )
    )
    ok, _ = classify_iam_safety(plan)
    assert ok is False


# ---------------------------------------------------------------------------
# cost safety
# ---------------------------------------------------------------------------


def test_cost_safety_passes_no_changes():
    ok, reason = classify_cost_safety(_plan())
    assert ok is True
    assert "No infrastructure change" in reason


def test_cost_safety_passes_known_zero_cost_type():
    plan = _plan(
        _resource_change(
            "google_monitoring_alert_policy.new", "google_monitoring_alert_policy", ["create"]
        )
    )
    ok, _ = classify_cost_safety(plan)
    assert ok is True


def test_cost_safety_passes_reasoning_engine_in_place_update():
    plan = _plan(
        _resource_change(
            "google_vertex_ai_reasoning_engine.sre_agent",
            "google_vertex_ai_reasoning_engine",
            ["update"],
        )
    )
    ok, _ = classify_cost_safety(plan)
    assert ok is True


def test_cost_safety_blocks_reasoning_engine_replace():
    plan = _plan(
        _resource_change(
            "google_vertex_ai_reasoning_engine.sre_agent",
            "google_vertex_ai_reasoning_engine",
            ["delete", "create"],
        )
    )
    ok, reason = classify_cost_safety(plan)
    assert ok is False
    assert "COST SAFETY STOP" in reason


def test_cost_safety_passes_cloud_run_update_with_unchanged_min_instances():
    before = {"template": [{"scaling": [{"min_instance_count": 0, "max_instance_count": 3}]}]}
    after = {"template": [{"scaling": [{"min_instance_count": 0, "max_instance_count": 3}]}]}
    plan = _plan(
        _resource_change(
            "google_cloud_run_v2_service.mcp[0]",
            "google_cloud_run_v2_service",
            ["update"],
            before=before,
            after=after,
        )
    )
    ok, _ = classify_cost_safety(plan)
    assert ok is True


def test_cost_safety_blocks_cloud_run_min_instance_increase():
    before = {"template": [{"scaling": [{"min_instance_count": 0, "max_instance_count": 3}]}]}
    after = {"template": [{"scaling": [{"min_instance_count": 2, "max_instance_count": 3}]}]}
    plan = _plan(
        _resource_change(
            "google_cloud_run_v2_service.mcp[0]",
            "google_cloud_run_v2_service",
            ["update"],
            before=before,
            after=after,
        )
    )
    ok, reason = classify_cost_safety(plan)
    assert ok is False
    assert "min_instance_count 0 -> 2" in reason


def test_cost_safety_handles_single_object_nesting_not_only_list_nesting():
    # Per the official JSON format spec, a "single" nesting mode block is a
    # plain object, not a one-item list -- must not assume list-only.
    before = {"template": {"scaling": {"min_instance_count": 0}}}
    after = {"template": {"scaling": {"min_instance_count": 0}}}
    plan = _plan(
        _resource_change(
            "google_cloud_run_v2_service.mcp[0]",
            "google_cloud_run_v2_service",
            ["update"],
            before=before,
            after=after,
        )
    )
    ok, _ = classify_cost_safety(plan)
    assert ok is True


def test_cost_safety_blocks_unrecognized_new_resource_type():
    plan = _plan(
        _resource_change("google_compute_instance.gpu_box", "google_compute_instance", ["create"])
    )
    ok, reason = classify_cost_safety(plan)
    assert ok is False
    assert "unrecognized type" in reason


def test_cost_safety_blocks_new_cloud_run_service_creation():
    plan = _plan(
        _resource_change(
            "google_cloud_run_v2_service.mcp[0]",
            "google_cloud_run_v2_service",
            ["create"],
            before=None,
            after={"template": [{"scaling": [{"min_instance_count": 0}]}]},
        )
    )
    ok, reason = classify_cost_safety(plan)
    assert ok is False
