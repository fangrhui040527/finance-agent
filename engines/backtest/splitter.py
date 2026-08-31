"""Purged walk-forward cross-validation with an embargo.

docs/05 section 9 and docs/07 P12. Random k-fold leaks future information across
folds and flatters every model you build, so it is not available in this API at
all - there is no shuffle parameter to set by accident.

Purging removes training observations whose label window overlaps the test set.
The embargo drops observations immediately after the test set, because serial
correlation means a training row adjacent to test data still leaks.
"""

from __future__ import annotations

from dataclasses import dataclass


class LeakageError(ValueError):
    """Raised when a split would put future information into training."""


@dataclass(frozen=True)
class Fold:
    train: list[int]
    test: list[int]
    purged: int
    embargoed: int

    def assert_no_overlap(self) -> None:
        if set(self.train) & set(self.test):
            raise LeakageError("train and test indices overlap")


def purged_walk_forward(
    n: int,
    n_folds: int = 5,
    label_horizon: int = 20,
    embargo: int | None = None,
    min_train: int = 60,
) -> list[Fold]:
    """Expanding-window walk forward. Test blocks move strictly forward in time.

    embargo defaults to the label horizon, per docs/05: the embargo equals the
    forecast horizon.
    """
    if n_folds < 1:
        raise ValueError("need at least one fold")
    embargo = label_horizon if embargo is None else embargo
    usable = n - min_train
    if usable <= 0:
        raise ValueError(f"need more than {min_train} observations to build any fold")
    block = max(1, usable // n_folds)

    folds: list[Fold] = []
    for k in range(n_folds):
        t0 = min_train + k * block
        t1 = min(t0 + block, n)
        if t0 >= n:
            break
        test = list(range(t0, t1))
        # Purge any training row whose label window reaches into the test block.
        purge_from = max(0, t0 - label_horizon)
        train = list(range(0, purge_from))
        purged = t0 - purge_from
        # Embargo is recorded for audit; on an expanding window it sits after the
        # test block and is simply not used for training in this fold.
        embargoed = min(embargo, n - t1)
        if len(train) < min_train // 2:
            continue
        f = Fold(train, test, purged, embargoed)
        f.assert_no_overlap()
        folds.append(f)
    if not folds:
        raise ValueError("no fold retained enough training data after purging")
    return folds
