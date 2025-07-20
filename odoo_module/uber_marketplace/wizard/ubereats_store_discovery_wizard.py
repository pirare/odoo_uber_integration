from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class UberEatsStoreDiscoveryWizard(models.TransientModel):
    _name = 'ubereats.store.discovery.wizard'
    _description = 'Uber Eats Store Discovery Wizard'

    config_id = fields.Many2one('ubereats.config', string='Configuration', required=True)
    auth_token_id = fields.Many2one('ubereats.token', string='Authorization Token', required=True)

    discovered_store_ids = fields.One2many('ubereats.store.discovery.line', 'wizard_id',
                                           string='Discovered Stores')

    state = fields.Selection([
        ('init', 'Initialize'),
        ('discovered', 'Stores Discovered')
    ], default='init')

    def action_discover_stores(self):
        """Discover stores from Uber Eats API"""
        self.ensure_one()

        try:
            from ..models.ubereats_api import UberEatsAPI

            api = UberEatsAPI(self.config_id)
            stores_data = api.get_stores(self.auth_token_id.access_token)

            # Clear existing lines
            self.discovered_store_ids = [(5, 0, 0)]

            # Create new lines
            lines = []
            for store_data in stores_data.get('stores', []):
                # Check if store already exists
                existing = self.env['ubereats.store'].search([
                    ('store_id', '=', store_data['store_id']),
                    ('config_id', '=', self.config_id.id)
                ])

                lines.append((0, 0, {
                    'store_id': store_data['store_id'],
                    'name': store_data.get('name', ''),
                    'external_store_id': store_data.get('external_store_id', ''),
                    'is_existing': bool(existing),
                    'existing_store_id': existing.id if existing else False,
                }))

            self.discovered_store_ids = lines
            self.state = 'discovered'

            return {
                'type': 'ir.actions.act_window',
                'res_model': 'ubereats.store.discovery.wizard',
                'res_id': self.id,
                'view_mode': 'form',
                'target': 'new',
            }

        except Exception as e:
            _logger.error(f"Failed to discover stores: {str(e)}")
            raise UserError(_("Failed to discover stores: %s") % str(e))

    def action_import_selected(self):
        """Import selected stores"""
        selected_lines = self.discovered_store_ids.filtered('is_selected')

        if not selected_lines:
            raise UserError(_("Please select at least one store to import"))

        created_stores = self.env['ubereats.store']

        for line in selected_lines:
            if not line.is_existing:
                # Create new store
                store = self.env['ubereats.store'].create({
                    'config_id': self.config_id.id,
                    'store_id': line.store_id,
                    'name': line.name,
                    'external_store_id': line.external_store_id,
                    'pos_config_id': line.pos_config_id.id if line.pos_config_id else False,
                })
                created_stores |= store

                # Activate integration
                if line.auto_activate:
                    store.activate_integration(self.auth_token_id.access_token)

        # Return action to show created stores
        if created_stores:
            return {
                'type': 'ir.actions.act_window',
                'name': 'Imported Stores',
                'res_model': 'ubereats.store',
                'view_mode': 'tree,form',
                'domain': [('id', 'in', created_stores.ids)],
            }


class UberEatsStoreDiscoveryLine(models.TransientModel):
    _name = 'ubereats.store.discovery.line'
    _description = 'Uber Eats Store Discovery Line'

    wizard_id = fields.Many2one('ubereats.store.discovery.wizard', required=True, ondelete='cascade')

    # Store data
    store_id = fields.Char('Uber Eats Store ID', required=True)
    name = fields.Char('Store Name')
    external_store_id = fields.Char('External Store ID')

    # Selection
    is_selected = fields.Boolean('Select', default=True)
    is_existing = fields.Boolean('Already Exists')
    existing_store_id = fields.Many2one('ubereats.store', string='Existing Store')

    # Configuration
    pos_config_id = fields.Many2one('pos.config', string='POS Configuration')
    auto_activate = fields.Boolean('Auto Activate', default=True)