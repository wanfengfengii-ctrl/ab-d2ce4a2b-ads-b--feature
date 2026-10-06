"""Per-pair adjudication: validate both frames, then globally decode CPR."""
from __future__ import annotations

from .adsb import PositionFrame, parse_position_frame
from .altitude import decode_altitude
from .cpr import global_decode
from .errors import DecodeError
from .schemas import (
    Altitude,
    ErrorInfo,
    PairIn,
    PairResult,
    Position,
    PositionWithAltitude,
)

#: Even/odd pairs must be received no more than 10 seconds apart.
MAX_GAP_MS = 10_000


def process_pair(pair: PairIn, include_altitude: bool = False) -> PairResult:
    """Adjudicate one pair; a failure here never affects other pairs."""
    try:
        frames = [
            parse_position_frame(frame.raw, frame.time_ms, index=i)
            for i, frame in enumerate(pair.frames)
        ]
        first, second = frames

        if first.icao != second.icao:
            raise DecodeError(
                "ICAO_MISMATCH",
                f"ICAO addresses differ: {first.icao} vs {second.icao}",
            )
        if first.odd == second.odd:
            flag = "odd" if first.odd else "even"
            raise DecodeError(
                "SAME_CPR_FLAG",
                f"both frames carry the {flag} CPR flag; one even and one odd are required",
            )
        gap_ms = abs(first.time_ms - second.time_ms)
        if gap_ms > MAX_GAP_MS:
            raise DecodeError(
                "TIME_GAP_EXCEEDED",
                f"frames are {gap_ms} ms apart; the limit is {MAX_GAP_MS} ms",
            )

        even, odd = (second, first) if first.odd else (first, second)
        lat, lon = global_decode(even, odd)
        newer = _newer(even, odd)

        position_kwargs = dict(
            lat=round(lat, 6),
            lon=round(lon, 6),
            time_ms=newer.time_ms,
            icao=newer.icao,
            frame="odd" if newer.odd else "even",
        )
        if include_altitude:
            # The height comes from the exact same frame the horizontal fix
            # belongs to, so the fusion layer never mixes a barometric with
            # a geometric height, or heights from two different messages.
            height = decode_altitude(newer)
            position = PositionWithAltitude(
                altitude=Altitude(
                    value=height.value,
                    unit=height.unit,
                    reference=height.reference,
                ),
                **position_kwargs,
            )
        else:
            position = Position(**position_kwargs)

        return PairResult(id=pair.id, status="ok", position=position)
    except DecodeError as exc:
        return PairResult(
            id=pair.id,
            status="error",
            error=ErrorInfo(code=exc.code, message=exc.message),
        )
    except Exception as exc:  # defensive: one pair must never sink the batch
        return PairResult(
            id=pair.id,
            status="error",
            error=ErrorInfo(code="INTERNAL_ERROR", message=f"unexpected failure: {exc}"),
        )


def _newer(even: PositionFrame, odd: PositionFrame) -> PositionFrame:
    """The frame the reported position belongs to (ties resolve to even)."""
    return odd if odd.time_ms > even.time_ms else even
