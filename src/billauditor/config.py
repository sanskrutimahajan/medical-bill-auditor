"""Settings, read from environment variables or ``.env`` (prefix ``BILLAUDITOR_``).

The defaults are local and free: data on disk, traces on disk, and a local Ollama model.
Nothing here points at a billable service.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BILLAUDITOR_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    runs_dir: Path = Path("runs")
    # Long inputs and outputs are cut to this many characters in traces, to keep them readable.
    trace_max_chars: int = 4000

    # LLM settings (used from Phase 4)
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "qwen2.5:7b-instruct"
    llm_temperature: float = 0.0

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def manifest_path(self) -> Path:
        return self.raw_dir / "MANIFEST.tsv"

    @property
    def reference_db(self) -> Path:
        return self.data_dir / "processed" / "reference.duckdb"
