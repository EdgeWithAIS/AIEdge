"""Errors whose message is safe to show on the web page.

Any other error from a service the app calls (FEMA, Claude) is shown as a
generic message, and the details go to the server log only.
"""


class PublicError(RuntimeError):
    """A failure with a message written for the person using the page."""
