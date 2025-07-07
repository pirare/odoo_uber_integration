from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime, timedelta
import logging

_logger = logging.getLogger(__name__)


class UberEatsToken(models.Model):
    _name = 'ubereats.token'
    _description = 'Uber Eats OAuth Token'
    _order = 'create_date desc'

    config_id = fields.Many2one('ubereats.config', string='Configuration',
                                required=True, ondelete='cascade')

    # Token data
    access_token = fields.Text('Access Token', required=True)
    refresh_token = fields.Text('Refresh Token')
    token_type = fields.Char('Token Type', default='Bearer')
    expires_in = fields.Integer('Expires In (seconds)')
    expires_at = fields.Datetime('Expires At', compute='_compute_expires_at', store=True)
    scope = fields.Char('Scope')
    grant_type = fields.Selection([
        ('client_credentials', 'Client Credentials'),
        ('authorization_code', 'Authorization Code')
    ], string='Grant Type', required=True)

    # Status
    is_valid = fields.Boolean('Is Valid', compute='_compute_is_valid')

    @api.depends('create_date', 'expires_in')
    def _compute_expires_at(self):
        for token in self:
            if token.create_date and token.expires_in:
                token.expires_at = token.create_date + timedelta(seconds=token.expires_in)
            else:
                token.expires_at = False

    @api.depends('expires_at')
    def _compute_is_valid(self):
        now = datetime.now()
        for token in self:
            if token.expires_at:
                # Consider token invalid 5 minutes before expiration
                token.is_valid = token.expires_at > (now + timedelta(minutes=5))
            else:
                token.is_valid = False

    def get_auth_header(self):
        """Get authorization header for API requests"""
        self.ensure_one()
        if not self.is_valid:
            raise UserError(_("Token has expired. Please refresh the token."))
        return {'Authorization': f'{self.token_type} {self.access_token}'}