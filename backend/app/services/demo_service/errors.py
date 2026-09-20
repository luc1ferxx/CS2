"""Exceptions raised across the DemoService components."""



class DemoArtifactBindError(RuntimeError):
    pass


class DemoDispatchError(RuntimeError):
    pass


class ReplayBlobUnavailableError(ValueError):
    """The replay a render needs cannot be read.

    A ValueError subclass so the existing callers that catch ValueError keep
    working, but a distinct type so the API can answer 409 without deciding the
    status by substring-matching the message -- which silently reclassified
    this error to 400 the moment the wording was corrected.
    """
