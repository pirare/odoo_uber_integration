from odoo import http
from odoo.http import request
import logging
import json

_logger = logging.getLogger(__name__)


class UberEatsController(http.Controller):

    @http.route('/ubereats/oauth/callback', type='http', auth='user', website=True)
    def oauth_callback(self, **kwargs):
        """Handle OAuth callback from Uber"""

        # Get parameters
        code = kwargs.get('code')
        state = kwargs.get('state')
        error = kwargs.get('error')

        if error:
            # Handle error
            error_description = kwargs.get('error_description', 'Unknown error')
            return request.render('uber_marketplace.oauth_error', {
                'error': error,
                'error_description': error_description
            })

        if not code:
            return request.render('uber_marketplace.oauth_error', {
                'error': 'missing_code',
                'error_description': 'Authorization code is missing'
            })

        # Find the wizard by state (you should implement state storage)
        # For now, we'll redirect to a manual input form
        return request.render('uber_marketplace.oauth_success', {
            'code': code,
            'state': state
        })

    @http.route('/ubereats/webhook/<string:store_id>', type='json', auth='public',
                methods=['POST'], csrf=False)
    def webhook_endpoint(self, store_id, **kwargs):
        """Handle webhooks from Uber Eats"""

        # Verify webhook authenticity (implement signature verification)
        # For now, basic implementation

        try:
            # Get the JSON data
            data = request.jsonrequest

            # Log the webhook
            _logger.info(f"Received webhook for store {store_id}: {json.dumps(data)}")

            # Find the store
            Store = request.env['ubereats.store'].sudo()
            store = Store.search([('store_id', '=', store_id)], limit=1)

            if not store:
                _logger.error(f"Store not found: {store_id}")
                return {'status': 'error', 'message': 'Store not found'}

            # Process webhook based on type
            event_type = data.get('event_type')

            if event_type == 'orders.notification':
                # Handle new order
                self._process_new_order(store, data)
            elif event_type == 'orders.scheduled.notification':
                # Handle scheduled order
                self._process_scheduled_order(store, data)
            elif event_type == 'orders.release':
                # Handle order release
                self._process_order_release(store, data)
            elif event_type == 'delivery.state_changed':
                # Handle delivery status update
                self._process_delivery_update(store, data)
            else:
                _logger.warning(f"Unknown event type: {event_type}")

            return {'status': 'success'}

        except Exception as e:
            _logger.error(f"Error processing webhook: {str(e)}")
            return {'status': 'error', 'message': str(e)}

    def _process_new_order(self, store, data):
        """Process new order webhook"""
        # This will be implemented when we add order management
        pass

    def _process_scheduled_order(self, store, data):
        """Process scheduled order webhook"""
        # This will be implemented when we add order management
        pass

    def _process_order_release(self, store, data):
        """Process order release webhook"""
        # This will be implemented when we add order management
        pass

    def _process_delivery_update(self, store, data):
        """Process delivery status update webhook"""
        # This will be implemented when we add order management
        pass

# Additional utility classes