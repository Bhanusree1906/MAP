"""Bootstrap Salesforce: org-catalog rendering + DX scaffold git flow.

Pure-function / fake-sandbox coverage only — no Docker, no `sf`.
"""

from __future__ import annotations

from types import SimpleNamespace

from agents.pipeline_agent import salesforce_bootstrap as boot
from agents.pipeline_agent.org_catalog import _field_line, render_catalog_markdown


class TestRenderCatalogMarkdown:
    def _catalog(self, **overrides):
        base = {
            "org": {
                "id": "00Dxx",
                "username": "svc@example.com",
                "alias": "devorg",
                "instance_url": "https://x.my.salesforce.com",
                "api_version": "62.0",
            },
            "metadata": {
                "CustomObject": ["Product__c", "Order__c"],
                "PermissionSet": ["Product_Master_Admin"],
            },
            "objects": {
                "Product__c": {
                    "label": "Product",
                    "custom": True,
                    "fields": [
                        {"name": "SKU_Code__c", "type": "string", "length": 40, "nillable": False},
                        {"name": "Product2__c", "type": "reference", "referenceTo": ["Product2"]},
                    ],
                }
            },
            "custom_object_count": 2,
            "describes_truncated": False,
            "generated_at": "2026-08-27T00:00:00+00:00",
        }
        base.update(overrides)
        return base

    def test_lists_org_facts_and_custom_objects(self):
        md = render_catalog_markdown(self._catalog())
        assert "Org id: `00Dxx`" in md
        assert "API version: `62.0`" in md
        assert "`Product__c`" in md and "`Order__c`" in md
        assert "`Product_Master_Admin`" in md

    def test_renders_field_detail_and_references(self):
        md = render_catalog_markdown(self._catalog())
        assert "SKU_Code__c" in md and "required" in md
        assert "-> Product2" in md

    def test_empty_org_says_so(self):
        md = render_catalog_markdown(
            self._catalog(metadata={"CustomObject": []}, objects={}, custom_object_count=0)
        )
        assert "no custom objects yet" in md

    def test_custom_objects_section_drops_standard_and_managed_entries(self):
        # a fresh org returns hundreds of standard/system CustomObject names
        cat = self._catalog(
            metadata={
                "CustomObject": [
                    "Account", "Task", "TenantPvsnLicConsumption",  # standard/system
                    "npsp__General_Accounting_Unit__c",             # managed package
                    "Lead_Priority__c", "Order__c",                 # genuine custom
                ],
                "PermissionSet": ["Sales_User", "npsp__Nonprofit_Admin", "Lead_Priority_Admin"],
            },
            objects={},
        )
        md = render_catalog_markdown(cat)
        assert "`Lead_Priority__c`" in md and "`Order__c`" in md
        assert "`Account`" not in md and "TenantPvsnLicConsumption" not in md
        assert "npsp__" not in md
        # permission sets: managed dropped, standard + custom kept
        assert "`Sales_User`" in md and "`Lead_Priority_Admin`" in md

    def test_slim_caps_and_reports_overflow(self):
        from agents.pipeline_agent.org_catalog import _slim

        kept, dropped = _slim([f"Thing{i}" for i in range(60)], cap=40)
        assert len(kept) == 40 and dropped == 20

    def test_field_line_picklist_values_are_capped(self):
        line = _field_line(
            {
                "name": "Stage__c",
                "type": "picklist",
                "picklistValues": [{"value": f"V{i}", "active": True} for i in range(30)],
            }
        )
        assert line.count(",") <= 20
        assert "…" in line


class TestComposeProjectMemory:
    def test_points_at_official_dx_docs_and_embeds_catalog(self):
        settings = SimpleNamespace(default_branch="main")
        md = boot.compose_project_memory(settings, "# Org catalog\n\n- thing")
        assert "developer.salesforce.com" in md
        assert "salesforce-org-harness" in md
        assert "# Org catalog" in md
        assert "Default git branch: `main`" in md

    def test_handles_empty_catalog(self):
        md = boot.compose_project_memory(SimpleNamespace(default_branch=None), "   ")
        assert "Catalog dump was empty" in md
        assert "`main`" in md  # falls back to main


class _FakeSandbox:
    def __init__(self, script):
        self.script = script  # substring -> result dict
        self.calls: list[str] = []

    def execute(self, command, *a, **k):
        self.calls.append(command)
        for key, result in self.script.items():
            if key in command:
                return result
        return {"success": True, "stdout": "", "stderr": ""}

    def clone_repository(self, *a, **k):
        return self.script.get("__clone__", {"success": True})

    def read_file(self, path):
        return self.script.get(f"__read__{path}", {"success": False})


