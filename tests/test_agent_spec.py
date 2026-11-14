from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.agents.spec import AgentSpec, SquadTemplate, load_all_squads

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


def test_all_templates_load():
    squads = load_all_squads(TEMPLATES)
    assert {"engineering-squad", "legal-audit-squad", "market-research-squad"} <= set(squads)
    for squad in squads.values():
        assert any(m.is_reviewer for m in squad.members)


def test_model_config_alias():
    spec = AgentSpec.model_validate(
        {"role_name": "x", "system_prompt": "p", "model_config": {"model": "gpt-5.4-mini"}}
    )
    assert spec.llm.model == "gpt-5.4-mini" and spec.llm.provider == "openai"


def test_duplicate_roles_rejected():
    member = {"role_name": "a", "system_prompt": "p"}
    with pytest.raises(ValidationError):
        SquadTemplate.model_validate(
            {
                "name": "s",
                "domain": "custom",
                "supervisor": {"role_name": "sup", "system_prompt": "p"},
                "members": [member, member],
            }
        )


@pytest.mark.parametrize("reviewer", [
    {"role_name": "reviewer", "system_prompt": "p", "is_reviewer": True, "reviews": ["reviewer"]},
    {"role_name": "reviewer", "system_prompt": "p", "is_reviewer": True, "reviews": ["worker"]},
    {"role_name": "reviewer", "system_prompt": "p", "is_reviewer": True, "reviews": [], "model_config": {"provider": "openai"}},
])
def test_invalid_review_topologies(reviewer):
    with pytest.raises(ValidationError):
        SquadTemplate.model_validate({"name": "s", "domain": "custom", "supervisor": {"role_name": "sup", "system_prompt": "p"}, "members": [{"role_name": "worker", "system_prompt": "p"}, reviewer]})


def test_valid_cross_provider_review():
    squad = SquadTemplate.model_validate({"name": "s", "domain": "custom", "supervisor": {"role_name": "sup", "system_prompt": "p"}, "members": [{"role_name": "worker", "system_prompt": "p", "reports_to": "sup"}, {"role_name": "reviewer", "system_prompt": "p", "is_reviewer": True, "reviews": ["worker"], "model_config": {"provider": "openai"}}]})
    assert squad.members[1].reviews == ["worker"]


def agent(role, **extra):
    return {"role_name": role, "system_prompt": "p", **extra}


def squad(*members, supervisor=None, **extra):
    return {"name": "s", "domain": "custom", "supervisor": supervisor or agent("sup"), "members": list(members), **extra}


REVIEWER = agent("rev", is_reviewer=True, reviews=["worker"], model_config={"provider": "openai"})


@pytest.mark.parametrize("model_config", [{"model": "nope"}, {"provider": "openai", "model": "claude-haiku-5-5"}])
def test_model_must_match_catalog(model_config):
    with pytest.raises(ValidationError):
        AgentSpec.model_validate(agent("x", model_config=model_config))


@pytest.mark.parametrize("worker", [
    agent("worker", allowed_mcp_tools=["rm_rf"]),
    agent("worker", allowed_mcp_tools=["write_file"], permissions=["read"]),
    agent("worker", allowed_mcp_tools=["read_file_lines"], permissions=["read"], hitl_tools=["delete_file"]),
    agent("worker", allowed_mcp_tools=["delete_file"], permissions=["write_draft"]),
    agent("worker", permissions=["admin"]),
])
def test_tool_grants_enforced(worker):
    with pytest.raises(ValidationError):
        SquadTemplate.model_validate(squad(worker, REVIEWER))


def test_reviewer_cannot_edit():
    editing_reviewer = {**REVIEWER, "permissions": ["read", "write_draft"], "allowed_mcp_tools": ["write_file"]}
    with pytest.raises(ValidationError):
        SquadTemplate.model_validate(squad(agent("worker"), editing_reviewer))


def test_supervisor_routes_only():
    with pytest.raises(ValidationError):
        SquadTemplate.model_validate(squad(agent("worker"), REVIEWER,
                                           supervisor=agent("sup", permissions=["write_draft"], allowed_mcp_tools=["write_file"])))


def test_budgets_must_fit_squad_limit():
    with pytest.raises(ValidationError):
        SquadTemplate.model_validate(squad(agent("worker", budget_usd=4.5), REVIEWER, budget_limit_usd=5.0))
    ok = SquadTemplate.model_validate(squad(agent("worker", budget_usd=3.0), REVIEWER, budget_limit_usd=5.0))
    assert ok.budget_limit_usd == 5.0


def test_hitl_tool_accepted_when_listed():
    worker = agent("worker", permissions=["write_draft"], allowed_mcp_tools=["delete_file"], hitl_tools=["delete_file"])
    assert SquadTemplate.model_validate(squad(worker, REVIEWER)).members[0].hitl_tools == ["delete_file"]
