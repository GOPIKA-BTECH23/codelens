"""Exceptions that tell the worker how to treat a failed job."""


class PermanentJobError(Exception):
    """The job can never succeed, so retrying is pointless (repository uninstalled, run row
    missing, malformed payload). The worker marks it `dead` at once. Any OTHER exception is
    assumed transient and goes back to the queue with exponential backoff."""
