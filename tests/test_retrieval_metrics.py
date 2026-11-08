import math

import pytest

from scripts.evaluate_retrieval import score_ranking


def test_page_ranking_metrics_account_for_order_missing_hits_and_short_results() -> None:
    relevant = {("a.pdf", 1), ("b.pdf", 1)}
    ranked = [("other.pdf", 1), ("a.pdf", 1), ("b.pdf", 1)]
    scores = score_ranking(ranked, relevant, 3)
    assert scores.precision == pytest.approx(2 / 3)
    assert scores.recall == 1
    assert scores.reciprocal_rank == 0.5
    assert scores.ndcg == pytest.approx((1 / math.log2(3) + 0.5) / (1 + 1 / math.log2(3)))
    short = score_ranking([("a.pdf", 1)], relevant, 3)
    assert short.precision == pytest.approx(1 / 3)
    assert short.recall == 0.5
    assert score_ranking([], relevant, 3).ndcg == 0
    assert score_ranking(sorted(relevant), relevant, 2).ndcg == 1
    with pytest.raises(ValueError, match="deduplicated"):
        score_ranking([("a.pdf", 1), ("a.pdf", 1)], relevant, 3)
