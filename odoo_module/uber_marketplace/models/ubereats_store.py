from odoo import models, fields, api, _
from odoo.exceptions import UserError
from ubereats_api import UberEatsAPI
import requests
import logging


_logger = logging.getLogger(__name__)

class UberEatsStore(models.Model):
    _name = 'ubereats.store'
    _description = 'Uber Eats Store'

    config_id = fields.Many2one('ubereats.config', string='Configuration',
                                required=True, ondelete='cascade')
    pos_config_id = fields.Many2one('pos.config', string='POS Configuration')

    # Uber Eats Store Info
    store_id = fields.Char('Uber Eats Store ID', required=True)
    name = fields.Char('Store Name')
    external_store_id = fields.Char('External Store ID')

    # Integration configuration
    integrator_store_id = fields.Char('Integrator Store ID',
                                      help="Your internal store identifier")
    merchant_store_id = fields.Char('Merchant Store ID',
                                    help="Merchant's store number")

    # Integration settings
    is_order_manager = fields.Boolean('Is Order Manager', default=True)
    integration_enabled = fields.Boolean('Integration Enabled', default=False)
    require_manual_acceptance = fields.Boolean('Require Manual Acceptance', default=False)

    # Configuration
    allow_special_instructions = fields.Boolean('Allow Special Instructions', default=False)
    allow_single_use_items = fields.Boolean('Allow Single Use Items', default=False)

    # Webhooks config
    enable_order_release_webhooks = fields.Boolean('Order Release Webhooks', default=False)
    enable_schedule_order_webhooks = fields.Boolean('Schedule Order Webhooks', default=True)
    enable_delivery_status_webhooks = fields.Boolean('Delivery Status Webhooks', default=True)
    webhooks_version = fields.Char('Webhooks Version', default='1.0.0')

    # Status
    is_active = fields.Boolean('Active', default=False)
    last_sync_date = fields.Datetime('Last Sync Date')

    def activate_integration(self, user_token):
        """Activate integration using user authorization token"""
        self.ensure_one()

        config = self.config_id
        url = f"{config.api_base_url}/v1/eats/stores/{self.store_id}/pos_data"

        data = {
            'integrator_store_id': self.integrator_store_id or self.external_store_id,
            'merchant_store_id': self.merchant_store_id or self.name,
            'is_order_manager': self.is_order_manager,
            'require_manual_acceptance': self.require_manual_acceptance,
            'allowed_customer_requests': {
                'allow_special_instruction_requests': self.allow_special_instructions,
                'allow_single_use_items_requests': self.allow_single_use_items
            },
            'webhooks_config': {
                'order_release_webhooks': {
                    'is_enabled': self.enable_order_release_webhooks
                },
                'schedule_order_webhooks': {
                    'is_enabled': self.enable_schedule_order_webhooks
                },
                'delivery_status_webhooks': {
                    'is_enabled': self.enable_delivery_status_webhooks
                },
                'webhooks_version': self.webhooks_version
            }
        }

        headers = {'Authorization': f'Bearer {user_token}'}

        try:
            response = requests.post(url, json=data, headers=headers)
            response.raise_for_status()

            self.is_active = True
            self.integration_enabled = True
            self.last_sync_date = fields.Datetime.now()

            return True

        except requests.exceptions.RequestException as e:
            _logger.error(f"Failed to activate integration: {str(e)}")
            raise UserError(_(f"Failed to activate integration: {str(e)}"))


    def toggle_status(self):
        """Toggle store pause status"""
        self.ensure_one()

        if not self.is_active:
            return {'success': False, 'error': 'Store is not active'}

        api = UberEatsAPI(self.config_id)

        try:
            # Get current status
            new_status = not self.is_paused

            # Update via API
            api.update_store_status(self.store_id, new_status)

            # Update local status
            self.is_paused = new_status

            return {'success': True}

        except Exception as e:
            return {'success': False, 'error': str(e)}


    @api.model
    def sync_store_data(self):
        """Sync store data from Uber Eats"""
        for store in self:
            if not store.is_active:
                continue

            api = UberEatsAPI(store.config_id)

            try:
                # Get store details
                data = api.get_store_details(store.store_id)

                # Update store data
                store.write({
                    'name': data.get('name', store.name),
                    'external_store_id': data.get('external_store_id', store.external_store_id),
                    'last_sync_date': fields.Datetime.now(),
                })

            except Exception as e:
                _logger.error(f"Failed to sync store {store.store_id}: {str(e)}")