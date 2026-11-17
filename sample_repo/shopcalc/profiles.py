"""Customer profile helpers for the sample shop."""


def display_name(user):
    """Return the user's display name, e.g. 'Ada Lovelace'."""
    return user["first"].strip() + " " + user["last"].strip()


def initials(user):
    """Return the user's initials, e.g. 'AL'."""
    return (user["first"][0] + user["last"][0]).upper()
