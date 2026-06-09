"""NinjaOne API v2 client module."""

import requests
from typing import Optional, List, Dict


class NinjaOneError(Exception):
    """Base exception for NinjaOne client errors."""
    pass


class NinjaOneAuthError(NinjaOneError):
    """Raised when authentication with NinjaOne fails."""
    pass


class NinjaOneAPIError(NinjaOneError):
    """Raised when a NinjaOne API request returns a 4xx/5xx status."""
    pass


class NinjaOneClient:
    """Client for interacting with the NinjaOne API v2."""

    REGIONS = {
        'US': 'app.ninjarmm.com',
        'US2': 'us2.ninjarmm.com',
        'EU': 'eu.ninjarmm.com',
        'CA': 'ca.ninjarmm.com',
        'OC': 'oc.ninjarmm.com',
    }

    def __init__(self, client_id: str, client_secret: str, region: str = 'US', base_url: Optional[str] = None):
        """Initialize the NinjaOne client.

        Args:
            client_id: OAuth client ID.
            client_secret: OAuth client secret.
            region: NinjaOne region key. Defaults to 'US'.
            base_url: Custom API base URL. If provided, overrides the region map.

        Raises:
            ValueError: If the region is not supported.
        """
        if base_url:
            self._base_url = base_url.rstrip('/')
            self._region = region
        else:
            if region not in self.REGIONS:
                raise ValueError(f"Unsupported region: {region}")
            self._region = region
            self._base_url = f"https://{self.REGIONS[region]}"

        self._client_id = client_id
        self._client_secret = client_secret
        self._token: Optional[str] = None

    @property
    def base_url(self) -> str:
        """Return the API base URL for the configured region."""
        return self._base_url

    def authenticate(self) -> str:
        """Authenticate with NinjaOne using client credentials.

        Returns:
            The access token string.

        Raises:
            NinjaOneAuthError: If authentication fails.
        """
        url = f"{self._base_url}/ws/oauth/token"
        data = {
            'grant_type': 'client_credentials',
            'client_id': self._client_id,
            'client_secret': self._client_secret,
            'scope': 'monitoring management',
        }

        resp = requests.post(url, data=data, timeout=30)
        if not resp.ok:
            raise NinjaOneAuthError(
                f"Authentication failed: {resp.status_code} {resp.text}"
            )

        payload = resp.json()
        token = payload.get('access_token')
        if not token:
            raise NinjaOneAuthError("No access_token in authentication response")

        self._token = token
        return token

    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """Send an authenticated request to the NinjaOne API.

        Ensures a valid token exists before sending. Handles 401 by
        re-authenticating once and retrying. Raises on 4xx/5xx after retries.

        Args:
            method: HTTP method (e.g. 'GET', 'POST').
            endpoint: API endpoint path (e.g. '/api/v2/organizations').
            **kwargs: Additional arguments passed to ``requests.request``.

        Returns:
            The ``requests.Response`` object.

        Raises:
            NinjaOneAPIError: If the API returns a 4xx/5xx status after retry.
        """
        if self._token is None:
            self.authenticate()

        url = f"{self._base_url}{endpoint}"
        headers = kwargs.pop('headers', {})
        headers['Authorization'] = f"Bearer {self._token}"

        resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)

        if resp.status_code == 401:
            self.authenticate()
            headers['Authorization'] = f"Bearer {self._token}"
            resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)

        if not resp.ok:
            raise NinjaOneAPIError(
                f"API error: {resp.status_code} {resp.text}"
            )

        return resp

    def get_organizations(self) -> List[Dict]:
        """Retrieve all organizations accessible to the authenticated client.

        Returns:
            A list of organization dictionaries.
        """
        resp = self._request('GET', '/api/v2/organizations')
        return resp.json()

    def get_locations(self, org_id: int) -> List[Dict]:
        """Retrieve locations for a specific organization.

        Args:
            org_id: The organization ID.

        Returns:
            A list of location dictionaries.
        """
        resp = self._request(
            'GET', f'/api/v2/organization/{org_id}/locations'
        )
        return resp.json()

    def list_organizations(self) -> List[tuple]:
        """Return a list of (org_id, org_name) tuples sorted by name."""
        orgs = self.get_organizations()
        return sorted([(o['id'], o['name']) for o in orgs], key=lambda x: x[1])

    def list_locations(self, org_id: int) -> List[tuple]:
        """Return a list of (loc_id, loc_name) tuples sorted by name."""
        locs = self.get_locations(org_id)
        return sorted([(l['id'], l['name']) for l in locs], key=lambda x: x[1])

    def get_org_by_name(self, name: str) -> Optional[tuple]:
        """Fuzzy-match organization name (case-insensitive substring).

        Returns:
            (org_id, org_name) or None if no match.
        """
        name_lower = name.lower()
        for org_id, org_name in self.list_organizations():
            if name_lower in org_name.lower():
                return (org_id, org_name)
        return None

    def get_location_by_name(self, org_id: int, name: str) -> Optional[tuple]:
        """Fuzzy-match location name (case-insensitive substring).

        Returns:
            (loc_id, loc_name) or None if no match.
        """
        name_lower = name.lower()
        for loc_id, loc_name in self.list_locations(org_id):
            if name_lower in loc_name.lower():
                return (loc_id, loc_name)
        return None

    def get_installer_url(
        self, org_id: int, location_id: int, installer_type: str = 'LINUX_DEB'
    ) -> str:
        """Retrieve a download URL for a device installer.

        Args:
            org_id: The organization ID.
            location_id: The location ID within the organization.
            installer_type: Installer type identifier. Defaults to 'LINUX_DEB'.

        Returns:
            The presigned installer URL string.
        """
        endpoint = (
            f'/v2/organization/{org_id}/location/{location_id}'
            f'/installer/{installer_type}'
        )
        resp = self._request('GET', endpoint)
        return resp.json()['url']
