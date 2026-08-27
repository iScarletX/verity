"""Generate the historical research prototype's macOS Seatbelt profile.

This profile is **not** a sufficient boundary for untrusted code. Although it
denies network operations and limits writes to a staging directory, it allows
``file-read*`` across the host plus ``process-exec`` and ``process-fork``.
Those permissions expose host data to the reviewed process and leave escape,
resource-tree, observer-tampering, and cleanup gaps that the in-process audit
hook cannot close. The product and standalone user paths therefore never use
this profile; it remains only for direct prototype tests pending a container or
microVM redesign.

The profile text has one caller-supplied value: the escaped, absolute staging
directory used by those controlled tests.
"""

from __future__ import annotations


def _escape_sb_string(path: str) -> str:
    """Escape a path for embedding inside a Seatbelt ``"..."`` literal.

    Seatbelt profile strings use ordinary Scheme-style string escaping:
    backslash and double-quote must be escaped. Paths under a tmpdir we
    created ourselves never contain these characters in practice, but we
    escape defensively since this string is built from a runtime path.
    """
    return path.replace("\\", "\\\\").replace('"', '\\"')


def build_sandbox_profile(tmpdir: str) -> str:
    """Return the full ``.sb`` profile text scoped to ``tmpdir``.

    ``tmpdir`` must be an absolute, already-resolved path (the caller is
    responsible for calling this only after the staging directory has
    been created and, ideally, realpath-resolved so writes cannot escape
    through an unresolved symlink component).
    """
    escaped = _escape_sb_string(tmpdir)
    return (
        "(version 1)\n"
        "(deny default)\n"
        "(allow file-read*)\n"
        f'(allow file-write* (subpath "{escaped}"))\n'
        "(allow process-exec)\n"
        "(allow process-fork)\n"
        "(allow signal)\n"
        "(allow sysctl-read)\n"
    )
