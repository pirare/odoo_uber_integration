odoo.define('ubereats_integration.config_form', function (require) {
"use strict";

var FormController = require('web.FormController');
var FormRenderer = require('web.FormRenderer');
var viewRegistry = require('web.view_registry');

/**
 * Custom Form Controller for Uber Eats Config
 */
var UberEatsConfigFormController = FormController.extend({

    /**
     * Override to handle custom buttons
     */
    _onButtonClicked: function (event) {
        if (event.data.attrs.name === 'action_test_connection') {
            this._testConnection();
        } else {
            this._super.apply(this, arguments);
        }
    },

    /**
     * Test API connection
     */
    _testConnection: function () {
        var self = this;

        this._rpc({
            model: 'ubereats.config',
            method: 'test_connection',
            args: [[this.renderer.state.res_id]],
        }).then(function (result) {
            if (result.success) {
                self.displayNotification({
                    title: _t("Success"),
                    message: _t("Connection test successful!"),
                    type: 'success',
                });
            } else {
                self.displayNotification({
                    title: _t("Error"),
                    message: result.error || _t("Connection test failed"),
                    type: 'danger',
                });
            }
        });
    },
});

// Register the custom controller
var UberEatsConfigFormView = FormView.extend({
    config: _.extend({}, FormView.prototype.config, {
        Controller: UberEatsConfigFormController,
    }),
});

viewRegistry.add('ubereats_config_form', UberEatsConfigFormView);

return UberEatsConfigFormController;

});

// static/src/js/ubereats_store_status.js
odoo.define('ubereats_integration.store_status', function (require) {
"use strict";

var Widget = require('web.Widget');
var widget_registry = require('web.widget_registry');
var core = require('web.core');
var _t = core._t;

/**
 * Store Status Toggle Widget
 */
var StoreStatusToggle = Widget.extend({
    template: 'UberEatsStoreStatusToggle',
    events: {
        'click .o_ubereats_status_toggle': '_onToggleClick',
    },

    /**
     * @override
     */
    init: function (parent, params) {
        this._super.apply(this, arguments);
        this.record = params.record;
        this.fieldName = params.fieldName;
    },

    /**
     * @override
     */
    start: function () {
        this._super.apply(this, arguments);
        this._render();
    },

    /**
     * Render the widget
     */
    _render: function () {
        var isPaused = this.record.data[this.fieldName];
        this.$el.toggleClass('o_ubereats_paused', isPaused);
        this.$el.toggleClass('o_ubereats_active', !isPaused);

        this.$('.o_ubereats_status_text').text(
            isPaused ? _t('Paused') : _t('Active')
        );
    },

    /**
     * Handle toggle click
     */
    _onToggleClick: function (ev) {
        ev.preventDefault();
        var self = this;

        this._rpc({
            model: 'ubereats.store',
            method: 'toggle_status',
            args: [[this.record.res_id]],
        }).then(function (result) {
            if (result.success) {
                self.record.data[self.fieldName] = !self.record.data[self.fieldName];
                self._render();

                self.displayNotification({
                    title: _t("Success"),
                    message: _t("Store status updated"),
                    type: 'success',
                });
            } else {
                self.displayNotification({
                    title: _t("Error"),
                    message: result.error || _t("Failed to update status"),
                    type: 'danger',
                });
            }
        });
    },
});

widget_registry.add('ubereats_store_status', StoreStatusToggle);

return StoreStatusToggle;

});
