"""Integer paise everywhere. No floats touch money."""


def require_paise(value, name: str = "amount") -> int:
    """Return ``value`` if it is a non-negative int (bool excluded); raise ``ValueError`` otherwise."""
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer number of paise, got {value!r}")
    return value


def rupees(paise: int) -> str:
    """Format paise for humans: ``rupees(249900) == 'INR 2,499.00'``."""
    sign = "-" if paise < 0 else ""
    whole, frac = divmod(abs(paise), 100)
    return f"{sign}INR {whole:,}.{frac:02d}"
