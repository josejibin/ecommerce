define([
        'routers/offer_router',
        'views/offer_view',
        'pages/page',
        'collections/offer_collection'
    ],
    function (OfferRouter,
              OfferView,
              Page,
              OfferCollection) {
        'use strict';

        return Page.extend({
            title: gettext('Redeem'),

            initialize: function(options) {
                this.collection = new OfferCollection();
                this.view = new OfferView({code: options.code, collection: this.collection});
                // The collection needs to be fetch first in order to determine if the collection is empty
                // to display the error message, therefor the fetch is synchronous and done before render.
                this.collection.fetch({remove: false, data: {code: options.code, limit: 50}, async: false});
                this.render();
            }
        });
    }
);
