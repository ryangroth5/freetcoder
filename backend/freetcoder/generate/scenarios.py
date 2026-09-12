"""Concrete settings to build a question around.

Asking for "hash maps, medium, leetcode" is a search key into memorised
problems: four such prompts across three models all returned the same one, and
one model emitted a URL slug as its title. A setting the model has to invent
around displaces that key, and costs nothing -- no call, no tokens, just a line
in the prompt.

Deliberately mundane and specific. The point is not the domain but that it is
not "arrays".
"""

from __future__ import annotations

import random

SCENARIOS: tuple[str, ...] = (
    "a tide gauge logging sea levels",
    "a warehouse picker walking one aisle",
    "a nurse rota of shift swaps",
    "a bakery tracking loaves through a day",
    "a bike-share dock counting departures",
    "a seismograph recording tremor amplitudes",
    "a greenhouse logging overnight temperatures",
    "a ferry timetable with sailings and delays",
    "a library tracking loans and returns",
    "a call centre logging queue lengths",
    "a beekeeper weighing hives through a season",
    "a locksmith recording key cuttings",
    "a car park barrier counting entries and exits",
    "a river gauge recording flood peaks",
    "a printing press logging sheet misfeeds",
    "an orchard recording fruit picked per tree",
)


def pick(rng: random.Random | None = None) -> str:
    return (rng or random).choice(SCENARIOS)


def brief(scenario: str) -> str:
    """The instruction that goes with a scenario, wherever it is used."""
    return (
        f"### Setting\n\n"
        f"Build this question around {scenario}.\n\n"
        "The setting is not decoration: the quantities, the question asked and "
        "the examples should belong to that world. Do not reproduce a published "
        "problem -- a familiar technique is expected, a familiar problem is not. "
        "A question that could be restated as a well-known puzzle with the nouns "
        "swapped has not used the setting."
    )
