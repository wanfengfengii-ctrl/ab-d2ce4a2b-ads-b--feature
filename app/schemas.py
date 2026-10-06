"""Request/response schemas for the pair-decode endpoint."""
from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
)
from typing_extensions import TypedDict

MIN_PAIRS = 1
MAX_PAIRS = 200

#: Pair ids arrive as "编号"; accept numbers as well as strings.
PairId = Annotated[str, BeforeValidator(lambda v: v if isinstance(v, str) else str(v))]


class FrameIn(BaseModel):
    """One received frame: receive time in epoch milliseconds + 28 hex chars."""

    model_config = ConfigDict(extra="forbid")

    time_ms: int = Field(description="receive timestamp, epoch milliseconds")
    raw: str = Field(description="28 hexadecimal characters (112-bit Mode S frame)")


class PairIn(BaseModel):
    """A uniquely-numbered even/odd pair of ADS-B airborne position frames."""

    model_config = ConfigDict(extra="forbid")

    id: PairId = Field(min_length=1, max_length=64)
    frames: list[FrameIn] = Field(min_length=2, max_length=2)


class DecodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pairs: list[PairIn] = Field(min_length=MIN_PAIRS, max_length=MAX_PAIRS)
    include_altitude: bool = Field(
        default=False,
        alias="includeAltitude",
        description="also decode the altitude of the selected newer frame, "
                    "with an explicit vertical reference",
    )

    @field_validator("pairs")
    @classmethod
    def _unique_ids(cls, pairs: list[PairIn]) -> list[PairIn]:
        seen: set[str] = set()
        for pair in pairs:
            if pair.id in seen:
                raise ValueError(f"duplicate pair id: {pair.id!r}")
            seen.add(pair.id)
        return pairs


class Position(BaseModel):
    """Decoded position of the newer frame of the pair."""

    lat: float = Field(description="latitude in decimal degrees, 6 decimal places")
    lon: float = Field(description="longitude in decimal degrees, normalised to [-180, 180)")
    time_ms: int = Field(description="receive time of the frame the position was decoded from")
    icao: str = Field(description="24-bit ICAO address, uppercase hex")
    frame: Literal["even", "odd"] = Field(description="CPR flag of the newer frame")


class ErrorInfo(BaseModel):
    code: str = Field(description="stable machine-readable error code")
    message: str = Field(description="human-readable detail")


class Altitude(BaseModel):
    """Altitude of the same newer frame, with an explicit vertical datum."""

    reference: Literal["barometric", "gnss"] = Field(
        description="vertical reference: pressure altitude (TC 9-18) or GNSS "
                    "height above the ellipsoid (TC 20-22)"
    )
    unit: Literal["ft", "m"] = Field(description="unit of value")
    value: int = Field(description="altitude in `unit`; barometric may be negative")


class _PairResultRequired(TypedDict):
    id: str
    status: Literal["ok", "error"]


class PairResultOut(_PairResultRequired, total=False):
    """Serialized shape of a pair verdict; ``altitude`` only when requested."""

    position: Optional[Position]
    error: Optional[ErrorInfo]
    altitude: Optional[Altitude]


class PairResult(BaseModel):
    """Per-pair verdict; exactly one of ``position`` / ``error`` is set.

    ``altitude`` is only present when the request asked for it
    (``includeAltitude``) and the pair decoded successfully; when absent the
    key is omitted entirely so the default response contract is unchanged.
    """

    id: str
    status: Literal["ok", "error"]
    position: Optional[Position] = None
    error: Optional[ErrorInfo] = None
    altitude: Optional[Altitude] = None

    @model_serializer(mode="plain", return_type=PairResultOut)
    def _omit_altitude_unless_present(self) -> PairResultOut:
        out = PairResultOut(
            id=self.id, status=self.status, position=self.position, error=self.error
        )
        if self.altitude is not None:
            out["altitude"] = self.altitude
        return out


class DecodeResponse(BaseModel):
    results: list[PairResult]
