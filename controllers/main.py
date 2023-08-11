# Part of Odoo. See LICENSE file for full copyright and licensing details.

import base64
import io
import logging
import mimetypes
import re

from PyPDF2 import PdfFileReader

from odoo import http, models, tools, Command, _
from odoo.http import request, content_disposition
from odoo.addons.iap.tools import iap_tools
from odoo.addons.sign.controllers.main import Sign

_logger = logging.getLogger()


class Sign(Sign):

    def get_document_qweb_context(self, sign_request_id, token, **post):
        sign_request = http.request.env['sign.request'].sudo().browse(sign_request_id).exists()
        if not sign_request:
            return request.render('sign.deleted_sign_request')
        current_request_item = sign_request.request_item_ids.filtered(lambda r: r.access_token == token)
        _logger.info('----------------current_request_item----------------- %s', current_request_item)
        if not current_request_item and sign_request.access_token != token:
            return request.not_found()

        sign_item_types = http.request.env['sign.item.type'].sudo().search_read([])

        # Currently only Signature, Initials, Text are allowed to be added while signing
        edit_while_signing_allowed_type_ids = {
            request.env.ref('sign.sign_item_type_signature').id,
            request.env.ref('sign.sign_item_type_initial').id,
            request.env.ref('sign.sign_item_type_text').id,
        }
        for item_type in sign_item_types:
            item_type['edit_while_signing_allowed'] = item_type['id'] in edit_while_signing_allowed_type_ids

        if current_request_item:
            for item_type in sign_item_types:
                if item_type['auto_field']:
                    if item_type['sign_type'] == 'res.partner':
                        try:
                            auto_field = current_request_item.partner_id.mapped(item_type['auto_field'])
                            item_type['auto_value'] = auto_field[0] if auto_field and not isinstance(auto_field, models.BaseModel) else ''
                        except Exception:
                            item_type['auto_value'] = ''
                    elif item_type['sign_type'] == 'hr.employee':
                        try:
                            _logger.info('----------------partner----------------- %s', current_request_item.partner_id)
                            employee = self.env['hr.employee'].search([('address_home_id','=',current_request_item.partner_id.id)], limit=1)
                            _logger.info('----------------employee----------------- %s', employee)
                            auto_field = employee.mapped(item_type['auto_field'])
                            item_type['auto_value'] = auto_field[0] if auto_field and not isinstance(auto_field, models.BaseModel) else ''
                        except Exception as e:
                            item_type['auto_value'] = ''
                            _logger.exception("----------------employee errorr-----------------: %s" % str(e))
                if item_type['item_type'] in ['signature', 'initial']:
                    signature_field_name = 'sign_signature' if item_type['item_type'] == 'signature' else 'sign_initials'
                    user_signature = current_request_item._get_user_signature(signature_field_name)
                    user_signature_frame = current_request_item._get_user_signature_frame(signature_field_name+'_frame')
                    item_type['auto_value'] = 'data:image/png;base64,%s' % user_signature.decode() if user_signature else False
                    item_type['frame_value'] = 'data:image/png;base64,%s' % user_signature_frame.decode() if user_signature_frame else False

            if current_request_item.state == 'sent':
                """ When signer attempts to sign the request again,
                its localisation should be reset.
                We prefer having no/approximative (from geoip) information
                than having wrong old information (from geoip/browser)
                on the signer localisation.
                """
                current_request_item.write({
                    'latitude': request.geoip.get('latitude', 0.0),
                    'longitude': request.geoip.get('longitude', 0.0),
                })

        item_values = {}
        frame_values = {}
        sr_values = http.request.env['sign.request.item.value'].sudo().search([('sign_request_id', '=', sign_request.id), '|', ('sign_request_item_id', '=', current_request_item.id), ('sign_request_item_id.state', '=', 'completed')])
        for value in sr_values:
            item_values[value.sign_item_id.id] = value.value
            frame_values[value.sign_item_id.id] = value.frame_value

        if sign_request.state != 'shared':
            request.env['sign.log'].sudo().create({
                'sign_request_id': sign_request.id,
                'sign_request_item_id': current_request_item.id,
                'action': 'open',
            })

        return {
            'sign_request': sign_request,
            'current_request_item': current_request_item,
            'state_to_sign_request_items_map': dict(tools.groupby(sign_request.request_item_ids, lambda sri: sri.state)),
            'token': token,
            'nbComments': len(sign_request.message_ids.filtered(lambda m: m.message_type == 'comment')),
            'isPDF': (sign_request.template_id.attachment_id.mimetype.find('pdf') > -1),
            'webimage': re.match('image.*(gif|jpe|jpg|png)', sign_request.template_id.attachment_id.mimetype),
            'hasItems': len(sign_request.template_id.sign_item_ids) > 0,
            'sign_items': sign_request.template_id.sign_item_ids,
            'item_values': item_values,
            'frame_values': frame_values,
            'frame_hash': current_request_item.frame_hash if current_request_item else '',
            'role': current_request_item.role_id.id if current_request_item else 0,
            'role_name': current_request_item.role_id.name if current_request_item else '',
            'readonly': not (current_request_item and current_request_item.state == 'sent' and sign_request.state in ['sent', 'shared']),
            'sign_item_types': sign_item_types,
            'sign_item_select_options': sign_request.template_id.sign_item_ids.mapped('option_ids'),
            'refusal_allowed': sign_request.refusal_allowed and sign_request.state == 'sent',
            'portal': post.get('portal'),
            'company_id': (sign_request.communication_company_id or self.env.company).id,
        }
