import pytest

from warnetech_core.errors import PromptNotFound, PromptRenderError
from warnetech_core.prompts import PromptRegistry, PromptTemplate


def make(**kwargs):
    base = {
        "id": "t", "version": 1,
        "messages": (("user", "Hello {{ name }}"),),
    }
    return PromptTemplate(**{**base, **kwargs})


def test_variables_are_inferred_from_the_body():
    assert make().variables == ("name",)


def test_declared_variables_must_match_the_body():
    with pytest.raises(ValueError, match="do not match"):
        make(variables=("other",))


def test_render_substitutes_values():
    assert make().render({"name": "Ada"})[0].content == "Hello Ada"


def test_whitespace_inside_braces_is_tolerated():
    template = make(messages=(("user", "Hi {{name}} and {{  name  }}"),))
    assert template.render({"name": "Bo"})[0].content == "Hi Bo and Bo"


def test_missing_variable_is_an_error_not_a_silent_gap():
    with pytest.raises(PromptRenderError, match="missing: name"):
        make().render({})


def test_unexpected_variable_catches_typos():
    with pytest.raises(PromptRenderError, match="unexpected: nmae"):
        make().render({"name": "Ada", "nmae": "typo"})


def test_non_strict_render_leaves_gaps_alone():
    rendered = make().render({}, strict=False)
    assert rendered[0].content == "Hello {{ name }}"


def test_checksum_tracks_content_not_metadata():
    assert make().checksum == make(description="different").checksum
    assert make().checksum != make(messages=(("user", "Bye {{ name }}"),)).checksum


def test_latest_version_wins_when_unspecified():
    registry = PromptRegistry()
    registry.add("p", [("user", "v1")], version=1)
    registry.add("p", [("user", "v2")], version=2)
    assert registry.get("p").version == 2
    assert registry.get("p", 1).render()[0].content == "v1"


def test_published_versions_are_immutable():
    registry = PromptRegistry()
    registry.add("p", [("user", "original")], version=1)
    with pytest.raises(ValueError, match="bump the version"):
        registry.add("p", [("user", "edited")], version=1)


def test_reregistering_identical_content_is_a_noop():
    registry = PromptRegistry()
    registry.add("p", [("user", "same")], version=1)
    registry.add("p", [("user", "same")], version=1)
    assert len(registry) == 1


def test_missing_prompt_and_missing_version_have_distinct_errors():
    registry = PromptRegistry()
    registry.add("p", [("user", "x")], version=1)
    with pytest.raises(PromptNotFound, match="no prompt with id"):
        registry.get("absent")
    with pytest.raises(PromptNotFound, match="available: v1"):
        registry.get("p", 7)


def test_loading_from_yaml_directory(tmp_path):
    (tmp_path / "a.yaml").write_text(
        "id: loaded\nversion: 3\nmodel: chat.fast\n"
        "messages:\n  - role: user\n    content: 'Hi {{ who }}'\n"
    )
    registry = PromptRegistry.from_dir(tmp_path)
    template = registry.get("loaded")
    assert (template.version, template.model, template.variables) == (3, "chat.fast", ("who",))


def test_yaml_file_may_hold_a_list_of_versions(tmp_path):
    (tmp_path / "a.yaml").write_text(
        "- id: multi\n  version: 1\n  messages: [{role: user, content: one}]\n"
        "- id: multi\n  version: 2\n  messages: [{role: user, content: two}]\n"
    )
    assert PromptRegistry.from_dir(tmp_path).versions("multi") == [1, 2]


def test_bad_role_is_rejected_at_load_time(tmp_path):
    (tmp_path / "a.yaml").write_text(
        "id: bad\nmessages:\n  - role: wizard\n    content: hi\n"
    )
    with pytest.raises(ValueError, match="unknown role"):
        PromptRegistry.from_dir(tmp_path)
