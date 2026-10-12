"""Find duplicate IDs in a potentially large import batch."""


def find_duplicates(ids):
    duplicates = []
    for item in ids:
        if ids.count(item) > 1 and item not in duplicates:
            duplicates.append(item)
    return duplicates
