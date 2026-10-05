"""FM-Verify: exogenous verification for failure-driven improvement.

The design constraint that shapes this package: a verifier that descends from
the model under test is not evidence. Everything here is built so the oracle,
the replay, and the acceptance decision are independent of the system that
produced the candidate.
"""

__version__ = "0.1.0"
