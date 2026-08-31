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


def combinatorial_purged_splits(
    n_obs: int, n_blocks: int = 6, test_blocks: int = 2, embargo: int = 0
) -> list[Fold]:
    """Every C(n_blocks, test_blocks) block combination as a purged fold.

    One walk-forward path is one draw; CPCV scores a strategy across MANY
    train/test paths, which is what makes PBO computable. Purging and embargo
    follow the same rule as purged_walk_forward: train rows adjacent to a test
    block are dropped so overlapping information cannot leak across the cut.
    """
    from itertools import combinations

    if n_blocks < 2 or not 0 < test_blocks < n_blocks:
        raise ValueError("need n_blocks >= 2 and 0 < test_blocks < n_blocks")
    bounds = [round(i * n_obs / n_blocks) for i in range(n_blocks + 1)]
    blocks = [list(range(bounds[i], bounds[i + 1])) for i in range(n_blocks)]
    folds: list[Fold] = []
    for combo in combinations(range(n_blocks), test_blocks):
        test = [i for b in combo for i in blocks[b]]
        train: list[int] = []
        dropped = 0
        for b in range(n_blocks):
            if b in combo:
                continue
            for i in blocks[b]:
                # purge + embargo around every test block boundary
                if any(blocks[c][0] - embargo <= i <= blocks[c][-1] + embargo for c in combo):
                    dropped += 1
                    continue
                train.append(i)
        folds.append(Fold(list(train), list(test), purged=dropped, embargoed=embargo))
    return folds


@dataclass(frozen=True)
class LeakageReport:
    contaminated: tuple[tuple[int, int], ...]  # (fold_index, train_row)

    @property
    def clean(self) -> bool:
        return not self.contaminated


def detect_boundary_leakage(folds: list[Fold], horizon: int) -> LeakageReport:
    """Audit folds AFTER construction: does any train row sit within `horizon`
    rows of a test row? A splitter bug that leaks one overlapping label makes
    every score built on it quietly optimistic; this is the check that refuses
    to take the splitter's word for it."""
    bad: list[tuple[int, int]] = []
    for f_idx, fold in enumerate(folds):
        test = set(fold.test)
        for row in fold.train:
            if any(abs(row - t) <= horizon for t in test):
                bad.append((f_idx, row))
    return LeakageReport(tuple(bad))
