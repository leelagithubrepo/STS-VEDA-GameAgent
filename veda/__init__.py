"""VEDA: a provenance-first, research-and-experience game agent."""

from .agent import AutonomousAgent, Decision, DecisionPolicy, NoopPolicy
from .knowledge import Claim, ClaimKind, KnowledgeBase, Source
from .research import ResearchIntake, ResearchNote
from .research_catalog import load_catalog
from .vision import LocalOllamaVisionProvider, StructuredGameState, VisionProvider
from .decision_protocol import DecisionBrief, build_decision_brief

__all__ = [
    "AutonomousAgent", "Claim", "ClaimKind", "Decision", "DecisionPolicy",
    "KnowledgeBase", "NoopPolicy", "Source",
    "ResearchIntake", "ResearchNote",
    "load_catalog",
    "LocalOllamaVisionProvider", "StructuredGameState", "VisionProvider",
    "DecisionBrief", "build_decision_brief",
]
