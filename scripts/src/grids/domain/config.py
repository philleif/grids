"""Domain configuration -- Pydantic models for domain.yaml parsing and validation."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class PDFSource(BaseModel):
    path: str
    collection: str
    description: str = ""


class WebResearchConfig(BaseModel):
    enabled: bool = True
    seed_queries: list[str] = Field(default_factory=list)
    max_results_per_query: int = 10


class SourcesConfig(BaseModel):
    pdfs: list[PDFSource] = Field(default_factory=list)
    web_research: WebResearchConfig = Field(default_factory=WebResearchConfig)


class MasterConfig(BaseModel):
    name: str
    description: str
    system_prompt_file: str | None = None
    knowledge_collections: list[str] = Field(default_factory=list)


class SubAgentConfig(BaseModel):
    name: str
    aspect: str
    concepts: list[str] = Field(default_factory=list)
    strictness: float = Field(default=0.8, ge=0.0, le=1.0)


class RulesConfig(BaseModel):
    max_iterations: int = 3
    approval_threshold: float = Field(default=75, ge=0.0, le=100.0)
    master_veto_threshold: float = Field(default=50, ge=0.0, le=100.0)


class DomainInfo(BaseModel):
    name: str
    description: str


class DomainConfig(BaseModel):
    domain: DomainInfo
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    master: MasterConfig
    sub_agents: list[SubAgentConfig] = Field(default_factory=list)
    rules: RulesConfig = Field(default_factory=RulesConfig)

    @property
    def all_collections(self) -> list[str]:
        collections = list(self.master.knowledge_collections)
        for pdf in self.sources.pdfs:
            if pdf.collection not in collections:
                collections.append(pdf.collection)
        if self.sources.web_research.enabled:
            web_coll = f"{self.domain.name}-web"
            if web_coll not in collections:
                collections.append(web_coll)
        return collections


def load_domain(path: str | Path) -> DomainConfig:
    """Load and validate a domain config from a YAML file."""
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return DomainConfig(**raw)
