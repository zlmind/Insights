"""The local publishing agent: poll the server, drive the CLIs, report back.

Runs on the owner's Windows machine, never on the server. Standard library
only, so installing it is `python -m agent` from a checkout, not a pip dance.
"""
