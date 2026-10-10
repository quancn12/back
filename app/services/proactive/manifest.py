"""Explicit artifacts only. Absent calibration is a readiness result, never a prior."""
import hashlib
import json
from functools import lru_cache
from dataclasses import asdict
from pathlib import Path

from .engine import validate_config
from .model import C01Model
from .types import ALGORITHM, PROTOCOL, Config

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "Proactive Recommendation Algorithm"
DOCUMENTS = ("00_AI_RESEARCH_INDEX.md", "04_IMPLEMENTATION_HANDOFF.md", "01_ALGORITHM_SPEC.md",
             "02_DATA_AND_INTEGRATION_CONTRACT.md", "03_EXPERIMENT_PROTOCOL.md")
REQUIRED = ("source_kind", "model_id", "parameters", "priors", "counts", "utility_provenance",
            "consent_protocol", "catalog_provenance", "feature_schema", "split_provenance")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def package_hashes():
    return {name: hashlib.sha256((PACKAGE / name).read_bytes()).hexdigest() for name in DOCUMENTS}


@lru_cache(maxsize=1)
def software_provenance():
    source = Path(__file__).parent
    return {"package_hashes": package_hashes(),
            "code_hash": digest({p.name: p.read_text(encoding="utf-8") for p in sorted(source.glob('*.py'))})}


def load_manifest(path):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = [name for name in REQUIRED if raw.get(name) is None]
    if missing:
        raise ValueError("BLOCKED_DATA_OR_CALIBRATION: " + ", ".join(missing))
    if raw.get("algorithm_version") != ALGORITHM or raw.get("protocol_version") != PROTOCOL:
        raise ValueError("Frozen version mismatch")
    if raw.get("package_hashes") != package_hashes():
        raise ValueError("Research package hash mismatch")
    cfg = Config(model_id=raw["model_id"], config_hash=digest(raw), **raw["parameters"])
    validate_config(cfg)
    model = C01Model(cfg.needs, raw["priors"], raw["counts"])
    model.validate()
    return raw, cfg, model


def readiness():
    archive = PACKAGE / "ORIGINAL_RESEARCH_ARCHIVE_2026-09-11.zip"
    return {"algorithm_version": ALGORITHM, "protocol_version": PROTOCOL,
            "status": "BLOCKED_DATA_OR_CALIBRATION", "package_hashes": package_hashes(),
            "archive_available": archive.exists(),
            "missing": ["approved CPT priors/counts and feature bins", "D3 common-scale utilities/costs",
                        "approved question likelihoods", "versioned journey events and episode anchors",
                        "verified catalog capability, bounds, constraints and TTL", "study governance/authorization",
                        "D1/D2 labels and grouped splits", "archived exact C02/B-H/B-E and result schemas"],
            "empirical_experiments_executed": False}
