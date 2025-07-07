import requests
from odoo import models, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class UberEatsAPI:
    """Utility class for Uber Eats API calls"""

    def __init__(self, config):
        self.config = config
        self.base_url = config.api_base_url
        self.auth_url = config.auth_base_url

    def _get_headers(self, token=None):
        """Get headers for API request"""
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

        if token:
            headers['Authorization'] = f'Bearer {token}'
        elif self.config.active_token_id:
            headers.update(self.config.active_token_id.get_auth_header())
        else:
            raise UserError(_("No valid token available"))

        return headers

    def get_stores(self, user_token):
        """Get list of stores accessible by the user"""
        url = f"{self.base_url}/v1/eats/stores"
        headers = self._get_headers(token=user_token)

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            _logger.error(f"Failed to get stores: {str(e)}")
            raise UserError(_(f"Failed to get stores: {str(e)}"))

    def get_store_details(self, store_id):
        """Get detailed information about a store"""
        url = f"{self.base_url}/v1/eats/stores/{store_id}"
        headers = self._get_headers()

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            _logger.error(f"Failed to get store details: {str(e)}")
            raise UserError(_(f"Failed to get store details: {str(e)}"))

    def update_store_status(self, store_id, paused):
        """Update store availability status"""
        url = f"{self.base_url}/v1/eats/stores/{store_id}/status"
        headers = self._get_headers()
        data = {'paused': paused}

        try:
            response = requests.patch(url, json=data, headers=headers)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            _logger.error(f"Failed to update store status: {str(e)}")
            raise UserError(_(f"Failed to update store status: {str(e)}"))