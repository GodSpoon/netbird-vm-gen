"""Unit tests for deploy.lib.ninjaone_client."""

import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from deploy.lib.ninjaone_client import (
    NinjaOneClient,
    NinjaOneAuthError,
    NinjaOneAPIError,
)


def test_init_validates_region():
    """Invalid region should raise ValueError."""
    try:
        NinjaOneClient("id", "secret", region="XX")
        assert False, "Expected ValueError for invalid region"
    except ValueError as exc:
        assert "XX" in str(exc)


def test_init_sets_base_url():
    """Base URL should be constructed from region."""
    client = NinjaOneClient("id", "secret", region="EU")
    assert client.base_url == "https://eu.ninjarmm.com"


def test_authenticate_success():
    """Successful auth should store and return token."""
    client = NinjaOneClient("id", "secret")
    mock_resp = Mock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"access_token": "token-123"}

    with patch("requests.post", return_value=mock_resp) as mock_post:
        token = client.authenticate()

    assert token == "token-123"
    assert client._token == "token-123"
    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    assert kwargs["data"]["grant_type"] == "client_credentials"


def test_authenticate_failure():
    """Failed auth should raise NinjaOneAuthError."""
    client = NinjaOneClient("id", "secret")
    mock_resp = Mock()
    mock_resp.ok = False
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized"

    with patch("requests.post", return_value=mock_resp):
        try:
            client.authenticate()
            assert False, "Expected NinjaOneAuthError"
        except NinjaOneAuthError:
            pass


def test_get_organizations():
    """Should return list of orgs."""
    client = NinjaOneClient("id", "secret")
    client._token = "token-123"
    mock_resp = Mock()
    mock_resp.ok = True
    mock_resp.json.return_value = [{"id": 1, "name": "Org1"}]

    with patch("requests.request", return_value=mock_resp) as mock_req:
        orgs = client.get_organizations()

    assert len(orgs) == 1
    assert orgs[0]["name"] == "Org1"
    mock_req.assert_called_once()


def test_get_locations():
    """Should return list of locations for an org."""
    client = NinjaOneClient("id", "secret")
    client._token = "token-123"
    mock_resp = Mock()
    mock_resp.ok = True
    mock_resp.json.return_value = [{"id": 10, "name": "Main Office"}]

    with patch("requests.request", return_value=mock_resp) as mock_req:
        locs = client.get_locations(1)

    assert len(locs) == 1
    assert locs[0]["name"] == "Main Office"


def test_get_installer_url():
    """Should return URL string."""
    client = NinjaOneClient("id", "secret")
    client._token = "token-123"
    mock_resp = Mock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"url": "https://example.com/installer.deb"}

    with patch("requests.request", return_value=mock_resp) as mock_req:
        url = client.get_installer_url(1, 10)

    assert url == "https://example.com/installer.deb"
