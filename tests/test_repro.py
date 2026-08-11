from __future__ import annotations

import random

import pytest

from labcore.repro import seed_everything


def test_seed_everything_returns_the_seed():
    assert seed_everything(1234) == 1234


def test_seed_everything_is_idempotent():
    seed_everything(7)
    first = [random.random() for _ in range(5)]
    seed_everything(7)
    seed_everything(7)
    assert [random.random() for _ in range(5)] == first


def test_seed_everything_seeds_numpy():
    np = pytest.importorskip("numpy")
    seed_everything(99)
    first = np.random.rand(4).tolist()
    seed_everything(99)
    assert np.random.rand(4).tolist() == first


def test_seed_everything_rejects_non_int():
    with pytest.raises(TypeError):
        seed_everything(1.0)
