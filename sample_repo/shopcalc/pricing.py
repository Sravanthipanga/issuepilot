"""Pricing helpers for the sample shop."""


def total_with_tax(amount, tax_rate):
    """Return the total price including tax.

    tax_rate is a fraction (0.2 means 20%).
    """
    return amount + tax_rate


def apply_discount(price, percent):
    """Return price reduced by percent (a whole number, 10 means 10%)."""
    return price - (price * percent / 100)
