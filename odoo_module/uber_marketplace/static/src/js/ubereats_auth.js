odoo.define('uber_marketplace.auth', function (require) {
"use strict";

var core = require('web.core');
var Dialog = require('web.Dialog');
var _t = core._t;

/**
 * OAuth Authorization Handler
 */
var UberEatsAuth = {

    /**
     * Open OAuth authorization window
     */
    openAuthWindow: function(url) {
        var width = 600;
        var height = 700;
        var left = (screen.width - width) / 2;
        var top = (screen.height - height) / 2;

        var authWindow = window.open(
            url,
            'UberEatsAuth',
            'width=' + width + ',height=' + height + ',left=' + left + ',top=' + top
        );

        // Check if window was closed
        var checkInterval = setInterval(function() {
            if (authWindow.closed) {
                clearInterval(checkInterval);
                // Trigger callback check
                window.location.reload();
            }
        }, 1000);
    },

    /**
     * Handle OAuth callback
     */
    handleCallback: function(code, state) {
        // Store the code in session storage
        if (code) {
            sessionStorage.setItem('ubereats_auth_code', code);
            sessionStorage.setItem('ubereats_auth_state', state);

            // Close the window
            if (window.opener) {
                window.close();
            }
        }
    },

    /**
     * Get stored auth code
     */
    getStoredAuthCode: function() {
        var code = sessionStorage.getItem('ubereats_auth_code');
        var state = sessionStorage.getItem('ubereats_auth_state');

        // Clear the stored values
        sessionStorage.removeItem('ubereats_auth_code');
        sessionStorage.removeItem('ubereats_auth_state');

        return {
            code: code,
            state: state
        };
    }
};

// Export for use in other modules
return UberEatsAuth;

});