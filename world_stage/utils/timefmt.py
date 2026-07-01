import datetime


def format_timedelta(td: datetime.timedelta | None) -> str | None:
    if td is None:
        return None
    days, seconds = td.days, td.seconds
    hours = seconds // 3600
    seconds %= 3600
    minutes = seconds // 60
    seconds %= 60

    values = []
    if days > 0:
        values.append(f"{days} days")
    if hours > 0:
        values.append(f"{hours} hours")
    if minutes > 0:
        values.append(f"{minutes} minutes")
    if seconds > 0:
        values.append(f"{seconds} seconds")
    return ", ".join(values)


def parse_timedelta(td: str) -> datetime.timedelta:
    """Parse a timedetla string in the format 'MM:SS' or 'HH:MM:SS'."""
    parts = list(map(int, td.split(":")))
    if len(parts) == 2:
        return datetime.timedelta(minutes=parts[0], seconds=parts[1])
    elif len(parts) == 3:
        return datetime.timedelta(hours=parts[0], minutes=parts[1], seconds=parts[2])
    else:
        raise ValueError("Invalid timedelta format. Use 'MM:SS' or 'HH:MM:SS'.")


def dt_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def format_seconds(seconds: int | None) -> str:
    """Format seconds into a string in the format M:SS."""
    if seconds is None or seconds <= 0:
        return ""
    minutes = seconds // 60
    seconds %= 60
    return f"{minutes}:{seconds:02}"


def parse_seconds(td: str | None) -> int | None:
    """Parse a string in the format M:SS into seconds."""
    if td is None:
        return None
    parts = list(map(int, td.split(":")))
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    else:
        raise ValueError("Invalid time format. Use 'M:SS'.")
