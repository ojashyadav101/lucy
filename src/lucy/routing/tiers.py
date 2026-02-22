"""Model tiers for intelligent routing.

Defines the available model tiers, their associated models, 
and capabilities for the routing system.
"""

from __future__ import annotations

from enum import IntEnum
from dataclasses import dataclass


class ModelTier(IntEnum):
    """Model complexity tiers.
    
    Higher tier means more capable but more expensive/slower.
    """
    TIER_0_CACHE = 0      # Semantic cache hit (no LLM call)
    TIER_1_FAST = 1       # Simple tasks (Gemini Flash, GPT-4o-mini)
    TIER_2_STANDARD = 2   # General tasks (Kimi, GPT-4o)
    TIER_3_FRONTIER = 3   # Complex reasoning/coding (Claude 3.5 Sonnet)


@dataclass
class TierConfig:
    """Configuration for a specific model tier."""
    
    tier: ModelTier
    primary_model: str
    fallback_models: list[str]
    max_tokens: int
    description: str


# System tier definitions
TIERS = {
    ModelTier.TIER_1_FAST: TierConfig(
        tier=ModelTier.TIER_1_FAST,
        primary_model="openrouter/google/gemini-2.5-flash",
        fallback_models=["openrouter/openai/gpt-4o-mini"],
        max_tokens=4096,
        description="Fast, cheap models for simple lookups, routing, and classification.",
    ),
    ModelTier.TIER_2_STANDARD: TierConfig(
        tier=ModelTier.TIER_2_STANDARD,
        primary_model="openrouter/openai/gpt-4o-mini",
        fallback_models=["openrouter/moonshotai/kimi-k2.5", "openrouter/openai/gpt-4o"],
        max_tokens=8192,
        description="Standard models for general conversation and straightforward tasks.",
    ),
    ModelTier.TIER_3_FRONTIER: TierConfig(
        tier=ModelTier.TIER_3_FRONTIER,
        primary_model="openrouter/anthropic/claude-3.5-sonnet",
        fallback_models=["openrouter/anthropic/claude-3-opus", "openrouter/openai/gpt-4o"],
        max_tokens=8192,
        description="Frontier models for complex reasoning, multi-step planning, and coding.",
    ),
}


def get_tier_config(tier: ModelTier) -> TierConfig:
    """Get the configuration for a specific tier.
    
    Args:
        tier: The requested model tier.
        
    Returns:
        The tier configuration.
        
    Raises:
        ValueError: If the tier is not configured.
    """
    if tier not in TIERS:
        # Fallback to standard tier if requested tier is missing
        if tier == ModelTier.TIER_0_CACHE:
            raise ValueError("Tier 0 (Cache) does not have a model configuration.")
        return TIERS[ModelTier.TIER_2_STANDARD]
        
    return TIERS[tier]
