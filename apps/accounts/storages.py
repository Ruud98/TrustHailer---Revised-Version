"""
Where identity documents are kept, which is nowhere the public can reach.

THE BUG THIS FIXES
------------------
`VerificationDocument.file` used the default storage. In development that is a
directory under `MEDIA_ROOT`, which `runserver` serves at `/media/`. In
production it is the R2 bucket configured with `querystring_auth=False` — a
public bucket, by design, because it holds car photos and avatars and we do not
want to sign a URL for every thumbnail on a browse page.

Either way, an ID scan landed somewhere a URL could reach. The filename is a
random hex string, so it was not enumerable, but "nobody will guess the URL" is
not access control, and the URL is written into an admin page and every
reviewer's browser history.

HOW IT WORKS NOW
----------------
Documents go to a separate storage alias, `kyc`. In production that is a second
R2 bucket with no public access; in development it is a directory outside
`MEDIA_ROOT` that no URL maps to. Nothing renders a document URL anywhere —
reviewers read the file through a staff-only view that streams it (see
`accounts.views.kyc_document`), so access is checked by Django on every read
rather than by the obscurity of a path.

That view is also the only place a read can be logged, which matters: looking
at somebody's ID is an event worth having a record of.
"""
from django.core.files.storage import FileSystemStorage, storages


class PrivateFileSystemStorage(FileSystemStorage):
    """
    Local storage for files that must never be addressable.

    WHY THIS SUBCLASS EXISTS
    ------------------------
    Passing `base_url=None` to `FileSystemStorage` does NOT mean "no URL". Its
    `base_url` property falls back to `settings.MEDIA_URL` when the value is
    None, so `document.file.url` cheerfully returns `/media/kyc/…` — a URL that
    happens to 404 today only because `runserver` serves MEDIA_ROOT and the
    file is not in it. That is accidental safety, and accidental safety stops
    being safety the moment somebody adds a route.

    Raising here makes the guarantee real and loud: any code that tries to
    render a link to an identity document fails immediately and visibly, in
    development, rather than quietly emitting a path in production. A test
    holds this.
    """

    def url(self, name):
        raise ValueError(
            "Identity documents have no URL. Read them through "
            "accounts.views.kyc_document, which checks staff access and logs it."
        )


def kyc_storage():
    """
    Resolve the private storage at call time.

    A callable rather than an instance, so the environment decides — the
    reference serialised into the migration is this function, not a bucket
    name, and tests can point it at a throwaway directory without a migration.
    """
    return storages["kyc"]
