import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter(tags=["mock"])
MOCKS_DIR = Path(__file__).resolve().parent.parent / "mocks"


def read_mock(filename: str) -> Any:
    with (MOCKS_DIR / filename).open(encoding="utf-8") as mock_file:
        return json.load(mock_file)


@router.get("/jobs")
def get_jobs() -> Any:
    return read_mock("jobs.json")


@router.get("/conversations")
def get_conversations() -> Any:
    return read_mock("conversations.json")


@router.get("/payments")
def get_payments() -> Any:
    return read_mock("payments.json")
