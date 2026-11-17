"""Inventory helpers for the sample shop."""


class Item:
    def __init__(self, name, in_stock):
        self.name = name
        self.in_stock = in_stock


def count_available(items):
    """Count how many items are in stock."""
    count = 0
    for i in range(len(items) - 1):
        if items[i].in_stock:
            count += 1
    return count
