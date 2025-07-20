from odoo import models, fields, api, _
from odoo.exceptions import UserError
import urllib.parse
import secrets
import string
import requests
import logging

_logger = logging.getLogger(__name__)

class UberEatsAuthWizard(models.TransientModel):
    _name = 'ubereats.auth.wizard'
    _description = 'Uber Eats Authorization Wizard'

    config_id = fields.Many2one('ubereats.config', string='Configuration', required=True)
    auth_url = fields.Char('Authorization URL', compute='_compute_auth_url')
    state = fields.Char('State', default=lambda self: self._generate_state())

    step = fields.Selection([
        ('authorize', 'Authorize'),
        ('callback', 'Callback')
    ], default='authorize')

    authorization_code = fields.Char('Authorization Code')

    def _generate_state(self):
        """Generate random state for OAuth security"""
        return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))

    @api.depends('config_id', 'state')
    def _compute_auth_url(self):
        for wizard in self:
            if wizard.config_id:
                params = {
                    'client_id': wizard.config_id.client_id,
                    'response_type': 'code',
                    'redirect_uri': wizard.config_id.redirect_uri,
                    'scope': 'eats.pos_provisioning',
                    'state': wizard.state
                }
                base_url = f"{wizard.config_id.auth_base_url}/oauth/v2/authorize"
                wizard.auth_url = f"{base_url}?{urllib.parse.urlencode(params)}"
            else:
                wizard.auth_url = False

    def action_open_auth_url(self):
        """Open authorization URL in new window"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': self.auth_url,
            'target': 'new',
        }

    def action_proceed_to_callback(self):
        """Proceed to callback step after OAuth authorization"""
        self.ensure_one()
        self.step = 'callback'
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ubereats.auth.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_process_callback(self):
        """Process the authorization callback"""
        self.ensure_one()

        if not self.authorization_code:
            raise UserError(_("Authorization code is required"))

        # Exchange code for token
        url = f"{self.config_id.auth_base_url}/oauth/v2/token"
        data = {
            'client_id': self.config_id.client_id,
            'client_secret': self.config_id.client_secret,
            'grant_type': 'authorization_code',
            'redirect_uri': self.config_id.redirect_uri,
            'code': self.authorization_code
        }

        try:
            response = requests.post(url, data=data)
            response.raise_for_status()

            token_data = response.json()

            # Create authorization token
            auth_token = self.env['ubereats.token'].create({
                'config_id': self.config_id.id,
                'access_token': token_data['access_token'],
                'refresh_token': token_data.get('refresh_token', ''),
                'token_type': token_data['token_type'],
                'expires_in': token_data['expires_in'],
                'scope': token_data['scope'],
                'grant_type': 'authorization_code',
            })

            self.config_id.last_auth_date = fields.Datetime.now()

            # Open store discovery wizard
            return {
                'type': 'ir.actions.act_window',
                'name': 'Discover Stores',
                'res_model': 'ubereats.store.discovery.wizard',
                'view_mode': 'form',
                'target': 'new',
                'context': {
                    'default_config_id': self.config_id.id,
                    'default_auth_token_id': auth_token.id,
                }
            }

        except requests.exceptions.RequestException as e:
            _logger.error(f"Failed to exchange code for token: {str(e)}")
            raise UserError(_(f"Failed to exchange code for token: {str(e)}"))


