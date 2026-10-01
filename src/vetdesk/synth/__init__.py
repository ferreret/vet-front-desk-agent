"""Synthetic clinic generator: clean world, legacy-style database and call scenarios."""

from .defects import apply_defects
from .world import GeneratorConfig, World, build_clean_world


def generate_world(config: GeneratorConfig | None = None) -> World:
    """Build the clinic and decide how the legacy database stores each record."""
    world = build_clean_world(config or GeneratorConfig())
    apply_defects(world)
    return world


__all__ = ["GeneratorConfig", "World", "generate_world"]
