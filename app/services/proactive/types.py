"""Policy DTOs. Times are UTC minutes; evaluation truth has no field here."""
from __future__ import annotations

from dataclasses import dataclass, field

ALGORITHM = "C01-F1.0"
PROTOCOL = "C01-E1.0"
IMPLEMENTATION = "vietsage-proactive-1"
NEEDS = ("meal_coordination", "clothes_changing_drying", "rest_opportunity", "transport_coordination")
FEATURES = ("current_activity", "last_activity", "next_activity", "trip_phase", "local_time_block",
            "elapsed_time_bin", "free_time_bin", "coarse_location", "weather", "budget",
            "accessibility", "interruption_tolerance") + tuple(f"{n}_status" for n in NEEDS)
ANSWERS = ("yes", "no", "uncertain", "abort")
EPS = 1e-12


@dataclass(frozen=True)
class Event:
    event_id: str
    known_at: float
    valid_at: float
    kind: str
    authorized: bool
    revision: int = 0
    commitment: bool = False
    features: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Episode:
    id: str
    need: int
    anchor: str
    status: str = "open"


@dataclass(frozen=True)
class Service:
    id: str
    need: int
    episode: str
    text: str
    benefit: float
    harm: float
    base_cost: float
    opens: float | None
    closes: float | None
    delay: float | None
    duration: float | None
    captured_at: float | None
    ttl: float | None
    verified: bool = False
    compatible: bool | None = None
    authorized: bool = False
    price: float | None = None
    currency: str | None = None
    evidence_id: str | None = None


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    base_cost: float
    max_delay: float
    # One row per joint assignment, four normalized answer probabilities.
    likelihoods: tuple[tuple[float, ...], ...] | None
    service_ids: tuple[str, ...]


@dataclass(frozen=True)
class Message:
    id: str
    time: float
    kind: str
    episode: str | None = None
    transaction: str | None = None


@dataclass(frozen=True)
class Config:
    model_id: str
    config_hash: str
    needs: int
    horizon: float
    history_window: float
    recency_half_life: float
    burden_window: float
    burden_half_life: float
    cooldown: float
    cap: int
    kappa: float
    tau: float


@dataclass(frozen=True)
class Snapshot:
    time: float
    landmark: float
    features: dict[str, str]
    missing: tuple[str, ...]
    episodes: tuple[Episode, ...]
    services: tuple[Service, ...]
    questions: tuple[Question, ...]
    ledger: tuple[Message, ...] | None
    consent: bool | None
    attention: bool | None
    next_commitment: float | None
    pending: bool = False
    suppressed: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    recency: dict[str, float] = field(default_factory=dict)
    context_hash: str = ""


@dataclass
class Decision:
    initial_action: str = "WAIT"
    candidate_id: str | None = None
    target_episode_id: str | None = None
    target_need: str | None = None
    net_value: float = 0.0
    content_score: float | None = None
    confidence: None = None
    reason_codes: list[str] = field(default_factory=list)
    need_probabilities: dict[str, float] = field(default_factory=dict)
    content_values: dict[str, float] = field(default_factory=dict)
    action_values: dict[str, float] = field(default_factory=dict)
    answer_probabilities: dict[str, dict[str, float]] = field(default_factory=dict)
    excluded_candidates: list[dict] = field(default_factory=list)
    intervention_costs: dict[str, float] = field(default_factory=dict)
    thresholds: dict[str, float | None] = field(default_factory=dict)
    burden: float = 0.0
    count: int = 0
    algorithm_version: str = ALGORITHM
    model_id: str | None = None
    config_hash: str | None = None

    @property
    def should_intervene(self) -> bool:
        return self.initial_action != "WAIT"
