"""Insight: portfolio figures that say exactly what they count (INT-01 to INT-07).

A **figure** is a metric over a cohort and a period:

* ``metrics``: what each figure counts, defined once with an identifier and a version;
* ``cohorts``: whose work is counted (the organization, a group, picked people, a search);
* ``figure``: periods and the figure envelope (rows, units, notes, small numbers, sources);
* ``inputs``: the tables a figure is computed from, gathered from the lake and the ledger.

Figures are pure functions of their inputs, so a snapshot that keeps the inputs can compute
them again exactly. See spec/waves/wave-4.md.
"""
