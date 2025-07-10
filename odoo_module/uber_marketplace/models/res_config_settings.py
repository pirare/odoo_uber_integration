from odoo import models, fields, api


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ubereats_default_config_id = fields.Many2one('ubereats.config',
                                                 string='Default Uber Eats Configuration',
                                                 config_parameter='uber_marketplace.default_config_id')