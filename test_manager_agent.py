"""LangGraph manager: Codex workers, no 3-attempt cap, diary on re-delegate."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from agents.pipeline_agent import manager
from agents.skills.service import SkillInstall


def _settings(**overrides):
    base = dict(
        default_branch="main",
        sandbox_kind="salesforce",
        project_name="DMS",
        project_id="proj-1",
        engine="codex",
        bot_git_name="pipeline-agent",
        bot_git_email="bot@example.com",
        google_model="gemini-2.5-flash",
        thinking_budget=0,
        max_output_tokens=4096,
        jira=SimpleNamespace(connection_name="jira"),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestManagerEnabled:
    def test_salesforce_with_google_key_is_on(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "x")
        monkeypatch.delenv("PIPELINE_AGENT_MANAGER", raising=False)
        assert manager.manager_enabled(_settings(sandbox_kind="salesforce")) is True

    def test_non_salesforce_is_off_unless_flagged(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "x")
        monkeypatch.delenv("PIPELINE_AGENT_MANAGER", raising=False)
        assert manager.manager_enabled(_settings(sandbox_kind="default")) is False

    def test_explicit_off_wins(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "x")
        monkeypatch.setenv("PIPELINE_AGENT_MANAGER", "0")
        assert manager.manager_enabled(_settings(sandbox_kind="salesforce")) is False


class TestWorkerBrief:
    def test_skill_prefix_is_always_present(self, monkeypatch):
        monkeypatch.setattr(
            manager,
            "list_project_installs",
            lambda *a, **k: [SkillInstall(slug="salesforce-developer", version="1", name="SF Dev")],
        )
        monkeypatch.setattr(manager, "get_skill", lambda _s: SimpleNamespace(description="Apex and LWC"))
        text = manager.worker_instruction_for_tests("p1", "PGD-1", _settings(), "Build Product__c")
        assert "salesforce-developer" in text
        assert "open any installed skill" in text.lower() or "Open any installed skill" in text
        assert "Build Product__c" in text
        assert "dry-run" in text

    def test_brief_says_worker_is_already_on_the_branch_and_must_not_reset_it(self, monkeypatch):
        monkeypatch.setattr(manager, "list_project_installs", lambda *a, **k: [])
        monkeypatch.setattr(manager, "get_skill", lambda _s: None)
        text = manager.worker_instruction_for_tests("p1", "PGD-48", _settings(), "Build UOM")
        assert "feature/PGD-48" in text
        assert "already created and checked out" in text
        assert "git stash" in text and "git reset --hard" in text
        assert "push -u origin HEAD" in text
        assert "git switch -c feature/PGD-48-<slug>" not in text

    def test_second_brief_contains_the_diary(self, monkeypatch):
        monkeypatch.setattr(manager, "list_project_installs", lambda *a, **k: [])
        monkeypatch.setattr(manager, "get_skill", lambda _s: None)
        text = manager.worker_instruction_for_tests(
            "p1",
            "PGD-1",
            _settings(),
            "Fix the deploy",
            diary=["Validate: FAILED\ndry-run: You cannot deploy to a required field"],
        )
        assert "not starting cold" in text
        assert "required field" in text
        assert "Fix the deploy" in text


class TestSandboxGitAuth:
    def test_clone_url_uses_x_access_token_not_token_as_username(self):
        from sandbox.manager import authenticated_clone_url

        url = authenticated_clone_url("https://github.com/acme/widgets", "tok123")
        assert url == "https://x-access-token:tok123@github.com/acme/widgets"

    def test_configure_sandbox_git_sets_bot_identity_and_token_helper(self):
        from agents.pipeline_agent.sandbox_runtime import (
            _GIT_CREDENTIAL_HELPER,
            configure_sandbox_git,
        )

        commands: list[str] = []

        def execute(command: str):
            commands.append(command)
            return {"success": True, "stdout": "", "stderr": ""}

        sandbox = SimpleNamespace(execute=execute)
        settings = SimpleNamespace(
            bot_git_name="pipeline-agent",
            bot_git_email="pipeline-agent@users.noreply.github.com",
            repository_url="https://github.com/acme/widgets",
        )
        configure_sandbox_git(sandbox, settings)

        assert "x-access-token" in _GIT_CREDENTIAL_HELPER
        assert "GH_TOKEN" in _GIT_CREDENTIAL_HELPER
        joined = "\n".join(commands)
        assert "git config user.name" in joined
        assert "pipeline-agent" in joined
        assert "pipeline-agent@users.noreply.github.com" in joined
        assert "credential.helper" in joined
        assert "git remote set-url origin" in joined
        assert "github.com/acme/widgets" in joined
        assert "git ls-remote --exit-code origin HEAD" in joined
        assert "tok123" not in joined
        assert "x-access-token:" not in joined

    def test_configure_sandbox_git_fails_fast_when_remote_auth_fails(self):
        from agents.pipeline_agent.sandbox_runtime import configure_sandbox_git

        def execute(command: str):
            if command == "git ls-remote --exit-code origin HEAD":
                return {"success": False, "stdout": "", "stderr": "Authentication failed"}
            return {"success": True, "stdout": "", "stderr": ""}

        settings = SimpleNamespace(
            bot_git_name="pipeline-agent",
            bot_git_email="pipeline-agent@users.noreply.github.com",
            repository_url="https://github.com/acme/widgets",
        )
        with pytest.raises(RuntimeError, match="GitHub authentication check failed"):
            configure_sandbox_git(SimpleNamespace(execute=execute), settings)


class TestDockerUnreachable:
    def test_server_api_version_error_is_rewritten(self):
        from sandbox.manager import _docker_unreachable_message

        raw = (
            "Error while fetching server API version: "
            "('Connection aborted.', FileNotFoundError(2, 'No such file or directory'))"
        )
        hint = _docker_unreachable_message(RuntimeError(raw))
        assert hint is not None
        assert "Start Docker Desktop" in hint
        assert "server API version" in hint

    def test_unrelated_errors_are_left_alone(self):
        from sandbox.manager import _docker_unreachable_message

        assert _docker_unreachable_message(RuntimeError("sf org login failed")) is None


class TestStaleFeatureBranchCleanup:
    def _sandbox(self, ls_remote_stdout: str):
        calls: list[str] = []

        def execute(command: str, *a, **k):
            calls.append(command)
            if "ls-remote" in command:
                return {"success": True, "stdout": ls_remote_stdout, "stderr": ""}
            return {"success": True, "stdout": "", "stderr": ""}

        return SimpleNamespace(execute=execute), calls

    def test_deletes_only_this_tickets_stale_branches(self):
        from agents.pipeline_agent.sandbox_runtime import _delete_stale_feature_branches

        stdout = (
            "aaaa\trefs/heads/main\n"
            "bbbb\trefs/heads/feature/PGD-48-order-quantity-uom\n"
            "cccc\trefs/heads/feature/PGD-48-calc\n"
            "dddd\trefs/heads/feature/PGD-9-other\n"
        )
        sandbox, calls = self._sandbox(stdout)
        _delete_stale_feature_branches(sandbox, "PGD-48", None)
        deletes = [c for c in calls if "push origin --delete" in c]
        assert any("feature/PGD-48-order-quantity-uom" in c for c in deletes)
        assert any("feature/PGD-48-calc" in c for c in deletes)
        assert not any("PGD-9" in c for c in deletes)
        assert not any("main" in c.split("--delete")[-1] for c in deletes)

    def test_no_matching_branches_is_a_noop(self):
        from agents.pipeline_agent.sandbox_runtime import _delete_stale_feature_branches

        sandbox, calls = self._sandbox("aaaa\trefs/heads/main\n")
        _delete_stale_feature_branches(sandbox, "PGD-48", None)
        assert not any("--delete" in c for c in calls)

    def test_ls_remote_failure_is_swallowed(self):
        from agents.pipeline_agent.sandbox_runtime import _delete_stale_feature_branches

        def execute(command: str, *a, **k):
            return {"success": False, "stdout": "", "stderr": "network down"}

        _delete_stale_feature_branches(SimpleNamespace(execute=execute), "PGD-48", None)


class TestNpmInstallProject:
    def test_symlinks_the_baked_toolchain_when_link_mode(self, monkeypatch):
        monkeypatch.setenv("PIPELINE_AGENT_NPM_MODE", "link")
        from agents.pipeline_agent.sandbox_runtime import _npm_install_project

        calls: list[str] = []

        def execute(command: str, *a, **k):
            calls.append(command)
            if command == "test -f package.json":
                return {"success": True, "stdout": "", "stderr": ""}
            if "ln -s" in command:  # baked toolchain present
                return {"success": True, "stdout": "linked", "stderr": ""}
            return {"success": True, "stdout": "", "stderr": ""}

        _npm_install_project(SimpleNamespace(execute=execute), None)
        assert any("ln -s" in c and "/opt/sf-toolchain/node_modules" in c for c in calls)
        assert not any("npm ci" in c for c in calls)

    def test_default_mode_is_npm_ci_not_symlink(self, monkeypatch):
        monkeypatch.delenv("PIPELINE_AGENT_NPM_MODE", raising=False)
        from agents.pipeline_agent.sandbox_runtime import _npm_install_project

        calls: list[str] = []

        def execute(command: str, *a, **k):
            calls.append(command)
            if command == "test -f package.json":
                return {"success": True, "stdout": "", "stderr": ""}
            return {"success": True, "stdout": "added 900 packages", "stderr": ""}

        _npm_install_project(SimpleNamespace(execute=execute), None)
        assert not any("ln -s" in c for c in calls)
        assert any("npm ci" in c for c in calls)

    def test_falls_back_to_npm_ci_when_no_baked_toolchain(self):
        from agents.pipeline_agent.sandbox_runtime import _npm_install_project

        calls: list[str] = []

        def execute(command: str, *a, **k):
            calls.append(command)
            if command == "test -f package.json":
                return {"success": True, "stdout": "", "stderr": ""}
            if "ln -s" in command:  # baked toolchain absent -> command fails
                return {"success": False, "stdout": "", "stderr": ""}
            return {"success": True, "stdout": "added 900 packages", "stderr": ""}

        _npm_install_project(SimpleNamespace(execute=execute), None)
        assert any("npm ci" in c and "npm install" in c for c in calls)

    def test_noop_when_no_package_json(self):
        from agents.pipeline_agent.sandbox_runtime import _npm_install_project

        calls: list[str] = []

        def execute(command: str, *a, **k):
            calls.append(command)
            return {"success": False, "stdout": "", "stderr": ""}

        _npm_install_project(SimpleNamespace(execute=execute), None)
        assert calls == ["test -f package.json"]


class TestExcludePipelineFiles:
    def test_writes_exclude_file_and_skip_worktrees_tracked_scaffolding(self):
        from agents.pipeline_agent.sandbox_runtime import _exclude_pipeline_files

        calls: list[str] = []
        written: dict[str, str] = {}

        def execute(command: str, *a, **k):
            calls.append(command)
            if command.startswith("git ls-files"):
                # TICKET.md is already tracked (merged in from an earlier PR)
                return {"success": True, "stdout": "TICKET.md\0", "stderr": ""}
            return {"success": True, "stdout": "", "stderr": ""}

        sandbox = SimpleNamespace(
            execute=execute,
            read_file=lambda p: {"success": True, "content": "# git ls-files --others\n"},
            write_file=lambda p, c: written.__setitem__(p, c) or {"success": True},
        )
        _exclude_pipeline_files(sandbox, None)

        assert ".git/info/exclude" in written
        body = written[".git/info/exclude"]
        assert "package-lock.json" in body and ".pipeline/" in body and "node_modules/" in body
        joined = "\n".join(calls)
        assert "git update-index --skip-worktree -- TICKET.md" in joined
        assert "git rm" not in joined  # a staged deletion would leak into the branch diff

    def test_exclude_file_not_rewritten_if_already_present(self):
        from agents.pipeline_agent.sandbox_runtime import _exclude_pipeline_files

        writes: list = []
        sandbox = SimpleNamespace(
            execute=lambda c, *a, **k: {"success": True, "stdout": "", "stderr": ""},
            read_file=lambda p: {"success": True, "content": "# pipeline scaffolding (added by prepare_sandbox_repo)\nTICKET.md\n"},
            write_file=lambda p, c: writes.append(p) or {"success": True},
        )
        _exclude_pipeline_files(sandbox, None)
        assert writes == []


class TestClassifyTicket:
    def test_types_and_budgets(self):
        from agents.pipeline_agent.classify import classify_ticket, budget_for

        cases = {
            "Add a custom picklist field Lead_Priority__c on Lead with High/Normal": "metadata-single",
            "Add several fields, a validation rule and a permission set to Order__c": "metadata",
            "Implement OpportunityRiskScoringEngine.cls with @IsTest coverage": "apex",
            "Build the leadPriorityConsole Lightning Web Component with wire adapters": "lwc",
            "Create Flow 'Lead - Set Priority from Lead Source' (record-triggered)": "flow",
            "An Apex service plus an LWC that calls it": "mixed",
        }
        for text, expected in cases.items():
            assert classify_ticket(text) == expected, text
        # negated mentions must not pull a metadata ticket to `mixed` (run df1198c7)
        assert classify_ticket(
            "Create a High Priority Leads list view without changing the existing "
            "Lead field, Flow, sharing, or code."
        ) in ("metadata", "metadata-single")
        assert budget_for("metadata-single")[0] < budget_for("apex")[0]
        assert budget_for("metadata-single")[1] is True  # light review
        assert budget_for("apex")[1] is False

    def test_session_picks_up_the_budget(self):
        from agents.pipeline_agent import manager

        s = manager._Session(
            _settings(), SimpleNamespace(), "AG-1", "Add a custom field to Lead", "p", None, None
        )
        assert s.ticket_type == "metadata-single"
        assert s.max_turns == 8 and s.light_review is True


class TestFoldSkillsIntoAgentsMd:
    def test_concatenates_bodies_and_bans_cat(self):
        from agents.pipeline_agent.sandbox_runtime import _fold_skills_into_agents_md

        out = _fold_skills_into_agents_md(
            "# Identity\n\nYou are Nova.",
            [("salesforce-developer", "# SF dev\n\nUse sf."), ("project-memory", "# Memory\n\nlayout")],
        )
        assert "You are Nova." in out
        assert "## Skill: salesforce-developer" in out and "Use sf." in out
        assert "## Skill: project-memory" in out and "layout" in out
        assert "Do NOT `cat`" in out and "~/.codex/skills/" in out


class TestCheckoutTicketBranch:
    def test_checks_out_feature_key_off_origin_default(self):
        from agents.pipeline_agent.sandbox_runtime import _checkout_ticket_branch

        calls: list[str] = []

        def execute(command: str, *a, **k):
            calls.append(command)
            return {"success": True, "stdout": "", "stderr": ""}

        settings = SimpleNamespace(default_branch="main")
        _checkout_ticket_branch(SimpleNamespace(execute=execute), settings, "PGD-12", None)
        assert any("git switch -C feature/PGD-12 origin/main" in c for c in calls)
        assert not any("<slug>" in c for c in calls)

    def test_falls_back_to_local_create_when_origin_ref_missing(self):
        from agents.pipeline_agent.sandbox_runtime import _checkout_ticket_branch

        calls: list[str] = []

        def execute(command: str, *a, **k):
            calls.append(command)
            ok = "origin/main" not in command  # first (origin-based) form fails
            return {"success": ok, "stdout": "", "stderr": "no origin ref"}

        _checkout_ticket_branch(
            SimpleNamespace(execute=execute), SimpleNamespace(default_branch="main"), "PGD-12", None
        )
        assert calls[-1] == "git switch -C feature/PGD-12"

    def test_raises_when_both_forms_fail(self):
        import pytest

        from agents.pipeline_agent.sandbox_runtime import _checkout_ticket_branch

        def execute(command: str, *a, **k):
            return {"success": False, "stdout": "", "stderr": "fatal"}

        with pytest.raises(RuntimeError):
            _checkout_ticket_branch(
                SimpleNamespace(execute=execute),
                SimpleNamespace(default_branch="main"),
                "PGD-12",
                None,
            )


def _sandbox_execute(
    ahead: int = 1,
    dirty: str = "",
    deploy_ok: bool = True,
    changed: list[str] | None = None,
):
    changed_files = changed if changed is not None else ["force-app/main/default/classes/Foo.cls"]

    def execute(cmd: str, *a, **k):
        if "rev-list --count" in cmd:
            return {"success": True, "stdout": str(ahead), "stderr": ""}
        if "status --porcelain" in cmd:
            return {"success": True, "stdout": dirty, "stderr": ""}
        if "diff --name-only" in cmd:
            return {"success": True, "stdout": "\n".join(changed_files), "stderr": ""}
        if "sf project deploy" in cmd:
            return {
                "success": deploy_ok,
                "stdout": "Succeeded" if deploy_ok else "Failed",
                "stderr": "",
            }
        return {"success": True, "stdout": "ok", "stderr": ""}

    return execute


class TestDispatchGuards:
    def _session(
        self,
        ahead: int = 1,
        dirty: str = "",
        deploy_ok: bool = True,
        changed: list[str] | None = None,
        codex_stdout: str = "done",
    ):
        sandbox = SimpleNamespace(
            current_branch=lambda: "feature/PGD-1-x",
            read_file=lambda p: {"success": True, "content": "ticket"},
            execute=_sandbox_execute(ahead=ahead, dirty=dirty, deploy_ok=deploy_ok, changed=changed),
            run_codex=lambda *a, **k: {"exit_code": 0, "stdout": codex_stdout, "stderr": ""},
            open_pull_request=lambda **k: {"success": True, "url": "https://github.com/o/r/pull/1"},
        )
        return manager._Session(_settings(), sandbox, "PGD-1", "plan", "proj-1", None, None)

    def test_no_three_call_cap_on_delegate(self):
        session = self._session()
        session.gate_passed = False
        for i in range(5):
            out = asyncio.run(
                session.dispatch("delegate_worker", {"brief": f"fix it {i}"}, {"diary": session.diary})
            )
            assert not out.startswith("REFUSED")

    def test_identical_brief_is_refused_with_the_gate_text(self):
        session = self._session()
        session.gate_passed = False
        session.last_validation = "FAILED: branch only changes TICKET.md"
        first = asyncio.run(session.dispatch("delegate_worker", {"brief": "build it"}, {"diary": []}))
        assert not first.startswith("REFUSED")
        again = asyncio.run(session.dispatch("delegate_worker", {"brief": "build it"}, {"diary": []}))
        assert again.startswith("REFUSED")
        assert "word-for-word identical" in again
        assert "TICKET.md" in again

    def test_a_changed_brief_after_the_first_is_allowed(self):
        session = self._session()
        session.gate_passed = False
        asyncio.run(session.dispatch("delegate_worker", {"brief": "build it"}, {"diary": []}))
        out = asyncio.run(
            session.dispatch("delegate_worker", {"brief": "build it, and add the UOM field"}, {"diary": []})
        )
        assert not out.startswith("REFUSED")

    def test_delegate_refused_after_validate_passed(self):
        session = self._session()
        session.gate_passed = True
        out = asyncio.run(session.dispatch("delegate_worker", {"brief": "polish"}, {"diary": []}))
        assert out.startswith("REFUSED")
        assert "already passed" in out

    def test_deliver_refused_while_validate_failed(self):
        session = self._session()
        session.gate_passed = False
        out = asyncio.run(session.dispatch("deliver", {}, {"diary": []}))
        assert out.startswith("REFUSED")
        assert "validate failed" in out

    def test_deliver_refused_before_validate(self):
        session = self._session()
        out = asyncio.run(session.dispatch("deliver", {}, {"diary": []}))
        assert out.startswith("REFUSED")

    def test_empty_report_refused(self):
        session = self._session()
        out = asyncio.run(session.dispatch("report_to_ticket", {"outcome": "blocked", "markdown": "  "}, {}))
        assert out.startswith("REFUSED")
        assert session.finished is False

    def test_delegate_refused_after_the_validate_retry_cap(self):
        session = self._session(deploy_ok=False, changed=["force-app/x.cls"])
        session.calls.append("delegate_worker")
        for _ in range(manager._MAX_VALIDATE_RETRIES):
            asyncio.run(session.dispatch("validate", {}, {"diary": []}))
        assert session.validate_failures >= manager._MAX_VALIDATE_RETRIES
        out = asyncio.run(session.dispatch("delegate_worker", {"brief": "try harder"}, {"diary": []}))
        assert out.startswith("REFUSED")
        assert "report_to_ticket" in out and "blocked" in out

    def test_validate_refused_before_first_delegate(self):
        session = self._session()
        out = asyncio.run(session.dispatch("validate", {}, {"diary": []}))
        assert out.startswith("REFUSED")
        assert "delegate_worker first" in out

    def test_blocked_refused_without_a_single_worker_attempt(self):
        """AG-217: manager went inspect_context -> report_to_ticket(blocked) in
        2 steps because a default value was unspecified."""
        session = self._session()
        out = asyncio.run(
            session.dispatch(
                "report_to_ticket",
                {"outcome": "blocked", "markdown": "The default value is ambiguous."},
                {},
            )
        )
        assert out.startswith("REFUSED")
        assert "without a single worker attempt" in out
        assert session.finished is False

    def test_delivered_refused_when_pr_open_but_not_shipped(self):
        session = self._session()
        session.calls.append("delegate_worker")
        session.gate_passed = True
        session.pr_url = "https://github.com/o/r/pull/1"
        out = asyncio.run(
            session.dispatch(
                "report_to_ticket", {"outcome": "delivered", "markdown": "Done."}, {}
            )
        )
        assert out.startswith("REFUSED")
        assert "ship" in out.lower()

    def test_ship_refused_before_deliver(self):
        session = self._session()
        session.gate_passed = True
        out = asyncio.run(session.dispatch("ship", {}, {"diary": []}))
        assert out.startswith("REFUSED")
        assert "deliver first" in out

    def test_ship_deploys_to_org_then_merges(self):
        calls: list[str] = []

        def execute(cmd, *a, **k):
            calls.append(cmd)
            return {"success": True, "stdout": "Deploy Succeeded | 12 components", "stderr": ""}

        sandbox = SimpleNamespace(
            current_branch=lambda: "feature/PGD-1",
            read_file=lambda p: {"success": True, "content": "t"},
            execute=execute,
        )
        session = manager._Session(_settings(), sandbox, "PGD-1", "plan", "proj-1", None, None)
        session.gate_passed = True
        session.pr_url = "https://github.com/o/r/pull/1"
        out = asyncio.run(session.dispatch("ship", {}, {"diary": []}))
        assert any("sf project deploy start --source-dir force-app" in c and "dry-run" not in c for c in calls)
        assert any("gh pr merge" in c for c in calls)
        assert session.deployed is True and session.merged is True
        assert "report_to_ticket" in out

    def test_ship_deploy_failure_reopens_delegation(self):
        def execute(cmd, *a, **k):
            if "sf project deploy start" in cmd:
                return {"success": False, "stdout": "", "stderr": "Required field missing: Foo__c"}
            return {"success": True, "stdout": "", "stderr": ""}

        sandbox = SimpleNamespace(
            current_branch=lambda: "feature/PGD-1",
            read_file=lambda p: {"success": True, "content": "t"},
            execute=execute,
            run_codex=lambda *a, **k: {"exit_code": 0, "stdout": "fixed", "stderr": ""},
        )
        session = manager._Session(_settings(), sandbox, "PGD-1", "plan", "proj-1", None, None)
        session.calls.append("delegate_worker")
        session.gate_passed = True
        session.pr_url = "https://github.com/o/r/pull/1"
        out = asyncio.run(session.dispatch("ship", {}, {"diary": []}))
        assert "FAILED" in out and "Required field missing" in out
        assert session.deployed is False
        assert session.gate_passed is None  # re-delegation is unblocked
        follow = asyncio.run(session.dispatch("delegate_worker", {"brief": "fix the required field"}, {"diary": []}))
        assert not follow.startswith("REFUSED")

    def test_validate_fails_with_zero_commits_even_if_deploy_would_pass(self):
        session = self._session(ahead=0, dirty=" M force-app/main/default/objects/Order__c/fields/Quantity_UOM__c.field-meta.xml")
        session.calls.append("delegate_worker")  # validate runs after work is delegated
        out = asyncio.run(session.dispatch("validate", {}, {"diary": []}))
        assert out.startswith("FAILED")
        assert "no commits ahead" in out
        assert "uncommitted" in out
        assert session.gate_passed is False

    def test_validate_fails_when_branch_is_empty_and_clean(self):
        session = self._session(ahead=0, dirty="")
        session.calls.append("delegate_worker")  # validate runs after work is delegated
        out = asyncio.run(session.dispatch("validate", {}, {"diary": []}))
        assert out.startswith("FAILED")
        assert "Nothing was committed" in out
        assert session.gate_passed is False

    def test_validate_passes_when_ahead_and_deploy_ok(self):
        session = self._session(ahead=1)
        session.calls.append("delegate_worker")  # validate runs after work is delegated
        out = asyncio.run(session.dispatch("validate", {}, {"diary": []}))
        assert out.startswith("PASSED")
        assert session.gate_passed is True

    def test_delegate_allowed_after_false_green_zero_commits(self):
        """PGD-48: validate was wrongly greened; Codex must still be able to commit."""
        session = self._session(ahead=0, dirty=" M force-app/foo.xml")
        session.gate_passed = True
        out = asyncio.run(session.dispatch("delegate_worker", {"brief": "commit and push the field"}, {"diary": []}))
        assert not out.startswith("REFUSED")

    def test_deliver_refused_with_zero_commits(self):
        session = self._session(ahead=0)
        session.gate_passed = True
        out = asyncio.run(session.dispatch("deliver", {}, {"diary": []}))
        assert out.startswith("REFUSED")
        assert "no unique commits" in out

    def test_validate_fails_when_branch_only_touches_ticket_md(self):
        """PGD-48: a commit whose whole diff is TICKET.md passed validate."""
        session = self._session(ahead=1, changed=["TICKET.md"])
        session.calls.append("delegate_worker")  # validate runs after work is delegated
        out = asyncio.run(session.dispatch("validate", {}, {"diary": []}))
        assert out.startswith("FAILED")
        assert "scaffolding" in out
        assert session.gate_passed is False

    def test_validate_passes_with_a_real_source_change(self):
        session = self._session(
            ahead=1, changed=["TICKET.md", "force-app/main/default/classes/OrderHandler.cls"]
        )
        session.calls.append("delegate_worker")  # validate runs after work is delegated
        out = asyncio.run(session.dispatch("validate", {}, {"diary": []}))
        assert out.startswith("PASSED")
        assert session.gate_passed is True

    def test_delegate_reopens_after_a_failed_review(self):
        """PGD-48: review said 'fail — implementation missing', the guard still refused."""
        session = self._session(ahead=1, dirty="")
        session.gate_passed = True
        session.review_failed = True
        session.last_review = "Verdict: fail\nMissing: UOM fields, conversion logic."
        out = asyncio.run(
            session.dispatch("delegate_worker", {"brief": "add the UOM fields"}, {"diary": []})
        )
        assert not out.startswith("REFUSED")
        assert session.review_retries == 1
        assert session.review_failed is False
        assert session.gate_passed is None

    def test_delegate_refused_once_the_post_review_retry_is_spent(self):
        session = self._session(ahead=1, dirty="")
        session.gate_passed = True
        session.review_failed = False
        session.review_retries = manager._MAX_REVIEW_RETRIES
        out = asyncio.run(session.dispatch("delegate_worker", {"brief": "again"}, {"diary": []}))
        assert out.startswith("REFUSED")
        assert "retry is spent" in out

    def test_deliver_refused_while_review_failed(self):
        session = self._session(ahead=1)
        session.gate_passed = True
        session.review_failed = True
        out = asyncio.run(session.dispatch("deliver", {}, {"diary": []}))
        assert out.startswith("REFUSED")
        assert "review" in out.lower()

    def test_review_marks_failure_and_tells_the_manager_to_re_delegate(self):
        session = self._session(codex_stdout="Verdict: **fail**\nMissing: everything.")
        out = asyncio.run(session.dispatch("review", {}, {"diary": []}))
        assert session.review_failed is True
        assert "delegate_worker ONCE more" in out

    def test_review_pass_clears_the_flag(self):
        session = self._session(codex_stdout="Verdict: pass\nLooks complete.")
        session.review_failed = True
        asyncio.run(session.dispatch("review", {}, {"diary": []}))
        assert session.review_failed is False

    def test_review_does_not_fail_on_an_empty_diff_when_the_branch_has_commits(self):
        """AG-195: fully committed + pushed, but `git diff main...HEAD` came back
        empty and the reviewer said 'nothing delivered', blocking the ticket."""

        def execute(cmd: str, *a, **k):
            if "rev-list --count" in cmd:
                return {"success": True, "stdout": "3", "stderr": ""}
            if cmd.startswith("git diff"):
                return {"success": True, "stdout": "", "stderr": ""}
            if "rev-parse --verify" in cmd:
                return {"success": True, "stdout": "abc123", "stderr": ""}
            return {"success": True, "stdout": "", "stderr": ""}

        sandbox = SimpleNamespace(
            current_branch=lambda: "feature/AG-195",
            read_file=lambda p: {"success": True, "content": "ticket"},
            execute=execute,
            run_codex=lambda *a, **k: {"exit_code": 0, "stdout": "Verdict: fail", "stderr": ""},
        )
        session = manager._Session(_settings(), sandbox, "AG-195", "plan", "proj-1", None, None)
        out = asyncio.run(session.dispatch("review", {}, {"diary": []}))
        assert out.startswith("ERROR")
        assert "git range problem" in out
        assert session.review_failed is False

    def test_review_feeds_the_reviewer_the_committed_diff_not_an_empty_one(self):
        captured: dict[str, str] = {}

        def run_codex(instruction, *a, **k):
            captured["instruction"] = instruction
            return {"exit_code": 0, "stdout": "Verdict: warn\nlooks ok", "stderr": ""}

        def execute(cmd: str, *a, **k):
            if cmd.startswith("git diff") and "--stat" in cmd:
                return {"success": True, "stdout": " Foo.cls | 10 +++", "stderr": ""}
            if cmd.startswith("git diff") and "--name-only" in cmd:
                return {"success": True, "stdout": "force-app/main/default/classes/Foo.cls", "stderr": ""}
            if cmd.startswith("git diff"):
                return {"success": True, "stdout": "diff --git a/Foo.cls b/Foo.cls\n+code", "stderr": ""}
            if cmd.startswith("git log"):
                return {"success": True, "stdout": "abc123 AG-1: add Foo", "stderr": ""}
            if "rev-list --count" in cmd:
                return {"success": True, "stdout": "1", "stderr": ""}
            if "rev-parse --verify" in cmd:
                return {"success": True, "stdout": "abc", "stderr": ""}
            return {"success": True, "stdout": "", "stderr": ""}

        sandbox = SimpleNamespace(
            current_branch=lambda: "feature/AG-1",
            read_file=lambda p: {"success": True, "content": "ticket"},
            execute=execute,
            run_codex=run_codex,
        )
        session = manager._Session(_settings(), sandbox, "AG-1", "plan", "proj-1", None, None)
        asyncio.run(session.dispatch("review", {}, {"diary": []}))
        text = captured["instruction"]
        assert "ALREADY" in text and "committed and pushed" in text
        assert "AG-1: add Foo" in text
        assert "Foo.cls" in text
        assert "+code" in text

    def test_failed_review_text_is_forwarded_into_the_next_brief(self):
        captured: dict[str, str] = {}

        def run_codex(instruction, *a, **k):
            captured["instruction"] = instruction
            return {"exit_code": 0, "stdout": "done", "stderr": ""}

        session = self._session()
        session.sandbox.run_codex = run_codex
        session.gate_passed = True
        session.review_failed = True
        session.last_review = "Verdict: fail\nMissing: Product__c.UOM__c and the conversion trigger."
        asyncio.run(session.dispatch("delegate_worker", {"brief": "finish it"}, {"diary": []}))
        assert "conversion trigger" in captured["instruction"]
        assert "previous review FAILED" in captured["instruction"]


class _RecordingSandbox:
    """Records every git command and answers a small script."""

    def __init__(self, *, branch="feature/AG-155", dirty=True, ahead=0, script=None):
        self._branch = branch
        self._dirty = dirty
        self._ahead = ahead
        self.script = script or {}
        self.calls: list[str] = []

    def current_branch(self):
        return self._branch

    def execute(self, cmd, *a, **k):
        self.calls.append(cmd)
        for key, result in self.script.items():
            if key in cmd:
                return result
        if "status --porcelain" in cmd:
            return {"success": True, "stdout": " M force-app/x.cls" if self._dirty else "", "stderr": ""}
        if "rev-list --count" in cmd:
            return {"success": True, "stdout": str(self._ahead), "stderr": ""}
        return {"success": True, "stdout": "", "stderr": ""}


def _bare_session(sandbox):
    return manager._Session(_settings(), sandbox, "AG-155", "plan", "proj-1", None, None)


class TestRemoteBranchGate:
    def test_validate_fails_when_local_commits_were_not_pushed(self):
        sb = _RecordingSandbox(
            dirty=False,
            ahead=1,
            script={
                "git ls-remote --exit-code --heads": {
                    "success": False,
                    "stdout": "",
                    "stderr": "Authentication failed",
                }
            },
        )
        report = _bare_session(sb)._run_validate()
        assert report.startswith("FAILED: branch `feature/AG-155` has local commits")
        assert "Authentication failed" in report

    def test_review_refuses_to_claim_an_unpushed_branch_was_pushed(self):
        sb = _RecordingSandbox(
            dirty=False,
            ahead=1,
            script={
                "git ls-remote --exit-code --heads": {
                    "success": False,
                    "stdout": "",
                    "stderr": "Authentication failed",
                }
            },
        )
        result = asyncio.run(_bare_session(sb)._do_review({}, {}))
        assert result.startswith("REFUSED: branch `feature/AG-155` is not confirmed on origin")
        assert not any("git diff" in call for call in sb.calls)

    def test_delivery_refuses_a_branch_that_is_not_on_origin(self):
        sb = _RecordingSandbox(
            dirty=False,
            ahead=1,
            script={
                "git ls-remote --exit-code --heads": {
                    "success": False,
                    "stdout": "",
                    "stderr": "remote branch missing",
                }
            },
        )
        session = _bare_session(sb)
        session.gate_passed = True
        result = asyncio.run(session._do_deliver({}, {}))
        assert result.startswith("REFUSED: branch `feature/AG-155` is not present on origin")
        assert not any("gh pr create" in call for call in sb.calls)


class TestAutocommitPending:
    def test_commits_and_pushes_when_dirty_and_zero_commits_ahead(self):
        sb = _RecordingSandbox(dirty=True, ahead=0)
        note = _bare_session(sb)._autocommit_pending("worker session snapshot")
        assert note and "Auto-committed" in note and "pushed" in note
        assert any(c == "git add -A" for c in sb.calls)
        assert any(c.startswith("git commit -m ") and "AG-155: worker session snapshot" in c for c in sb.calls)
        assert any("git push -u origin HEAD" in c for c in sb.calls)

    def test_noop_when_tree_is_clean(self):
        sb = _RecordingSandbox(dirty=False, ahead=0)
        assert _bare_session(sb)._autocommit_pending("x") is None
        assert not any("commit" in c for c in sb.calls)

    def test_noop_when_worker_already_committed(self):
        sb = _RecordingSandbox(dirty=True, ahead=2)
        assert _bare_session(sb)._autocommit_pending("x") is None
        assert not any("git commit" in c for c in sb.calls)

    def test_noop_on_default_branch(self):
        sb = _RecordingSandbox(branch="main", dirty=True, ahead=0)
        assert _bare_session(sb)._autocommit_pending("x") is None

    def test_nothing_to_commit_race_is_not_reported_as_a_save(self):
        sb = _RecordingSandbox(
            dirty=True, ahead=0,
            script={"git commit -m": {"success": False, "stdout": "nothing to commit, working tree clean", "stderr": ""}},
        )
        assert _bare_session(sb)._autocommit_pending("x") is None

    def test_push_failure_is_surfaced_not_swallowed(self):
        sb = _RecordingSandbox(
            dirty=True, ahead=0,
            script={"git push": {"success": False, "stdout": "", "stderr": "remote rejected"}},
        )
        note = _bare_session(sb)._autocommit_pending("snapshot")
        assert "push` FAILED" in note and "remote rejected" in note


class TestSessionWasCutOff:
    def _s(self):
        return _bare_session(_RecordingSandbox())

    def test_timeout_counts_as_cut_off(self):
        s = self._s()
        s._last_engine_result = {"exit_code": 124, "timed_out": True, "stdout": "partial"}
        assert s._session_was_cut_off() is True

    def test_nonzero_exit_counts_as_cut_off(self):
        s = self._s()
        s._last_engine_result = {"exit_code": 1, "stdout": "boom"}
        assert s._session_was_cut_off() is True

    def test_empty_stdout_counts_as_cut_off(self):
        s = self._s()
        s._last_engine_result = {"exit_code": 0, "stdout": "   ", "stderr": "..."}
        assert s._session_was_cut_off() is True

    def test_clean_finish_is_not_cut_off(self):
        s = self._s()
        s._last_engine_result = {"exit_code": 0, "stdout": "PGD-1 implemented and pushed."}
        assert s._session_was_cut_off() is False


class TestDelegateWorkerAutocommits:
    def test_delegate_autocommits_a_short_session_and_tells_the_manager(self):
        sb = _RecordingSandbox(dirty=True, ahead=0)
        sb.run_codex = lambda *a, **k: {"exit_code": 0, "stdout": "", "stderr": "OpenAI Codex v1"}
        session = _bare_session(sb)
        out = asyncio.run(session.dispatch("delegate_worker", {"brief": "add the field"}, {"diary": []}))
        assert "ended early" in out
        assert "Auto-committed" in out
        assert any("git push -u origin HEAD" in c for c in sb.calls)
        assert "Auto-committed" in session.diary[-1]


class TestWorkerBriefCommitDiscipline:
    def test_prefix_tells_worker_to_commit_before_verifying(self, monkeypatch):
        monkeypatch.setattr(manager, "list_project_installs", lambda *a, **k: [])
        monkeypatch.setattr(manager, "get_skill", lambda _s: None)
        text = manager.worker_instruction_for_tests("p1", "AG-155", _settings(), "Add Lead_Priority__c")
        assert "COMMIT DISCIPLINE" in text
        assert "BEFORE any verification step" in text
        assert "AG-155: wip" in text
        # WS-5.2: the worker must NOT own the dry-run deploy
        assert "Do NOT run `sf project deploy start --dry-run` yourself" in text


class TestClean:
    def test_strips_ansi_and_sf_spinner_lines(self):
        raw = (
            "Deploying Metadata (dry-run)\n"
            "\x1b[2K\x1b[1A\x1b[2K Deploy ID: 0Afj123  Elapsed Time: 1.98s\n"
            "\x1b[2K Components: 3/5 (60%) Deploying Metadata\n"
            "Status: Succeeded\n"
        )
        out = manager._clean(raw)
        assert "\x1b[" not in out
        assert "Elapsed Time" not in out
        assert "Status: Succeeded" in out

    def test_keep_last_lines_trims_the_head(self):
        text = "\n".join(f"line {i}" for i in range(100))
        out = manager._clean(text, keep_last_lines=5)
        assert out.startswith("…\n")
        assert "line 99" in out and "line 10" not in out


class TestPrBody:
    def _session(self):
        def execute(cmd, *a, **k):
            if "git diff --name-status" in cmd:
                return {"success": True, "stdout": "A\tforce-app/main/default/classes/Foo.cls", "stderr": ""}
            if cmd.startswith("git log --format=%s"):
                return {"success": True, "stdout": "AG-1: add Foo\nAG-1: wip", "stderr": ""}
            if "rev-parse --verify" in cmd:
                return {"success": True, "stdout": "abc", "stderr": ""}
            return {"success": True, "stdout": "", "stderr": ""}

        sandbox = SimpleNamespace(current_branch=lambda: "feature/AG-1", execute=execute,
                                  read_file=lambda p: {"success": True, "content": "t"})
        return manager._Session(_settings(), sandbox, "AG-1", "plan", "proj-1", None, None)

    def test_pr_body_is_structured_no_ansi_no_branch_dump(self):
        s = self._session()
        s.gate_passed = True
        s.last_review = "Verdict: warn\nlgtm"
        s.diary = ["Worker finished:\nAssumption: default Lead_Priority is Normal\ndone"]
        body = s._pr_body()
        assert body.startswith("## AG-1")
        assert "### Files changed" in body and "Foo.cls" in body
        assert "### Commits" in body and "AG-1: add Foo" in body
        assert "dry-run deploy: passed" in body and "review: warn" in body
        assert "default Lead_Priority is Normal" in body
        assert "Current branch:" not in body and "\x1b[" not in body


class FakeModel:
    def __init__(self, turns: list[AIMessage]):
        self.turns = turns
        self.calls = 0

    async def ainvoke(self, _messages):
        message = self.turns[min(self.calls, len(self.turns) - 1)]
        self.calls += 1
        return message


def _call(name: str, args: dict, cid: str = "c1") -> dict:
    return {"name": name, "args": args, "id": cid}


class TestGraphLoop:
    def test_report_ends_the_run(self, monkeypatch):
        monkeypatch.setattr(manager, "list_project_installs", lambda *a, **k: [])
        monkeypatch.setattr(manager, "get_skill", lambda _s: None)
        monkeypatch.setattr(manager, "fetch_memory_markdown", lambda _p: "catalog")

        posted: list[str] = []

        async def fake_add(conn, key, body):
            posted.append(body)

        monkeypatch.setattr(manager, "add_comment", fake_add)
        monkeypatch.setattr(manager, "get_jira_connection", lambda _n: object())

        sandbox = SimpleNamespace(
            current_branch=lambda: "feature/PGD-1-x",
            read_file=lambda p: {"success": True, "content": "As an Admin..."},
            execute=lambda *a, **k: {"success": True, "stdout": "ok", "stderr": ""},
        )
        model = FakeModel(
            [
                AIMessage(content="", tool_calls=[_call("inspect_context", {"what": "ticket"})]),
                AIMessage(
                    content="",
                    tool_calls=[
                        _call(
                            "report_to_ticket",
                            {
                                "outcome": "already_implemented",
                                "markdown": "Product__c already has SKU_Code__c. Nothing to build.",
                            },
                            "c2",
                        )
                    ],
                ),
                AIMessage(content="should not run"),
            ]
        )
        from langgraph.checkpoint.memory import InMemorySaver

        outcome = asyncio.run(
            manager.run_managed_pipeline(
                _settings(),
                sandbox,
                "PGD-1",
                "plan text",
                project_id="p1",
                run_id="run-1",
                model=model,
                checkpointer=InMemorySaver(),
            )
        )
        assert outcome.outcome == "already_implemented"
        assert "SKU_Code__c" in outcome.report_markdown
        assert posted, "Jira comment must be posted by report_to_ticket"
        assert model.calls == 2
