"""Run one free-form Writer draft from an existing Reader task card."""

from hashlib import sha256
from importlib.resources import files


PROMPT_HASHES = {
    'NORMAL': '3f9ae5292bd68d8e7f7d39dadd01c63ce3b3c902a9a65fee78a8d42bbe37a8db',
    'INTRO': 'c66e26bd9627ce90bb5dbba9aee186f015aba847f1ab1bdbf0be9e043366e47f',
    'FINALE': '5e3a072c3ff6e5c7b20a6a52ebaa37f18bf2c8e5ded3011e0ff0cf19eb8b2a63',
    'ANSWER': 'c3093b4d776c4b3bae693225ecfce9c7fe3dd24ad8ff60f26fcc93c0d3085fca',
}


def prompt(mode):
    expected = PROMPT_HASHES[mode]
    raw = files(__package__).joinpath(f'{mode}.txt').read_bytes()
    if sha256(raw).hexdigest() != expected:
        raise ValueError(f'{mode} Writer prompt differs from the frozen file')
    return raw
