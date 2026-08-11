"""Single-call RNG seeding for a whole pipeline.

Lifted from scAnalysis/lib/reproducibility.py. Audit fix C2/M5 (2026-05-22):
before that module existed, scVI/scANVI training across 40b, 40b3, 40d, 50b,
and 60_trajectory had NO seeds set anywhere — latent representations,
predicted cell-types, Bayesian DE LFCs, and trajectory dynamics all changed
run-to-run. The model_cache schema_hash matched (same shape x HVG x hparams)
but loaded a different trained checkpoint than the original save — a silent
reproducibility failure.

USAGE  (in every script that does ANY stochastic operation)

    from labcore.repro import seed_everything
    seed_everything(1234)

That's it. The function is idempotent (safe to call multiple times) and
deterministic (same seed -> same hash of subsequently-drawn samples).

WHAT IT SEEDS

    - Python's built-in random
    - NumPy (np.random.seed + np.random.default_rng + Generator state)
    - PyTorch (CPU + every visible CUDA device + cudnn determinism)
    - scvi-tools (scvi.settings.seed; affects scVI/scANVI/totalVI/...)
    - scVelo (scv.settings.seed if scvelo is installed)
    - PYTHONHASHSEED via os.environ (only effective if set BEFORE process
      start; we set it for forked subprocesses)

LIMITATIONS

    - CUDA non-determinism in ops like atomicAdd / scatter can still
      cause sub-bit-exact diffs. We set torch.use_deterministic_algorithms(True)
      where available, but some scvi-tools ops fall through. For
      paper-quality bit-exact reruns, fix this AND use CPU.
    - Setting PYTHONHASHSEED here is best-effort; for guaranteed dict-
      iteration determinism, set it in the sbatch script wrapper.
"""

from __future__ import annotations

import contextlib
import os
import random

# Module-level NumPy Generator initialised lazily on first seed_everything.
# Importers can do ``from labcore.repro import _rng`` if they want a
# project-shared Generator (rare; np.random.* globals are fine for most).
_rng: object | None = None


def _seed_numpy(seed: int) -> None:
    """Seed NumPy's legacy globals and refresh the shared Generator."""
    try:
        import numpy as np

        np.random.seed(seed)
        # The shared Generator is the whole point of the module-level name, so
        # the global write is deliberate rather than accidental state.
        global _rng  # noqa: PLW0603
        _rng = np.random.default_rng(seed)
    except ImportError:
        pass


def _seed_torch(seed: int, strict_cuda: bool) -> None:
    """Seed PyTorch CPU/CUDA RNGs and pin cudnn to deterministic kernels."""
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        # Cheap and safe even without CUDA.
        with contextlib.suppress(AttributeError):
            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.benchmark = False
        if strict_cuda:
            os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
            with contextlib.suppress(AttributeError):
                torch.use_deterministic_algorithms(True)
    except ImportError:
        pass


def _seed_single_cell_stack(seed: int) -> None:
    """Seed scvi-tools and scVelo, whichever of them is installed."""
    # scvi-tools — settings.seed is the canonical entry point
    try:
        import scvi

        scvi.settings.seed = seed
    except ImportError:
        pass

    # scVelo — exposes settings.seed since 0.2.x
    try:
        import scvelo as scv

        if hasattr(scv.settings, "seed"):
            scv.settings.seed = seed
    except ImportError:
        pass


def seed_everything(seed: int, *, strict_cuda: bool = False) -> int:
    """Initialise every RNG in the project's stack with a single call.

    Args:
        seed: integer seed. Use the project's shared SEED constant unless you
            have a specific reason to override (e.g. multi-seed sensitivity
            analysis, in which case pass the variant seed explicitly).
        strict_cuda: when True, call ``torch.use_deterministic_algorithms(True)``
            and set CUBLAS_WORKSPACE_CONFIG for fully-bit-exact CUDA
            (slow). Default False (fast, near-deterministic).

    Returns:
        The seed value (echo, so calling code can log it).

    Raises:
        TypeError: If ``seed`` is not an int.
    """
    if not isinstance(seed, int):
        raise TypeError(f"seed must be int, got {type(seed).__name__}")

    os.environ.setdefault("PYTHONHASHSEED", str(seed))
    random.seed(seed)
    _seed_numpy(seed)
    _seed_torch(seed, strict_cuda)
    _seed_single_cell_stack(seed)
    return seed
