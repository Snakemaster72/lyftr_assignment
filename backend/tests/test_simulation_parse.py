import pytest

from app.llm import SimulationHeaderError, parse_simulation_header

KEYS = ["key_1", "key_2"]


@pytest.mark.parametrize(
    ("header", "plan"),
    [
        ("key_1=429", {"key_1": 429}),
        ("key_1=429,key_2=500", {"key_1": 429, "key_2": 500}),
        ("*=429", {"key_1": 429, "key_2": 429}),
        ("*=429,key_2=503", {"key_1": 429, "key_2": 503}),  # specific overrides *
        ("key_2=503,*=429", {"key_1": 429, "key_2": 503}),  # regardless of order
        (" key_1 = 502 , ", {"key_1": 502}),
        ("key_3=429", {}),  # unknown ids ignored
    ],
)
def test_parse_valid(header, plan):
    assert parse_simulation_header(header, KEYS) == plan


@pytest.mark.parametrize("header", ["bogus", "key_1", "key_1=", "=429", "key_1=abc", "key_1=404", "key_1=-1"])
def test_parse_invalid(header):
    with pytest.raises(SimulationHeaderError):
        parse_simulation_header(header, KEYS)
