"""VEDA: a provenance-first, research-and-experience game agent."""

from importlib import import_module

__all__ = [
    "AutonomousAgent", "Claim", "ClaimKind", "Decision", "DecisionPolicy",
    "KnowledgeBase", "NoopPolicy", "Source",
    "ResearchIntake", "ResearchNote",
    "load_catalog",
    "LocalOllamaVisionProvider", "StructuredGameState", "VisionProvider",
    "DecisionBrief", "build_decision_brief",
]

_EXPORT_MODULES = {
    **dict.fromkeys(("AutonomousAgent", "Decision", "DecisionPolicy", "NoopPolicy"), ".agent"),
    **dict.fromkeys(("Claim", "ClaimKind", "KnowledgeBase", "Source"), ".knowledge"),
    **dict.fromkeys(("ResearchIntake", "ResearchNote"), ".research"),
    "load_catalog": ".research_catalog",
    **dict.fromkeys(("LocalOllamaVisionProvider", "StructuredGameState", "VisionProvider"), ".vision"),
    **dict.fromkeys(("DecisionBrief", "build_decision_brief"), ".decision_protocol"),
}


def __getattr__(name):
    # Lightweight bridge modules also run in the separately managed SDK
    # environment. Importing one must not load the full Python 3.11+ agent.
    module = _EXPORT_MODULES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
