define([
        'backbone',
        'collections/paginated_collection',
        'models/offer_model'
    ],
    function (Backbone,
              PaginatedCollection,
              OfferModel) {
        'use strict';

        return PaginatedCollection.extend({
            model: OfferModel,
            url: '/api/v2/vouchers/offers/',

            initialize: function() {
                this.empty = false;
                this.page = 1;
                this.perPage = 6;
                this.empty = false;
                this.updateLimits();
                this.on('update', this.updateNumberOfPages);
            },

            parse: function(response) {
                this._super(response);
                if (response.results.length === 0) {
                    this.empty = true;
                }
                return response.results;
            },

            updateNumberOfPages: function() {
                this.numberOfPages = Math.ceil(this.length / this.perPage);
            },

            updateLimits: function() {
                this.lowerLimit = (this.page - 1) * this.perPage;
                this.upperLimit = this.page * this.perPage;
            },

            goToPage: function(pageNumber) {
                this.page = pageNumber;
                this.updateLimits();
                return this.slice(this.lowerLimit, this.upperLimit);
            },

            nextPage: function() {
                if (this.onLastPage()) {
                    return false;
                } else {
                    return this.goToPage(this.page + 1);
                }
            },

            previousPage: function() {
                if (this.onFirstPage()) {
                    return false;
                } else {
                    return this.goToPage(this.page - 1);
                }
            },

            onFirstPage: function() {
                return this.page === 1;
            },

            onLastPage: function() {
                return this.page === this.numberOfPages;
            }
        });
    }
);
