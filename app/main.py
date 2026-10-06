"""HTTP API: health check plus the paired ADS-B position decode endpoint."""
from __future__ import annotations

from fastapi import FastAPI

from .decoder import process_pair
from .schemas import DecodeRequest, DecodeResponse

app = FastAPI(
    title="ADS-B Paired Position Decoder",
    version="1.0.0",
    summary="Adjudicates paired DF17 airborne-position frames and globally "
            "decodes CPR positions before radar fusion.",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/adsb/positions/decode", response_model=DecodeResponse)
def decode_positions(request: DecodeRequest) -> DecodeResponse:
    """Adjudicate 1-200 uniquely-numbered even/odd frame pairs.

    Every pair is processed independently: a bad pair yields a stable error
    code under its own id and never shadows the healthy pairs in the batch.
    With ``includeAltitude`` the altitude of the same newer frame is decoded
    as well, tagged with its vertical reference (barometric ft / GNSS m).
    """
    return DecodeResponse(
        results=[
            process_pair(pair, include_altitude=request.include_altitude)
            for pair in request.pairs
        ]
    )
