"""Quality checks: the caveat engine that decides PASS / WARN / BLOCK for an output.

See :mod:`cgjrkit.qa.caveat_check` and ``docs/caveat-rules.md``.
"""

from cgjrkit.qa.caveat_check import CaveatReport, list_rules, register_rule, run_caveat_check

__all__ = ["CaveatReport", "list_rules", "register_rule", "run_caveat_check"]
