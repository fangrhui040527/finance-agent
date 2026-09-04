"""The paper book: a hypothetical USD ledger marked from cached daily bars.

docs/22. This package is arithmetic over prices the collector already cached.
It records target weights, applies them to a make-believe account at the next
cached open with the real fee cards, the recorded FX rate and a stated
slippage, and marks the result to market. It sends nothing anywhere, imports
nothing from `core/broker`, and cannot be pointed at a gateway: there is no
field for one. `tests/test_paper_boundary.py` fails if that import appears.

Two books share one file: the *decided* book, whose targets a person or the
nightly routine records inside code-enforced caps, and the *control* book,
which holds an equal-lot spread of whatever is fundable and is rebalanced
monthly. The question the record answers after thirteen weeks is not "how much
did it make" but "did the deciding add anything the passive book did not".
"""
