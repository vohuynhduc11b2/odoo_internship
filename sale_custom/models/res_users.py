from odoo import models, fields

class ResUsers(models.Model):
    _inherit = 'res.users'
    slp_code = fields.Char("SlpCode", index=True)
    business_area = fields.Char("Business Area")
    branch = fields.Char("Branch")

    
