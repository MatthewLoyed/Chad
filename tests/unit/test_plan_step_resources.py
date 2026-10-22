from src.core.orchestration.plan_step_resources import (
    classify_step_resources,
    resolve_skill_name,
    step_references_skill,
)


def test_resolve_skill_name_exact_and_alias():
    catalog = {"frontend-design", "deep_research"}
    assert resolve_skill_name("frontend-design", catalog) == "frontend-design"
    assert resolve_skill_name("frontend_design_skill", catalog) == "frontend-design"
    assert resolve_skill_name("frontend_design", catalog) == "frontend-design"


def test_resolve_skill_name_unknown():
    assert resolve_skill_name("not_a_real_skill", {"frontend-design"}) is None


def test_classify_step_resources_skill_vs_tool():
    registry = {"build_html_file", "load_skill"}
    catalog = {"frontend-design"}

    direct, skills, offline = classify_step_resources(
        ["build_html_file", "frontend_design_skill", "missing_tool"],
        registry,
        catalog,
    )

    assert direct == ["build_html_file"]
    assert skills == [("frontend_design_skill", "frontend-design")]
    assert offline == ["missing_tool"]


def test_step_references_skill():
    catalog = {"frontend-design"}
    assert step_references_skill(["frontend_design_skill"], catalog)
    assert step_references_skill(["load_skill"], catalog)
    assert not step_references_skill(["build_html_file"], catalog)
