"""Scrape job configuration and discovered target state."""

from dataclasses import dataclass
from typing import Literal, Mapping


@dataclass(frozen=True, slots=True)
class ScrapeConfig:
    """One loaded scrape job. Missing timing fields mean the collector supplied no value."""

    job_name: str
    scrape_interval: str | None = None
    scrape_timeout: str | None = None
    metrics_path: str | None = None
    scheme: str | None = None


@dataclass(frozen=True, slots=True)
class ScrapeTarget:
    """One discovered target. Dropped targets can have only discovery labels."""

    state: Literal["active", "dropped"]
    discovered_labels: Mapping[str, str] | None = None
    labels: Mapping[str, str] | None = None
    scrape_pool: str | None = None
    scrape_url: str | None = None
    health: str | None = None
    last_error: str | None = None
    last_scrape: str | None = None
    last_scrape_duration: float | None = None  # seconds
    scrape_interval: str | None = None
    scrape_timeout: str | None = None
