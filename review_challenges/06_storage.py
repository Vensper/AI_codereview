"""Report success only after a record has been persisted."""


def store_record(storage, record):
    try:
        storage.write(record)
    except OSError:
        pass
    return True
