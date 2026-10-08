import pytest

from buttercast.models import prepare


def test_provided_weekly_price_parser_uses_only_price_and_preserves_unknown_provenance():
    from buttercast.data import parse_weekly_prices

    rows = parse_weekly_prices("week_ending,butter_price_usd_lb,butter_sales_lb,usdkrw\n"
                               "2026-09-19,1.5,,junk\n2026-09-26,1.6,999,junk\n")
    assert rows[0]["midpoint"] == 1.5
    assert rows[0]["unit"] == "USD/lb"
    assert rows[0]["synthetic"] is None
    assert rows[0]["publication_verified"] is False
    assert "usdkrw" not in rows[0]
    assert "butter_sales_lb" not in rows[0]
    with pytest.raises(ValueError, match="누락"):
        parse_weekly_prices("week_ending,butter_price_usd_lb\n2026-09-12,1.5\n2026-09-26,1.6\n")
    with pytest.raises(ValueError, match="가격"):
        parse_weekly_prices("week_ending,butter_price_usd_lb\n2026-09-12,nan\n")


def test_bundled_provided_prices_allow_weekly_training_without_supplements():
    from buttercast.data import AUDIT, parse_weekly_prices

    rows = parse_weekly_prices((AUDIT / "butter_weekly_train.csv").read_text())
    assert len(rows) == 761
    splits, _ = prepare(rows, interval_days=7)
    assert len(splits["train"]) > 400
    assert splits["validation"][-1]["actual_known_at"] < splits["test"][0]["issued_at"]
