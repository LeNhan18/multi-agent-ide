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
        {"role_name": "x", "system_prompt": "p", "model_config": {"model": "m"}}
    )
    assert spec.llm.model == "m"


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
