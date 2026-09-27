"""Exceptions raised across the DemoService components."""



class DemoArtifactBindError(RuntimeError):
    pass


class DemoDispatchError(RuntimeError):
    pass


class DemoGoneError(LookupError):
    """The demo (or its job) was deleted while this operation was in flight.

    Deliberately not a ValueError: callers map ValueError to 400/409, and a
    row that no longer exists is a 404 at the API and a quiet no-op in the
    worker (docs/data_deletion_v1.md).
    """


class ReplayBlobUnavailableError(ValueError):
    """The replay a render needs cannot be read.

    A ValueError subclass so the existing callers that catch ValueError keep
    working, but a distinct type so the API can answer 409 without deciding the
    status by substring-matching the message -- which silently reclassified
    this error to 400 the moment the wording was corrected.
    """
