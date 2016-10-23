# -*- coding: utf-8 -*-
# © 2016 Eficent Business and IT Consulting Services S.L.
#   (http://www.eficent.com)
# © 2016 Aleph Objects, Inc. (https://www.alephobjects.com/)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).

from openerp import api, fields, models, _
from datetime import timedelta
from openerp.addons import decimal_precision as dp

UNIT = dp.get_precision('Product Unit of Measure')


_PRIORITY_LEVEL = [
    ('red', 'Red'),
    ('yellow', 'Yellow'),
    ('green', 'Green')
]


class StockWarehouseOrderpoint(models.Model):
    _inherit = 'stock.warehouse.orderpoint'
    _description = "Stock Buffer"

    @api.multi
    @api.depends("adu_calculation_method")
    def _compute_adu(self):
        for rec in self:
            if rec.adu_calculation_method:
                rec.adu = rec.adu_calculation_method.compute_adu(rec)

    @api.multi
    @api.depends("dlt", "adu", "buffer_profile_id.lead_time_factor",
                 "buffer_profile_id.variability_factor")
    def _compute_red_zone(self):
        for rec in self:
            rec.red_base_qty = rec.dlt * rec.adu * \
                rec.buffer_profile_id.lead_time_factor
            rec.red_safety_qty = rec.red_base_qty * \
                rec.buffer_profile_id.variability_factor
            rec.red_zone_qty = rec.red_base_qty + rec.red_safety_qty

    @api.multi
    @api.depends("dlt", "adu", "buffer_profile_id.lead_time_factor",
                 "red_zone_qty")
    def _compute_green_zone(self):
        for rec in self:
            # Using imposed or desired minimum order cycle
            rec.green_zone_oc = rec.order_cycle * rec.adu
            # Using lead time factor
            rec.green_zone_lt_factor = \
                rec.dlt*rec.adu*rec.buffer_profile_id.lead_time_factor
            # Using minimum order quantity
            rec.green_zone_moq = rec.minimum_order_quantity

            # The biggest option of the above will be used as the green zone
            #  value
            rec.green_zone_qty = max(rec.green_zone_oc,
                                     rec.green_zone_lt_factor,
                                     rec.green_zone_moq)

            rec.top_of_green = \
                rec.green_zone_qty + rec.yellow_zone_qty + rec.red_zone_qty

    @api.multi
    @api.depends("dlt", "adu", "buffer_profile_id.lead_time_factor",
                 "red_zone_qty")
    def _compute_yellow_zone(self):
        for rec in self:
            if rec.buffer_profile_id.replenish_method == 'min_max':
                rec.yellow_zone_qty = 0
            else:
                rec.yellow_zone_qty = rec.dlt * rec.adu
            rec.top_of_yellow = rec.yellow_zone_qty + rec.red_zone_qty

    @api.multi
    @api.depends("net_flow_position", "net_flow_position_percent",
                 "top_of_yellow", "top_of_red")
    def _compute_planning_priority(self):
        for rec in self:
            if rec.net_flow_position >= rec.top_of_yellow:
                rec.planning_priority_level = 'green'
            elif rec.net_flow_position >= rec.top_of_red:
                rec.planning_priority_level = 'yellow'
            else:
                rec.planning_priority_level = 'red'
            rec.planning_priority = '%s (%s %%)' % (
                rec.planning_priority_level.title(),
                rec.net_flow_position_percent)

    def _search_planning_priority_level(self, operator, value):
        """Search on the planning priority by evaluating on all
        records"""
        all_records = self.search([])

        if operator == '=':
            found_ids = [a.id for a in all_records
                         if a.planning_priority_level == value]
        elif operator == 'in' and isinstance(value, list):
            found_ids = [a.id for a in all_records
                         if a.planning_priority_level in value]
        elif operator in ("!=", "<>"):
            found_ids = [a.id for a in all_records
                         if a.planning_priority_level != value]
        elif operator == 'not in' and isinstance(value, list):
            found_ids = [a.id for a in all_records
                         if a.planning_priority_level not in value]
        else:
            raise NotImplementedError(
                'Search operator %s not implemented for value %s'
                % (operator, value)
            )
        return [('id', 'in', found_ids)]

    @api.multi
    @api.depends("product_location_qty",
                 "top_of_yellow", "top_of_red")
    def _compute_execution_priority(self):
        for rec in self:
            if rec.product_location_qty_available_not_res >= rec.top_of_red:
                rec.execution_priority_level = 'green'
            elif rec.product_location_qty >= rec.top_of_red*0.5:
                rec.execution_priority_level = 'yellow'
            else:
                rec.execution_priority_level = 'red'
            if rec.top_of_green:
                on_hand_percent = round((
                    rec.product_location_qty_available_not_res /
                    rec.top_of_green), 2) * 100
            else:
                on_hand_percent = 0.0
            rec.execution_priority = '%s (%s %%)' % (
                rec.execution_priority_level.title(), on_hand_percent)

    def _search_execution_priority_level(self, operator, value):
        """Search on the execution priority by evaluating on all
        records"""
        all_records = self.search([])

        if operator == '=':
            found_ids = [a.id for a in all_records
                         if a.execution_priority_level == value]
        elif operator == 'in' and isinstance(value, list):
            found_ids = [a.id for a in all_records
                         if a.execution_priority_level in value]
        elif operator in ("!=", "<>"):
            found_ids = [a.id for a in all_records
                         if a.execution_priority_level != value]
        elif operator == 'not in' and isinstance(value, list):
            found_ids = [a.id for a in all_records
                         if a.execution_priority_level not in value]
        else:
            raise NotImplementedError(
                'Search operator %s not implemented for value %s'
                % (operator, value)
            )
        return [('id', 'in', found_ids)]

    @api.multi
    def _search_stock_moves_qualified_demand_domain(self):
        self.ensure_one()
        horizon = self.order_spike_horizon
        if not horizon:
            date_to = fields.Date.to_string(fields.date.today())

        else:
            date_to = fields.Date.to_string(fields.date.today() + timedelta(
                days=horizon))
        date_from = fields.Date.to_string(fields.date.today())
        locations = self.env['stock.location'].search(
            [('id', 'child_of', [self.location_id.id])])
        return [('product_id', '=', self.product_id.id),
                ('state', 'in', ['draft', 'waiting', 'confirmed',
                                 'assigned']),
                ('location_id', 'in', locations.ids),
                ('date', '>=', date_from), ('date', '<=', date_to)]

    @api.multi
    @api.depends("outgoing_location_qty", "product_id", "location_id")
    def _compute_qualified_demand(self):
        for rec in self:
            rec.qualified_demand = 0.0
            domain = rec._search_stock_moves_qualified_demand_domain()
            moves = self.env['stock.move'].search(domain)
            demand_by_days = {}
            move_dates = [fields.Datetime.from_string(dt).date()
                              for dt in moves.mapped('date')]
            for move_date in move_dates:
                demand_by_days[move_date] = 0.0
            for move in moves:
                date = fields.Datetime.from_string(move.date).date()
                demand_by_days[date] += move.product_qty
            for date in demand_by_days.keys():
                if demand_by_days[date] >= rec.order_spike_threshold \
                        or date == fields.date.today():
                    rec.qualified_demand += demand_by_days[date]

    @api.multi
    @api.depends("product_location_qty", "incoming_location_qty",
                 "top_of_green", "qualified_demand")
    def _compute_net_flow_position(self):
        for rec in self:
            rec.net_flow_position = rec.product_location_qty + \
                                    rec.incoming_location_qty - \
                                    rec.qualified_demand
            usage = 0.0
            if rec.top_of_green:
                usage = round(rec.net_flow_position /
                              rec.top_of_green, 2) * 100
            rec.net_flow_position_percent = usage

    @api.multi
    @api.depends("qualified_demand", "top_of_green")
    def _compute_procure_recommended(self):
        for rec in self:
            procure_recommended_qty = 0.0
            if rec.net_flow_position < rec.top_of_green:
                qty = rec.top_of_green - rec.net_flow_position\
                      - rec.to_approve_qty
                if qty >= 0.0:
                    procure_recommended_qty = qty
                    rec.procure_recommended_date = \
                        fields.date.today() + timedelta(days=int(rec.dlt))
            rec.procure_recommended_qty = procure_recommended_qty

    @api.multi
    @api.depends("red_zone_qty")
    def _compute_order_spike_threshold(self):
        # TODO: Add various methods to compute the spike threshold
        for rec in self:
            rec.order_spike_threshold = 0.5 * rec.red_zone_qty

    @api.model
    def _get_to_approve_qty(self, procurement):
        """Method to obtain the quantity to approve. We assume that by
        default all stock pickings are approved. We focus on purchase orders"""
        uom_obj = self.env['product.uom']
        qty = uom_obj._compute_qty_obj(
            procurement.product_uom,
            procurement.product_qty,
            procurement.product_id.uom_id)
        return qty

    @api.multi
    def _compute_procured_pending_approval_qty(self):
        for rec in self:
            to_approve_qty = 0.0
            procurements = self.env['procurement.order'].search(
                [('location_id', '=', rec.location_id.id),
                 ('product_id', '=', rec.product_id.id),
                 ('to_approve', '=', True)])
            for procurement in procurements:
                to_approve_qty += self._get_to_approve_qty(procurement)
            rec.to_approve_qty = to_approve_qty

    buffer_profile_id = fields.Many2one(
        comodel_name='stock.buffer.profile',
        string="Buffer Profile")
    dlt = fields.Float(string="Decoupled Lead Time (days)")
    adu = fields.Float(string="Average Daily Usage (ADU)",
                       compute="_compute_adu",
                       default=0.0)
    adu_calculation_method = fields.Many2one(
        comodel_name="product.adu.calculation.method",
        string="ADU calculation method")
    adu_fixed = fields.Float(string="Fixed ADU",
                             default=1.0)
    order_cycle = fields.Float(string="Minimum Order Cycle (days)")
    minimum_order_quantity = fields.Float(string="Minimum Order Quantity")
    red_base_qty = fields.Float(string="Red Base Qty",
                                compute="_compute_red_zone")
    red_safety_qty = fields.Float(string="Red Safety Qty",
                                  compute="_compute_red_zone")
    red_zone_qty = fields.Float(string="Red Zone Qty",
                                compute="_compute_red_zone")
    top_of_red = fields.Float(string="Top of Red",
                              related="red_zone_qty")
    green_zone_qty = fields.Float(string="Green Zone Qty",
                                  compute="_compute_green_zone")
    green_zone_lt_factor = fields.Float(string="Green Zone Lead Time Factor",
                                        compute="_compute_green_zone",
                                        help="Green zone Lead Time Factor")
    green_zone_moq = fields.Float(string="Green Zone Minimum Order Quantity",
                                  compute="_compute_green_zone",
                                  help="Green zone minimum order quantity")
    green_zone_oc = fields.Float(string="Green Zone Order Cycle",
                                 compute="_compute_green_zone",
                                 help="Green zone order cycle")
    yellow_zone_qty = fields.Float(string="Yellow Zone Qty",
                                   compute="_compute_yellow_zone")
    top_of_yellow = fields.Float(string="Top of Yellow",
                                 compute="_compute_yellow_zone")
    top_of_green = fields.Float(string="Top of Green",
                                compute="_compute_green_zone")
    order_spike_horizon = fields. Float(string="Order Spike Horizon")
    order_spike_threshold = fields.Float(
        string="Order Spike Threshold",
        compute="_compute_order_spike_threshold")
    qualified_demand = fields.Float(string="Qualified demand",
                                    compute="_compute_qualified_demand")
    net_flow_position = fields.Float(
        string="Net flow position",
        compute="_compute_net_flow_position")
    net_flow_position_percent = fields.Float(
        string="Net flow position (% of TOG)",
        compute="_compute_net_flow_position")
    planning_priority_level = fields.Selection(
        string="Planning Priority Level",
        selection=_PRIORITY_LEVEL,
        compute="_compute_planning_priority",
        search="_search_planning_priority_level")
    planning_priority = fields.Char(
        string="Planning priority",
        compute="_compute_planning_priority")
    execution_priority_level = fields.Selection(
        string="On-Hand Alert Level",
        selection=_PRIORITY_LEVEL,
        compute="_compute_execution_priority",
        search="_search_execution_priority_level")
    execution_priority = fields.Char(
        string="On-Hand Alert",
        compute="_compute_execution_priority")
    procure_recommended_qty = fields.Float(
        string='Procure recommendation',
        compute="_compute_procure_recommended")
    procure_recommended_date = fields.Date(
        string='Request Date',
        compute="_compute_procure_recommended")
    to_approve_qty = fields.Float(
        string='Procured pending approval',
        compute="_compute_procured_pending_approval_qty")
    product_location_qty_available_not_res = fields.Float(
        string='Quantity On Location (Unreserved)', digits=UNIT,
        compute='_product_available_qty')

    @api.multi
    @api.onchange("red_zone_qty")
    def onchange_red_zone_qty(self):
        for rec in self:
            rec.product_min_qty = self.red_zone_qty

    @api.multi
    @api.onchange("top_of_green")
    def onchange_green_zone_qty(self):
        for rec in self:
            rec.product_max_qty = self.top_of_green

    @api.model
    def _prepare_procurement_context(self):
        return {
            'default_date_planned': self.procure_recommended_date,
            'default_product_qty': self.procure_recommended_qty,
            'default_product_id': self.product_id.id,
            'default_product_uom': self.product_id.uom_id.id,
            'default_location_id': self.location_id.id,
            'default_warehouse_id': self.warehouse_id.id,
            'default_name': self.name,
            'default_origin': self.name,
        }

    @api.multi
    def button_create_procurement(self):
        self.ensure_one()
        ctx = self._prepare_procurement_context()
        return {

            'name': _('Procurement Order'),
            'view_type': 'form',
            'view_mode': 'form, tree',
            'res_model': 'procurement.order',
            'view_id': False,
            'context': str(ctx),
            'type': 'ir.actions.act_window'
        }

    @api.model
    def _search_open_stock_moves_domain(self):
        return [('product_id', '=', self.product_id.id),
                ('state', 'in', ['draft', 'waiting', 'confirmed',
                                 'assigned']),
                ('location_dest_id', '=', self.location_id.id)]

    @api.model
    def _stock_move_tree_view(self, lines):
        views = []
        tree_view = self.env.ref('stock.view_move_tree', False)
        if tree_view:
            views += [(tree_view.id, 'tree')]
        form_view = self.env.ref(
            'stock.view_move_form', False)
        if form_view:
            views += [(form_view.id, 'form')]

        return {'type': 'ir.actions.act_window',
                'res_model': 'stock.move',
                'view_type': 'form',
                'views': views,
                'view_mode': 'tree,form',
                'domain': str([('id', 'in', lines.ids)])
                }

    @api.multi
    def open_moves(self):
        self.ensure_one()
        """ Utility method used to add an "Open Moves" button in the buffer
        planning view"""
        domain = self._search_open_stock_moves_domain()
        records = self.env['stock.move'].search(domain)
        return self._stock_move_tree_view(records)

    @api.multi
    def name_get(self):
        """Use the company name and template as name."""
        if 'name_show_extended' in self.env.context:
            res = []
            for orderpoint in self:
                name = orderpoint.name
                if orderpoint.product_id.default_code:
                    name += " [%s]" % orderpoint.product_id.default_code
                name += " %s" % orderpoint.product_id.name
                name += " - %s" % orderpoint.warehouse_id.name
                name += " - %s" % orderpoint.location_id.name
                res.append((orderpoint.id, name))
            return res
        return super(StockWarehouseOrderpoint, self).name_get()

    @api.multi
    def _product_available_qty(self):
        super(StockWarehouseOrderpoint, self)._product_available_qty()
        for rec in self:
            product_available = rec.product_id.with_context(
                location=rec.location_id.id
            )._product_available()[rec.product_id.id]
            rec.product_location_qty_available_not_res = product_available[
                'qty_available_not_res']