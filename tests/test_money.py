import pytest

from agentgate.money import require_paise, rupees


def test_rupees_formats_paise():
    assert rupees(249900) == "INR 2,499.00"
    assert rupees(5) == "INR 0.05"
    assert rupees(0) == "INR 0.00"
    assert rupees(-150) == "-INR 1.50"


def test_require_paise_accepts_non_negative_int():
    assert require_paise(0) == 0
    assert require_paise(100) == 100


@pytest.mark.parametrize("bad", [True, -1, 1.5, "100", None])
def test_require_paise_rejects_non_ints(bad):
    with pytest.raises(ValueError):
        require_paise(bad)
