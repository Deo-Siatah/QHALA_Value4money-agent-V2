# These tests cover the arithmetic in the stability report, not the agent itself. The harness needs
# a live model and a live database and takes minutes, so it is run by hand; the way it decides
# whether the agent is steady has to be right without that.

from evals.stability import median_spread, summarise, within_band, TOLERANCE_PCT


def run(median=None, positions=None, pool=12):
    """One run's worth of numbers, as the harness collects them."""
    return {"run_id": "run-1", "median_kes": median,
            "pool_size": pool, "confidence": "high" if median else "insufficient",
            "bids": [{"bidder": bidder, "position": position}
                     for bidder, position in (positions or {}).items()]}


def test_one_median_has_nothing_to_compare_against():
    assert within_band([2_000_000.0]) is True


def test_runs_that_agree_are_steady():
    assert within_band([2_000_000.0, 2_050_000.0, 2_100_000.0]) is True


def test_runs_that_disagree_are_not_steady():
    """The October problem: medians from 1.94M to 2.84M over the same three bids."""
    assert within_band([1_940_000.0, 2_400_000.0, 2_840_000.0]) is False


def test_the_band_is_measured_against_the_middle_not_the_extremes():
    """One wild run must not be able to widen the band by being the yardstick."""
    middles = [2_000_000.0, 2_050_000.0, 2_100_000.0]

    assert within_band(middles + [9_000_000.0]) is False
    assert within_band(middles + [2_100_000.0]) is True


def test_the_tolerance_is_a_setting_not_a_guess():
    medians = [2_000_000.0, 2_200_000.0]   # 4.8% either side of the middle

    assert within_band(medians, tolerance_pct=2.0) is False
    assert within_band(medians, tolerance_pct=5.0) is True


def test_the_spread_says_how_far_apart_the_medians_were():
    spread = median_spread([2_000_000.0, 2_500_000.0])

    assert spread["lowest"] == 2_000_000.0
    assert spread["highest"] == 2_500_000.0
    assert spread["spread_pct"] == 25.0


def test_no_medians_at_all_is_reported_rather_than_crashing():
    assert median_spread([])["n"] == 0
    assert median_spread([])["spread_pct"] is None


def test_a_bid_that_changes_position_between_runs_is_named():
    """Apex came out within_range in some runs and far_above in others, on identical bids."""
    report = summarise([
        run(2_000_000.0, {"Apex": "within_range", "Bora": "far_above"}),
        run(2_600_000.0, {"Apex": "far_above", "Bora": "far_above"}),
    ])

    assert report["unstable_bids"] == {"Apex": ["far_above", "within_range"]}
    assert report["stable"] is False


def test_a_report_where_nothing_moved_is_reported_as_steady():
    report = summarise([
        run(2_000_000.0, {"Apex": "within_range", "Bora": "far_above"}),
        run(2_050_000.0, {"Apex": "within_range", "Bora": "far_above"}),
    ])

    assert report["stable"] is True
    assert report["unstable_bids"] == {}
    assert report["pool_sizes"] == [12, 12]


def test_runs_with_no_benchmark_are_not_called_steady():
    """A run that found nothing has no median, and must not be reported as agreeing with itself."""
    report = summarise([run(None), run(None)])

    assert report["medians"] == []
    assert report["stable"] is False


def test_the_default_tolerance_is_tighter_than_the_failure_it_catches():
    """The observed spread was about 19% either side of the middle, and it changed positions."""
    assert TOLERANCE_PCT < 19.0