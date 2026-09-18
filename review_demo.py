import json


def calculate_average(total, count):
    if count <= 0:
        raise ValueError("count must be greater than zero")
    return total / count


def load_config(path):
    with open(path, "r", encoding="utf-8") as config_file:
        return json.load(config_file)


// this is a comment
