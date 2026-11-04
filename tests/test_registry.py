import pytest

from warnetech_core.errors import ConfigError, ModelNotRegistered
from warnetech_core.registry import ModelRegistry, ModelSpec


def test_task_alias_resolves_to_concrete_model():
    registry = ModelRegistry.default()
    assert registry.resolve("chat.default").provider == "anthropic"


def test_swapping_a_provider_is_one_alias_change():
    registry = ModelRegistry.default()
    registry.alias("chat.default", "openai.gpt-4o")
    assert registry.resolve("chat.default").provider == "openai"


def test_cost_is_computed_from_the_spec():
    spec = ModelSpec(
        alias="x", provider="p", provider_model="m",
        input_cost_per_mtok=3.0, output_cost_per_mtok=15.0,
    )
    assert spec.cost_usd(1_000_000, 0) == pytest.approx(3.0)
    assert spec.cost_usd(0, 200_000) == pytest.approx(3.0)


def test_fallback_chain_is_ordered_and_deduplicated():
    registry = ModelRegistry(specs=[
        ModelSpec(alias="a", provider="p", provider_model="m", fallbacks=("b", "c")),
        ModelSpec(alias="b", provider="p", provider_model="m", fallbacks=("a",)),
        ModelSpec(alias="c", provider="p", provider_model="m"),
    ])
    assert [s.alias for s in registry.chain("a")] == ["a", "b", "c"]


def test_chain_respects_max_length():
    registry = ModelRegistry.default()
    assert len(registry.chain("chat.default", max_length=1)) == 1


def test_unknown_model_raises_with_a_useful_message():
    with pytest.raises(ModelNotRegistered, match="not registered"):
        ModelRegistry.default().resolve("nope")


def test_alias_cycles_are_detected():
    registry = ModelRegistry(specs=[ModelSpec(alias="real", provider="p", provider_model="m")])
    registry._aliases["a"] = "b"
    registry._aliases["b"] = "a"
    with pytest.raises(ConfigError, match="cycle"):
        registry.resolve("a")


def test_yaml_overrides_merge_onto_builtin_specs():
    registry = ModelRegistry.default()
    registry.load_dict({
        "models": [{"alias": "openai.gpt-4o", "input_cost_per_mtok": 99.0}],
        "aliases": {"chat.default": "openai.gpt-4o"},
    })
    spec = registry.resolve("chat.default")
    assert spec.input_cost_per_mtok == 99.0
    # Untouched fields survive the merge.
    assert spec.provider_model == "gpt-4o"


def test_new_model_from_yaml_requires_provider_fields():
    with pytest.raises(ConfigError, match="missing required field"):
        ModelRegistry.default().load_dict({"models": [{"alias": "brand.new"}]})


def test_aliasing_an_unknown_target_is_rejected():
    with pytest.raises(ModelNotRegistered):
        ModelRegistry.default().alias("chat.default", "does.not.exist")


def test_offline_aliases_repoint_every_task_alias():
    registry = ModelRegistry.default().use_offline_models()
    for alias in ("chat.default", "chat.fast", "chat.cheap"):
        assert registry.resolve(alias).provider == "echo"
    assert registry.resolve("embed.default").provider == "echo"
