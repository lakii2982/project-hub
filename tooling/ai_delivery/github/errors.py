"""Public, redacted failures; never attach gh stderr or credentials."""


class Refused(ValueError):
    pass


class Conflict(Refused):
    pass
