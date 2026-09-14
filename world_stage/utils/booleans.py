from werkzeug.datastructures import MultiDict


def parse_bool(value: str) -> bool | None:
    value = value.casefold()
    if value in ("1", "on", "true", "yes"):
        return True
    if value in ("0", "off", "false", "no"):
        return False
    return None


def query_bool(args: MultiDict, name: str, default: bool) -> bool:
    for key, value in args.items(multi=True):
        if key.casefold() == name.casefold() and (parsed := parse_bool(value)) is not None:
            default = parsed
    return default