class TestGitScaffoldFlow:
    def test_ensure_git_repo_falls_back_to_init_when_clone_fails(self):
        sb = _FakeSandbox({"__clone__": {"success": False, "error": "empty repo"}})
        settings = SimpleNamespace(
            repository_url="https://github.com/o/r.git", git_token="tok", default_branch="main"
        )
        out = boot._ensure_git_repo(sb, settings)
        assert out["ok"] is True and out.get("initialized") is True
        assert any(c == "git init" for c in sb.calls)
        assert any("git remote add origin" in c for c in sb.calls)

    def test_commit_and_push_skips_when_nothing_staged(self):
        # `git diff --cached --quiet` exits 0 => nothing staged => skipped.
        sb = _FakeSandbox({"git diff --cached --quiet": {"success": True, "stdout": "", "stderr": ""}})
        settings = SimpleNamespace(
            bot_git_name="pipeline-agent", bot_git_email="bot@x", default_branch="main"
        )
        out = boot._commit_and_push_scaffold(sb, settings)
        assert out == {"success": True, "skipped": True}
        assert not any("git push" in c for c in sb.calls)

    def test_commit_and_push_runs_full_sequence_when_staged(self):
        sb = _FakeSandbox(
            {"git diff --cached --quiet": {"success": False, "stdout": "", "stderr": ""}}
        )
        settings = SimpleNamespace(
            bot_git_name="pipeline-agent", bot_git_email="bot@x", default_branch="main"
        )
        out = boot._commit_and_push_scaffold(sb, settings)
        assert out == {"success": True, "skipped": False}
        joined = "\n".join(sb.calls)
        assert "git commit -m" in joined
        assert "git branch -M main" in joined
        assert "git push -u origin HEAD" in joined


class TestBootstrapJob:
    def setup_method(self):
        boot.reset_bootstrap_jobs_for_tests()

    def teardown_method(self):
        boot.reset_bootstrap_jobs_for_tests()

    def test_job_records_steps_and_succeeds(self, monkeypatch):
        def fake_boot(_project_id, on_event=None):
            if on_event:
                on_event("step", "Collecting org catalog")
                on_event("timing", "org catalog: 12 ms")
            return {
                "catalog_objects": 2,
                "api_version": "62.0",
                "scaffolded": False,
                "scaffold_skipped": True,
                "message": "Org catalog saved (2 custom objects).",
            }

        monkeypatch.setattr(boot, "bootstrap_salesforce_project", fake_boot)
        job = boot.start_bootstrap_job("proj-1")
        assert job.id
        for _ in range(80):
            current = boot.get_bootstrap_job("proj-1", job.id)
            if current and current.status != "running":
                break
            import time

            time.sleep(0.025)
        current = boot.get_bootstrap_job("proj-1", job.id)
        assert current is not None
        assert current.status == "succeeded"
        assert current.result and current.result["catalog_objects"] == 2
        kinds = [e.kind for e in boot.list_bootstrap_events("proj-1", job.id)]
        texts = [e.content for e in boot.list_bootstrap_events("proj-1", job.id)]
        assert "step" in kinds and "timing" in kinds
        assert any("Starting Salesforce bootstrap" in t for t in texts)
        assert any("Collecting org catalog" in t for t in texts)

    def test_second_start_reuses_running_job(self, monkeypatch):
        import threading

        started = threading.Event()
        release = threading.Event()

        def fake_boot(_project_id, on_event=None):
            started.set()
            release.wait(timeout=2)
            return {
                "catalog_objects": 0,
                "api_version": "",
                "scaffolded": False,
                "scaffold_skipped": True,
                "message": "done",
            }

        monkeypatch.setattr(boot, "bootstrap_salesforce_project", fake_boot)
        first = boot.start_bootstrap_job("proj-1")
        assert started.wait(timeout=2)
        second = boot.start_bootstrap_job("proj-1")
        assert second.id == first.id
        release.set()

    def test_failed_job_keeps_error_and_events(self, monkeypatch):
        def fake_boot(_project_id, on_event=None):
            if on_event:
                on_event("step", "Opening Salesforce CLI sandbox")
            raise RuntimeError("No Salesforce connection with an auth URL")

        monkeypatch.setattr(boot, "bootstrap_salesforce_project", fake_boot)
        job = boot.start_bootstrap_job("proj-1")
        for _ in range(80):
            current = boot.get_bootstrap_job("proj-1", job.id)
            if current and current.status != "running":
                break
            import time

            time.sleep(0.025)
        current = boot.get_bootstrap_job("proj-1", job.id)
        assert current is not None
        assert current.status == "failed"
        assert "auth URL" in (current.error or "")
        texts = [e.content for e in boot.list_bootstrap_events("proj-1", job.id)]
        assert any("Bootstrap failed" in t for t in texts)
