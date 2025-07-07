from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import json
import logging
from datetime import datetime, timedelta
import base64

_logger = logging.getLogger(__name__)


class UberEatsConfig(models.Model):
    _name = 'ubereats.config'
    _description = 'Uber Eats Configuration'
    _rec_name = 'name'

    name = fields.Char('Configuration Name', required=True)
    client_id = fields.Char('Client ID', required=True, help="Your Uber Eats API Client ID")
    client_secret = fields.Char('Client Secret', required=True, help="Your Uber Eats API Client Secret")
    redirect_uri = fields.Char('Redirect URI', required=True,
                               default=lambda self: self._get_default_redirect_uri(),
                               help="OAuth redirect URI registered with Uber")

    # Environment
    is_sandbox = fields.Boolean('Sandbox Mode', default=True,
                                help="Use Uber Eats sandbox environment for testing")

    # API Endpoints
    auth_base_url = fields.Char('Auth Base URL', compute='_compute_urls', store=True)
    api_base_url = fields.Char('API Base URL', compute='_compute_urls', store=True)

    # Status
    is_active = fields.Boolean('Active', default=False)
    last_auth_date = fields.Datetime('Last Authorization Date', readonly=True)

    # Tokens
    token_ids = fields.One2many('ubereats.token', 'config_id', string='Tokens')
    active_token_id = fields.Many2one('ubereats.token', string='Active Token',
                                      compute='_compute_active_token')

    # Store connections
    store_ids = fields.One2many('ubereats.store', 'config_id', string='Connected Stores')

    @api.depends('is_sandbox')
    def _compute_urls(self):
        for config in self:
            config.auth_base_url = 'https://auth.uber.com'
            config.api_base_url = 'https://api.uber.com'

    def _get_default_redirect_uri(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        return f"{base_url}/ubereats/oauth/callback"

    @api.depends('token_ids')
    def _compute_active_token(self):
        for config in self:
            config.active_token_id = config.token_ids.filtered(
                lambda t: t.is_valid and t.grant_type == 'client_credentials'
            )[:1]

    def action_authorize(self):
        """Open OAuth authorization wizard"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Uber Eats Authorization',
            'res_model': 'ubereats.auth.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_config_id': self.id,
            }
        }

    def generate_client_credentials_token(self):
        """Generate a client credentials token for API operations"""
        self.ensure_one()

        url = f"{self.auth_base_url}/oauth/v2/token"
        data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'client_credentials',
            'scope': 'eats.store eats.order eats.store.status.write eats.report'
        }

        try:
            response = requests.post(url, data=data)
            response.raise_for_status()

            token_data = response.json()

            # Create token record
            token = self.env['ubereats.token'].create({
                'config_id': self.id,
                'access_token': token_data['access_token'],
                'token_type': token_data['token_type'],
                'expires_in': token_data['expires_in'],
                'scope': token_data['scope'],
                'grant_type': 'client_credentials',
            })

            self.is_active = True

            return token

        except requests.exceptions.RequestException as e:
            _logger.error(f"Failed to generate token: {str(e)}")
            raise UserError(_(f"Failed to generate token: {str(e)}"))

    def refresh_tokens(self):
        """Refresh all expired tokens"""
        for config in self:
            # Refresh client credentials tokens
            expired_tokens = config.token_ids.filtered(
                lambda t: not t.is_valid and t.grant_type == 'client_credentials'
            )

            if expired_tokens:
                config.generate_client_credentials_token()

    @api.model
    def _cron_refresh_tokens(self):
        """Cron job to refresh tokens"""
        configs = self.search([('is_active', '=', True)])
        configs.refresh_tokens()

    def test_connection(self):
        """Test the API connection"""
        self.ensure_one()

        try:
            # Try to generate a token
            token = self.generate_client_credentials_token()

            # Try a simple API call
            url = f"{self.api_base_url}/v1/eats/stores"
            headers = token.get_auth_header()

            response = requests.get(url, headers=headers)
            response.raise_for_status()

            return {'success': True}

        except Exception as e:
            return {'success': False, 'error': str(e)}