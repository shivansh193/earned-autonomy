import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "hands_off_merge", Path(__file__).resolve().parents[2] / "ci" / "hands_off_merge.py")
hom = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hom)

CARD = "## Autonomy: Hands-off\n...\n- **Final: hands_off** at risk 0.00\n- Pipeline 77"


def fake_api(mr=None, notes=None, diffs=None):
    mr = {"sha": "abc", "detailed_merge_status": "mergeable", "head_pipeline": {"id": 77, "status": "success", "sha": "abc"},
          **(mr or {})}
    notes = notes if notes is not None else [{"body": CARD, "author": {"username": "ai-ea-autonomy-router-gitlab-ai-hackathon"}}]
    diffs = diffs if diffs is not None else [{"old_path": "backend/app/api/routes/items.py", "new_path": "backend/app/api/routes/items.py"}]

    def api(method, path, body=None):
        if path.endswith("/notes?sort=desc&order_by=created_at&per_page=50"):
            return 200, notes
        if "/pipelines?" in path:
            return 200, [{"id": 77, "sha": "abc", "status": "success"}, {"id": 78, "sha": "abc", "status": "success"},
                         {"id": 70, "sha": "old", "status": "success"}]
        if "/diffs" in path:
            return 200, diffs
        return 200, mr
    return api


@pytest.fixture
def mr():
    return {"iid": 5, "sha": "abc", "draft": False}


def test_merges_an_app_change_with_a_current_hands_off_card(monkeypatch, mr):
    monkeypatch.setattr(hom, "api", fake_api())
    assert hom.why_not("1", mr) is None


def test_never_merges_changes_to_its_own_controls(monkeypatch, mr):
    monkeypatch.setattr(hom, "api", fake_api(diffs=[{"old_path": "ci/route.sh", "new_path": "ci/route.sh"}]))
    assert "controls" in hom.why_not("1", mr)


def test_card_must_be_for_the_current_pipeline_and_from_the_router(monkeypatch, mr):
    stale = [{"body": CARD.replace("Pipeline 77", "Pipeline 76"), "author": {"username": "ai-ea-autonomy-router-gitlab-ai-hackathon"}}]
    monkeypatch.setattr(hom, "api", fake_api(notes=stale))
    assert "current commit" in hom.why_not("1", mr)
    forged = [{"body": CARD, "author": {"username": "someone"}}]
    monkeypatch.setattr(hom, "api", fake_api(notes=forged))
    assert "Autonomy Router" in hom.why_not("1", mr)


def test_supervised_cards_and_failed_pipelines_wait(monkeypatch, mr):
    sup = [{"body": CARD.replace("hands_off", "supervised"), "author": {"username": "ai-ea-autonomy-router-gitlab-ai-hackathon"}}]
    monkeypatch.setattr(hom, "api", fake_api(notes=sup))
    assert hom.why_not("1", mr) is not None
    monkeypatch.setattr(hom, "api", fake_api(mr={"head_pipeline": {"id": 77, "status": "failed", "sha": "abc"}}))
    assert "passed pipeline" in hom.why_not("1", mr)
    assert hom.why_not("1", {**mr, "draft": True}) == "draft"


def test_router_account_must_match_exactly(monkeypatch, mr):
    lookalike = [{"body": CARD, "author": {"username": "ai-ea-autonomy-router-gitlab-ai-hackathon-evil"}}]
    monkeypatch.setattr(hom, "api", fake_api(notes=lookalike))
    assert "Autonomy Router" in hom.why_not("1", mr)


def test_a_control_file_past_the_first_page_still_blocks(monkeypatch, mr):
    app = [{"old_path": f"backend/f{i}.py", "new_path": f"backend/f{i}.py"} for i in range(100)]
    base = fake_api()

    def api(method, path, body=None):
        if "/diffs" in path:
            return 200, (app if "page=1" in path and "page=10" not in path else [{"old_path": "ci/x.sh", "new_path": "ci/x.sh"}])
        return base(method, path, body)
    monkeypatch.setattr(hom, "api", api)
    assert "controls" in hom.why_not("1", mr)


def test_unchecked_merge_status_waits(monkeypatch, mr):
    monkeypatch.setattr(hom, "api", fake_api(mr={"detailed_merge_status": "unchecked"}))
    assert "unchecked" in hom.why_not("1", mr)


def test_card_for_another_passed_pipeline_of_the_same_commit_counts(monkeypatch, mr):
    other = [{"body": CARD.replace("Pipeline 77", "Pipeline 78"), "author": {"username": "ai-ea-autonomy-router-gitlab-ai-hackathon"}}]
    monkeypatch.setattr(hom, "api", fake_api(notes=other))
    assert hom.why_not("1", mr) is None
    old = [{"body": CARD.replace("Pipeline 77", "Pipeline 70"), "author": {"username": "ai-ea-autonomy-router-gitlab-ai-hackathon"}}]
    monkeypatch.setattr(hom, "api", fake_api(notes=old))
    assert "current commit" in hom.why_not("1", mr)
