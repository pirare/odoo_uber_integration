from odoo import models, fields, api


class PosConfig(models.Model):
    _inherit = 'pos.config'

    ubereats_enabled = fields.Boolean('Enable Uber Eats Integration')
    ubereats_store_id = fields.Many2one('ubereats.store', string='Uber Eats Store')
    ubereats_config_id = fields.Many2one('ubereats.config',
                                         related='ubereats_store_id.config_id',
                                         string='Uber Eats Configuration')