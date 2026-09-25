"""Role catalog used by the local runtime's validated module gateway."""

from dataclasses import dataclass

from rahasya.brain.settings import AgentRole, BrainSettings


@dataclass(frozen=True)
class AgentDefinition:
    role: AgentRole
    assignment: str
    modules: tuple[str, ...] = ()


AGENTS = (
    AgentDefinition(
        AgentRole.COORDINATOR,
        "Assign bounded questions, prioritize evidence gaps, and recommend when to stop.",
    ),
    AgentDefinition(
        AgentRole.IDENTITY,
        "Interpret identifier lookups; distinguish registration signals from supported profile links.",
        ("LookupByEmail", "LookupByPhone", "GravatarLookup", "GitHubEmailSearch", "HunterIOReverse"),
    ),
    AgentDefinition(
        AgentRole.SOCIAL,
        "Select profile checks for supported handles; return candidate associations with evidence.",
        ("Sherlock", "Maigret", "WhatsMyName", "GitHubProfileScrape", "LiveProbe"),
    ),
    AgentDefinition(
        AgentRole.EXPOSURE,
        "Interpret exposure records, deduplicate findings, and identify exposed data categories.",
        ("HIBP", "IntelligenceX", "LeakLookup"),
    ),
    AgentDefinition(
        AgentRole.DARKWEB,
        "Review relevant mentions from enabled sources; distinguish ambiguity from source failure.",
        ("Ahmia", "OnionSearch"),
    ),
    AgentDefinition(
        AgentRole.MEDIA_ARCHIVE,
        "Interpret metadata, image similarity, and archive references without asserting identity from similarity.",
        ("ExifData", "ImageHash", "WaybackMachine"),
    ),
    AgentDefinition(
        AgentRole.REVIEWER,
        "Assess cited evidence and contradictions; propose claim status without directly changing ground truth.",
    ),
    AgentDefinition(
        AgentRole.REPORTER,
        "Explain reviewed findings, computed scores, coverage, and uncertainty with evidence references.",
    ),
)


def describe_agents(settings: BrainSettings) -> list[dict]:
    """Return configuration metadata without importing or running OSINT tools."""
    return [
        {
            "role": agent.role.value,
            "model": settings.models.for_role(agent.role),
            "assignment": agent.assignment,
            "planned_modules": list(agent.modules),
        }
        for agent in AGENTS
    ]
