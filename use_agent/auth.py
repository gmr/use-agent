"""Google OAuth installed-app flow for Gmail."""

import logging
import pathlib

import google.auth.transport.requests
import google.oauth2.credentials
import google_auth_oauthlib.flow

LOGGER = logging.getLogger(__name__)


def load_credentials(
    *,
    credentials_file: pathlib.Path,
    token_file: pathlib.Path,
    scopes: tuple[str, ...],
) -> google.oauth2.credentials.Credentials:
    """Return valid Credentials, running the OAuth flow if needed.

    If a token file exists, load and refresh it. Otherwise, run the
    installed-app browser flow using ``credentials_file`` and persist
    the resulting token.
    """
    creds: google.oauth2.credentials.Credentials | None = None
    if token_file.exists():
        creds = (
            google.oauth2.credentials.Credentials.from_authorized_user_file(
                str(token_file), list(scopes)
            )
        )
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        LOGGER.info('refreshing Gmail OAuth token')
        creds.refresh(google.auth.transport.requests.Request())
        _write_token(token_file, creds)
        return creds
    if not credentials_file.exists():
        raise FileNotFoundError(
            f'OAuth client secret not found at {credentials_file}. '
            'Create one in Google Cloud Console (Desktop app) and '
            'place it there, or set USE_AGENT_CREDENTIALS.'
        )
    LOGGER.info('running OAuth installed-app flow')
    flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
        str(credentials_file), list(scopes)
    )
    creds = flow.run_local_server(port=0)
    _write_token(token_file, creds)
    return creds


def _write_token(
    path: pathlib.Path,
    creds: google.oauth2.credentials.Credentials,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json())
    path.chmod(0o600)
